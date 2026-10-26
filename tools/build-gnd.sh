#!/bin/sh
# Builds GNOME Network Displays with tools/gnd-window-resize.patch into ~/.local/opt/gnd, which
# tv-remote mirror then uses instead of the system copy. The patch keeps a mirrored window streaming
# when it is resized (or a video in it goes full screen). The fix that matters: pipewiresrc no longer
# clocks the pipeline, since its clock stops while a resize is renegotiated and everything waiting on
# it hangs. Also: square pixels before the encoder (a window of any shape is letterboxed, and the
# encoder's input never changes), a non-async capture sink, frames copied out of PipeWire, and a
# crash on exit fixed.
# The build runs in a container of this machine's Ubuntu release, so no -dev packages are installed here.
#   tools/build-gnd.sh [version]     (default: the installed gnome-network-displays version)
set -eu

TOOLS=$(cd "$(dirname "$0")" && pwd)
PREFIX="$HOME/.local/opt/gnd"
. /etc/os-release
VERSION=${1:-$(dpkg-query -W -f '${Version}' gnome-network-displays 2>/dev/null | sed 's/-.*//')}
[ -n "$VERSION" ] || { echo "Couldn't tell which version to build; pass one, e.g. $0 0.99.0" >&2; exit 2; }
command -v docker >/dev/null || { echo "This needs Docker." >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone -q --depth 1 --branch "$VERSION" https://gitlab.gnome.org/GNOME/gnome-network-displays.git "$WORK/src"
git -C "$WORK/src" apply "$TOOLS/gnd-window-resize.patch"

tar -C "$WORK/src" --exclude=.git -c . | docker run --rm -i "ubuntu:$VERSION_ID" sh -c '
    set -e; export DEBIAN_FRONTEND=noninteractive
    {   # stdout carries the built files back; everything else goes to stderr
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends meson ninja-build gcc pkg-config gettext \
            libglib2.0-dev libglib2.0-bin desktop-file-utils appstream libgtk-4-dev libadwaita-1-dev \
            libportal-gtk4-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstrtspserver-1.0-dev \
            libavahi-client-dev libavahi-gobject-dev libnm-dev libpulse-dev libsystemd-dev libjson-glib-dev \
            libsoup-3.0-dev libprotobuf-c-dev
        mkdir /src && cd /src && tar x <&3
        meson setup /b --prefix=/opt/gnd -Dbuild_daemon=false -Dfirewalld_zone=false
        ninja -C /b && DESTDIR=/out ninja -C /b install
    } 3<&0 </dev/null >&2
    tar -C /out/opt/gnd -c bin share' >"$WORK/gnd.tar"

mkdir -p "$PREFIX"
tar -C "$PREFIX" -xf "$WORK/gnd.tar"
echo "Installed $PREFIX/bin/gnome-network-displays ($VERSION, patched)"
