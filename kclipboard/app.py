#!/usr/bin/env python3
"""KClipboard - clipboard history + emoji panel for KDE Plasma 6 (Wayland), in the
style of the Windows 11 Win+V flyout.

Pieces:
  * clipd.py      - data-control helper: watches / sets the clipboard without focus
  * kwin/         - KWin script: opens the popup under the mouse cursor, reports the
                    active window (to pick Ctrl+V vs Ctrl+Shift+V for terminals)
  * ydotool       - injects the paste keystroke ("paste on select")
  * KGlobalAccel  - global shortcut (Meta+V by default)
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid

from PyQt6.QtCore import (QEvent, QObject, QPointF, QProcess, QRectF, QSize, Qt, QTimer,
                          pyqtClassInfo, pyqtSignal, pyqtSlot)
from PyQt6.QtDBus import (QDBusAbstractAdaptor, QDBusConnection, QDBusInterface)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QIcon,
                         QKeySequence, QPainter, QPalette, QPen, QPixmap)
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication, QButtonGroup,
                             QComboBox, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel,
                             QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
                             QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                             QStackedWidget, QSystemTrayIcon, QToolButton, QVBoxLayout,
                             QWidget)

import glyphs

APP_ID = "kclipboard"
APP_NAME = "KClipboard"
SERVICE = "io.github.kclipboard"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP_ID)
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP_ID)
IMAGE_DIR = os.path.join(DATA_DIR, "images")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
CONFIG_FILE = os.path.join(CONF_DIR, "config.json")
AUTOSTART_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                              "autostart", f"{APP_ID}.desktop")

DEFAULT_CONFIG = {
    "max_items": 50,
    "paste_on_select": True,
    "paste_delay_ms": 160,
    "restore_delay_ms": 900,
    "shortcut": "Meta+V",
    "emoji_shortcut": "",          # e.g. "Meta+." (Plasma's Emoji Selector owns it by default)
    "terminal_classes": [
        "konsole", "org.kde.konsole", "yakuake", "org.kde.yakuake", "kitty", "alacritty",
        "foot", "footclient", "org.wezfurlong.wezterm", "wezterm", "com.mitchellh.ghostty",
        "ghostty", "gnome-terminal-server", "org.gnome.terminal", "org.gnome.ptyxis",
        "org.gnome.console", "kgx", "xterm", "uxterm", "tilix", "com.gexperts.tilix",
        "terminator", "st", "st-256color", "urxvt", "qterminal", "lxterminal",
        "xfce4-terminal", "dev.warp.warp", "warp", "cool-retro-term", "tabby", "hyper",
    ],
    "language": "auto",            # "auto" (follow system), "vi" or "en"
    "theme": "auto",               # "auto", "light" or "dark"
    "position": "mouse",           # "mouse" (open under the cursor) or "center"
    "default_tab": "clipboard",    # "clipboard", "emoji" or "last"
    "last_tab": "clipboard",
    "save_images": True,
    "persist_history": True,
    "terminal_paste": True,        # Ctrl+Shift+V when the target window is a terminal
    "restore_after_emoji": True,   # put the previous clipboard back after inserting a glyph
    "recent_emoji": [],
    "skin_tone": 0,
}

SHADOW = 12          # px of transparent margin used for the drop shadow
RADIUS = 8.0
WIDTH, HEIGHT = 372, 480
EMOJI_FONT = "Noto Color Emoji"

# ---------------------------------------------------------------- i18n
def _resolve_language(lang):
    if lang and lang != "auto":
        return lang
    return (os.environ.get("LANGUAGE") or os.environ.get("LC_MESSAGES") or
            os.environ.get("LANG") or "en").split(":")[0]


def set_language(lang):
    global UI_LANG, _VI
    UI_LANG = _resolve_language(lang)
    _VI = UI_LANG.lower().startswith("vi")


def _configured_language():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("language", "auto")
    except (OSError, ValueError, AttributeError):
        return "auto"


set_language(_configured_language())
STRINGS = {
    "clipboard": ("Clipboard", "Bảng nhớ tạm"),
    "emoji": ("Emoji", "Emoji"),
    "kaomoji": ("Kaomoji", "Kaomoji"),
    "symbols": ("Symbols", "Ký hiệu"),
    "clear_all": ("Clear all", "Xóa tất cả"),
    "empty": ("You'll see your clipboard history here.\nCopy something to get started.",
              "Lịch sử bảng nhớ tạm sẽ hiện ở đây.\nHãy sao chép thứ gì đó để bắt đầu."),
    "pin": ("Pin", "Ghim"),
    "unpin": ("Unpin", "Bỏ ghim"),
    "delete": ("Delete", "Xóa"),
    "copy_only": ("Copy (don't paste)", "Chỉ sao chép (không dán)"),
    "paste_plain": ("Paste as plain text", "Dán dạng văn bản thuần"),
    "more": ("See more", "Thêm"),
    "search_emoji": ("Search emoji", "Tìm emoji"),
    "recent": ("Recently used", "Dùng gần đây"),
    "no_results": ("No results", "Không có kết quả"),
    "skin_tone": ("Skin tone", "Màu da"),
    "show": ("Show clipboard", "Mở bảng nhớ tạm"),
    "show_emoji": ("Show emoji", "Mở emoji"),
    "paste_on_select": ("Paste on select", "Dán khi chọn"),
    "autostart": ("Start on login", "Chạy khi đăng nhập"),
    "clear_history": ("Clear history (keep pinned)", "Xóa lịch sử (giữ mục đã ghim)"),
    "quit": ("Quit", "Thoát"),
    "image": ("Image", "Hình ảnh"),
    "settings": ("Settings", "Cài đặt"),
    "sec_general": ("General", "Chung"),
    "sec_history": ("History", "Lịch sử"),
    "sec_paste": ("Pasting", "Dán"),
    "sec_shortcuts": ("Shortcuts", "Phím tắt"),
    "paste_on_select_desc": ("Paste into the previous window right after you pick an item",
                             "Dán ngay vào cửa sổ trước đó khi bạn chọn một mục"),
    "position": ("Open at", "Vị trí mở"),
    "pos_mouse": ("Mouse cursor", "Con trỏ chuột"),
    "pos_center": ("Screen center", "Giữa màn hình"),
    "default_tab": ("Tab shown on open", "Tab hiện khi mở"),
    "tab_last": ("Last used", "Dùng lần cuối"),
    "language": ("Language", "Ngôn ngữ"),
    "theme": ("Theme", "Giao diện"),
    "opt_auto": ("System", "Theo hệ thống"),
    "theme_light": ("Light", "Sáng"),
    "theme_dark": ("Dark", "Tối"),
    "max_items": ("Items to keep", "Số mục lưu tối đa"),
    "max_items_desc": ("Pinned items don't count", "Không tính các mục đã ghim"),
    "save_images": ("Save images", "Lưu hình ảnh"),
    "persist_history": ("Keep history after restart", "Giữ lịch sử sau khi khởi động lại"),
    "clear_now": ("Clear", "Xóa"),
    "terminal_paste": ("Ctrl+Shift+V in terminals", "Ctrl+Shift+V trong terminal"),
    "terminal_paste_desc": ("Konsole, kitty, Alacritty… need it to paste",
                            "Konsole, kitty, Alacritty… cần tổ hợp này để dán"),
    "restore_after_emoji": ("Restore clipboard after emoji", "Khôi phục clipboard sau khi chèn emoji"),
    "restore_after_emoji_desc": ("Inserting an emoji or symbol won't replace what you copied",
                                 "Chèn emoji hay ký hiệu sẽ không ghi đè thứ bạn đã sao chép"),
    "paste_delay": ("Paste delay", "Độ trễ khi dán"),
    "paste_delay_desc": ("Raise it if items sometimes fail to paste",
                         "Tăng lên nếu đôi khi dán không thành công"),
    "sc_clipboard": ("Open KClipboard", "Mở KClipboard"),
    "sc_emoji": ("Open the emoji tab", "Mở thẳng tab emoji"),
    "sc_hint": ("Click a field, then press the new key combination",
                "Bấm vào ô rồi nhấn tổ hợp phím mới"),
    "shortcut_taken": ("{key} is already used by {owner}", "{key} đang được dùng bởi {owner}"),
    "shortcut_failed": ("Could not register {key}: it is already used by another app. "
                        "Free it in System Settings → Shortcuts.",
                        "Không đăng ký được {key}: phím tắt đang được ứng dụng khác dùng. "
                        "Hãy gỡ nó trong System Settings → Shortcuts."),
}


def tr(key):
    en, vi = STRINGS[key]
    return vi if _VI else en


# ---------------------------------------------------------------- persistence
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


class History(QObject):
    changed = pyqtSignal()

    def __init__(self, max_items, persist=True):
        super().__init__()
        self.max_items = max_items
        self.persist = persist
        stored = load_json(HISTORY_FILE, {}).get("items", []) if persist else []
        self.items = [i for i in stored
                      if i.get("kind") == "text" or os.path.exists(i.get("path", ""))]
        self._save_timer = QTimer(singleShot=True, interval=400, timeout=self._save)
        self._cleanup_images()

    def _save(self):
        if self.persist:
            save_json(HISTORY_FILE, {"version": 1, "items": self.items})
        elif os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)

    def set_persist(self, persist):
        self.persist = persist
        self._save()

    def set_max_items(self, max_items):
        self.max_items = max_items
        self._trim()
        self._touch()

    def _touch(self):
        self._save_timer.start()
        self.changed.emit()

    def _cleanup_images(self):
        used = {i.get("path") for i in self.items}
        if os.path.isdir(IMAGE_DIR):
            for name in os.listdir(IMAGE_DIR):
                path = os.path.join(IMAGE_DIR, name)
                if path not in used:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def _trim(self):
        unpinned = [i for i in self.items if not i.get("pinned")]
        for item in unpinned[self.max_items:]:
            self.items.remove(item)
            self._drop_file(item)

    def _drop_file(self, item):
        path = item.get("path")
        if path and not any(i.get("path") == path for i in self.items):
            try:
                os.remove(path)
            except OSError:
                pass

    def find(self, item_id):
        return next((i for i in self.items if i["id"] == item_id), None)

    def _key(self, item):
        return (item["kind"], item.get("text") if item["kind"] == "text" else item.get("path"))

    def add(self, entry):
        key = self._key(entry)
        existing = next((i for i in self.items if self._key(i) == key), None)
        if existing:
            if entry.get("html"):
                existing["html"] = entry["html"]
            if not existing.get("pinned"):
                self.items.remove(existing)
                self.items.insert(0, existing)
            existing["ts"] = time.time()
            self._touch()
            return existing
        entry.update(id=uuid.uuid4().hex, pinned=False, ts=time.time())
        self.items.insert(0, entry)
        self._trim()
        self._touch()
        return entry

    def promote(self, item):
        if item in self.items and not item.get("pinned"):
            self.items.remove(item)
            self.items.insert(0, item)
            self._touch()

    def remove(self, item_id):
        item = self.find(item_id)
        if item:
            self.items.remove(item)
            self._drop_file(item)
            self._touch()

    def toggle_pin(self, item_id):
        item = self.find(item_id)
        if item:
            item["pinned"] = not item.get("pinned")
            self._trim()
            self._touch()

    def clear(self):
        for item in [i for i in self.items if not i.get("pinned")]:
            self.items.remove(item)
            self._drop_file(item)
        self._touch()


# ---------------------------------------------------------------- clipboard helper process
class ClipService(QObject):
    text_copied = pyqtSignal(str, object)     # text, html
    image_copied = pyqtSignal(str, str)       # path, mime
    status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.proc = None
        self._buf = b""
        self._restarts = 0
        self.start()

    def start(self):
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(
            lambda: sys.stderr.write(bytes(self.proc.readAllStandardError()).decode(errors="replace")))
        self.proc.finished.connect(self._finished)
        self.proc.start(sys.executable, [os.path.join(HERE, "clipd.py"), IMAGE_DIR])

    def _finished(self, *_):
        self._restarts += 1
        if self._restarts < 20:
            QTimer.singleShot(min(30000, 1000 * self._restarts), self.start)

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "text":
                self.text_copied.emit(ev["text"], ev.get("html"))
            elif kind == "image":
                self.image_copied.emit(ev["path"], ev.get("mime", "image/png"))
            elif kind == "ready":
                self._restarts = 0
                self.status.emit("ready:" + ev.get("protocol", ""))
            elif kind == "error":
                print("clipd:", ev.get("msg"), file=sys.stderr)
                self.status.emit("error:" + ev.get("msg", ""))

    def set(self, entry, plain=False):
        if entry["kind"] == "image":
            cmd = {"cmd": "set", "image": entry["path"], "mime": entry.get("mime", "image/png")}
        else:
            cmd = {"cmd": "set", "text": entry["text"]}
            if entry.get("html") and not plain:
                cmd["html"] = entry["html"]
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.write((json.dumps(cmd, ensure_ascii=False) + "\n").encode())

    def stop(self):
        if self.proc:
            self.proc.finished.disconnect()
            self.proc.write(b'{"cmd":"quit"}\n')
            self.proc.waitForFinished(500)
            self.proc.kill()


# ---------------------------------------------------------------- theme
class Theme:
    current = None      # theme of the live popup, for widgets created after it

    def __init__(self, mode="auto"):
        app = QApplication.instance()
        pal = app.palette()
        scheme = app.styleHints().colorScheme()
        if mode in ("light", "dark"):
            self.dark = mode == "dark"
        elif scheme == Qt.ColorScheme.Unknown:
            self.dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        else:
            self.dark = scheme == Qt.ColorScheme.Dark
        self.accent = pal.color(QPalette.ColorRole.Highlight)
        if self.dark:
            self.bg, self.border = QColor(32, 32, 32, 250), QColor(255, 255, 255, 22)
            self.card, self.card_border = "#2d2d2d", "rgba(255,255,255,0.06)"
            self.hover, self.text, self.sub = "#383838", "#ffffff", "#c8c8c8"
            self.btn_hover = "rgba(255,255,255,0.08)"
        else:
            self.bg, self.border = QColor(243, 243, 243, 252), QColor(0, 0, 0, 30)
            self.card, self.card_border = "#ffffff", "rgba(0,0,0,0.07)"
            self.hover, self.text, self.sub = "#f6f6f6", "#1b1b1b", "#5f5f5f"
            self.btn_hover = "rgba(0,0,0,0.06)"

    def icon(self, names, size=18, checked_accent=False):
        """Theme icon tinted to our text colour (the system icon theme may not match a
        forced light/dark setting). Returns a null QIcon if none of the names exist."""
        src = next((i for i in map(QIcon.fromTheme, names) if not i.isNull()), None)
        if src is None:
            return QIcon()
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()

        def tinted(color):
            pix = src.pixmap(QSize(size, size), dpr)
            p = QPainter(pix)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(pix.rect(), QColor(color))
            p.end()
            return pix
        icon = QIcon(tinted(self.text))
        if checked_accent:
            icon.addPixmap(tinted(self.accent), QIcon.Mode.Normal, QIcon.State.On)
        return icon

    def stylesheet(self):
        a = self.accent.name()
        bg = self.bg.name()   # opaque: "transparent" renders black on translucent windows
        return f"""
        QWidget {{ color: {self.text}; }}
        QLabel#title {{ font-weight: 600; font-size: 10.5pt; }}
        QLabel#sub, QLabel#empty {{ color: {self.sub}; }}
        QLabel#section {{ font-weight: 600; padding: 6px 2px 0px 2px; }}
        QLabel#desc {{ color: {self.sub}; font-size: 8.5pt; }}
        QLabel#warn {{ color: {"#ff99a4" if self.dark else "#c42b1c"}; font-size: 8.5pt;
                       padding: 0px 2px; }}
        QFrame#setrow {{ background: {self.card}; border: 1px solid {self.card_border};
                         border-radius: 6px; }}
        QComboBox, QSpinBox, QPushButton#btn {{ background: {self.btn_hover};
                         border: 1px solid {self.card_border}; border-radius: 5px;
                         padding: 4px 8px; min-height: 20px; }}
        QComboBox:hover, QPushButton#btn:hover {{ border: 1px solid {a}; }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{ background: {self.card}; color: {self.text};
                         border: 1px solid {self.card_border}; outline: none;
                         selection-background-color: {a}; }}
        QSpinBox {{ border-bottom: 2px solid {a}; }}
        QKeySequenceEdit QLineEdit {{ background: {self.btn_hover}; padding: 4px 8px; }}
        QToolButton#tab, QToolButton#tabemoji {{ border: none; border-radius: 5px;
                           padding: 0px; font-size: 12pt; }}
        QToolButton#tabemoji {{ font-family: '{EMOJI_FONT}'; font-size: 14pt; }}
        QToolButton#tab:hover, QToolButton#tabemoji:hover {{ background: {self.btn_hover}; }}
        QToolButton#tab:checked, QToolButton#tabemoji:checked {{
                           background: {self.btn_hover}; border-bottom: 3px solid {a}; }}
        QToolButton::menu-indicator {{ image: none; width: 0px; }}
        QListWidget#emojigrid {{ font-family: '{EMOJI_FONT}'; font-size: 22px; }}
        QListWidget#symgrid {{ font-size: 15pt; }}
        QListWidget#kaogrid {{ font-size: 10.5pt; }}
        QToolButton#cat {{ border: none; border-radius: 5px; padding: 2px;
                           font-family: '{EMOJI_FONT}'; font-size: 13pt;
                           min-width: 30px; min-height: 30px; }}
        QToolButton#cat:hover {{ background: {self.btn_hover}; }}
        QToolButton#cat:checked {{ background: {self.btn_hover};
                                   border-bottom: 2px solid {a}; }}
        QToolButton#symcat {{ border: none; border-radius: 5px; padding: 2px;
                              font-size: 12pt; min-width: 30px; min-height: 30px; }}
        QToolButton#symcat:hover {{ background: {self.btn_hover}; }}
        QToolButton#symcat:checked {{ background: {self.btn_hover};
                                      border-bottom: 2px solid {a}; }}
        QPushButton#link {{ border: none; color: {a}; padding: 4px 6px; border-radius: 4px; }}
        QPushButton#link:hover {{ background: {self.btn_hover}; }}
        QFrame#card {{ background: {self.card}; border: 1px solid {self.card_border};
                       border-radius: 6px; }}
        QFrame#card:hover {{ background: {self.hover}; }}
        QFrame#card[selected="true"] {{ border: 2px solid {a}; }}
        QToolButton#mini {{ border: none; border-radius: 4px; padding: 1px;
                            min-width: 22px; min-height: 22px; color: {self.sub}; }}
        QToolButton#mini:hover {{ background: {self.btn_hover}; }}
        QToolButton#mini:checked {{ color: {a}; }}
        QLineEdit {{ background: {self.card}; border: 1px solid {self.card_border};
                     border-bottom: 2px solid {a}; border-radius: 5px; padding: 5px 8px; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ border-radius: 5px; padding: 0px; }}
        QListWidget::item:hover {{ background: {self.btn_hover}; }}
        QListWidget::item:selected {{ background: {self.btn_hover}; color: {self.text};
                                      border: 2px solid {a}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: {bg}; width: 6px; margin: 0px; border: none; }}
        QScrollBar::handle:vertical {{ background: {self.sub}; border-radius: 3px;
                                       min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                                       background: {bg}; height: 0px; border: none; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {bg}; }}
        QMenu {{ background: {self.card}; border: 1px solid {self.card_border};
                 border-radius: 6px; padding: 4px; }}
        QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {self.btn_hover}; }}
        """


# ---------------------------------------------------------------- clipboard page
def text_preview(text, max_lines=4, max_chars=220):
    lines = text.strip("\n").expandtabs(4).splitlines() or [""]
    out, truncated = [], len(lines) > max_lines
    for line in lines[:max_lines]:
        if len(line) > max_chars:
            line, truncated = line[:max_chars], True
        # let QLabel wrap long URLs / hashes instead of widening the card
        line = " ".join(w if len(w) <= 32 else "\u200b".join(w[i:i + 24] for i in range(0, len(w), 24))
                        for w in line.split(" "))
        out.append(line)
    return "\n".join(out) + (" …" if truncated else "")


class ItemCard(QFrame):
    clicked = pyqtSignal(str)
    menu_requested = pyqtSignal(str, object)
    pin_toggled = pyqtSignal(str)

    def __init__(self, item, pixmap_cache):
        super().__init__()
        self.item_id = item["id"]
        self.setObjectName("card")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 4, 8)
        lay.setSpacing(6)

        if item["kind"] == "image":
            label = QLabel()
            pix = pixmap_cache.get(item["path"])
            if pix is None:
                pix = QPixmap(item["path"])
                if not pix.isNull():
                    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
                    pix = pix.scaled(QSize(int(270 * dpr), int(120 * dpr)),
                                     Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                    pix.setDevicePixelRatio(dpr)
                pixmap_cache[item["path"]] = pix
            if pix.isNull():
                label.setText(tr("image"))
            else:
                label.setPixmap(pix)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            label = QLabel(text_preview(item["text"]))
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            fm = QFontMetrics(label.font())
            label.setMaximumHeight(fm.lineSpacing() * 4 + 4)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setMinimumWidth(10)
        lay.addWidget(label, 1)

        side = QVBoxLayout()
        side.setSpacing(2)
        more = QToolButton(objectName="mini", text="⋯", toolTip=tr("more"))
        more.clicked.connect(lambda: self.menu_requested.emit(
            self.item_id, more.mapToGlobal(more.rect().bottomLeft())))
        pin = QToolButton(objectName="mini", checkable=True,
                          toolTip=tr("unpin") if item.get("pinned") else tr("pin"))
        icon = Theme.current.icon(("window-pin", "pin"), 16, checked_accent=True)
        if icon.isNull():
            pin.setText("📌")
        else:
            pin.setIcon(icon)
        pin.setChecked(bool(item.get("pinned")))
        pin.clicked.connect(lambda: self.pin_toggled.emit(self.item_id))
        side.addWidget(more)
        side.addWidget(pin)
        side.addStretch(1)
        lay.addLayout(side)

    def set_selected(self, sel):
        if self.property("selected") != sel:
            self.setProperty("selected", sel)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.item_id)
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(self.item_id, e.globalPosition().toPoint())


class ClipboardPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.cards = []
        self.selected = 0
        self.pixmaps = {}
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        head.addWidget(QLabel(tr("clipboard"), objectName="title"))
        head.addStretch(1)
        self.clear_btn = QPushButton(tr("clear_all"), objectName="link")
        self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_btn.clicked.connect(ctrl.history.clear)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)

        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.list_lay = QVBoxLayout(self.inner)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(6)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.empty = QLabel(tr("empty"), objectName="empty",
                            alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        lay.addWidget(self.empty, 1)

    def rebuild(self):
        while self.list_lay.count():
            w = self.list_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = self.ctrl.history.items
        live = {i.get("path") for i in items}
        self.pixmaps = {k: v for k, v in self.pixmaps.items() if k in live}
        self.cards = []
        for item in items:
            card = ItemCard(item, self.pixmaps)
            card.clicked.connect(self._clicked)
            card.menu_requested.connect(self._menu)
            card.pin_toggled.connect(self.ctrl.history.toggle_pin)
            self.list_lay.addWidget(card)
            self.cards.append(card)
        self.list_lay.addStretch(1)
        has = bool(items)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.clear_btn.setEnabled(any(not i.get("pinned") for i in items))
        self.select(min(self.selected, max(0, len(self.cards) - 1)))

    def select(self, idx):
        if not self.cards:
            return
        self.selected = max(0, min(idx, len(self.cards) - 1))
        for i, c in enumerate(self.cards):
            c.set_selected(i == self.selected)
        QTimer.singleShot(0, lambda: self.cards and self.selected < len(self.cards) and
                          self.scroll.ensureWidgetVisible(self.cards[self.selected], 0, 6))

    def _clicked(self, item_id):
        self.ctrl.paste_item(item_id)

    def _menu(self, item_id, pos):
        item = self.ctrl.history.find(item_id)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction(tr("copy_only"), lambda: self.ctrl.paste_item(item_id, paste=False))
        if item.get("html"):
            menu.addAction(tr("paste_plain"), lambda: self.ctrl.paste_item(item_id, plain=True))
        menu.addAction(tr("unpin") if item.get("pinned") else tr("pin"),
                       lambda: self.ctrl.history.toggle_pin(item_id))
        menu.addSeparator()
        menu.addAction(tr("delete"), lambda: self.ctrl.history.remove(item_id))
        menu.exec(pos)

    def current_id(self):
        return self.cards[self.selected].item_id if self.cards else None

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_Down:
            self.select(self.selected + 1)
        elif key == Qt.Key.Key_Up:
            self.select(self.selected - 1)
        elif key == Qt.Key.Key_Home:
            self.select(0)
        elif key == Qt.Key.Key_End:
            self.select(len(self.cards) - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current_id():
            plain = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.ctrl.paste_item(self.current_id(), plain=plain)
        elif key == Qt.Key.Key_Delete and self.current_id():
            self.ctrl.history.remove(self.current_id())
        elif key == Qt.Key.Key_P and e.modifiers() & Qt.KeyboardModifier.ControlModifier \
                and self.current_id():
            self.ctrl.history.toggle_pin(self.current_id())
        elif key == Qt.Key.Key_Menu and self.cards:
            card = self.cards[self.selected]
            self._menu(card.item_id, card.mapToGlobal(card.rect().center()))
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- emoji / kaomoji / symbols
class GlyphList(QListWidget):
    picked = pyqtSignal(str)

    def __init__(self, name, grid=None):
        super().__init__(objectName=name)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.grid = grid
        if grid:
            self.setGridSize(grid)
            self.setUniformItemSizes(True)
        else:
            self.setSpacing(3)
        self.itemClicked.connect(lambda it: self.picked.emit(it.data(Qt.ItemDataRole.UserRole)))

    def fill(self, entries):
        """entries: iterable of (value, display, tooltip)."""
        self.clear()
        self.ensurePolished()
        fm = QFontMetrics(self.font())
        for value, display, tip in entries:
            it = QListWidgetItem(display)
            it.setData(Qt.ItemDataRole.UserRole, value)
            it.setToolTip(tip)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if self.grid:
                it.setSizeHint(self.grid)
            else:
                it.setSizeHint(QSize(fm.horizontalAdvance(display) + 22, fm.height() + 16))
            self.addItem(it)
        if self.count():
            self.setCurrentRow(0)
        self.scrollToTop()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentItem():
            self.picked.emit(self.currentItem().data(Qt.ItemDataRole.UserRole))
            return
        super().keyPressEvent(e)


class CategoryBar(QWidget):
    changed = pyqtSignal(object)

    def __init__(self, cats, style="cat"):
        """cats: list of (key, icon, tooltip)."""
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self.group = QButtonGroup(self, exclusive=True)
        self.buttons = {}
        for key, icon, tip in cats:
            b = QToolButton(objectName=style, text=icon, toolTip=tip, checkable=True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)

    def set_current(self, key):
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)
        elif self.group.checkedButton():
            self.group.setExclusive(False)
            self.group.checkedButton().setChecked(False)
            self.group.setExclusive(True)


class EmojiPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.data = None
        self.category = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText=tr("search_emoji"), clearButtonEnabled=True)
        self.search.textChanged.connect(self.refresh)
        self.search.installEventFilter(self)
        top.addWidget(self.search, 1)
        self.tone_btn = QToolButton(objectName="cat", toolTip=tr("skin_tone"))
        self.tone_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tone_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tone_menu = QMenu(self)
        for idx, tone in enumerate(glyphs.SKIN_TONES):
            act = tone_menu.addAction("✋" + tone)
            act.setFont(QFont(EMOJI_FONT, 14))
            act.triggered.connect(lambda _=False, i=idx: self.set_tone(i))
        self.tone_btn.setMenu(tone_menu)
        top.addWidget(self.tone_btn)
        lay.addLayout(top)

        self.title = QLabel(objectName="sub")
        lay.addWidget(self.title)
        self.grid = GlyphList("emojigrid", grid=QSize(42, 42))
        self.grid.picked.connect(self._picked)
        lay.addWidget(self.grid, 1)
        cats = [("recent", "🕘", tr("recent"))] + [
            (cid, icon, vi if _VI else en) for cid, icon, en, vi in glyphs.EMOJI_CATEGORIES]
        self.cats = CategoryBar(cats)
        self.cats.changed.connect(self.set_category)
        lay.addWidget(self.cats)
        self._update_tone_button()

    def ensure_loaded(self):
        if self.data is None:
            self.data = glyphs.load_emoji(UI_LANG)
            self.by_char = {e.char: e for e in self.data}
            self.set_category("recent" if self.ctrl.config["recent_emoji"] else 1)

    def _update_tone_button(self):
        self.tone_btn.setText("✋" + glyphs.SKIN_TONES[self.ctrl.config.get("skin_tone", 0)])

    def set_tone(self, idx):
        self.ctrl.config["skin_tone"] = idx
        self.ctrl.save_config()
        self._update_tone_button()
        self.refresh()

    def _variant(self, emo):
        tone = glyphs.SKIN_TONES[self.ctrl.config.get("skin_tone", 0)]
        return emo.tones.get(tone, emo.char) if tone else emo.char

    def set_category(self, cat):
        self.category = cat
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.refresh()

    def refresh(self):
        if self.data is None:
            return
        query = self.search.text().strip().lower()
        if query:
            words = query.split()
            items = [e for e in self.data if all(w in e.search for w in words)]
            self.cats.set_current(None)
            self.title.setText(tr("no_results") if not items else "")
        elif self.category == "recent":
            items = [self.by_char[c] for c in self.ctrl.config["recent_emoji"] if c in self.by_char]
            self.cats.set_current("recent")
            self.title.setText(tr("recent"))
        else:
            items = [e for e in self.data if e.category == self.category]
            self.cats.set_current(self.category)
            cat = next(c for c in glyphs.EMOJI_CATEGORIES if c[0] == self.category)
            self.title.setText(cat[3] if _VI else cat[2])
        self.title.setVisible(bool(self.title.text()))
        self.grid.fill((self._variant(e), self._variant(e), e.name) for e in items)

    def _picked(self, char):
        base = next((e.char for e in self.data if char == e.char or char in e.tones.values()), char)
        recent = [c for c in self.ctrl.config["recent_emoji"] if c != base]
        self.ctrl.config["recent_emoji"] = ([base] + recent)[:36]
        self.ctrl.save_config()
        self.ctrl.paste_glyph(char)

    def on_show(self):
        self.ensure_loaded()
        self.search.clear()
        if self.category == "recent":
            self.refresh()
        self.search.setFocus()

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Tab):
                self.grid.setFocus()
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.grid.count():
                self.grid.picked.emit(self.grid.item(0).data(Qt.ItemDataRole.UserRole))
                return True
        return super().eventFilter(obj, e)


class TextGlyphPage(QWidget):
    """Kaomoji and symbols: a category bar + a flowing list of strings."""

    def __init__(self, ctrl, groups, icons, grid=None, tooltip_names=False):
        super().__init__()
        self.ctrl, self.groups, self.tooltip_names = ctrl, groups, tooltip_names
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(objectName="sub")
        lay.addWidget(self.title)
        self.grid = GlyphList("symgrid" if grid else "kaogrid", grid=grid)
        self.grid.picked.connect(ctrl.paste_glyph)
        lay.addWidget(self.grid, 1)
        names = list(groups)
        self.cats = CategoryBar([(n, icons[i], self._label(n)) for i, n in enumerate(names)],
                                style="cat" if grid is None else "symcat")
        self.cats.changed.connect(self.set_group)
        lay.addWidget(self.cats)
        self.set_group(names[0])

    @staticmethod
    def _label(name):
        vi, _, en = name.partition(" ・ ")
        return vi if _VI or not en else en

    def set_group(self, name):
        self.cats.set_current(name)
        self.title.setText(self._label(name))

        def tip(s):
            if not self.tooltip_names or len(s) != 1:
                return s
            return f"{s}  U+{ord(s):04X}  {unicodedata.name(s, '').title()}"
        self.grid.fill((s, s, tip(s)) for s in self.groups[name])

    def on_show(self):
        self.grid.setFocus()


# ---------------------------------------------------------------- settings page
class Switch(QAbstractButton):
    """Win11-style toggle switch."""

    def __init__(self, checked=False):
        super().__init__(checkable=True, checked=checked)
        self.setFixedSize(40, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        theme = self.window().theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, 39, 19)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent)
            p.drawRoundedRect(track, 9.5, 9.5)
            p.setBrush(QColor("#000000" if theme.accent.lightness() > 140 else "#ffffff"))
            p.drawEllipse(QRectF(24, 4, 12, 12))
        else:
            p.setPen(QPen(QColor(theme.sub), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track, 9.5, 9.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.sub))
            p.drawEllipse(QRectF(5, 5, 10, 10))


class _Combo(QComboBox):
    def wheelEvent(self, e):      # don't change values while scrolling the page
        e.ignore()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)        # chevron (the stylesheet removes the native arrow)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(self.window().theme.sub), 1.4))
        x, y = self.width() - 15, self.height() / 2 - 1.5
        p.drawPolyline(QPointF(x - 4, y), QPointF(x, y + 4), QPointF(x + 4, y))


class _Spin(QSpinBox):
    def wheelEvent(self, e):
        e.ignore()


class SettingsPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        head.addWidget(QLabel(tr("settings"), objectName="title"))
        head.addStretch(1)
        lay.addLayout(head)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.form = QVBoxLayout(inner)
        self.form.setContentsMargins(0, 0, 6, 0)
        self.form.setSpacing(5)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        cfg = ctrl.config

        self._section(tr("sec_general"))
        self._switch("paste_on_select", tr("paste_on_select"), tr("paste_on_select_desc"))
        self._combo("position", tr("position"),
                    [("mouse", tr("pos_mouse")), ("center", tr("pos_center"))])
        self._combo("default_tab", tr("default_tab"),
                    [("clipboard", tr("clipboard")), ("emoji", tr("emoji")), ("last", tr("tab_last"))])
        self.autostart = Switch(os.path.exists(AUTOSTART_FILE))
        self.autostart.toggled.connect(ctrl.set_autostart)
        self._row(tr("autostart"), self.autostart)
        self._combo("language", tr("language"),
                    [("auto", tr("opt_auto")), ("vi", "Tiếng Việt"), ("en", "English")])
        self._combo("theme", tr("theme"),
                    [("auto", tr("opt_auto")), ("light", tr("theme_light")), ("dark", tr("theme_dark"))])

        self._section(tr("sec_history"))
        self._spin("max_items", tr("max_items"), 5, 500, 5, desc=tr("max_items_desc"))
        self._switch("save_images", tr("save_images"))
        self._switch("persist_history", tr("persist_history"))
        clear = QPushButton(tr("clear_now"), objectName="btn")
        clear.clicked.connect(ctrl.history.clear)
        self._row(tr("clear_history"), clear)

        self._section(tr("sec_paste"))
        self._switch("terminal_paste", tr("terminal_paste"), tr("terminal_paste_desc"))
        self._switch("restore_after_emoji", tr("restore_after_emoji"), tr("restore_after_emoji_desc"))
        self._spin("paste_delay_ms", tr("paste_delay"), 50, 2000, 10, suffix=" ms",
                   desc=tr("paste_delay_desc"))

        self._section(tr("sec_shortcuts"))
        self.key_edits = {}
        self._keyseq("toggle", tr("sc_clipboard"), tr("sc_hint"))
        self._keyseq("emoji", tr("sc_emoji"))
        self.warn = QLabel(objectName="warn", wordWrap=True, visible=False)
        self.form.addWidget(self.warn)
        self.form.addStretch(1)

    # -- row builders
    def _section(self, title):
        self.form.addWidget(QLabel(title, objectName="section"))

    def _row(self, title, widget, desc=None):
        row = QFrame(objectName="setrow")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 7, 10, 7)
        v = QVBoxLayout()
        v.setSpacing(1)
        v.addWidget(QLabel(title, wordWrap=True))
        if desc:
            v.addWidget(QLabel(desc, objectName="desc", wordWrap=True))
        h.addLayout(v, 1)
        h.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self.form.addWidget(row)

    def _switch(self, key, title, desc=None):
        sw = Switch(bool(self.ctrl.config.get(key)))
        sw.toggled.connect(lambda on: self.ctrl.set_option(key, on))
        self._row(title, sw, desc)

    def _combo(self, key, title, options):
        box = _Combo()
        box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for value, label in options:
            box.addItem(label, value)
        box.setCurrentIndex(max(0, box.findData(self.ctrl.config.get(key))))
        box.setMinimumWidth(128)
        box.activated.connect(lambda _i: self.ctrl.set_option(key, box.currentData()))
        self._row(title, box)

    def _spin(self, key, title, lo, hi, step, suffix="", desc=None):
        spin = _Spin()
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setValue(int(self.ctrl.config.get(key, lo)))
        spin.setMinimumWidth(84)
        spin.valueChanged.connect(lambda v: self.ctrl.set_option(key, v))
        self._row(title, spin, desc)

    def _keyseq(self, action, title, desc=None):
        edit = QKeySequenceEdit()
        edit.setMaximumSequenceLength(1)
        edit.setClearButtonEnabled(True)
        edit.setFixedWidth(128)
        edit.editingFinished.connect(lambda: self._shortcut_edited(action, edit))
        self.key_edits[action] = edit
        self._row(title, edit, desc)

    def _shortcut_edited(self, action, edit):
        key = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        ok, owner = self.ctrl.set_shortcut(action, key)
        self.warn.setVisible(not ok)
        if not ok:
            self.warn.setText(tr("shortcut_taken").format(key=key, owner=owner or "?"))
            edit.setKeySequence(QKeySequence(self.ctrl.current_shortcut(action)))
        edit.clearFocus()

    def on_show(self):
        self.warn.hide()
        self.autostart.blockSignals(True)
        self.autostart.setChecked(os.path.exists(AUTOSTART_FILE))
        self.autostart.blockSignals(False)
        for action, edit in self.key_edits.items():
            edit.setKeySequence(QKeySequence(self.ctrl.current_shortcut(action)))


# ---------------------------------------------------------------- popup window
class Popup(QWidget):
    TABS = ("clipboard", "emoji", "kaomoji", "symbols")

    def __init__(self, ctrl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.ctrl = ctrl
        self.theme = Theme(ctrl.config.get("theme", "auto"))
        Theme.current = self.theme
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(WIDTH + 2 * SHADOW, HEIGHT + 2 * SHADOW)
        self.setStyleSheet(self.theme.stylesheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(SHADOW + 10, SHADOW + 8, SHADOW + 10, SHADOW + 10)
        root.setSpacing(8)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.tab_group = QButtonGroup(self, exclusive=True)
        self.tab_buttons = {}
        for key, label in zip(self.TABS, ("📋", "😊", ";-)", "Ω")):
            b = QToolButton(objectName="tabemoji" if key in ("emoji", "clipboard") else "tab",
                            text=label, toolTip=tr(key), checkable=True)
            b.setFixedSize(44, 34)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tab_group.addButton(b)
            self.tab_buttons[key] = b
            tabs.addWidget(b)
        tabs.addStretch(1)
        gear = QToolButton(objectName="tab", toolTip=tr("settings"), checkable=True)
        icon = self.theme.icon(("configure", "settings-configure", "preferences-system"))
        if icon.isNull():
            gear.setText("⚙")
        else:
            gear.setIcon(icon)
            gear.setIconSize(QSize(18, 18))
        gear.setFixedSize(38, 34)
        gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        gear.clicked.connect(lambda: self.show_tab("settings"))
        self.tab_group.addButton(gear)
        self.tab_buttons["settings"] = gear
        tabs.addWidget(gear)
        root.addLayout(tabs)

        self.stack = QStackedWidget()
        self.pages = {
            "emoji": EmojiPage(ctrl),
            "kaomoji": TextGlyphPage(ctrl, glyphs.KAOMOJI, ["👋", "😄", "❤️", "😢", "😠", "😲", "✨"]),
            "symbols": TextGlyphPage(ctrl, glyphs.SYMBOLS,
                                     ["…", "₫", "±", "→", "²", "Ω", "■", "┼", "✓", "Ă"],
                                     grid=QSize(38, 38), tooltip_names=True),
            "clipboard": ClipboardPage(ctrl),
            "settings": SettingsPage(ctrl),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)
        self.current = "clipboard"
        self._was_active = False

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SHADOW, 0, -2):          # soft drop shadow
            p.setBrush(QColor(0, 0, 0, 7 if self.theme.dark else 4))
            p.drawRoundedRect(panel.adjusted(-i, -i + 3, i, i + 3), RADIUS + i, RADIUS + i)
        p.setBrush(self.theme.bg)
        p.setPen(QPen(self.theme.border, 1))
        p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    def show_tab(self, key):
        self.current = key
        if key in self.TABS:
            self.ctrl.remember_tab(key)
        self.tab_buttons[key].setChecked(True)
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if key == "clipboard":
            page.rebuild()
            page.select(0)
            page.setFocus()
        else:
            page.on_show()

    def open(self, tab="clipboard"):
        self.show_tab(tab)
        self._was_active = False
        self.show()
        self.raise_()
        self.activateWindow()

    def close_popup(self):
        if self.isVisible():
            self.hide()

    def event(self, e):
        t = e.type()
        if t == QEvent.Type.WindowActivate:
            self._was_active = True
        elif t == QEvent.Type.WindowDeactivate and self._was_active and self.isVisible():
            # behave like a flyout: close when the user clicks elsewhere
            QTimer.singleShot(60, self._maybe_close)
        return super().event(e)

    def _maybe_close(self):
        if not self.isActiveWindow() and QApplication.activePopupWidget() is None:
            self.close_popup()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_popup()
            return
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            idx = self.TABS.index(self.current) if self.current in self.TABS else 0
            step = -1 if e.key() == Qt.Key.Key_Backtab or mods & Qt.KeyboardModifier.ShiftModifier else 1
            self.show_tab(self.TABS[(idx + step) % len(self.TABS)])
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- DBus
INTROSPECTION = f"""
<interface name="{SERVICE}">
  <method name="toggle"/>
  <method name="show"/>
  <method name="showEmoji"/>
  <method name="showSettings"/>
  <method name="hide"/>
  <method name="showTab">
    <arg direction="in" type="s" name="tab"/>
  </method>
  <method name="quit"/>
  <method name="setActiveWindow">
    <arg direction="in" type="s" name="resourceClass"/>
    <arg direction="in" type="s" name="caption"/>
  </method>
