#!/usr/bin/env bash
# Remove KClipboard.   ./uninstall.sh [--purge]   (--purge also deletes history & settings)
set -u
command -v kclipboard >/dev/null && kclipboard --quit 2>/dev/null
sleep 0.5
for action in toggle emoji; do
    gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
        --method org.kde.KGlobalAccel.unregister kclipboard "$action" >/dev/null 2>&1
done
rm -rf "$HOME/.local/lib/kclipboard" "$HOME/.local/bin/kclipboard" \
       "${XDG_DATA_HOME:-$HOME/.local/share}/applications/kclipboard.desktop" \
       "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/kclipboard.desktop"
if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/kclipboard" "${XDG_CONFIG_HOME:-$HOME/.config}/kclipboard"
fi
echo "KClipboard removed. Old Meta+V owner: restore it in System Settings > Shortcuts"
echo "(or copy back ~/.config/kglobalshortcutsrc.bak-kclipboard-* and log out/in)."
