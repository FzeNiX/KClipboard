#!/usr/bin/env python3
"""clipd - tiny Wayland clipboard daemon built on ext-data-control-v1 (or wlr-data-control).

KWin only exposes clipboard contents to *unfocused* clients through the data-control
protocol, which neither Qt nor wl-clipboard 2.2 can speak on Plasma 6.  This helper talks
to libwayland-client directly through ctypes, so no extra packages are needed.

Protocol with the parent process (JSON lines):
  stdout events:  {"event": "ready", "protocol": "..."}
                  {"event": "text", "text": "...", "html": "..."|null}
                  {"event": "image", "path": "...", "mime": "image/png"}
                  {"event": "error", "msg": "..."}
  stdin commands: {"cmd": "set", "text": "...", "html": "..."}      (html optional)
                  {"cmd": "set", "image": "/path", "mime": "image/png"}
                  {"cmd": "quit"}
"""
import ctypes
import ctypes.util
import fcntl
import hashlib
import json
import os
import select
import sys
import threading
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_int32, c_uint32,
                    c_void_p, cast)

OWN_MIME = b"application/x-kclipboard"
SECRET_MIMES = ("x-kde-passwordManagerHint",)
TEXT_MIMES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT")
MAX_TEXT = 4 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024
IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/bmp": "bmp", "image/gif": "gif",
             "image/webp": "webp", "image/x-bmp": "bmp"}


