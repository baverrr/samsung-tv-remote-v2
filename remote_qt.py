"""Qt interface for the TV remote: a handheld-remote layout plus the TV Cast window.

Started by tv_remote.py (no arguments, or a tvremote:// link). Talks to the TV through tv_remote.TV and
cast.Caster on worker threads; their events reach the GUI through a Qt signal.
Icons are Lucide (ISC, icons/LICENSE-lucide), recoloured at load time (QSS images use pre-tinted copies).
Sizes follow the system font (KDE's font setting), so a larger font makes the whole remote larger.
"""

import functools
import math
import os
import queue
import socket
import struct
import threading
import time
import traceback
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import cast
import tv_remote as core  # the launcher runs tv_remote.py as __main__; this imports it again as a module

HERE = Path(__file__).resolve().parent
INSTANCE_SOCKET = f"\0tv-remote-{os.getuid()}"  # abstract unix socket: one remote window per user
ICONS = HERE / "icons"

# Design tokens: dark, like a physical remote. Text (fg, muted) meets 4.5:1 on every surface; edge and the
# resting accent/danger fills meet 3:1 against the surfaces they sit on (the pressed accent_down is 2.66:1 on
# body; it only shows while held); the 2 px focus ring meets 3:1 against every fill, including the hover and
# pressed ones. test_gui.py checks these ratios.
T = {
    "bg": "#0e1014", "body": "#181b21", "body_top": "#1f232a", "raised": "#262b34", "hover": "#30363f",
    "pressed": "#1c2027", "inset": "#111317", "border": "#373d49", "edge": "#666d7c", "focus": "#ffffff",
    "fg": "#f1f3f7", "muted": "#a3abba",
    "accent": "#5b93ff", "accent_fill": "#2f6fe4", "accent_hover": "#3d7cf0", "accent_down": "#2559bd",
    "danger": "#e5484d", "danger_hover": "#f05a5f", "ok": "#3dd68c", "warn": "#f5a524", "off": "#6b7280",
}
DESIGN_PT = 10  # the system font size the pixel sizes below were drawn for

# @fs_* are the three type sizes (caption, body, heading), derived from the system font in stylesheet().
QSS = """
QWidget { color: @fg; font-size: @fs_body; }
QWidget#window { background: @bg; }
QScrollArea, QScrollArea > QWidget > QWidget#scrollContent { background: transparent; border: none; }
QFrame#body {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @body_top, stop:1 @body);
    border: 1px solid @border; border-radius: 34px;
}
QToolTip { background: @raised; color: @fg; border: 1px solid @border; padding: 4px 6px; }

QToolButton {
    background: @raised; border: 1px solid @edge; border-radius: 12px; padding: 6px 10px;
}
QToolButton:hover { background: @hover; }
QToolButton:pressed, QToolButton:checked { background: @pressed; }
QToolButton:disabled { color: @off; border-color: @border; }
QToolButton::menu-indicator { image: none; width: 0; }
QToolButton[tone="danger"] { background: @danger; border-color: @danger; }
QToolButton[tone="danger"]:hover { background: @danger_hover; border-color: @danger_hover; }
QToolButton[tone="accent"] { background: @accent_fill; border-color: @accent_fill; color: #ffffff; font-weight: 600; }
QToolButton[tone="accent"]:hover { background: @accent_hover; border-color: @accent_hover; }
QToolButton[tone="accent"]:pressed { background: @accent_down; border-color: @accent_down; }
QToolButton[tone="outline"] { background: transparent; }
QToolButton[tone="outline"]:hover { background: @hover; }
QToolButton[tone="ghost"] { background: transparent; border-color: transparent; color: @muted; }
QToolButton[tone="ghost"]:hover { background: @hover; color: @fg; }
QToolButton[tone="tile"] { border-radius: 16px; padding: 10px; font-weight: 600; }

QFrame#display { background: @inset; border: 1px solid @border; border-radius: 14px; }
QLabel#statusText { color: @muted; }
QLabel[role="caption"] { color: @muted; font-size: @fs_caption; }
QLabel[role="muted"] { color: @muted; }
QLabel[role="section"] { color: @muted; font-size: @fs_caption; font-weight: 600; letter-spacing: 1px; }
QLabel#title { font-size: @fs_heading; font-weight: 600; }

QFrame#rocker { background: @raised; border: 1px solid @edge; }
QFrame#rocker QToolButton { background: transparent; border-color: transparent; }
QFrame#rocker QToolButton:hover { background: @hover; }
QFrame#rocker QToolButton:pressed { background: @pressed; }
QLabel#rockerLabel { color: @muted; font-size: @fs_caption; font-weight: 700; letter-spacing: 1px; }

QFrame#card { background: @body_top; border: 1px solid @border; border-radius: 18px; }
QFrame#card[drag="true"] { border: 1px dashed @accent; }

QLineEdit {
    background: @inset; border: 1px solid @edge; border-radius: 12px; padding: 8px 10px;
    selection-background-color: @accent_fill;
}
QMenu { background: @raised; border: 1px solid @border; border-radius: 10px; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: @accent_fill; color: #ffffff; }

QSlider::groove:horizontal { height: 4px; background: @raised; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QSlider::add-page:horizontal { background: @edge; border-radius: 2px; }
QSlider::handle:horizontal { background: @fg; width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
                             border: 1px solid @fg; }
QSlider::handle:horizontal:hover { background: #ffffff; }
QSlider::sub-page:horizontal:disabled { background: @raised; }
QSlider::add-page:horizontal:disabled { background: @raised; }
QSlider::handle:horizontal:disabled { background: @border; border-color: @border; }

QSpinBox { background: @inset; border: 1px solid @edge; border-radius: 8px; padding: 3px 6px; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; border: none; background: transparent; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: @hover; border-radius: 4px; }
QSpinBox::up-arrow { image: url(@chev_up); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url(@chev_down); width: 10px; height: 10px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid @edge; background: @inset;
}
QCheckBox::indicator:checked { background: @accent_fill; border-color: @accent_fill; image: url(@check); }
QListWidget { background: @inset; border: 1px solid @edge; border-radius: 12px; padding: 4px; outline: none; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; }
QListWidget::item:hover { background: @hover; }
QListWidget::item:selected { background: @accent_fill; color: #ffffff; }
QListWidget::item:disabled { color: @off; }
QListWidget::indicator {
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid @edge; background: @inset;
}
QListWidget::indicator:checked { background: @accent_fill; border-color: @accent_fill; image: url(@check); }
QListWidget::indicator:checked:selected { border-color: #ffffff; }
QMessageBox { background: @body; }

/* Keyboard focus last, so it wins over every tone: a 2 px white ring (padding shrinks by 1 px to keep
   the content still). Round keys set padding 0 on themselves, which takes precedence. */
QToolButton:focus, QToolButton[tone]:focus { border: 2px solid @focus; padding: 5px 9px; }
QToolButton[tone="tile"]:focus { padding: 9px; }
QFrame#rocker QToolButton:focus { border: 2px solid @focus; }
QLineEdit:focus { border: 2px solid @focus; padding: 7px 9px; }
QSpinBox:focus { border: 2px solid @focus; padding: 2px 5px; }
QCheckBox::indicator:focus { border: 2px solid @focus; }
QListWidget:focus { border: 2px solid @focus; padding: 3px; }
QSlider::handle:horizontal:focus { background: @accent_fill; border: 2px solid @focus; }
/* The ring keys are invisible buttons; DPad paints their states and focus ring. After the focus rules,
   so a focused ring key doesn't get the square border (clipped by its mask into stray white marks). */
QToolButton[tone="ring"], QToolButton[tone="ring"]:hover, QToolButton[tone="ring"]:pressed,
QToolButton[tone="ring"]:focus { background: transparent; border: none; }
"""


def font_pt():
    """The system font size in points (KDE's General font)."""
    app = QApplication.instance()
    pt = app.font().pointSizeF() if isinstance(app, QApplication) else -1
    return pt if pt > 0 else DESIGN_PT


def px(n):
    """A design size in pixels, grown with the system font but never shrunk below the design size
    (so 44 px keys stay at least 44 px)."""
    return round(n * max(1.0, font_pt() / DESIGN_PT))


