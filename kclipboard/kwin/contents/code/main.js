// Loaded at runtime by kclipboard (org.kde.kwin.Scripting.loadScript).
// Wayland clients cannot position their own windows, so KWin does it for us:
// every time the popup is mapped it is moved next to the mouse cursor (or centered)
// and focused.
const APP_ID = "kclipboard";
const SERVICE = "io.github.kclipboard";
const GAP = 6;
const PLACEMENT = "__PLACEMENT__";   // "mouse" or "center", filled in by app.py

function isOurs(w) {
    return w && (w.resourceClass == APP_ID || w.desktopFileName == APP_ID);
}

function place(w) {
    const cur = workspace.cursorPos;
    const output = workspace.screenAt ? workspace.screenAt(cur) : workspace.activeScreen;
    const area = workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
    const g = w.frameGeometry;
    let x = cur.x - 16;
    let y = cur.y + GAP;
    if (PLACEMENT == "center") {
        x = area.x + (area.width - g.width) / 2;
        y = area.y + (area.height - g.height) / 2;
    } else if (y + g.height > area.y + area.height) {
        y = cur.y - g.height - GAP;            // not enough room below: open above
    }
    x = Math.max(area.x, Math.min(x, area.x + area.width - g.width));
    y = Math.max(area.y, Math.min(y, area.y + area.height - g.height));
    w.frameGeometry = { x: x, y: y, width: g.width, height: g.height };
    w.keepAbove = true;
    w.skipTaskbar = true;
    w.skipPager = true;
    w.skipSwitcher = true;
    workspace.activeWindow = w;
}

workspace.windowAdded.connect(function (w) {
    if (isOurs(w)) {
        place(w);
    }
});

workspace.windowActivated.connect(function (w) {
    if (w && !isOurs(w)) {
        callDBus(SERVICE, "/", SERVICE, "setActiveWindow",
                 String(w.resourceClass || ""), String(w.caption || ""));
    }
});
