#!/bin/sh
# Installs Samsung & Apple TV Remote for the current user: a venv, the tv-remote launcher, the menu entry and
# tvremote:// links, the icon, (with --audio) the service that sends sound to the TV while mirroring,
# and (with --bridge) the HTTP bridge that ESP32 and other network remotes send their keys to.
# Run it from the checkout, wherever it lives; run it again after moving the checkout.
#   ./install.sh [--audio] [--bridge]
set -eu

APP_DIR=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="$HOME/.local/bin"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUDIO=no
BRIDGE=no
for arg in "$@"; do
    case "$arg" in
        --audio) AUDIO=yes ;;
        --bridge) BRIDGE=yes ;;
        *) echo "usage: $0 [--audio] [--bridge]" >&2; exit 2 ;;
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
# pyatv: the Apple TV remote (Companion, AirPlay and MRP protocols). srptools 2 made an argument pyatv
# 0.18 passes keyword-only, which breaks pairing, so it stays on 1.x. biplist: the AirPlay patch below.
"$APP_DIR/venv/bin/pip" install --quiet websocket-client "pyatv==0.18.0" "srptools==1.0.1" "biplist==1.0.3"
# AirPlay casting to tvOS 26+ needs an unreleased pyatv fix (see the patch's header). Applied once:
# a reverse dry run that succeeds means it's already in.
SITE=$("$APP_DIR/venv/bin/python" -c "import os, pyatv; print(os.path.dirname(os.path.dirname(pyatv.__file__)))")
if patch -p1 -R -s -f --dry-run -d "$SITE" < "$APP_DIR/tools/pyatv-airplay.patch" >/dev/null 2>&1; then
    :
elif ! command -v patch >/dev/null; then
    echo "Note: 'patch' is missing (sudo apt install patch), so AirPlay casting to an Apple TV stays off." >&2
elif patch -p1 -N -s -f --dry-run -d "$SITE" < "$APP_DIR/tools/pyatv-airplay.patch" >/dev/null 2>&1 &&
     patch -p1 -N -s -d "$SITE" < "$APP_DIR/tools/pyatv-airplay.patch"; then  # all or nothing
    echo "pyatv: patched for AirPlay casting ($SITE/pyatv)"
else
    echo "Note: tools/pyatv-airplay.patch didn't apply, so AirPlay casting to an Apple TV stays off." >&2
fi

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

if [ "$BRIDGE" = yes ]; then
    echo "bridge service: $UNITS/tv-remote-bridge.service (port 8198; token: tv-remote serve --token)"
    fill "$APP_DIR/packaging/tv-remote-bridge.service.in" "$UNITS/tv-remote-bridge.service"
    systemctl --user daemon-reload
    systemctl --user enable --now tv-remote-bridge.service
    systemctl --user restart tv-remote-bridge.service  # picks up a moved checkout or new code
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR isn't on your PATH; the menu entry works anyway." ;;
esac
echo "Done. Set your TV's address in ~/.config/tv-remote/config.json (see README), then open Samsung & Apple TV Remote."
