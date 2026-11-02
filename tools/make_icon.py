"""Renders tv-remote.svg as a Windows icon (16 to 256 px in one .ico) for the Start menu shortcut and
tvremote:// links. install.ps1 runs it with the venv's Python.

  python -I tools/make_icon.py <out.ico>
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window: it only paints into images

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SVG = Path(__file__).resolve().parent.parent / "tv-remote.svg"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    QSvgRenderer(str(SVG)).render(p, QRectF(0, 0, size, size))
    p.end()
    return image


def ico(images):
    """An .ico holding each image as a PNG (Windows Vista and later read those at every size)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    pngs = []
    for image in images:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        pngs.append(bytes(data.data()))
    out = bytearray((0, 0, 1, 0, len(pngs), 0))  # ICONDIR: reserved, type 1 (icon), count
    offset = 6 + 16 * len(pngs)
    for image, png in zip(images, pngs, strict=True):
        side = image.width() % 256  # 0 means 256
        out += bytes((side, side, 0, 0, 1, 0, 32, 0)) + len(png).to_bytes(4, "little") + offset.to_bytes(4, "little")
        offset += len(png)
    for png in pngs:
        out += png
    return bytes(out)


def main(args):
    if len(args) != 1:
        sys.exit("usage: make_icon.py <out.ico>")
    app = QGuiApplication([])  # noqa: F841 - QSvgRenderer and QImage need one
    Path(args[0]).write_bytes(ico([render(s) for s in SIZES]))


if __name__ == "__main__":
    main(sys.argv[1:])