def emit(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        os._exit(0)


# ---------------------------------------------------------------- libwayland bindings
lib = ctypes.CDLL(ctypes.util.find_library("wayland-client") or "libwayland-client.so.0")


class WlMessage(Structure):
    _fields_ = [("name", c_char_p), ("signature", c_char_p), ("types", POINTER(c_void_p))]


class WlInterface(Structure):
    pass


WlInterface._fields_ = [("name", c_char_p), ("version", c_int),
                        ("method_count", c_int), ("methods", POINTER(WlMessage)),
                        ("event_count", c_int), ("events", POINTER(WlMessage))]

lib.wl_display_connect.restype = c_void_p
lib.wl_display_connect.argtypes = [c_char_p]
for fn in ("wl_display_get_fd", "wl_display_dispatch", "wl_display_dispatch_pending",
           "wl_display_flush", "wl_display_roundtrip"):
    getattr(lib, fn).restype = c_int
    getattr(lib, fn).argtypes = [c_void_p]
lib.wl_proxy_marshal_flags.restype = c_void_p
lib.wl_proxy_add_listener.restype = c_int
lib.wl_proxy_add_listener.argtypes = [c_void_p, c_void_p, c_void_p]
lib.wl_proxy_get_version.restype = c_uint32
lib.wl_proxy_get_version.argtypes = [c_void_p]

WL_MARSHAL_FLAG_DESTROY = 1
wl_registry_interface = WlInterface.in_dll(lib, "wl_registry_interface")
wl_seat_interface = WlInterface.in_dll(lib, "wl_seat_interface")

_keep = []  # keep ctypes objects alive for the lifetime of the process


def _types(*ifaces):
    arr = (c_void_p * max(1, len(ifaces)))(*[ctypes.addressof(i) if i is not None else None
                                             for i in ifaces])
    _keep.append(arr)
    return arr


def _msgs(*items):
    arr = (WlMessage * len(items))(*[WlMessage(n, s, t) for n, s, t in items])
    _keep.append(arr)
    return arr


def make_interfaces(prefix, version):
    """Build the four data-control interfaces (ext and wlr flavours are wire-identical)."""
    mgr, dev, src, off = WlInterface(), WlInterface(), WlInterface(), WlInterface()
    _keep.extend([mgr, dev, src, off])
    null2 = _types(None, None)

    def fill(iface, name, methods, events):
        iface.name = name
        iface.version = version
        iface.method_count, iface.methods = len(methods), _msgs(*methods) if methods else None
        iface.event_count, iface.events = len(events), _msgs(*events) if events else None

    fill(mgr, prefix + b"_manager_v1",
         [(b"create_data_source", b"n", _types(src)),
          (b"get_data_device", b"no", _types(dev, wl_seat_interface)),
          (b"destroy", b"", null2)], [])
    fill(dev, prefix + b"_device_v1",
         [(b"set_selection", b"?o", _types(src)), (b"destroy", b"", null2),
          (b"set_primary_selection", b"?o", _types(src))],
         [(b"data_offer", b"n", _types(off)), (b"selection", b"?o", _types(off)),
          (b"finished", b"", null2), (b"primary_selection", b"?o", _types(off))])
    fill(src, prefix + b"_source_v1",
         [(b"offer", b"s", null2), (b"destroy", b"", null2)],
         [(b"send", b"sh", null2), (b"cancelled", b"", null2)])
    fill(off, prefix + b"_offer_v1",
         [(b"receive", b"sh", null2), (b"destroy", b"", null2)],
         [(b"offer", b"s", null2)])
    return mgr, dev, src, off


EXT = make_interfaces(b"ext_data_control", 1)
WLR = make_interfaces(b"zwlr_data_control", 2)

CB_GLOBAL = CFUNCTYPE(None, c_void_p, c_void_p, c_uint32, c_char_p, c_uint32)
CB_REMOVE = CFUNCTYPE(None, c_void_p, c_void_p, c_uint32)
CB_OBJ = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
CB_VOID = CFUNCTYPE(None, c_void_p, c_void_p)
CB_STR = CFUNCTYPE(None, c_void_p, c_void_p, c_char_p)
CB_SEND = CFUNCTYPE(None, c_void_p, c_void_p, c_char_p, c_int32)


def listener(*funcs):
    arr = (c_void_p * len(funcs))(*[cast(f, c_void_p) for f in funcs])
    _keep.append(arr)
    _keep.extend(funcs)
    return arr


def marshal(proxy, opcode, iface, flags, *args):
    version = lib.wl_proxy_get_version(proxy)
    return lib.wl_proxy_marshal_flags(c_void_p(proxy), c_uint32(opcode),
                                      ctypes.byref(iface) if iface is not None else None,
                                      c_uint32(version), c_uint32(flags), *args)


# ---------------------------------------------------------------- daemon
class ClipDaemon:
    def __init__(self, image_dir):
        self.image_dir = image_dir
        os.makedirs(image_dir, exist_ok=True)
        self.display = lib.wl_display_connect(None)
        if not self.display:
            emit({"event": "error", "msg": "cannot connect to Wayland display"})
            sys.exit(1)
        self.fd = lib.wl_display_get_fd(self.display)
        self.globals = {}
        self.offers = {}           # offer proxy -> list of mime types
        self.pending = None        # newest selection offer not yet processed
        self.source = None         # our own data source (if we own the selection)
        self.source_data = {}

        self._reg_listener = listener(CB_GLOBAL(self._on_global), CB_REMOVE(lambda *a: None))
        self.registry = lib.wl_proxy_marshal_flags(
            c_void_p(self.display), c_uint32(1), ctypes.byref(wl_registry_interface),
            c_uint32(lib.wl_proxy_get_version(self.display)), c_uint32(0), None)
        lib.wl_proxy_add_listener(self.registry, self._reg_listener, None)
        lib.wl_display_roundtrip(self.display)

        if "ext_data_control_manager_v1" in self.globals:
            self.ifaces, gname, proto = EXT, "ext_data_control_manager_v1", "ext-data-control-v1"
        elif "zwlr_data_control_manager_v1" in self.globals:
            self.ifaces, gname, proto = WLR, "zwlr_data_control_manager_v1", "wlr-data-control"
        else:
            emit({"event": "error", "msg": "compositor has no data-control protocol"})
            sys.exit(1)
        if "wl_seat" not in self.globals:
            emit({"event": "error", "msg": "no wl_seat"})
            sys.exit(1)

        mgr_iface = self.ifaces[0]
        name, ver = self.globals[gname]
        self.manager = self._bind(name, mgr_iface, min(ver, mgr_iface.version))
        sname, sver = self.globals["wl_seat"]
        self.seat = self._bind(sname, wl_seat_interface, 1)

        self.device = marshal(self.manager, 1, self.ifaces[1], 0, None, c_void_p(self.seat))
        self._dev_listener = listener(CB_OBJ(self._on_data_offer), CB_OBJ(self._on_selection),
                                      CB_VOID(self._on_finished), CB_OBJ(self._on_primary))
        lib.wl_proxy_add_listener(self.device, self._dev_listener, None)
        self._offer_listener = listener(CB_STR(self._on_offer_mime))
        self._src_listener = listener(CB_SEND(self._on_send), CB_VOID(self._on_cancelled))
        lib.wl_display_roundtrip(self.display)
        emit({"event": "ready", "protocol": proto})

    def _bind(self, name, iface, version):
        return lib.wl_proxy_marshal_flags(
            c_void_p(self.registry), c_uint32(0), ctypes.byref(iface), c_uint32(version),
            c_uint32(0), c_uint32(name), iface.name, c_uint32(version), None)

    # -- wayland callbacks (keep them tiny: just record state)
    def _on_global(self, _data, _reg, name, iface, version):
        self.globals[iface.decode()] = (name, version)

    def _on_data_offer(self, _data, _dev, offer):
        self.offers[offer] = []
        lib.wl_proxy_add_listener(offer, self._offer_listener, None)

    def _on_offer_mime(self, _data, offer, mime):
        self.offers.setdefault(offer, []).append(mime.decode(errors="replace"))

    def _on_selection(self, _data, _dev, offer):
        if self.pending and self.pending != offer:
            self._destroy_offer(self.pending)
        self.pending = offer or None

    def _on_primary(self, _data, _dev, offer):
        if offer and offer != self.pending:
            self._destroy_offer(offer)

    def _on_finished(self, _data, _dev):
        emit({"event": "error", "msg": "data-control device finished"})
        os._exit(1)

    def _on_send(self, _data, source, mime, fd):
        payload = self.source_data.get(mime.decode(errors="replace"), b"") \
            if source == self.source else b""

        def writer():  # write in a thread so a slow/self reader can never deadlock us
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
            except OSError:
                pass
            finally:
                os.close(fd)
        threading.Thread(target=writer, daemon=True).start()

    def _on_cancelled(self, _data, source):
        marshal(source, 1, None, WL_MARSHAL_FLAG_DESTROY)
        if source == self.source:
            self.source, self.source_data = None, {}

    def _destroy_offer(self, offer):
        if offer in self.offers:
            del self.offers[offer]
            marshal(offer, 1, None, WL_MARSHAL_FLAG_DESTROY)

    # -- reading an offer
    def _receive(self, offer, mime, limit):
        r, w = os.pipe()
        marshal(offer, 0, None, 0, mime.encode(), c_int32(w))
        lib.wl_display_flush(self.display)
        os.close(w)
        chunks, size, deadline = [], 0, time.monotonic() + 3.0
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                rl, _, _ = select.select([r], [], [], left)
                if not rl:
                    return None
                chunk = os.read(r, 1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    return None
                chunks.append(chunk)
        finally:
            os.close(r)
        return b"".join(chunks)

    def process_pending(self):
        offer, self.pending = self.pending, None
        mimes = self.offers.get(offer, [])
        try:
            if OWN_MIME.decode() in mimes or any(m in mimes for m in SECRET_MIMES):
                return
            text_mime = next((m for m in TEXT_MIMES if m in mimes), None)
            if text_mime is None:
                text_mime = next((m for m in mimes if m.startswith("text/plain")), None)
            image_mime = next((m for m in IMAGE_EXT if m in mimes), None)
            if image_mime and (text_mime is None or "text/html" not in mimes and not
                               any(m.startswith("text/uri-list") for m in mimes)):
                # pure image copy (screenshots, "copy image" in browsers, ...)
                data = self._receive(offer, image_mime, MAX_IMAGE)
                if data:
                    digest = hashlib.sha1(data).hexdigest()[:20]
                    path = os.path.join(self.image_dir, f"{digest}.{IMAGE_EXT[image_mime]}")
                    if not os.path.exists(path):
                        with open(path, "wb") as fh:
                            fh.write(data)
                    emit({"event": "image", "path": path, "mime": image_mime})
                    return
            if text_mime:
                data = self._receive(offer, text_mime, MAX_TEXT)
                if not data:
                    return
                text = data.decode("utf-8", errors="replace")
                if not text.strip():
                    return
                html = None
                if "text/html" in mimes:
                    raw = self._receive(offer, "text/html", MAX_TEXT)
                    if raw:
                        html = raw.decode("utf-8", errors="replace")
                emit({"event": "text", "text": text, "html": html})
        finally:
            self._destroy_offer(offer)

    # -- setting the selection
    def set_selection(self, cmd):
        data = {}
        if cmd.get("image"):
            try:
                with open(cmd["image"], "rb") as fh:
                    data[cmd.get("mime") or "image/png"] = fh.read()
            except OSError as exc:
                emit({"event": "error", "msg": f"cannot read image: {exc}"})
                return
        if cmd.get("text") is not None:
            encoded = cmd["text"].encode("utf-8")
            for m in TEXT_MIMES:
                data[m] = encoded
            if cmd.get("html"):
                data["text/html"] = cmd["html"].encode("utf-8")
        if not data:
            return
        data[OWN_MIME.decode()] = b"1"
        source = marshal(self.manager, 0, self.ifaces[2], 0, None)
        lib.wl_proxy_add_listener(source, self._src_listener, None)
        for mime in data:
            marshal(source, 0, None, 0, mime.encode())
        marshal(self.device, 0, None, 0, c_void_p(source))
        self.source, self.source_data = source, data
        lib.wl_display_flush(self.display)

    # -- main loop
    def run(self):
        stdin = sys.stdin.fileno()
        os.set_blocking(stdin, False)
        buf = b""
        while True:
            lib.wl_display_dispatch_pending(self.display)
            if self.pending:
                self.process_pending()
                continue
            lib.wl_display_flush(self.display)
            rl, _, _ = select.select([self.fd, stdin], [], [])
            if self.fd in rl and lib.wl_display_dispatch(self.display) < 0:
                emit({"event": "error", "msg": "wayland connection lost"})
                return
            if stdin in rl:
                try:
                    chunk = os.read(stdin, 1 << 20)
                except BlockingIOError:
                    chunk = None
                if chunk == b"":
                    return  # parent went away
                buf += chunk or b""
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        cmd = json.loads(line)
                    except ValueError:
                        continue
                    if cmd.get("cmd") == "set":
                        self.set_selection(cmd)
                    elif cmd.get("cmd") == "quit":
                        return


def main():
    image_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "kclipboard", "images")
    ClipDaemon(image_dir).run()


if __name__ == "__main__":
    main()