</interface>
"""


@pyqtClassInfo("D-Bus Interface", SERVICE)
@pyqtClassInfo("D-Bus Introspection", INTROSPECTION)
class Adaptor(QDBusAbstractAdaptor):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.ctrl = ctrl

    @pyqtSlot()
    def toggle(self):
        self.ctrl.toggle(self.ctrl.default_tab())

    @pyqtSlot()
    def show(self):
        self.ctrl.popup.open("clipboard")

    @pyqtSlot()
    def showEmoji(self):
        self.ctrl.popup.open("emoji")

    @pyqtSlot()
    def showSettings(self):
        self.ctrl.popup.open("settings")

    @pyqtSlot(str)
    def showTab(self, tab):
        if tab in self.ctrl.popup.pages:
            self.ctrl.popup.open(tab)

    @pyqtSlot()
    def hide(self):
        self.ctrl.popup.close_popup()

    @pyqtSlot()
    def quit(self):
        QTimer.singleShot(0, QApplication.quit)

    @pyqtSlot(str, str)
    def setActiveWindow(self, resource_class, caption):
        self.ctrl.active_class = resource_class


# ---------------------------------------------------------------- controller
class Controller(QObject):
    def __init__(self):
        super().__init__()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(load_json(CONFIG_FILE, {}))
        self.config = cfg
        self.active_class = ""
        self.current = None           # what we believe is in the clipboard right now
        self.history = History(cfg["max_items"], cfg["persist_history"])
        self.clip = ClipService()
        self.clip.text_copied.connect(self._on_text)
        self.clip.image_copied.connect(self._on_image)
        self.popup = Popup(self)
        self.history.changed.connect(self._history_changed)
        self._ydotool = shutil.which("ydotool")
        self._setup_tray()
        self._load_kwin_script()
        self._register_shortcuts()
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def save_config(self):
        save_json(CONFIG_FILE, self.config)

    # -- settings
    def set_option(self, key, value):
        if self.config.get(key) == value:
            return
        self.config[key] = value
        self.save_config()
        if key == "max_items":
            self.history.set_max_items(value)
        elif key == "persist_history":
            self.history.set_persist(value)
        elif key == "position":
            self._load_kwin_script()
        elif key in ("language", "theme"):
            if key == "language":
                set_language(value)
            QTimer.singleShot(0, self.rebuild_ui)

    def rebuild_ui(self):
        """Recreate the popup and tray so a new language / theme takes effect at once."""
        old, visible = self.popup, self.popup.isVisible()
        old._was_active = False
        old.hide()
        old.deleteLater()
        self.popup = Popup(self)
        if self.tray:
            self.tray.hide()
            self.tray.deleteLater()
        self._setup_tray()
        if visible:
            self.popup.open("settings")

    def default_tab(self):
        tab = self.config.get("default_tab", "clipboard")
        if tab == "last":
            tab = self.config.get("last_tab", "clipboard")
        return tab if tab in Popup.TABS else "clipboard"

    def remember_tab(self, tab):
        if self.config.get("last_tab") != tab:
            self.config["last_tab"] = tab
            if self.config.get("default_tab") == "last":
                self.save_config()

    # -- clipboard events
    def _on_text(self, text, html):
        if html and len(html) > 512 * 1024:
            html = None
        self.current = self.history.add({"kind": "text", "text": text, "html": html})

    def _on_image(self, path, mime):
        if not self.config["save_images"]:
            self.current = None
            if not any(i.get("path") == path for i in self.history.items):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        self.current = self.history.add({"kind": "image", "path": path, "mime": mime})

    def _history_changed(self):
        if self.popup.isVisible() and self.popup.current == "clipboard":
            self.popup.pages["clipboard"].rebuild()

    # -- actions
    def toggle(self, tab):
        if self.popup.isVisible():
            self.popup.close_popup()
        else:
            self.popup.open(tab)

    def paste_item(self, item_id, plain=False, paste=True):
        item = self.history.find(item_id)
        if not item:
            return
        self.popup.close_popup()
        self.clip.set(item, plain=plain)
        self.current = item
        self.history.promote(item)
        if paste and self.config["paste_on_select"]:
            QTimer.singleShot(self.config["paste_delay_ms"], self.send_paste)

    def paste_glyph(self, text):
        previous = self.current
        self.popup.close_popup()
        self.clip.set({"kind": "text", "text": text})
        if not self.config["paste_on_select"]:
            self.current = {"kind": "text", "text": text}
            return
        QTimer.singleShot(self.config["paste_delay_ms"], self.send_paste)
        if not self.config["restore_after_emoji"]:
            self.current = {"kind": "text", "text": text}
        elif previous:   # put the user's real clipboard back, like Win11's emoji panel
            QTimer.singleShot(self.config["paste_delay_ms"] + self.config["restore_delay_ms"],
                              lambda: self.clip.set(previous))

    def send_paste(self):
        if not self._ydotool:
            print("ydotool not found: cannot paste automatically", file=sys.stderr)
            return
        terminals = {c.lower() for c in self.config["terminal_classes"]}
        if self.config["terminal_paste"] and self.active_class.lower() in terminals:
            keys = ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]   # Ctrl+Shift+V
        else:
            keys = ["29:1", "47:1", "47:0", "29:0"]                   # Ctrl+V
        env = dict(os.environ)
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket")
        if "YDOTOOL_SOCKET" not in env and os.path.exists(sock):
            env["YDOTOOL_SOCKET"] = sock
        subprocess.Popen([self._ydotool, "key", *keys], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- KWin script
    def _kwin(self):
        return QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                              QDBusConnection.sessionBus())

    def _load_kwin_script(self):
        kwin = self._kwin()
        if not kwin.isValid():
            print("KWin scripting interface not available", file=sys.stderr)
            return
        if kwin.call("isScriptLoaded", APP_ID).arguments()[:1] == [True]:
            kwin.call("unloadScript", APP_ID)
        # the script is a template: bake the placement setting into a runtime copy
        with open(os.path.join(HERE, "kwin", "contents", "code", "main.js"), encoding="utf-8") as fh:
            code = fh.read().replace("__PLACEMENT__", self.config.get("position", "mouse"))
        script = os.path.join(os.environ.get("XDG_RUNTIME_DIR", DATA_DIR), f"{APP_ID}-kwin.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(code)
        kwin.call("loadScript", script, APP_ID)
        kwin.call("start")

    # -- global shortcuts (KGlobalAccel)
    def _gdbus(self, method, *args):
        cmd = ["gdbus", "call", "--session", "--dest", "org.kde.kglobalaccel",
               "--object-path", "/kglobalaccel", "--method", f"org.kde.KGlobalAccel.{method}", *args]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    SHORTCUTS = {"toggle": ("shortcut", "Show clipboard history"),
                 "emoji": ("emoji_shortcut", "Show emoji panel")}

    def _ident(self, action):
        return f"['{APP_ID}', '{action}', '{APP_NAME}', '{self.SHORTCUTS[action][1]}']"

    @staticmethod
    def _keycode(key):
        return QKeySequence(key)[0].toCombined() if key else 0

    def _register_shortcuts(self):
        bus = QDBusConnection.sessionBus()
        bus.connect("org.kde.kglobalaccel", f"/component/{APP_ID}", "org.kde.kglobalaccel.Component",
                    "globalShortcutPressed", self._on_shortcut)
        for action, (cfg_key, _friendly) in self.SHORTCUTS.items():
            ident = self._ident(action)
            self._gdbus("doRegister", ident)
            default = self._keycode(DEFAULT_CONFIG[cfg_key])
            self._gdbus("setShortcut", ident, f"@ai [{default}]" if default else "@ai []", "uint32 5")
            # flag 2 = SetPresent: KGlobalAccel keeps a shortcut the user saved earlier
            # (here or in System Settings) and only falls back to ours on first run.
            code = self._keycode(self.config.get(cfg_key))
            self._gdbus("setShortcut", ident, f"@ai [{code}]" if code else "@ai []", "uint32 2")
            if action == "toggle" and not self.current_shortcut(action):
                msg = tr("shortcut_failed").format(key=self.config.get(cfg_key) or "?")
                print(msg, file=sys.stderr)
                if self.tray:
                    self.tray.showMessage(APP_NAME, msg)

    def current_shortcut(self, action):
        """The key KGlobalAccel really has for this action, as portable text ('' if none)."""
        codes = [int(c) for c in re.findall(r"\d+", self._gdbus("shortcut", self._ident(action)))]
        code = next((c for c in codes if c), 0)
        return QKeySequence(code).toString(QKeySequence.SequenceFormat.PortableText) if code else ""

    def set_shortcut(self, action, key):
        """Change a shortcut from the settings page. Returns (ok, name_of_conflicting_app)."""
        code = self._keycode(key)
        result = self._gdbus("setShortcut", self._ident(action),
                             f"@ai [{code}]" if code else "@ai []", "uint32 6")   # 6 = force
        if code and str(code) not in result:
            owner = ""
            try:
                ids = ast.literal_eval(self._gdbus("action", str(code)))[0]
                owner = ids[2] if len(ids) > 2 else ""
            except (ValueError, SyntaxError, IndexError):
                pass
            return False, owner
        self.config[self.SHORTCUTS[action][0]] = key
        self.save_config()
        return True, ""

    @pyqtSlot(str, str, "qlonglong")
    def _on_shortcut(self, component, action, _ts):
        if component != APP_ID:
            return
        if action == "toggle":
            self.toggle(self.default_tab())
        elif action == "emoji":
            self.toggle("emoji")

    # -- tray
    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon.fromTheme("edit-paste", QIcon.fromTheme("klipper"))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(tr("show"), lambda: self.popup.open("clipboard"))
        menu.addAction(tr("show_emoji"), lambda: self.popup.open("emoji"))
        menu.addAction(tr("settings"), lambda: self.popup.open("settings"))
        menu.addSeparator()
        menu.addAction(tr("quit"), QApplication.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle(self.default_tab())
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def set_autostart(self, on):
        if on:
            os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
            with open(AUTOSTART_FILE, "w", encoding="utf-8") as fh:
                exe = shutil.which("kclipboard") or f"{sys.executable} {os.path.abspath(__file__)}"
                fh.write("[Desktop Entry]\nType=Application\nName=KClipboard\n"
                         f"Exec={exe}\n"
                         "Icon=edit-paste\nX-KDE-autostart-phase=2\nNoDisplay=true\n")
        elif os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)

    def shutdown(self):
        self.history._save()
        self.clip.stop()
        kwin = self._kwin()
        if kwin.isValid():
            kwin.call("unloadScript", APP_ID)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    QGuiApplication.setDesktopFileName(APP_ID)   # Wayland app_id -> KWin resourceClass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("No D-Bus session bus", file=sys.stderr)
        return 1
    method = {"--toggle": "toggle", "--show": "show", "--emoji": "showEmoji",
              "--settings": "showSettings", "--hide": "hide", "--quit": "quit"}.get(cmd)
    if not bus.registerService(SERVICE):
        # already running: forward the command to the running instance
        QDBusInterface(SERVICE, "/", SERVICE, bus).call(method or "show")
        return 0
    if cmd == "--quit":
        return 0
    ctrl = Controller()
    Adaptor(ctrl)
    bus.registerObject("/", ctrl)
    if method in ("toggle", "show"):
        QTimer.singleShot(300, lambda: ctrl.popup.open("clipboard"))
    elif method in ("showEmoji", "showSettings"):
        tab = "emoji" if method == "showEmoji" else "settings"
        QTimer.singleShot(300, lambda: ctrl.popup.open(tab))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
