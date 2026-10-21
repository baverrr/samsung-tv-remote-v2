#!/bin/sh
# Installs TV Remote for the current user: a venv, the tv-remote launcher, the menu entry and
# tvremote:// links, the icon, and (with --audio) the service that sends sound to the TV while mirroring.
# Run it from the checkout, wherever it lives; run it again after moving the checkout.
#   ./install.sh [--audio]
set -eu

APP_DIR=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="$HOME/.local/bin"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUDIO=no
for arg in "$@"; do
    case "$arg" in
        --audio) AUDIO=yes ;;
        *) echo "usage: $0 [--audio]" >&2; exit 2 ;;
    esac
done

# The paths go into a shell script, a .desktop file and a systemd unit; these characters would need
# different escaping in each (systemd expands %, the launcher would expand $ and `).
for dir in "$APP_DIR" "$BIN_DIR"; do
    case "$dir" in
        *[\"\$\`%\\]*) echo "Can't install from $dir: move the checkout to a path without \" \$ \` % or \\" >&2; exit 1 ;;
    esac
done

sed_escape() {  # make $1 safe as a sed replacement (| is the delimiter, & means "the match")
    printf '%s' "$1" | sed 's/[|&]/\\&/g'
}

fill() {  # template -> destination, with @APP_DIR@ and @BIN@ filled in
    mkdir -p "$(dirname "$2")"
    sed -e "s|@APP_DIR@|$(sed_escape "$APP_DIR")|g" -e "s|@BIN@|$(sed_escape "$BIN_DIR/tv-remote")|g" "$1" > "$2"
}

if ! python3 -c "import PySide6.QtWidgets, PySide6.QtSvg" 2>/dev/null; then
    echo "Qt for Python is missing: sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg" >&2
    exit 1
fi

echo "venv: $APP_DIR/venv"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv --system-site-packages "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet websocket-client

echo "launcher: $BIN_DIR/tv-remote"
fill "$APP_DIR/packaging/tv-remote.in" "$BIN_DIR/tv-remote"
chmod 755 "$BIN_DIR/tv-remote"

echo "menu entry: $DATA/applications/tv-remote.desktop"
fill "$APP_DIR/packaging/tv-remote.desktop.in" "$DATA/applications/tv-remote.desktop"
# Named tvremote, no dash: with one, icon themes can fall back to their own monochrome "tv" icon.
install -Dm644 "$APP_DIR/tv-remote.svg" "$DATA/icons/hicolor/scalable/apps/tvremote.svg"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}"  # xdg-mime writes mimeapps.list there
command -v xdg-mime >/dev/null && xdg-mime default tv-remote.desktop x-scheme-handler/tvremote 2>/dev/null || true

if [ "$AUDIO" = yes ]; then
    echo "audio service: $UNITS/tv-mirror-audio.service"
    fill "$APP_DIR/packaging/tv-mirror-audio.service.in" "$UNITS/tv-mirror-audio.service"
    systemctl --user daemon-reload
    systemctl --user enable --now tv-mirror-audio.service
    systemctl --user restart tv-mirror-audio.service  # picks up a moved checkout
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR isn't on your PATH; the menu entry works anyway." ;;
esac
echo "Done. Set your TV's address in ~/.config/tv-remote/config.json (see README), then open TV Remote."