def stylesheet():
    base = font_pt()
    # Three steps of a modular scale (major third, 1.25), with captions held at 0.9× (9 pt ≈ 12 px at the
    # 10 pt design size, 96 DPI).
    subs = {**T, "fs_caption": f"{base * 0.9:.1f}pt", "fs_body": f"{base:.1f}pt", "fs_heading": f"{base * 1.25:.1f}pt",
            "check": str(ICONS / "check-white.svg"), "chev_up": str(ICONS / "chevron-up-muted.svg"),
            "chev_down": str(ICONS / "chevron-down-muted.svg")}
    css = QSS
    for name in sorted(subs, key=len, reverse=True):  # @accent_fill before @accent
        css = css.replace("@" + name, subs[name])
    return css


def palette():
    """Dark palette, so dialogs and menus the stylesheet doesn't cover match the remote."""
    p = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, T["body"]), (QPalette.ColorRole.WindowText, T["fg"]), (QPalette.ColorRole.Base, T["inset"]),
                         (QPalette.ColorRole.AlternateBase, T["raised"]), (QPalette.ColorRole.Text, T["fg"]),
                         (QPalette.ColorRole.Button, T["raised"]), (QPalette.ColorRole.ButtonText, T["fg"]),
                         (QPalette.ColorRole.ToolTipBase, T["raised"]), (QPalette.ColorRole.ToolTipText, T["fg"]),
                         (QPalette.ColorRole.Highlight, T["accent_fill"]), (QPalette.ColorRole.HighlightedText, "#ffffff"),
                         (QPalette.ColorRole.PlaceholderText, T["muted"]), (QPalette.ColorRole.Link, T["accent"])):
        p.setColor(role, QColor(colour))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(T["off"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(T["off"]))
    return p


def svg_renderer(name, colour):
    data = (ICONS / f"{name}.svg").read_text().replace("currentColor", colour)
    return QSvgRenderer(QByteArray(data.encode()))


@functools.cache
def icon(name, colour=None, size=20):
    """Lucide icon tinted for normal and disabled states, sharp at 1x and 2x scale."""
    result = QIcon()
    for mode, tint in ((QIcon.Mode.Normal, colour or T["fg"]), (QIcon.Mode.Disabled, T["off"])):
        renderer = svg_renderer(name, tint)
        for scale in (1, 2):
            pm = QPixmap(size * scale, size * scale)
            pm.fill(Qt.GlobalColor.transparent)
            pm.setDevicePixelRatio(scale)
            p = QPainter(pm)
            renderer.render(p, QRectF(0, 0, size, size))
            p.end()
            result.addPixmap(pm, mode)
    return result


def button(text="", icon_name=None, tip=None, tone=None, round=None, min_size=None, repeat=False, on_click=None,
           icon_size=20, icon_colour=None, expand=False):
    """round: diameter of a round icon key. min_size: (w, h) floor for text keys, which grow with their text.
    round, min_size and icon_size are design sizes (scaled by px()). expand: take the row's spare width.
    The accessible name is the tip without its "(shortcut)" suffix."""
    b = QToolButton()
    if text:
        b.setText(text)
    if icon_name:
        s = px(icon_size)
        b.setIcon(icon(icon_name, icon_colour, s))
        b.setIconSize(QSize(s, s))
        if text:
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setText(" " + text)  # QSS can't set the icon-text gap
    tip = tip or text
    b.setToolTip(tip)
    b.setAccessibleName(tip.split(" (")[0])
    if tone:
        b.setProperty("tone", tone)
    if round:
        d = px(round)
        b.setFixedSize(d, d)
        b.setStyleSheet(f"QToolButton {{ border-radius: {d // 2}px; padding: 0px; }}")
    if min_size:
        b.setMinimumSize(px(min_size[0]), px(min_size[1]))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # arrow keys drive the TV; Tab still reaches every button
    if repeat:  # hold to repeat, like a real remote
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(350)
        b.setAutoRepeatInterval(120)
    if expand:
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    if on_click:
        b.clicked.connect(on_click)
    return b


def fmt_clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60}:{s % 60:02}"


def frame(name):
    f = QFrame()
    f.setObjectName(name)  # styled by QFrame#name in QSS
    return f


def label(text="", role=None, name=None):
    lab = QLabel(text)
    # Labels show file names and what the TV reports; never let those render as HTML.
    lab.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        lab.setProperty("role", role)
    if name:
        lab.setObjectName(name)
    return lab


