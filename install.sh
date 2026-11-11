#!/usr/bin/env bash
# Install KClipboard for the current user (no root needed).
#   ./install.sh               install + take over Meta+V (backs up kglobalshortcutsrc first)
#   ./install.sh --keep-shortcut-owner   do not touch the app that currently owns Meta+V
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kclipboard"
LIB="$HOME/.local/lib/kclipboard"
BIN="$HOME/.local/bin/kclipboard"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
TAKE_SHORTCUT=1
[[ "${1:-}" == "--keep-shortcut-owner" ]] && TAKE_SHORTCUT=0

ok()   { printf '  \e[32m✓\e[0m %s\n' "$*"; }
warn() { printf '  \e[33m!\e[0m %s\n' "$*"; }
die()  { printf '  \e[31m✗\e[0m %s\n' "$*"; exit 1; }

echo "Checking requirements"
python3 -c 'import PyQt6.QtWidgets, PyQt6.QtDBus' 2>/dev/null && ok "PyQt6" \
    || die "PyQt6 missing: sudo apt install python3-pyqt6"
command -v gdbus >/dev/null && ok "gdbus" || die "gdbus missing: sudo apt install libglib2.0-bin"
[[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] && ok "Wayland session" \
    || warn "not a Wayland session: clipboard monitoring needs KWin Wayland"
if command -v ydotool >/dev/null; then
    if systemctl --user is-active --quiet ydotool 2>/dev/null || pgrep -x ydotoold >/dev/null; then
        ok "ydotool daemon running (paste on select)"
    else
        warn "ydotoold is not running: systemctl --user enable --now ydotool"
    fi
else
    warn "ydotool missing (needed for paste on select): sudo apt install ydotool"
fi
fc-list : family | grep -i "Noto Color Emoji" >/dev/null && ok "Noto Color Emoji font" \
    || warn "Noto Color Emoji font missing: sudo apt install fonts-noto-color-emoji"

echo "Installing"
command -v kclipboard >/dev/null && kclipboard --quit 2>/dev/null || true
mkdir -p "$(dirname "$LIB")" "$(dirname "$BIN")" "$APPS" "$AUTOSTART"
rm -rf "$LIB"
cp -r "$SRC" "$LIB"
find "$LIB" -name __pycache__ -prune -exec rm -rf {} +
ok "files -> $LIB"

cat > "$BIN" <<EOF
#!/bin/sh
exec python3 "$LIB/app.py" "\$@"
EOF
chmod +x "$BIN"
ok "launcher -> $BIN"

cat > "$APPS/kclipboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KClipboard
GenericName=Clipboard history & emoji
Comment=Windows 11 style clipboard history with emoji, kaomoji and symbols
Exec=$BIN --show
Icon=edit-paste
Categories=Utility;
Keywords=clipboard;emoji;history;paste;
StartupNotify=false
Actions=emoji;settings;quit;

[Desktop Action emoji]
Name=Emoji
Exec=$BIN --emoji

[Desktop Action settings]
Name=Settings
Exec=$BIN --settings

[Desktop Action quit]
Name=Quit
Exec=$BIN --quit
EOF
cat > "$AUTOSTART/kclipboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KClipboard
Exec=$BIN
Icon=edit-paste
NoDisplay=true
X-KDE-autostart-phase=2
EOF
ok "menu entry + autostart"

KEY=268435542   # Meta+V
owner=$(gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
        --method org.kde.KGlobalAccel.action "$KEY" 2>/dev/null || true)
if [[ -n "$owner" && "$owner" != "(@as [],)" && "$owner" != *"'kclipboard'"* ]]; then
    if (( TAKE_SHORTCUT )); then
        cfg="${XDG_CONFIG_HOME:-$HOME/.config}/kglobalshortcutsrc"
        backup="$cfg.bak-kclipboard-$(date +%Y%m%d-%H%M%S)"
        cp "$cfg" "$backup"
        ids=$(sed -E 's/^\((\[.*\]),\)$/\1/' <<<"$owner")
        gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
            --method org.kde.KGlobalAccel.setForeignShortcut "$ids" "@ai []" >/dev/null
        ok "freed Meta+V from $ids (backup: $backup)"
    else
        warn "Meta+V is used by $owner - change it in System Settings > Shortcuts"
    fi
fi

nohup "$BIN" >/dev/null 2>&1 &
disown || true
ok "started - press Meta+V"