def captioned(widget, caption):
    """A round remote key with its printed label underneath."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    v.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
    cap = label(caption, "caption")
    cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    v.addWidget(cap)
    return box


def hbox(*widgets, spacing=8):
    row = QHBoxLayout()
    row.setSpacing(spacing)
    row.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if w is None:
            row.addStretch(1)
        elif isinstance(w, int):
            row.addSpacing(w)
        else:
            row.addWidget(w)
    return row


def bring_forward(window):
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


def place_body(window, body, margin):
    """Puts a window's body (the remote, or TV Cast) at its natural size, centred, so maximized, full-screen
    or resized windows add space around it instead of stretching its keys. Scrolls (decided once, at
    construction) only when the body is taller than the screen's available height: a large font or a short
    screen."""
    body.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    outer = QVBoxLayout(window)
    outer.setContentsMargins(margin, margin, margin, margin)
    hint = body.sizeHint()
    screen = window.screen() or QApplication.primaryScreen()
    room = screen.availableGeometry().height() - px(48) - 2 * margin  # px(48): room for the title bar
    if hint.height() <= room:
        outer.addWidget(body, 0, Qt.AlignmentFlag.AlignCenter)
        window.setMinimumSize(window.sizeHint())
        return
    content = QWidget()
    content.setObjectName("scrollContent")
    inner = QVBoxLayout(content)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.addWidget(body, 0, Qt.AlignmentFlag.AlignHCenter)
    scroll = QScrollArea()
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(scroll)
    window.setMinimumWidth(hint.width() + 2 * margin + scroll.verticalScrollBar().sizeHint().width() + 4)
    window.resize(window.minimumWidth(), room + 2 * margin)


def toggle_full_screen(window):
    window.showNormal() if window.isFullScreen() else window.showFullScreen()


class DPad(QWidget):
    """Round direction ring with OK in the middle.

    The four arrows and OK are real buttons, the arrows masked to their wedge of the ring and OK to the
    centre circle, so Tab, screen readers,
    hold-to-repeat and keyboard shortcuts treat them like every other key. The buttons are transparent; this
    widget paints the ring and lights the hovered, pressed or focused segment.
    """

    ANGLES: ClassVar[dict[str, int]] = {"KEY_RIGHT": 0, "KEY_UP": 90, "KEY_LEFT": 180, "KEY_DOWN": 270}
    CHEVRONS: ClassVar[dict[str, str]] = {"KEY_RIGHT": "chevron-right", "KEY_UP": "chevron-up", "KEY_LEFT": "chevron-left",
                "KEY_DOWN": "chevron-down"}

    def __init__(self, make_key):
        super().__init__()
        d = px(188)
        self.setFixedSize(d, d)
        self.chevrons = {key: svg_renderer(name, T["fg"]) for key, name in self.CHEVRONS.items()}
        self.keys = {}
        # Creation order is the Tab order: reading order (Up; Left, OK, Right; Down), not clockwise.
        for key, tip in (("KEY_UP", "Up (↑)"), ("KEY_LEFT", "Left (←)"), ("KEY_ENTER", "OK (Enter)"),
                         ("KEY_RIGHT", "Right (→)"), ("KEY_DOWN", "Down (↓)")):
            b = make_key(key, tip=tip, tone="ring", repeat=key != "KEY_ENTER")
            b.setParent(self)
            b.installEventFilter(self)
            b.pressed.connect(self.update)
            b.released.connect(self.update)
            self.keys[key] = b
        self._place_keys()

    def _geometry(self):
        r = self.width() / 2 - 1.5
        return QPointF(self.width() / 2, self.height() / 2), r, r * 0.37

    def _wedge(self, key, inset=0.0):
        c, r, r_ok = self._geometry()
        outer = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r).adjusted(inset, inset, -inset, -inset)
        inner_r = r_ok + 3 + inset  # 3 = half the 6 px groove pen around OK, so wedges start outside it
        inner = QRectF(c.x() - inner_r, c.y() - inner_r, 2 * inner_r, 2 * inner_r)
        a = self.ANGLES[key]
        path = QPainterPath()
        path.arcMoveTo(outer, a - 45)
        path.arcTo(outer, a - 45, 90)
        path.arcTo(inner, a + 45, -90)
        path.closeSubpath()
        return path

    def _place_keys(self):
        c, _, r_ok = self._geometry()
        for key, b in self.keys.items():
            if key == "KEY_ENTER":
                b.setGeometry(round(c.x() - r_ok), round(c.y() - r_ok), round(2 * r_ok), round(2 * r_ok))
                b.setMask(QRegion(0, 0, b.width(), b.height(), QRegion.RegionType.Ellipse))
            else:
                b.setGeometry(0, 0, self.width(), self.height())
                b.setMask(QRegion(self._wedge(key).toFillPolygon().toPolygon()))

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return False

    def _state(self, b):
        return "pressed" if b.isDown() else "hover" if b.underMouse() else None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, r, r_ok = self._geometry()
        p.setPen(QPen(QColor(T["edge"]), 1))
        p.setBrush(QColor(T["raised"]))
        p.drawEllipse(c, r, r)
        for key, angle in self.ANGLES.items():
            b = self.keys[key]
            state = self._state(b)
            if state:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T[state]))
                p.drawPath(self._wedge(key))
            if b.hasFocus():
                p.setPen(QPen(QColor(T["focus"]), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(self._wedge(key, inset=2))
            mid, s, rad = (r + r_ok) / 2 + 2, px(24), math.radians(angle)
            x, y = c.x() + math.cos(rad) * mid, c.y() - math.sin(rad) * mid
            self.chevrons[key].render(p, QRectF(x - s / 2, y - s / 2, s, s))
        ok = self.keys["KEY_ENTER"]
        fill = {"pressed": "accent_down", "hover": "accent_hover"}.get(self._state(ok) or "", "accent_fill")
        p.setPen(QPen(QColor(T["body"]), 6))  # groove between ring and OK
        p.setBrush(QColor(T[fill]))
        p.drawEllipse(c, r_ok, r_ok)
        if ok.hasFocus():
            p.setPen(QPen(QColor(T["focus"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r_ok - 3, r_ok - 3)
        p.setPen(QColor("#ffffff"))
        font = self.font()
        font.setPointSizeF(font_pt() * 1.25)  # the heading step of the type scale
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(c.x() - r_ok, c.y() - r_ok, 2 * r_ok, 2 * r_ok), Qt.AlignmentFlag.AlignCenter, "OK")


def animation_factor():
    """KDE's animation duration multiplier (System Settings › Animation speed; larger is slower);
    0 means animations are turned off."""
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeglobals"
    try:
        value = QSettings(str(path), QSettings.Format.IniFormat).value("KDE/AnimationDurationFactor", 1.0)
        return max(0.0, float(str(value)))
    except (TypeError, ValueError):
        return 1.0


class BusyBar(QWidget):
    """Thin indeterminate progress bar for slow work (scanning folders, converting photos, loading on the TV).

    It appears only when the work outlasts DELAY_MS, so quick loads don't flicker, and its height is always
    reserved, so nothing moves when it appears. With KDE animations off it shows without sliding.
    """

    DELAY_MS = 400

    def __init__(self):
        super().__init__()
        self.setFixedHeight(px(4))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Loading")
        self.active = False
        self.phase = 0.0
        self.delay = QTimer(self, singleShot=True, interval=self.DELAY_MS)
        self.delay.timeout.connect(self._show)
        self.slide = QVariantAnimation(self)
        self.slide.setStartValue(0.0)
        self.slide.setEndValue(1.0)
        self.slide.setLoopCount(-1)  # until set_busy(False)
        self.slide.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.slide.valueChanged.connect(self._step)

    def set_busy(self, busy):
        if busy and not self.active and not self.delay.isActive():
            self.delay.start()
        elif not busy:
            self.delay.stop()
            self.slide.stop()
            self.active = False
            self.setToolTip("")
            self.update()

    def _show(self):
        self.active = True
        self.setToolTip("Working on it…")
        factor = animation_factor()
        if factor > 0:
            self.slide.setDuration(round(1300 * factor))
            self.slide.start()
        self.update()

    def _step(self, value):
        self.phase = value
        self.update()

    def paintEvent(self, _):
        if not self.active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = self.height() / 2
        p.setBrush(QColor(T["raised"]))
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        w = self.width()
        seg = w * 0.3
        x = (w + seg) * self.phase - seg if self.slide.state() == QVariantAnimation.State.Running else (w - seg) / 2
        p.setClipRect(self.rect())
        p.setBrush(QColor(T["accent"]))
        p.drawRoundedRect(QRectF(x, 0, seg, self.height()), r, r)


class Bridge(QObject):
    message = Signal(str, object)  # emitted from worker threads, delivered on the GUI thread


# Keyboard → TV key. (eventFilter ignores Shift and Keypad, so "+" works on any layout and numpad keys match.)
KEYMAP = {
    Qt.Key.Key_Up: "KEY_UP", Qt.Key.Key_Down: "KEY_DOWN", Qt.Key.Key_Left: "KEY_LEFT", Qt.Key.Key_Right: "KEY_RIGHT",
    Qt.Key.Key_Return: "KEY_ENTER", Qt.Key.Key_Enter: "KEY_ENTER", Qt.Key.Key_Escape: "KEY_RETURN",
    Qt.Key.Key_Backspace: "KEY_RETURN", Qt.Key.Key_H: "KEY_HOME", Qt.Key.Key_Plus: "KEY_VOLUP", Qt.Key.Key_Equal: "KEY_VOLUP",
    Qt.Key.Key_Minus: "KEY_VOLDOWN", Qt.Key.Key_M: "KEY_MUTE", Qt.Key.Key_PageUp: "KEY_CHUP", Qt.Key.Key_PageDown: "KEY_CHDOWN",
    Qt.Key.Key_Space: "KEY_PAUSE", Qt.Key.Key_P: "KEY_PLAY", Qt.Key.Key_BracketLeft: "KEY_REWIND",
    Qt.Key.Key_BracketRight: "KEY_FF", Qt.Key.Key_S: "KEY_SOURCE", Qt.Key.Key_I: "KEY_INFO",
}
APPS_WHEN_ON = "Apps appear when the TV is on"
# Events that just set a fixed status line: (text, tone).
STATUS_EVENTS = {
    "pairing": ("Press Allow on the TV…", "warn"),
    "disconnected": ("Disconnected · reconnects on next key", "off"),
    "off_done": ("TV turned off · press power to turn it on", "off"),
    "off": ("TV is off or unreachable · press power", "off"),
}
SHORTCUT_HELP = [
    ("Arrow keys", "Move"), ("Enter", "OK"), ("Esc or Backspace", "Back"), ("H", "Home"),
    ("+ / −", "Volume"), ("M", "Mute"), ("Page Up / Down", "Channel"), ("Space / P", "Pause / Play"),
    ("[ / ]", "Rewind / Fast-forward"), ("S", "Source"), ("I", "Info"), ("T", "Type text"),
    ("Tab", "Move between on-screen keys (Space or Enter presses, Esc leaves)"), ("F11", "Full screen"),
    ("?", "This list"),
]


class RemoteWindow(QWidget):
    def __init__(self, cfg, open_cast_on_start=False):
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle("TV Remote")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # clicking the background leaves the text box
        self.cfg = cfg
        self.turned_off = False  # set once the TV confirmed it went off, until it connects again
        self.bridge = Bridge()
        self.bridge.message.connect(self.on_event)
        self.tv = core.TV(cfg, self.post)
        self.work, self.cast_work = queue.Queue(), queue.Queue()  # casting never delays remote keys
        for q, error_event in ((self.work, "error"), (self.cast_work, "cast_error")):
            threading.Thread(target=self._worker, args=(q, error_event), daemon=True).start()
        self.caster = None
        self.cast_window = None
        self.apps_dialog = None
        self.favorite_popup = None
        self.key_buttons = {}

        body = frame("body")
        v = QVBoxLayout(body)
        v.setContentsMargins(px(20), px(20), px(20), px(14))
        v.setSpacing(px(12))
        v.addLayout(self._top_row())
        v.addLayout(self._cast_row())
        self.dpad = DPad(self.key_button)
        v.addWidget(self.dpad, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addLayout(self._nav_row())
        v.addLayout(self._volume_row())
        v.addLayout(self._media_row())
        self._apps_section(v)
        v.addLayout(self._text_row())
        help_btn = button("Keyboard shortcuts", "circle-help", tip="Keyboard shortcuts (?)", tone="ghost",
                          on_click=self.show_shortcuts, icon_size=16, icon_colour=T["muted"])
        v.addLayout(hbox(None, help_btn, None))
        place_body(self, body, px(10))

        self.progress_pending = False
        self.poll = QTimer(self, interval=1000)
        self.poll.timeout.connect(self.poll_progress)
        self.poll.start()
        self.run(self.startup, "Couldn't connect")
        if open_cast_on_start:
            QTimer.singleShot(300, self.open_cast)

    # -- layout, top to bottom

    def _top_row(self):
        """Power · status display · source, each key with its printed label."""
        self.power_btn = button(icon_name="power", tip="Power (wakes the TV when it's off)", tone="danger",
                                round=44, on_click=self.power, icon_colour="#ffffff")
        display = frame("display")
        display.setFixedHeight(px(44))
        dh = QHBoxLayout(display)
        dh.setContentsMargins(12, 0, 12, 0)
        dh.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_text = label("Connecting…", name="statusText")
        self.status_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        dh.addWidget(self.status_dot)
        dh.addWidget(self.status_text, 1)
        self.set_status("Connecting…", "warn")
        source = self.key_button("KEY_SOURCE", icon_name="import", tip="Source / input (S)", round=44)
        top = QHBoxLayout()
        top.setSpacing(px(10))
        top.addWidget(captioned(self.power_btn, "Power"), 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(display, 1, Qt.AlignmentFlag.AlignTop)
        top.addWidget(captioned(source, "Source"), 0, Qt.AlignmentFlag.AlignTop)
        return top

    def _cast_row(self):
        mirror = button("Mirror screen", "screen-share", tip="Mirror this screen to the TV (Miracast)",
                        tone="outline", min_size=(0, 40), expand=True, on_click=lambda: self.run(self.mirror, "Couldn't start mirroring"))
        cast_btn = button("TV Cast", "cast", tip="Cast photos, videos or music to the TV", tone="outline",
                          min_size=(0, 40), expand=True, on_click=self.open_cast)
        return hbox(mirror, cast_btn)

    def _nav_row(self):
        """Back · home · menu."""
        nav = [captioned(self.key_button(k, icon_name=i, tip=t, round=48), c)
               for k, i, t, c in (("KEY_RETURN", "undo-2", "Back (Esc)", "Back"),
                                  ("KEY_HOME", "house", "Home (H)", "Home"),
                                  ("KEY_MENU", "menu", "Menu / settings", "Menu"))]
        return hbox(None, *nav, None, spacing=px(26))

    def _volume_row(self):
        """VOL rocker · mute/info/exit · CH rocker."""
        mid = QVBoxLayout()
        mid.setSpacing(px(6))
        mid.addWidget(captioned(self.key_button("KEY_MUTE", icon_name="volume-x", tip="Mute (M)", round=40),
                                "Mute"), 0, Qt.AlignmentFlag.AlignHCenter)
        for k, text, tip in (("KEY_INFO", "Info", "Info (I)"), ("KEY_EXIT", "Exit", "Exit the current app/menu")):
            mid.addWidget(self.key_button(k, text, tip=tip, min_size=(64, 30)), 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.rocker("VOL", ("KEY_VOLUP", "plus", "Volume up (+)"),
                                  ("KEY_VOLDOWN", "minus", "Volume down (−)")))
        row.addSpacing(px(18))
        row.addLayout(mid)
        row.addSpacing(px(18))
        row.addWidget(self.rocker("CH", ("KEY_CHUP", "chevron-up", "Channel up (Page Up)"),
                                  ("KEY_CHDOWN", "chevron-down", "Channel down (Page Down)")))
        row.addStretch(1)
        return row

    def _media_row(self):
        media = [self.key_button(k, icon_name=i, tip=t, round=44)
                 for k, i, t in (("KEY_REWIND", "rewind", "Rewind ([)"), ("KEY_PLAY", "play", "Play (P)"),
                                 ("KEY_PAUSE", "pause", "Pause (Space)"),
                                 ("KEY_FF", "fast-forward", "Fast-forward (])"))]
        return hbox(None, *media, None, spacing=px(12))

    def _apps_section(self, v):
        """App shortcuts, like the brand keys at the bottom of a remote."""
        self.apps_grid = QGridLayout()
        self.apps_grid.setSpacing(8)
        v.addSpacing(2)
        v.addWidget(label("APPS", "section"))
        v.addLayout(self.apps_grid)
        self.build_apps()

    def _text_row(self):
        """Text for TV search boxes."""
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Type into a TV search box…")
        self.entry.setAccessibleName("Text to type on the TV")
        self.entry.setToolTip("Open a search box on the TV, type here and press Enter (T)")
        self.entry.addAction(icon("keyboard", T["muted"], px(18)), QLineEdit.ActionPosition.LeadingPosition)
        send = button(icon_name="send-horizontal", tip="Send text to the TV", tone="accent", round=40,
                      on_click=self.send_text, icon_colour="#ffffff", icon_size=18)
        return hbox(self.entry, send)

    # -- building blocks

    def key_button(self, key, text="", **kw):
        b = button(text, on_click=lambda: self.send(key), **kw)
        self.key_buttons[key] = b
        return b

    def rocker(self, title, up, down):
        box = frame("rocker")
        w = px(60)
        box.setFixedWidth(w)
        box.setStyleSheet(f"QFrame#rocker {{ border-radius: {w // 2}px; }}")
        col = QVBoxLayout(box)
        col.setContentsMargins(px(6), px(6), px(6), px(6))
        col.setSpacing(4)
        (k_up, i_up, t_up), (k_down, i_down, t_down) = up, down
        col.addWidget(self.key_button(k_up, icon_name=i_up, tip=t_up, round=46, repeat=True))
        name = label(title, name="rockerLabel")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(name)
        col.addWidget(self.key_button(k_down, icon_name=i_down, tip=t_down, round=46, repeat=True))
        return box

    def build_apps(self, note=None):
        """App buttons from the config; note says why there are none yet (default: still loading)."""
        while self.apps_grid.count():
            item = self.apps_grid.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        apps = self.cfg["apps"]
        favs = [(k, a) for k in self.cfg["favorites"] if (a := core.installed_app(self.cfg, k))]
        for i, (key, app) in enumerate(favs):
            b = button(app["name"], tip=f"Open {app['name']} on the TV (right-click to move or remove it)",
                       min_size=(0, 36), expand=True, on_click=lambda _=False, a=app: self.launch(a))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, k=key, b=b: self.favorite_menu(k, b, pos))
            self.apps_grid.addWidget(b, i // 3, i % 3)
        self.apps_note = None if apps else note
        more = button("All apps" if apps else note or "Loading…", "layout-grid", tip="More apps, and Edit apps…",
                      tone="outline", icon_size=16, min_size=(0, 36), expand=True)
        menu = QMenu(more)
        others = (core.installed_app(self.cfg, k) for k in self.cfg["menu_apps"])
        for a in sorted((a for a in others if a), key=lambda a: a["name"].lower()):
            menu.addAction(a["name"], lambda a=a: self.launch(a))
        if not menu.isEmpty():
            menu.addSeparator()
        menu.addAction("Edit apps…", self.edit_apps)
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setEnabled(bool(apps))
        n = len(favs)
        self.apps_grid.addWidget(more, n // 3, n % 3, 1, 3 - n % 3)  # fills the rest of the last row
        for col in range(3):
            self.apps_grid.setColumnStretch(col, 1)

    def favorite_menu(self, key, b, pos):
        """Right-click (or the Menu key) on an app button: move it among the visible buttons, or remove it."""
        shown = [k for k in self.cfg["favorites"] if core.installed_app(self.cfg, k)]
        i = shown.index(key)
        menu = QMenu(b)
        left = menu.addAction("Move left", lambda: self.move_favorite(key, shown[i - 1]))
        right = menu.addAction("Move right", lambda: self.move_favorite(key, shown[(i + 1) % len(shown)]))
        left.setEnabled(i > 0)
        right.setEnabled(i < len(shown) - 1)
        menu.addAction("Remove from remote (keep in All apps)", lambda: self.unfavorite(key))
        menu.addSeparator()
        menu.addAction("Edit apps…", self.edit_apps)
        menu.popup(b.mapToGlobal(pos))
        self.favorite_popup = menu  # for tests

    def move_favorite(self, key, other):
        favs = list(self.cfg["favorites"])
        i, j = favs.index(key), favs.index(other)
        favs[i], favs[j] = favs[j], favs[i]
        self.save_apps(favs, self.cfg["menu_apps"])

    def unfavorite(self, key):
        self.save_apps([k for k in self.cfg["favorites"] if k != key], [key, *self.cfg["menu_apps"]])

    def save_apps(self, favorites, menu, custom=None, found=()):
        """New app lists (and apps added by ID, with what the TV said about them) into the config, then the
        buttons are rebuilt."""
        if custom is not None:
            self.cfg["custom_apps"] = custom
        self.cfg["apps"] = self.cfg["apps"] + [a for a in found if a not in self.cfg["apps"]]
        self.cfg["favorites"], self.cfg["menu_apps"] = list(favorites), list(menu)
        core.clean_app_lists(self.cfg)
        try:
            core.save_config(self.cfg)
        except OSError as e:
            self.set_status(f"Couldn't save the app list: {core.describe_error(e)}", "danger")
        self.build_apps(self.apps_note)

    def edit_apps(self):
        if self.apps_dialog is None:
            self.apps_dialog = AppsDialog(self)
            self.apps_dialog.finished.connect(self._apps_dialog_closed)
            self.check_apps()
        bring_forward(self.apps_dialog)

    def _apps_dialog_closed(self):
        self.apps_dialog = None

    def check_apps(self):
        """Ask the TV which apps it has now; the Edit apps window shows the answer."""
        def go():
            ok = False
            try:
                self.post("apps", self.tv.scan_apps())
                ok = not self.tv.apps_from_cache
            finally:
                self.post("apps_checked", ok)
        self.run(go, "Couldn't check the apps")

    def probe_app(self, app_id):
        """Ask the TV about one app ID (Edit apps › Advanced); the answer, or why there's none, comes
        back as an app_probe event."""
        def go():
            try:
                result = self.tv.probe_app(app_id)
            except (OSError, ValueError, KeyError, TypeError) as e:
                result = core.describe_error(e)
            self.post("app_probe", (app_id, result))
        self.run(go)

    # -- actions

    def _worker(self, q, error_event):
        while True:
            fn, label = q.get()
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - workers must survive; the error is shown, traceback logged
                traceback.print_exc()
                msg = core.describe_error(e)
                self.post(error_event, f"{label}: {msg}" if label else msg)

    def post(self, event, data=None):
        """Deliver an event to on_event on the GUI thread (callable from any thread)."""
        self.bridge.message.emit(event, data)

    def run(self, fn, label=None):
        """Run fn on the remote's worker thread; if it fails, the status line says label: why."""
        self.work.put((fn, label))

    def run_cast(self, fn):
        """Casting work has its own thread, so a slow TV load never delays remote keys. Its errors
        (e.g. "Couldn't play x.mp4: ...") already say what failed."""
        self.cast_work.put((fn, None))

    def send(self, key):
        self.run(lambda: self.tv.key(key), f"Couldn't send {key.removeprefix('KEY_')}")

    def launch(self, app):
        self.run(lambda: self.tv.launch(app), f"Couldn't open {app['name']}")

    def press(self, key, repeat=False):
        """A keyboard shortcut: animate the matching on-screen key, which sends it. Auto-repeats of a
        held key are sent directly: each animateClick() restarts its timer, so they'd collapse into one."""
        if key in self.key_buttons and not repeat:
            self.key_buttons[key].animateClick()
        else:
            self.send(key)

    def power(self):
        def go():
            if self.tv.power_state() == "on":
                self.post("status", "Turning the TV off…")
                if self.tv.turn_off():  # don't claim it until the TV says so
                    self.post("off_done")
                else:
                    self.post("error", core.TURN_OFF_FAILED)
            else:
                self.post("status", "Waking the TV…")
                self.tv.wake()
                if self.tv.wait_until_on():
                    self.tv.connect()
                    self.post("apps", self.tv.scan_apps())
                else:
                    self.post("error", core.WAKE_FAILED.format(host=self.cfg["host"]))
        self.run(go, "Couldn't change the power")

    def startup(self):
        if self.tv.power_state() == "off":
            self.post("off", self.tv.unreachable)
            return
        self.post("apps", self.tv.scan_apps())
        self.tv.connect()

    def mirror(self):
        core.start_mirroring()
        self.post("status", "Pick the TV in Network Displays, then Allow on the TV")

    def send_text(self):
        s = self.entry.text()
        if s:
            self.run(lambda: self.tv.text(s), "Couldn't type the text")
            self.entry.clear()
        self.setFocus()  # back to remote-key mode

    def show_shortcuts(self):
        rows = "".join(f"<tr><td style='padding:3px 18px 3px 0'><b>{k}</b></td><td>{a}</td></tr>"
                       for k, a in SHORTCUT_HELP)
        box = QMessageBox(QMessageBox.Icon.NoIcon, "Keyboard shortcuts", f"<table>{rows}</table>",
                          QMessageBox.StandardButton.Close, self)
        box.exec()

    def set_status(self, text, tone="accent"):
        self.status_text.setText(text)
        self.status_text.setToolTip(text)
        self.status_dot.setStyleSheet(f"background: {T[tone]}; border-radius: 4px;")

    # -- casting

    def get_caster(self):
        """The Caster, created on first use (GUI thread only) with the cast window's slideshow settings."""
        if self.caster is None:
            self.caster = cast.Caster(self.cfg["host"], lambda text: self.post("cast", text),
                                      lambda busy: self.post("cast_busy", busy))
            if self.cast_window:
                self.cast_window.apply_slideshow()
        return self.caster

    def open_cast(self):
        if self.cast_window is None:
            self.cast_window = CastWindow(self)
        bring_forward(self.cast_window)

    def report_progress(self, c):
        """Ask the TV for the playing position (cast thread). Always answers, even on errors, or
        progress_pending would stay set and polling would stop."""
        data = None
        try:
            data = c.progress()
        finally:
            self.post("cast_progress", data)

    def poll_progress(self):
        c = self.caster
        if c and c.current and not self.progress_pending:
            self.progress_pending = True
            self.run_cast(lambda: self.report_progress(c))

    # -- events

    def on_event(self, event, data):
        if event == "connected":
            self.turned_off = False
            self.set_status(f"Connected · {self.cfg['host']}", "ok")
            if self.apps_note == APPS_WHEN_ON:  # never scanned while the TV was on (e.g. turned on by hand)
                self.run(lambda: self.cfg["apps"] or self.post("apps", self.tv.scan_apps()), "Couldn't load the apps")
        elif event == "disconnected" and self.turned_off:
            pass  # the TV drops the connection as it goes to standby; keep "TV turned off"
        elif event in STATUS_EVENTS:
            self.turned_off = event == "off_done"
            self.set_status(*STATUS_EVENTS[event])
            if event == "off" and data:
                self.status_text.setToolTip(f"No answer from {self.cfg['host']}: {data}")
            if event == "off" and not self.cfg["apps"]:
                self.build_apps(APPS_WHEN_ON)
        elif event == "apps":
            self.build_apps(None if data else "No apps found")
        elif event == "apps_checked":
            if self.apps_dialog:
                self.apps_dialog.checked_tv(data)
        elif event == "app_probe":
            if self.apps_dialog and self.apps_dialog.add_dialog:
                self.apps_dialog.add_dialog.probe_done(*data)
        elif event == "status":
            self.set_status(data)
        elif event == "error":
            self.set_status(data, "danger")
        elif event == "raise":  # launched again (menu, Homepage link): bring this window forward
            cmd, token = data
            if token:
                os.environ["XDG_ACTIVATION_TOKEN"] = token  # lets KWin give us focus on Wayland
            bring_forward(self)
            if cmd == "cast":
                self.open_cast()
        elif event == "cast_error":
            if not (self.cast_window and self.cast_window.isVisible()):
                self.set_status(data, "danger")  # casting goes on with its window closed: show it here
            if self.cast_window:
                self.cast_window.on_event("cast", data)
        elif event in ("cast", "cast_progress", "cast_busy"):
            if event == "cast_progress":
                self.progress_pending = False
            if self.cast_window:
                self.cast_window.on_event(event, data)

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.KeyPress or not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        k, mods = e.key(), e.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        if k == Qt.Key.Key_F11 and not mods:
            toggle_full_screen(self)
            return True
        if self.entry.hasFocus():
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Handled here, not via returnPressed: QLineEdit passes Enter on to the window, which
                # (focus having left the box) would also send OK to the TV.
                self.send_text()
                return True
            if k == Qt.Key.Key_Escape:
                self.setFocus()
                return True
            return False
        if mods:  # leave Ctrl/Alt combinations to the system
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, QToolButton):  # reached with Tab: Space/Enter press it, Esc leaves Tab mode
            if k == Qt.Key.Key_Space:
                return False
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                focus.animateClick()
                return True
            if k == Qt.Key.Key_Escape:
                self.setFocus()
                return True
        if k == Qt.Key.Key_T:
            self.entry.setFocus()
        elif k == Qt.Key.Key_Question:
            self.show_shortcuts()
        elif k in KEYMAP:
            self.press(KEYMAP[k], repeat=e.isAutoRepeat())
        else:
            return False
        return True

    def showEvent(self, e):
        super().showEvent(e)
        # Qt would focus the first button (Power), and Space would then press it instead of pausing.
        QTimer.singleShot(0, self.setFocus)

    def closeEvent(self, e):
        if self.cast_window:
            self.cast_window.close()
        if self.apps_dialog:
            self.apps_dialog.reject()
        super().closeEvent(e)


KEY_ROLE = Qt.ItemDataRole.UserRole  # list items keep their app key here
INSTALLED = "Installed"
# What the Edit apps windows say about an app the TV doesn't list, by how the last check went.
NOT_INSTALLED = {None: "Checking the TV…", True: "Not on this TV", False: "Couldn't check (is the TV on?)"}


def dialog_body(dialog, title, intro, width):
    """A dialog styled like the remote's windows: title, wrapped intro, then whatever the caller adds."""
    dialog.setObjectName("window")
    dialog.setWindowTitle(title)
    body = frame("body")
    v = QVBoxLayout(body)
    v.setContentsMargins(px(22), px(22), px(22), px(18))
    v.setSpacing(px(12))
    v.addWidget(label(title, name="title"))
    text = label(intro, "muted")
    text.setWordWrap(True)
    v.addWidget(text)
    body.setFixedWidth(px(width))
    outer = QVBoxLayout(dialog)
    outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # grows when Advanced opens
    outer.setContentsMargins(px(10), px(10), px(10), px(10))
    outer.addWidget(body)
    return v


def app_list(name, min_rows):
    lst = QListWidget()
    lst.setAccessibleName(name)
    lst.setMinimumHeight(px(34) * min_rows)
    return lst


class AppsDialog(QDialog):
    """Edit apps: the remote's app buttons (up to MAX_FAVORITES ticked apps, in list order) and what's in All
    apps (the rest). Works on a copy; Save writes it to the config and rebuilds the remote's buttons."""

    def __init__(self, remote):
        super().__init__(remote)
        self.remote = remote
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.custom = [dict(a) for a in remote.cfg["custom_apps"]]
        self.found = []  # apps the TV confirmed while adding by ID, not yet in cfg["apps"]
        self.checked = None  # how the TV check started on opening went: None = still asking, then ok or not
        self.add_dialog = None
        v = dialog_body(self, "Edit apps", f"Tick up to {core.MAX_FAVORITES} apps for the remote's buttons; they "
                        "appear in this order. The rest are in All apps. Apps that aren't on the TV stay hidden "
                        "on the remote until they are.", 380)
        self.list = app_list("Your apps", 9)
        self.list.itemChanged.connect(self.item_changed)
        self.list.currentRowChanged.connect(self.update_buttons)
        v.addWidget(self.list)
        self.note = label("", "caption")
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        self.up = button(icon_name="chevron-up", tip="Move the selected app up", tone="outline", round=36,
                         on_click=lambda: self.move_selected(-1))
        self.down = button(icon_name="chevron-down", tip="Move the selected app down", tone="outline", round=36,
                           on_click=lambda: self.move_selected(1))
        self.remove = button("Remove", tip="Take the selected app off the remote and out of All apps",
                             tone="outline", on_click=self.remove_selected)
        add = button("Add apps…", "plus", tip="Add popular apps, or an app by its ID", tone="outline", icon_size=16,
                     on_click=self.open_add)
        v.addLayout(hbox(self.up, self.down, self.remove, None, add))
        reset = button("Reset to defaults", tip="The default apps, and no apps added by ID", tone="ghost",
                       on_click=self.reset)
        cancel = button("Cancel", tone="outline", on_click=self.reject)
        save = button("Save", tone="accent", on_click=self.save)
        v.addLayout(hbox(reset, None, cancel, save))
        self.fill(remote.cfg["favorites"], remote.cfg["menu_apps"])

    def view_cfg(self):
        """The config as this window has it: the TV's app list plus apps confirmed here, and its custom apps."""
        return {"apps": self.remote.cfg["apps"] + self.found, "custom_apps": self.custom}

    def status(self, key):
        return INSTALLED if core.installed_app(self.view_cfg(), key) else NOT_INSTALLED[self.checked]

    def app_keys(self):
        return [self.list.item(i).data(KEY_ROLE) for i in range(self.list.count())]

    def ticked(self):
        return [self.list.item(i).data(KEY_ROLE) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def fill(self, favorites, menu):
        self.list.clear()
        self.add_keys(favorites, ticked=True)
        self.add_keys(menu)
        self.list.setCurrentRow(0)
        self.update_buttons()

    def add_keys(self, keys, ticked=False):
        self.list.blockSignals(True)
        for key in keys:
            item = QListWidgetItem()
            item.setData(KEY_ROLE, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
            self.list.addItem(item)
            self.label_item(item)
        self.list.blockSignals(False)

    def label_item(self, item):
        key = item.data(KEY_ROLE)
        cfg = self.view_cfg()
        status = self.status(key)
        item.setText(f"{core.app_name(cfg, key)}  ·  {status}")
        item.setToolTip("App ID: " + ", ".join(core.app_ids(key)))
        item.setForeground(QColor(T["fg"] if status == INSTALLED else T["muted"]))

    def refresh(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.label_item(self.list.item(i))
        self.list.blockSignals(False)
        if self.add_dialog:
            self.add_dialog.refresh()

    def checked_tv(self, ok):
        self.checked = ok
        self.refresh()

    def item_changed(self, item):
        if item.checkState() == Qt.CheckState.Checked and len(self.ticked()) > core.MAX_FAVORITES:
            self.list.blockSignals(True)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.blockSignals(False)
            self.note.setText(f"Only {core.MAX_FAVORITES} apps fit on the remote; untick one first.")
        else:
            self.note.setText("")

    def update_buttons(self, *_):
        row = self.list.currentRow()
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < self.list.count() - 1)
        self.remove.setEnabled(row >= 0)

    def move_selected(self, step):
        row = self.list.currentRow()
        if not 0 <= row + step < self.list.count():
            return
        self.list.blockSignals(True)
        item = self.list.takeItem(row)
        self.list.insertItem(row + step, item)
        self.list.blockSignals(False)
        self.list.setCurrentRow(row + step)

    def remove_selected(self):
        row = self.list.currentRow()
        if row < 0:
            return
        key = self.list.takeItem(row).data(KEY_ROLE)
        self.note.setText(f"Removed {core.app_name(self.view_cfg(), key)}.")
        self.custom = [a for a in self.custom if core.CUSTOM + a["appId"] != key]
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        self.update_buttons()

    def reset(self):
        self.custom = []
        self.fill(core.DEFAULT_FAVORITES, core.DEFAULT_MENU)
        self.note.setText("Back to the default apps. Save to keep them.")

    def open_add(self):
        if self.add_dialog is None:
            self.add_dialog = AddAppDialog(self)
            self.add_dialog.finished.connect(self._add_dialog_closed)
        bring_forward(self.add_dialog)

    def _add_dialog_closed(self):
        self.add_dialog = None

    def add_app(self, key, found=None):
        """Append an app (unticked, so it goes in All apps); found is the TV's answer for an app added by ID."""
        if found:
            self.found.append(found)
            if key.startswith(core.CUSTOM):
                self.custom.append(found)
        self.add_keys([key])
        self.list.setCurrentRow(self.list.count() - 1)
        self.update_buttons()

    def save(self):
        ticked = self.ticked()
        self.remote.save_apps(ticked, [k for k in self.app_keys() if k not in ticked], self.custom, self.found)
        self.accept()


class AddAppDialog(QDialog):
    """Add apps: popular apps with publicly known IDs, each checked on the TV (only installed ones can be
    added), and under Advanced, any app by its ID, which the TV must confirm."""

    def __init__(self, apps_dialog):
        super().__init__(apps_dialog)
        self.apps = apps_dialog
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.pending = None  # the ID being asked about
        v = dialog_body(self, "Add apps", "Popular apps with publicly known IDs. Each is checked on your TV, and "
                        "only the ones it has can be added.", 360)
        self.list = app_list("Popular apps", 8)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self.update_add)
        self.list.itemDoubleClicked.connect(lambda _: self.add_selected())
        v.addWidget(self.list)

        self.advanced = QCheckBox("Advanced: add an app by its ID")
        self.advanced.setToolTip("For apps not listed above: the TV is asked whether it has the app with this ID")
        v.addWidget(self.advanced)
        self.id_box = QWidget()
        box = QVBoxLayout(self.id_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(px(6))
        hint = label("Tizen app IDs are mostly 13 digits, e.g. 3201907018807. Community lists (search “Samsung "
                     "Tizen app IDs”) have many more; an app can have a different ID on older TVs, so the TV "
                     "is asked first.", "caption")
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.id_entry = QLineEdit()
        self.id_entry.setPlaceholderText("App ID")
        self.id_entry.setAccessibleName("App ID")
        self.id_entry.returnPressed.connect(self.check_id)
        self.check = button("Check on TV", tone="outline", on_click=self.check_id)
        box.addLayout(hbox(self.id_entry, self.check))
        self.id_note = label("", "caption")
        self.id_note.setWordWrap(True)
        box.addWidget(self.id_note)
        self.id_box.setVisible(False)
        self.advanced.toggled.connect(self.id_box.setVisible)
        v.addWidget(self.id_box)

        cancel = button("Close", tone="outline", on_click=self.reject)
        self.add = button("Add", tone="accent", tip="Add the selected apps to All apps", on_click=self.add_selected)
        v.addLayout(hbox(None, cancel, self.add))
        self.refresh()

    def refresh(self):
        """List the catalog apps not added yet, with what the TV said about each; keeps the selection."""
        selected = {i.data(KEY_ROLE) for i in self.list.selectedItems()}
        self.list.clear()
        added = set(self.apps.app_keys())
        cfg = self.apps.view_cfg()
        # Installed apps first: they're the ones that can be added.
        for app in sorted(core.CATALOG, key=lambda a: self.apps.status(a["key"]) != INSTALLED):
            if app["key"] in added:
                continue
            status = self.apps.status(app["key"])
            item = QListWidgetItem(f"{core.app_name(cfg, app['key'])}  ·  {status}")
            item.setData(KEY_ROLE, app["key"])
            item.setToolTip("App ID: " + ", ".join(app["ids"]))
            if status != INSTALLED:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
            self.list.addItem(item)
            item.setSelected(app["key"] in selected and status == INSTALLED)
        self.update_add()

    def update_add(self):
        self.add.setEnabled(bool(self.list.selectedItems()))

    def add_selected(self):
        keys = [i.data(KEY_ROLE) for i in self.list.selectedItems()]
        for key in keys:
            self.apps.add_app(key)
        if keys:
            self.apps.note.setText(f"Added {len(keys)} app{'s' if len(keys) > 1 else ''} to All apps. "
                                   "Tick one to put it on the remote.")
            self.accept()

    def check_id(self):
        app_id = self.id_entry.text().strip()
        if not core.APP_ID_RE.fullmatch(app_id):
            self.id_note.setText("That isn't an app ID: use letters, digits, dots, dashes or underscores.")
            return
        key = core.key_for_id(app_id)
        if key in self.apps.app_keys():
            self.id_note.setText(f"{core.app_name(self.apps.view_cfg(), key)} is already in your apps.")
            return
        self.pending = app_id
        self.check.setEnabled(False)
        self.id_note.setText(f"Asking the TV about {app_id}…")
        self.apps.remote.probe_app(app_id)

    def probe_done(self, app_id, result):
        if app_id != self.pending:
            return  # an answer to an ID asked about earlier
        self.pending = None
        self.check.setEnabled(True)
        if isinstance(result, dict):
            key = core.key_for_id(app_id)
            self.apps.add_app(key, result)
            self.id_note.setText(f"Added {result['name']} to All apps.")
            self.id_entry.clear()
            self.refresh()
        elif result is None:
            self.id_note.setText(f"The TV doesn't have an app with the ID {app_id}.")
        else:
            self.id_note.setText(f"Couldn't ask the TV: {result}")


class CastWindow(QWidget):
    """Photos/videos/music picker and playback controls for DLNA casting."""

    def __init__(self, remote):
        super().__init__(None, Qt.WindowType.Window)
        self.remote = remote
        self.setObjectName("window")
        self.setWindowTitle("TV Cast")
        self.setAcceptDrops(True)
        body = frame("body")
        v = QVBoxLayout(body)
        v.setContentsMargins(px(22), px(22), px(22), px(18))
        v.setSpacing(px(14))

        v.addWidget(label("TV Cast", name="title"))
        v.addWidget(label("Play photos, videos and music from this PC on the TV.", "muted"))
        v.addWidget(self._picker())
        self._now_playing(v)
        self._transport(v)
        v.addLayout(self._slideshow())
        note = label("Files stream from this PC, so closing the remote stops casting. "
                     "Use the remote's VOL keys for volume. Esc closes this window.", "caption")
        note.setWordWrap(True)
        v.addWidget(note)
        body.setMinimumWidth(px(352))
        place_body(self, body, px(14))

    # -- layout, top to bottom

    def _picker(self):
        """Photos / Videos / Music / Folder tiles; files can also be dropped anywhere on the window."""
        self.pick = frame("card")
        grid = QGridLayout(self.pick)
        grid.setContentsMargins(px(12), px(12), px(12), px(12))
        grid.setSpacing(px(10))
        for i, (text, icon_name, kind) in enumerate((("Photos", "image", "image"), ("Videos", "film", "video"),
                                                     ("Music", "music", "audio"), ("Folder", "folder", "folder"))):
            b = button(text, icon_name, tip=f"Pick {text.lower()} to cast" if kind != "folder" else
                       "Cast everything in a folder", tone="tile", icon_size=26, min_size=(0, 76), expand=True,
                       on_click=lambda _=False, k=kind: self.pick_files(k))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setText(text)  # no leading gap when the icon is above
            grid.addWidget(b, i // 2, i % 2)
        drop = label("or drop files here", "caption")
        drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(drop, 2, 0, 1, 2)
        return self.pick

    def _now_playing(self, v):
        """Title, busy bar, seek bar and clocks."""
        v.addWidget(label("NOW PLAYING", "section"))
        self.now = label("Nothing casting")
        self.now.setWordWrap(True)
        v.addWidget(self.now)
        self.busy = BusyBar()
        v.addWidget(self.busy)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Playback position")
        self.seek.setToolTip("Drag, or use the arrow keys (5 s) and Page Up/Down (30 s)")
        self.seek.setEnabled(False)
        self.seek.setSingleStep(5)
        self.seek.setPageStep(30)
        self.seek.sliderReleased.connect(self.seek_released)
        self.seek.actionTriggered.connect(self.seek_action)
        # Keyboard and groove clicks seek once the keys stop, not on every step.
        self.seek_timer = QTimer(self, singleShot=True, interval=400)
        self.seek_timer.timeout.connect(self.seek_released)
        self.hold_position_until = 0.0  # don't let a stale progress report snap the handle back
        self.elapsed, self.total = label("0:00", "muted"), label("0:00", "muted")
        v.addWidget(self.seek)
        v.addLayout(hbox(self.elapsed, None, self.total))

    def _transport(self, v):
        """Previous · −10s · play/pause · +30s · next, then Stop."""
        self.play_btn = button(icon_name="play", tip="Play / pause", tone="accent", round=56, icon_size=24,
                               icon_colour="#ffffff", on_click=self.caster_do("toggle_pause", refresh=True))
        controls = [
            button(icon_name="skip-back", tip="Previous file", round=44, on_click=self.caster_do("prev")),
            button("−10s", tip="Back 10 seconds", min_size=(54, 36), on_click=self.caster_do("seek_by", -10)),
            self.play_btn,
            button("+30s", tip="Forward 30 seconds", min_size=(54, 36), on_click=self.caster_do("seek_by", 30)),
            button(icon_name="skip-forward", tip="Next file", round=44, on_click=self.caster_do("next")),
        ]
        v.addLayout(hbox(None, *controls, None, spacing=px(10)))
        stop = button("Stop casting", "square", tip="Stop casting", tone="outline", icon_size=14,
                      on_click=self.caster_do("stop"))
        v.addLayout(hbox(None, stop, None))

    def _slideshow(self):
        self.auto = QCheckBox("Photo slideshow")
        self.auto.setChecked(True)
        self.secs = QSpinBox()
        self.secs.setRange(2, 120)
        self.secs.setValue(8)
        self.secs.setPrefix("every ")
        self.secs.setSuffix(" s")
        self.secs.setAccessibleName("Seconds per photo")
        self.auto.toggled.connect(self.apply_slideshow)
        self.secs.valueChanged.connect(self.apply_slideshow)
        return hbox(self.auto, None, self.secs)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:  # usual for a secondary window; casting keeps going
            self.close()
        elif e.key() == Qt.Key.Key_F11:
            toggle_full_screen(self)
        else:
            super().keyPressEvent(e)

    def caster_do(self, method, *args, refresh=False):
        def go():
            c = self.remote.caster
            if c:
                getattr(c, method)(*args)
                if refresh:
                    self.remote.report_progress(c)
        return lambda: self.remote.run_cast(go)

    def pick_files(self, kind):
        if kind == "folder":
            folder = QFileDialog.getExistingDirectory(self, "Cast a folder to the TV", str(Path.home()))
            paths = [folder] if folder else []
        else:
            name, start = {"image": ("Photos", "Pictures"), "video": ("Videos", "Videos"),
                           "audio": ("Music", "Music")}[kind]
            # "x" + e: kind_of uses Path.suffix, and Path(".mp4").suffix is "" (a dotfile).
            exts = [e for e in [*cast.MIME, *cast.CONVERT_TO_JPEG] if cast.kind_of("x" + e) == kind]
            start = Path.home() / start
            paths, _ = QFileDialog.getOpenFileNames(
                self, f"Cast {name.lower()} to the TV", str(start if start.is_dir() else Path.home()),
                f"{name} ({' '.join('*' + e for e in exts)})")
        self.cast_paths(paths)

    def cast_paths(self, paths):
        if paths:
            self.now.setText("Starting…")
            c = self.remote.get_caster()
            self.remote.run_cast(lambda: c.cast(paths))

    def apply_slideshow(self, *_):
        """Copy the slideshow controls to the Caster (also when it's created, so these are the defaults)."""
        self.secs.setEnabled(self.auto.isChecked())
        c = self.remote.caster
        if c:
            c.slideshow = self.auto.isChecked()
            c.slide_seconds = self.secs.value()

    def seek_action(self, action):
        if action != QAbstractSlider.SliderAction.SliderMove:  # drags seek on release instead
            self.seek_timer.start()

    def seek_released(self):
        self.seek_timer.stop()
        target = self.seek.value()
        self.elapsed.setText(fmt_clock(target))
        self.hold_position_until = time.monotonic() + 2
        c = self.remote.caster
        if c:
            self.remote.run_cast(lambda: c.seek_to(target))

    def on_event(self, event, data):
        c = self.remote.caster
        if event == "cast_busy":
            self.busy.set_busy(data)
            return
        if event == "cast":
            self.now.setText(data)
            if not (c and c.current):
                self.show_progress(None)
        elif event == "cast_progress":
            self.show_progress(data)
        playing = bool(c and c.current and not c.paused)
        self.play_btn.setIcon(icon("pause" if playing else "play", "#ffffff", px(24)))

    def show_progress(self, data):
        c = self.remote.caster
        if data and data[1]:
            pos, dur = int(data[0]), int(data[1])
            self.seek.setEnabled(True)
            self.seek.setRange(0, dur)
            user_seeking = (self.seek.isSliderDown() or self.seek_timer.isActive()
                            or time.monotonic() < self.hold_position_until)
            if not user_seeking:
                self.seek.setValue(pos)
                self.elapsed.setText(fmt_clock(pos))
            self.total.setText(fmt_clock(dur))
        else:
            self.seek.setEnabled(False)
            self.seek.setValue(0)
            self.elapsed.setText("Photo" if c and c.current else "0:00")
            self.total.setText("")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.pick.setProperty("drag", "true")
            self.pick.style().polish(self.pick)

    def dragLeaveEvent(self, _):
        self.pick.setProperty("drag", "false")
        self.pick.style().polish(self.pick)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        self.cast_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])


def same_user(conn):
    """Whether the process at the other end of a unix socket runs as us. Abstract sockets have no file
    permissions, so any local user could otherwise talk to (or squat on) the instance socket."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1] == os.getuid()


def raise_existing(msg):
    """True if a remote is already running (it was sent msg: b'raise' or b'cast')."""
    # Our launch's activation token lets the running window take focus on Wayland.
    token = os.environ.get("XDG_ACTIVATION_TOKEN", "").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(INSTANCE_SOCKET)
            if not same_user(s):
                raise InstanceError("Another user's program is holding the TV Remote's instance socket.")
            s.sendall(msg + b"\n" + token)
        return True
    except OSError:
        return False


class InstanceError(RuntimeError):
    """Another remote holds the single-instance socket but never answers."""


def claim_instance(msg):
    """The single-instance listener for this new remote, or None if one is already running (it was sent
    msg). Two launches at the same moment can both find no remote; the one that loses the bind asks
    the winner instead, once it's listening."""
    for attempt in range(3):
        if attempt:
            time.sleep(0.2)
        if raise_existing(msg):
            return None
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(INSTANCE_SOCKET)
        except OSError:
            listener.close()
            continue
        listener.listen()
        return listener
    raise InstanceError("Another TV Remote seems to be starting but doesn't answer; try again in a moment.")


def run(cast_window=False):
    listener = claim_instance(b"cast" if cast_window else b"raise")
    if listener is None:
        return

    app = QApplication([])
    app.setApplicationName("TV Remote")
    app.setDesktopFileName("tv-remote")  # Wayland app_id: KDE matches it to tv-remote.desktop
    app.setWindowIcon(QIcon.fromTheme("tvremote", QIcon(str(HERE / "tv-remote.svg"))))
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())

    window = RemoteWindow(core.load_config(), cast_window)
    app.installEventFilter(window)  # keyboard shortcuts, see RemoteWindow.eventFilter

    def accept_raises():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:  # listener shut down: the window has closed
                return
            with conn:
                if not same_user(conn):
                    continue
                conn.settimeout(1)
                try:
                    msg = conn.recv(4096)
                except OSError:
                    msg = b"raise"
            cmd, _, token = msg.partition(b"\n")
            window.post("raise", (cmd.decode(errors="replace"), token.decode(errors="replace")))

    threading.Thread(target=accept_raises, daemon=True).start()
    window.show()
    app.exec()
    # Stop answering raise_existing() first: the cleanup below can wait on TV timeouts, and a relaunch
    # meanwhile must start a new window, not "raise" this closed one.
    listener.shutdown(socket.SHUT_RDWR)
    listener.close()
    if window.caster:
        if window.caster.current:
            window.caster.stop()  # the file server is going away with this process
        window.caster.close()
    window.tv.close()
