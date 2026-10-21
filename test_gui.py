"""Offscreen tests for the remote (pytest-qt): keyboard shortcuts, d-pad, typed text, the cast window,
busy bar and layout/contrast/scaling rules, plus Caster and TV unit tests.

The TV is stubbed, so nothing is sent over the network, and the config is a temporary copy.
Run:  ~/.local/share/tv-remote/venv/bin/python -I -m pytest -q ~/.local/share/tv-remote/test_gui.py
"""

import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no windows pop up on screen
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QToolButton

import remote_qt as rq
import tv_remote as core

KEYS = [(Qt.Key.Key_Up, "KEY_UP"), (Qt.Key.Key_Down, "KEY_DOWN"), (Qt.Key.Key_Left, "KEY_LEFT"),
        (Qt.Key.Key_Right, "KEY_RIGHT"), (Qt.Key.Key_Return, "KEY_ENTER"), (Qt.Key.Key_Escape, "KEY_RETURN"),
        (Qt.Key.Key_Backspace, "KEY_RETURN"), (Qt.Key.Key_H, "KEY_HOME"), (Qt.Key.Key_Plus, "KEY_VOLUP"),
        (Qt.Key.Key_Minus, "KEY_VOLDOWN"), (Qt.Key.Key_M, "KEY_MUTE"), (Qt.Key.Key_PageUp, "KEY_CHUP"),
        (Qt.Key.Key_PageDown, "KEY_CHDOWN"), (Qt.Key.Key_Space, "KEY_PAUSE"), (Qt.Key.Key_P, "KEY_PLAY"),
        (Qt.Key.Key_BracketLeft, "KEY_REWIND"), (Qt.Key.Key_BracketRight, "KEY_FF"),
        (Qt.Key.Key_S, "KEY_SOURCE"), (Qt.Key.Key_I, "KEY_INFO")]


HOST, MAC = core.DEFAULTS["host"], core.DEFAULTS["mac"]  # the TV the tests pretend to talk to
APPS = [{"appId": "3201907018807", "name": "Netflix"}, {"appId": "111299001912", "name": "YouTube"},
        {"appId": "3201601007625", "name": "Hulu"}, {"appId": "3201606009684", "name": "Spotify"}]


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path):
    """A temporary config with a fixed app list, so tests never read or write the real one."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"token": "test", "apps": APPS}))
    monkeypatch.setattr(core, "CONFIG", path)
    return path


@pytest.fixture
def sent(monkeypatch):
    """Keys and text the remote would have sent to the TV."""
    log = []
    monkeypatch.setattr(core.TV, "power_state", lambda self: "on")
    monkeypatch.setattr(core.TV, "connect", lambda self: self.on_event("connected", None))
    monkeypatch.setattr(core.TV, "scan_apps", lambda self: self.cfg["apps"])
    monkeypatch.setattr(core.TV, "key", lambda self, k: log.append(k))
    monkeypatch.setattr(core.TV, "text", lambda self, s: log.append("TEXT:" + s))
    return log


@pytest.fixture
def remote(qtbot, qapp, sent):
    qapp.setPalette(rq.palette())
    qapp.setStyleSheet(rq.stylesheet())
    w = rq.RemoteWindow(core.load_config())
    qtbot.addWidget(w)
    qapp.installEventFilter(w)
    w.show()
    qtbot.waitExposed(w)
    qtbot.waitUntil(lambda: w.hasFocus())  # showEvent hands focus to the window, not the Power button
    # Startup posts the app list, then "connected"; once that shows, the app buttons won't be rebuilt
    # (deleting any a test holds) while the test runs.
    qtbot.waitUntil(lambda: w.status_text.text().startswith("Connected"))
    sent.clear()  # nothing should have been sent yet anyway
    yield w
    qapp.removeEventFilter(w)


def expect(qtbot, sent, expected):
    """Keys go out on a worker thread (animated clicks after ~100 ms); then make sure nothing extra follows."""
    if expected:
        qtbot.waitUntil(lambda: len(sent) >= len(expected), timeout=3000)
    qtbot.wait(150)
    assert sent == expected


@pytest.mark.parametrize("qt_key, tv_key", KEYS, ids=[k.name for k, _ in KEYS])
def test_shortcut(qtbot, remote, sent, qt_key, tv_key):
    qtbot.keyClick(remote, qt_key)
    expect(qtbot, sent, [tv_key])


def test_space_never_presses_power(qtbot, remote, sent):
    # Power is first in Tab order; if it had focus, Space would turn the TV off.
    qtbot.keyClick(remote, Qt.Key.Key_Space)
    expect(qtbot, sent, ["KEY_PAUSE"])


def test_type_text(qtbot, remote, sent):
    qtbot.keyClick(remote, Qt.Key.Key_T)
    assert remote.entry.hasFocus()
    qtbot.keyClicks(remote.entry, "stranger things")  # h, m, p etc. must type, not send keys
    qtbot.keyClick(remote.entry, Qt.Key.Key_Return)
    expect(qtbot, sent, ["TEXT:stranger things"])  # and Enter must not also send OK
    assert remote.hasFocus()
    qtbot.keyClick(remote, Qt.Key.Key_Up)
    expect(qtbot, sent, ["TEXT:stranger things", "KEY_UP"])


def test_escape_leaves_text_box(qtbot, remote, sent):
    qtbot.keyClick(remote, Qt.Key.Key_T)
    qtbot.keyClick(remote.entry, Qt.Key.Key_Escape)
    assert remote.hasFocus()
    expect(qtbot, sent, [])


@pytest.mark.parametrize("where, tv_key", [("top", "KEY_UP"), ("bottom", "KEY_DOWN"), ("left", "KEY_LEFT"),
                                           ("right", "KEY_RIGHT"), ("centre", "KEY_ENTER"), ("corner", None),
                                           ("between OK and ring", None)])
def test_dpad_click(qtbot, remote, sent, where, tv_key):
    d = remote.dpad
    c, edge = d.width() // 2, d.width() - 14
    _, _, r_ok = d._geometry()
    pos = {"top": QPoint(c, 14), "bottom": QPoint(c, edge), "left": QPoint(14, c), "right": QPoint(edge, c),
           "centre": QPoint(c, c), "corner": QPoint(2, 2),  # outside the ring
           "between OK and ring": QPoint(c, c - round(r_ok) - 2)}[where]  # the groove
    key = d.childAt(pos)  # the arrows and OK are buttons masked to their wedge of the ring
    if tv_key is None:
        assert key is None
        return
    assert key is remote.key_buttons[tv_key]
    qtbot.mouseClick(key, Qt.MouseButton.LeftButton, pos=key.mapFrom(d, pos))
    expect(qtbot, sent, [tv_key])


def test_dpad_keys_reachable_by_tab(qtbot, remote):
    """The ring's keys are in the Tab order, in reading order, with names for screen readers."""
    names, w = [], remote.nextInFocusChain()
    while w is not remote:
        if w.parent() is remote.dpad:
            names.append(w.accessibleName())
        w = w.nextInFocusChain()
    assert names == ["Up", "Left", "OK", "Right", "Down"]
    for b in remote.dpad.keys.values():
        assert b.focusPolicy() & Qt.FocusPolicy.TabFocus


def test_tab_then_space_presses_dpad_key(qtbot, remote, sent):
    remote.dpad.keys["KEY_LEFT"].setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.keyClick(remote.dpad.keys["KEY_LEFT"], Qt.Key.Key_Space)
    expect(qtbot, sent, ["KEY_LEFT"])


def test_keyboard_arrow_animates_ring_key(qtbot, remote, sent):
    qtbot.keyClick(remote, Qt.Key.Key_Up)
    assert remote.dpad.keys["KEY_UP"].isDown()  # pressed look while the click animates
    expect(qtbot, sent, ["KEY_UP"])


def test_round_keys_have_printed_labels(remote):
    from PySide6.QtWidgets import QLabel
    captions = {lab.text() for lab in remote.findChildren(QLabel) if lab.property("role") == "caption"}
    assert {"Power", "Source", "Mute", "Back", "Home", "Menu"} <= captions


def test_home_button(qtbot, remote, sent):
    qtbot.mouseClick(remote.key_buttons["KEY_HOME"], Qt.MouseButton.LeftButton)
    expect(qtbot, sent, ["KEY_HOME"])


def test_cast_window_opens_and_esc_closes_it(qtbot, remote):
    remote.open_cast()
    cw = remote.cast_window
    assert cw is not None
    qtbot.addWidget(cw)
    qtbot.waitExposed(cw)
    qtbot.keyClick(cw, Qt.Key.Key_Escape)
    assert not cw.isVisible()


class FakeCaster:
    current, paused = True, False

    def __init__(self):
        self.seeks = []

    def seek_to(self, seconds):
        self.seeks.append(seconds)

    def progress(self):
        return (30, 600)


def test_seek_bar_works_from_keyboard(qtbot, remote):
    remote.open_cast()
    cw = remote.cast_window
    assert cw is not None
    qtbot.addWidget(cw)
    caster = remote.caster = FakeCaster()
    cw.show_progress((30, 600))
    cw.seek.setFocus()
    qtbot.keyClick(cw.seek, Qt.Key.Key_Right)  # +5 s
    qtbot.keyClick(cw.seek, Qt.Key.Key_PageUp)  # +30 s
    cw.show_progress((31, 600))  # a progress report while keys are still coming must not snap it back
    assert cw.seek.value() == 65
    qtbot.waitUntil(lambda: caster.seeks == [65], timeout=3000)  # one seek once the keys stop
    cw.show_progress((31, 600))  # stale report from before the seek landed
    assert cw.seek.value() == 65


def lum(colour):
    c = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((lum(rq.T.get(a, a)), lum(rq.T.get(b, b))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("fg, bg, need", [
    # text (WCAG 1.4.3)
    ("fg", "raised", 4.5), ("fg", "body", 4.5), ("muted", "body", 4.5), ("muted", "body_top", 4.5),
    ("muted", "inset", 4.5), ("muted", "raised", 4.5), ("#ffffff", "accent_fill", 4.5),
    # key edges, coloured keys and the slider handle (fg) on its groove (edge) (WCAG 1.4.11)
    ("fg", "edge", 4.5),
    ("edge", "body", 3), ("edge", "body_top", 3), ("edge", "inset", 3), ("accent_fill", "body", 3),
    ("danger", "body", 3), ("danger", "body_top", 3), ("accent_fill", "body_top", 3),
    # focus ring against every fill it can surround
    ("focus", "raised", 3), ("focus", "body", 3), ("focus", "body_top", 3), ("focus", "accent_fill", 3),
    ("focus", "danger", 3), ("focus", "hover", 3), ("focus", "inset", 3), ("focus", "pressed", 3),
    ("focus", "accent_hover", 3), ("focus", "accent_down", 3), ("focus", "danger_hover", 3), ("focus", "bg", 3),
])
def test_contrast(fg, bg, need):
    assert contrast(fg, bg) >= need, f"{fg} on {bg}: {contrast(fg, bg):.2f} < {need}"


def test_large_system_font_grows_the_remote(qtbot, qapp, sent):
    """With a bigger KDE font the remote scales up instead of clipping text (WCAG 1.4.4)."""
    from PySide6.QtWidgets import QToolButton
    original = qapp.font()
    try:
        normal = rq.px(100)
        big = qapp.font()
        big.setPointSizeF(15)
        qapp.setFont(big)
        qapp.setStyleSheet(rq.stylesheet())
        assert "13.5pt" in rq.stylesheet() and "18.8pt" in rq.stylesheet()  # caption · heading follow the font
        w = rq.RemoteWindow(core.load_config())
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        assert rq.px(100) == 150 > normal
        assert w.dpad.width() == rq.px(188)
        for b in w.findChildren(QToolButton):
            if b.isVisible() and b.text().strip():
                assert b.width() >= b.sizeHint().width(), f"{b.text()!r} is clipped"
                assert b.height() >= b.sizeHint().height(), f"{b.text()!r} is clipped"
    finally:
        qapp.setFont(original)
        qapp.setStyleSheet(rq.stylesheet())


@pytest.mark.parametrize("mode", ["full screen", "maximized", "resized"])
def test_bigger_window_centres_remote_instead_of_stretching(qtbot, remote, mode):
    """Full screen, maximized or a hand-resized window add space around the remote; keys keep their size."""
    from PySide6.QtWidgets import QFrame, QToolButton
    body = remote.findChild(QFrame, "body")
    keys = {b: b.size() for b in remote.findChildren(QToolButton) if b.isVisible()}
    natural = body.size()
    {"full screen": remote.showFullScreen, "maximized": remote.showMaximized,
     "resized": lambda: remote.resize(1400, 1000)}[mode]()
    # centred (a scroll bar, when a short screen puts the remote in a scroll area, shifts it by half its
    # width; allow its full width plus 2 px rounding)
    slack = 2 + remote.style().pixelMetric(remote.style().PixelMetric.PM_ScrollBarExtent)

    def centred():
        return abs(body.mapTo(remote, body.rect().center()).x() - remote.width() // 2) <= slack

    qtbot.waitUntil(lambda: remote.width() > natural.width() + 200)
    qtbot.waitUntil(centred)  # the window grows first; its layout catches up on a later event
    assert body.size() == natural
    assert {b: b.size() for b in keys} == keys


def test_f11_toggles_full_screen(qtbot, remote):
    qtbot.keyClick(remote, Qt.Key.Key_F11)
    qtbot.waitUntil(remote.isFullScreen)
    qtbot.keyClick(remote, Qt.Key.Key_F11)
    qtbot.waitUntil(lambda: not remote.isFullScreen())


def test_cast_window_full_screen_keeps_its_size(qtbot, remote):
    from PySide6.QtWidgets import QFrame
    remote.open_cast()
    cw = remote.cast_window
    assert cw is not None
    qtbot.addWidget(cw)
    qtbot.waitExposed(cw)
    body = cw.findChild(QFrame, "body")
    natural = body.size()
    qtbot.keyClick(cw, Qt.Key.Key_F11)
    qtbot.waitUntil(cw.isFullScreen)
    qtbot.waitUntil(lambda: cw.width() > natural.width() + 200)
    assert body.size() == natural


@pytest.fixture
def cast_window(qtbot, remote):
    remote.open_cast()
    cw = remote.cast_window
    assert cw is not None
    qtbot.addWidget(cw)
    qtbot.waitExposed(cw)
    return cw


def test_busy_bar_skips_quick_loads(qtbot, remote, cast_window):
    remote.on_event("cast_busy", True)
    qtbot.wait(100)
    remote.on_event("cast_busy", False)  # done before BusyBar.DELAY_MS
    qtbot.wait(rq.BusyBar.DELAY_MS + 100)
    assert not cast_window.busy.active


def test_busy_bar_shows_for_slow_loads(qtbot, remote, cast_window):
    bar = cast_window.busy
    height = bar.height()
    remote.on_event("cast_busy", True)
    assert not bar.active  # not straight away
    qtbot.waitUntil(lambda: bar.active, timeout=1000)
    assert bar.slide.state() == bar.slide.State.Running
    assert bar.height() == height  # its space was reserved, nothing moved
    remote.on_event("cast_busy", False)
    assert not bar.active and bar.slide.state() != bar.slide.State.Running


def test_busy_bar_holds_still_with_animations_off(qtbot, remote, cast_window, monkeypatch):
    monkeypatch.setattr(rq, "animation_factor", lambda: 0.0)
    remote.on_event("cast_busy", True)
    qtbot.waitUntil(lambda: cast_window.busy.active, timeout=1000)
    assert cast_window.busy.slide.state() != cast_window.busy.slide.State.Running


def test_caster_reports_busy_while_loading(tmp_path, monkeypatch):
    import cast
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    busy = []

    class FakeServer:
        def publish(self, path):
            assert busy[-1] is True  # busy while converting/publishing
            return "http://x/a", "image/jpeg", 4

        def close(self):
            pass

        def revoke(self):
            pass

    class FakeTV:
        def __getattr__(self, name):
            return lambda *args: None

    c = cast.Caster("127.0.0.1", on_busy=busy.append)
    monkeypatch.setattr(c, "server", FakeServer())  # stand-ins: no HTTP server, no TV
    monkeypatch.setattr(c, "tv", FakeTV())
    try:
        c.cast([str(photo)])
        assert busy[0] is True and busy[-1] is False
        busy.clear()
        with pytest.raises(ValueError):
            c.cast([str(tmp_path / "missing.mkv")])
        assert busy == [True, False]  # an error still ends the busy state
    finally:
        c.close()


def test_stop_reports_a_tv_that_does_not_answer(monkeypatch):
    """A Stop the TV doesn't answer is reported as unconfirmed, not "Stopped" (it used to swallow every
    error and claim "Stopped" while the TV kept playing)."""
    import cast
    statuses = []

    class DeafTV:
        def stop(self):
            raise OSError("timed out")

    c = cast.Caster("127.0.0.1", on_status=statuses.append)
    monkeypatch.setattr(c, "tv", DeafTV())
    try:
        c.stop()
        assert statuses == ["Stopped here, but the TV didn't confirm (timed out)"]
    finally:
        c.close()


def test_power_state_only_treats_network_errors_as_off(monkeypatch):
    tv = core.TV(dict(core.DEFAULTS))

    def unreachable(*_):
        raise OSError("no route to host")
    monkeypatch.setattr(core.TV, "info", unreachable)
    assert tv.power_state() == "off"
    monkeypatch.setattr(core.TV, "info", lambda *_: {"device": {"PowerState": "standby"}})
    assert tv.power_state() == "standby"
    monkeypatch.setattr(core.TV, "info", lambda *_: {})  # an odd reply is not "off"
    assert tv.power_state() == "on"

    def bug(*_):
        raise TypeError("a programming bug")
    monkeypatch.setattr(core.TV, "info", bug)
    with pytest.raises(TypeError):  # surfaces instead of looking like the TV is off
        tv.power_state()


def test_held_shortcut_repeats(qtbot, remote, sent):
    """Auto-repeat used to restart animateClick()'s timer, so holding + sent one volume step."""
    for repeat in (False, True, True, True):  # what holding the key produces
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Plus, Qt.KeyboardModifier.NoModifier, "+", repeat)
        QApplication.sendEvent(remote, event)
    qtbot.waitUntil(lambda: len(sent) == 4, timeout=3000)
    assert set(sent) == {"KEY_VOLUP"}


def test_progress_polling_survives_a_bad_reply(qtbot, remote):
    """A reply error outside OSError (http.client) left progress_pending set, freezing the seek bar."""
    calls = []

    class FlakyCaster:
        current = "x.mp4"

        def progress(self):
            calls.append(1)
            raise http.client.IncompleteRead(b"")

    remote.caster = FlakyCaster()
    remote.poll_progress()
    qtbot.waitUntil(lambda: not remote.progress_pending, timeout=3000)
    remote.poll_progress()
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)


def caster_with(monkeypatch, tv, tmp_path, names=("a.mp4", "b.mp4")):
    import cast
    files = []
    for n in names:
        (tmp_path / n).write_bytes(b"x")
        files.append(str(tmp_path / n))

    class FakeServer:
        read_error = None

        def publish(self, path):
            return f"http://x/{path.name}", "video/mp4", 1

        def close(self):
            pass

        def revoke(self):
            pass

    statuses = []
    c = cast.Caster("127.0.0.1", on_status=statuses.append)
    monkeypatch.setattr(c, "server", FakeServer())
    monkeypatch.setattr(c, "tv", tv)
    return c, files, statuses


def test_failed_load_clears_the_queue(monkeypatch, tmp_path):
    class RejectingTV:
        def stop(self):
            pass

        def load(self, *args):
            raise RuntimeError("TV rejected SetAVTransportURI: Illegal MIME-type")

    c, files, _ = caster_with(monkeypatch, RejectingTV(), tmp_path)
    try:
        with pytest.raises(RuntimeError, match=r"Couldn't play a\.mp4: TV rejected"):
            c.cast(files)
        assert c.current is None  # not shown as playing, no progress polling
    finally:
        c.close()


def test_watcher_skips_a_file_the_tv_never_plays(monkeypatch, tmp_path):
    import cast
    monkeypatch.setattr(cast, "LOAD_TIMEOUT_S", 0)
    loaded = []

    class TV:
        def stop(self):
            pass

        def load(self, url, *args):
            loaded.append(url)

        def play(self):
            pass

        def state(self):
            return "STOPPED"  # took the file, never started it

    c, files, statuses = caster_with(monkeypatch, TV(), tmp_path)
    try:
        c.cast(files)
        deadline = time.time() + 5
        while c.current is not None and time.time() < deadline:
            time.sleep(0.1)
        assert loaded == ["http://x/a.mp4", "http://x/b.mp4"]
        assert c.current is None
        assert any("couldn't play a.mp4; skipped" in s for s in statuses)
        assert statuses[-1] == "The TV couldn't play b.mp4"
    finally:
        c.close()


def test_scan_keeps_cached_apps_whose_probe_timed_out(monkeypatch, config):
    tv = core.TV(core.load_config())
    netflix, youtube = APPS[0]["appId"], APPS[1]["appId"]

    class Reply:
        def __init__(self, name):
            self.body = json.dumps({"name": name}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return self.body

    def urlopen(url, timeout):
        app_id = url.rsplit("/", 1)[1]
        if app_id == netflix:
            return Reply("Netflix")
        if app_id == youtube:
            raise TimeoutError("timed out")  # TV still waking up
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    apps = tv.scan_apps()
    assert [a["name"] for a in apps] == ["Netflix", "YouTube"]  # Hulu, Spotify: 404 = uninstalled
    assert json.loads(config.read_text())["apps"] == apps


def test_scan_of_unreachable_tv_keeps_cache(monkeypatch, config):
    tv = core.TV(core.load_config())

    def urlopen(url, timeout):
        raise OSError("no route to host")

    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    assert tv.scan_apps() == APPS
    assert json.loads(config.read_text())["apps"] == APPS


def test_broken_config_is_reported(config):
    config.write_text("{not json")
    with pytest.raises(core.ConfigError, match="isn't valid JSON"):
        core.load_config()
    config.write_text("[]")
    with pytest.raises(core.ConfigError):
        core.load_config()


@pytest.fixture
def media_server():
    import cast
    s = cast.MediaServer("127.0.0.1")  # "the TV" is this machine, so the test client is allowed
    yield s
    s.close()


def fetch(server, url, headers=None):
    path = url.split(str(server.httpd.server_port), 1)[1]
    conn = http.client.HTTPConnection(server.ip, server.httpd.server_port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read(), r.getheader("Content-Range")
    finally:
        conn.close()


def test_cast_server_serves_ranges_to_the_tv(media_server, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(bytes(range(100)))
    url, mime, size = media_server.publish(f)
    assert (mime, size) == ("video/mp4", 100)
    assert fetch(media_server, url)[:2] == (200, bytes(range(100)))
    assert fetch(media_server, url, {"Range": "bytes=10-19"}) == (206, bytes(range(10, 20)), "bytes 10-19/100")
    assert fetch(media_server, url, {"Range": "bytes=-10"})[2] == "bytes 90-99/100"
    assert fetch(media_server, url, {"Range": "bytes=100-"})[0] == 416


def test_cast_server_refuses_other_hosts(media_server, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"secret")
    url, _, _ = media_server.publish(f)
    media_server.allowed = {HOST}  # the real TV; this test client is someone else on the LAN
    assert fetch(media_server, url)[0] == 403


def test_revoked_links_stop_working(media_server, tmp_path, monkeypatch):
    photo = tmp_path / "a.webp"
    photo.write_bytes(b"x")

    def fake_jpeg(_src):
        out = media_server.tmp / "conv.jpg"
        out.write_bytes(b"jpeg")
        return out

    monkeypatch.setattr(media_server, "_to_jpeg", fake_jpeg)
    url, mime, _ = media_server.publish(photo)
    assert mime == "image/jpeg" and fetch(media_server, url)[0] == 200
    media_server.revoke()
    assert fetch(media_server, url)[0] == 404
    assert not (media_server.tmp / "conv.jpg").exists()  # converted copy deleted


def test_queue_moving_on_revokes_the_previous_link(monkeypatch, tmp_path):
    class TV:
        def __getattr__(self, name):
            return lambda *args: None

    c, files, _ = caster_with(monkeypatch, TV(), tmp_path)
    revoked = []
    monkeypatch.setattr(c.server, "revoke", lambda: revoked.append(c.index))
    try:
        c.cast(files)
        c.next()
        c.stop()
        assert revoked == [0, 1, -1]  # before each file is published, and when the queue ends
    finally:
        c.close()


class FakeSocket:
    """recv_data replays a script: an exception to raise, or an (opcode, data) frame."""

    def __init__(self, script):
        self.script = list(script)
        self.pings = 0
        self.closed = False

    def recv_data(self, control_frame=False):
        assert control_frame
        step = self.script.pop(0) if self.script else core.websocket.WebSocketConnectionClosedException()
        if isinstance(step, Exception):
            raise step
        return step

    def ping(self):
        self.pings += 1

    def shutdown(self):
        self.closed = True


def run_reader(script):
    events = []
    tv = core.TV(core.load_config(), lambda e, d: events.append(e))
    ws = FakeSocket(script)
    tv.ws = ws  # pyright: ignore[reportAttributeAccessIssue] - a stand-in socket
    tv._reader(ws)
    return tv, ws, events


def test_silent_connection_is_dropped_after_a_ping():
    timeout = core.websocket.WebSocketTimeoutException
    tv, ws, events = run_reader([timeout(), timeout(), (core.websocket.ABNF.OPCODE_TEXT, '{"event": "late"}')])
    assert ws.pings == 1 and ws.closed and tv.ws is None
    assert events == ["disconnected"]  # gave up before the late message


def test_pongs_keep_the_connection():
    timeout = core.websocket.WebSocketTimeoutException
    pong, text = core.websocket.ABNF.OPCODE_PONG, core.websocket.ABNF.OPCODE_TEXT
    _, ws, events = run_reader([timeout(), (pong, b""), timeout(), (pong, b""), (text, '{"event": "ms.x"}'),
                                (text, "garbled{"), (text, '["not a dict"]')])
    assert ws.pings == 2
    assert events == ["ms.x", "disconnected"]  # garbled/odd messages skipped; closed at the end of the script


@pytest.mark.parametrize(("states", "expected"), [(["on", "standby"], True), (["on", "off"], True),
                                                  (["on"] * 20, False)])
def test_wait_until_off(monkeypatch, states, expected):
    tv = core.TV(core.load_config())
    replies = iter(states)
    monkeypatch.setattr(core.TV, "power_state", lambda self: next(replies, "on"))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    clock = iter(range(100))
    monkeypatch.setattr(core.time, "time", lambda: next(clock))
    assert tv.wait_until_off() is expected


@pytest.mark.parametrize(("description", "message"), [
    ("Transport is locked", "the TV is busy. Close any menu or prompt on the TV, then try again"),
    ("Illegal MIME-type", "TV rejected Play: Illegal MIME-type"),
])
def test_soap_errors_are_readable(monkeypatch, description, message):
    import cast
    body = f"<s:Fault><detail><UPnPError><errorDescription>{description}</errorDescription></UPnPError></detail></s:Fault>"

    def reject(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", None, io.BytesIO(body.encode()))  # type: ignore[arg-type]
    monkeypatch.setattr(cast.urllib.request, "urlopen", reject)
    with pytest.raises(RuntimeError, match=f"^{re.escape(message)}$"):
        cast.Renderer("192.0.2.1").play()


@pytest.mark.parametrize("error", [urllib.error.URLError(TimeoutError("timed out")),
                                   OSError(113, "No route to host")])
def test_cli_reports_an_unreachable_tv_in_one_line(monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(core.TV, "info", fail)
    monkeypatch.setattr(core.TV, "connect", fail)
    for args in (["info"], ["key", "mute"]):
        with pytest.raises(SystemExit, match=r"^Couldn't reach the TV at .*(timed out|No route to host)"):
            core.cli(args)


@pytest.mark.parametrize(("waits", "keys", "expected"), [
    ([True], 1, True),  # the first press worked
    ([False, True], 2, True),  # the TV dropped the first press
    ([False, False], 2, False),  # never more than one retry
])
def test_turn_off_retries_once(monkeypatch, waits, keys, expected):
    tv = core.TV(core.load_config())
    pressed, replies = [], iter(waits)
    monkeypatch.setattr(core.TV, "key", lambda self, k: pressed.append(k))
    monkeypatch.setattr(core.TV, "wait_until_off", lambda self, timeout=10: next(replies))
    assert tv.turn_off() is expected
    assert pressed == ["KEY_POWER"] * keys


@pytest.mark.parametrize(("off", "status"), [(True, "TV turned off · press power to turn it on"),
                                             (False, core.TURN_OFF_FAILED)])
def test_power_off_is_confirmed(qtbot, remote, sent, monkeypatch, off, status):
    monkeypatch.setattr(core.TV, "wait_until_off", lambda self, timeout=10: off)
    remote.power()
    qtbot.waitUntil(lambda: remote.status_text.text() == status, timeout=3000)
    assert sent == (["KEY_POWER"] if off else ["KEY_POWER", "KEY_POWER"])  # one retry, then give up
    if off:
        remote.on_event("disconnected", None)  # the TV drops the connection as it goes to standby
        assert remote.status_text.text() == status


@pytest.mark.parametrize(("header", "expected"), [
    (None, (0, 99, False)), ("", (0, 99, False)), ("bytes=-", (0, 99, False)), ("items=1-2", (0, 99, False)),
    ("bytes=0-", (0, 99, True)), ("bytes=10-19", (10, 19, True)), ("bytes=-10", (90, 99, True)),
    ("bytes=90-200", (90, 99, True)), ("bytes=-200", (0, 99, True)),
    ("bytes=100-", None), ("bytes=-0", None), ("bytes=50-10", None),
])
def test_byte_range(header, expected):
    import cast
    assert cast.byte_range(header, 100) == expected


def test_seek_asks_the_tv_for_its_position_once(monkeypatch, tmp_path):
    class TV:
        def __init__(self):
            self.position_calls, self.seeks = 0, []

        def __getattr__(self, name):
            return lambda *args: None

        def position(self):
            self.position_calls += 1
            return 100, 600

        def seek(self, seconds):
            self.seeks.append(seconds)

    tv = TV()
    c, files, _ = caster_with(monkeypatch, tv, tmp_path)
    try:
        c.cast(files)
        c.seek_by(30)
        c.seek_to(9999)
        c.seek_by(-500)
        assert tv.seeks == [130, 599, 0]  # clamped to the file
        assert tv.position_calls == 3
    finally:
        c.close()


class ScriptedTV:
    """A renderer whose GetTransportInfo replies follow a script (a state, or an exception to raise)."""

    def __init__(self, states, position=(5, 100)):
        self.states, self.pos, self.loaded = list(states), position, []

    def stop(self):
        pass

    def load(self, url, *args):
        self.loaded.append(url)

    def play(self):
        pass

    def state(self):
        s = self.states.pop(0)
        if isinstance(s, Exception):
            raise s
        return s

    def position(self):
        return self.pos


def watched(monkeypatch, tmp_path, tv, names=("a.mp4", "b.mp4")):
    """A Caster whose watcher only runs when the test calls _tick(), so each poll is deterministic."""
    c, files, statuses = caster_with(monkeypatch, tv, tmp_path, names)
    c._stop.set()  # ends the background watcher thread
    c.cast(files)
    statuses.clear()
    return c, statuses


def tick(c, n):
    for _ in range(n):
        c._tick()


@pytest.mark.parametrize(("states", "position", "status", "next_file"), [
    (["PLAYING", "ERROR"], (5, 100), "The TV hit an error playing a.mp4; skipped · 2/2 · b.mp4", "b.mp4"),
    (["ERROR"], (0, 0), "The TV couldn't play a.mp4; skipped · 2/2 · b.mp4", "b.mp4"),
    (["PLAYING", "STOPPED"], (5, 100), "Stopped on TV", None),  # stopped from the TV's own remote
    (["PLAYING", "STOPPED"], (98, 100), None, "b.mp4"),  # finished: next file
    (["PLAYING", "STOPPED"], (0, 0), None, "b.mp4"),  # no duration known counts as finished
])
def test_watcher_reacts_to_how_a_file_ends(monkeypatch, tmp_path, states, position, status, next_file):
    tv = ScriptedTV(states, position)
    c, statuses = watched(monkeypatch, tmp_path, tv)
    try:
        tick(c, len(states))
        assert (c.current.name if c.current else None) == next_file
        if status:
            assert status in statuses
        assert "Stopped on TV" not in statuses or status == "Stopped on TV"
    finally:
        c.close()


def test_watcher_says_finished_after_the_last_file(monkeypatch, tmp_path):
    c, statuses = watched(monkeypatch, tmp_path, ScriptedTV(["PLAYING", "STOPPED"], (98, 100)), names=("a.mp4",))
    try:
        tick(c, 2)
        assert c.current is None and statuses[-1] == "Finished"
    finally:
        c.close()


def test_watcher_gives_up_on_a_tv_that_stopped_answering(monkeypatch, tmp_path):
    import cast
    tv = ScriptedTV([OSError("No route to host")] * cast.MAX_POLL_FAILURES)
    c, statuses = watched(monkeypatch, tmp_path, tv)
    try:
        tick(c, cast.MAX_POLL_FAILURES - 1)
        assert c.current is not None  # a few missed polls are survivable
        assert statuses[-1] == "Cast error: No route to host"
        tick(c, 1)
        assert c.current is None  # so the CLI's cast exits and the GUI's play button resets
        assert statuses[-1] == "Lost contact with the TV (No route to host); casting stopped"
    finally:
        c.close()


def test_one_good_poll_resets_the_failure_count(monkeypatch, tmp_path):
    import cast
    n = cast.MAX_POLL_FAILURES - 1
    tv = ScriptedTV([OSError("timed out")] * n + ["PLAYING"] + [OSError("timed out")] * n)
    c, _ = watched(monkeypatch, tmp_path, tv)
    try:
        tick(c, 2 * n + 1)
        assert c.current is not None
    finally:
        c.close()


def test_cast_server_does_not_fall_back_to_a_blocked_port(monkeypatch):
    import socket

    import cast
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setattr(cast, "PORT", taken.getsockname()[1])
        with pytest.raises(RuntimeError, match=r"port \d+ is in use \(is another cast running\?\)"):
            cast.MediaServer("127.0.0.1")


def test_cli_cast_reports_nothing_castable_in_one_line(monkeypatch, tmp_path):
    import cast
    closed = []
    monkeypatch.setattr(cast.Caster, "close", lambda self: closed.append(True) or self._stop.set())
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(SystemExit, match=r"^Nothing castable there"):
        core.cli(["cast", str(tmp_path)])
    assert closed == [True]


def test_cli_reports_a_refused_pairing_as_refused(monkeypatch):
    def refuse(self):
        raise core.PairingRefused("The TV refused this remote (ms.channel.unauthorized). Accept the prompt")
    monkeypatch.setattr(core.TV, "connect", refuse)
    with pytest.raises(SystemExit, match=r"^The TV refused this remote"):
        core.cli(["key", "mute"])


def test_config_with_the_token_is_never_readable_by_others(config, monkeypatch):
    tmp = config.with_suffix(".tmp")
    tmp.write_text("{}")  # a leftover from a crash, created with the umask (0644)
    modes, dumps = [], json.dumps

    def spy(*args, **kwargs):  # the token is serialised just before it's written: what mode is the file?
        modes.append(tmp.stat().st_mode & 0o777 if tmp.exists() else None)
        return dumps(*args, **kwargs)
    monkeypatch.setattr(core.json, "dumps", spy)
    core.save_config({"token": "secret"})
    assert modes == [0o600]  # already private while the token goes in, not just after a chmod
    assert config.stat().st_mode & 0o777 == 0o600
    assert not config.with_suffix(".tmp").exists()
    assert json.loads(config.read_text()) == {"token": "secret"}


class FakeWS:
    def __init__(self, replies=(), fail_sends=0):
        self.replies, self.fail_sends = list(replies), fail_sends
        self.sent, self.aborted, self.closed = [], False, False

    def recv(self):
        return json.dumps(self.replies.pop(0))

    def send(self, data):
        if self.fail_sends:
            self.fail_sends -= 1
            raise BrokenPipeError("broken pipe")
        self.sent.append(json.loads(data))

    def settimeout(self, t):
        pass

    def abort(self):
        self.aborted = True

    def close(self, **kwargs):
        self.closed = True


def test_send_reconnects_once_after_a_dead_socket(monkeypatch):
    tv = core.TV(core.load_config())
    dead, fresh = FakeWS(fail_sends=1), FakeWS()
    tv.ws = dead  # pyright: ignore[reportAttributeAccessIssue] - a stand-in socket
    monkeypatch.setattr(core.TV, "connect", lambda self: setattr(self, "ws", fresh) or fresh)
    tv.key("KEY_MUTE")
    assert dead.aborted and dead.sent == []
    assert [m["params"]["DataOfCmd"] for m in fresh.sent] == ["KEY_MUTE"]  # sent exactly once


def test_send_gives_up_after_the_second_failure(monkeypatch):
    tv = core.TV(core.load_config())
    tv.ws = FakeWS(fail_sends=1)  # pyright: ignore[reportAttributeAccessIssue] - a stand-in socket
    monkeypatch.setattr(core.TV, "connect", lambda self: setattr(self, "ws", FakeWS(fail_sends=1)) or self.ws)
    with pytest.raises(BrokenPipeError):
        tv.key("KEY_MUTE")
    assert tv.ws is None


def test_send_does_not_retry_a_failed_connect(monkeypatch):
    tv = core.TV(core.load_config())
    attempts = []

    def fail(self):
        attempts.append(1)
        raise OSError("No route to host")
    monkeypatch.setattr(core.TV, "connect", fail)
    with pytest.raises(OSError):
        tv.key("KEY_MUTE")
    assert attempts == [1]


@pytest.fixture
def no_reader(monkeypatch):
    """connect() without starting the websocket reader thread."""
    monkeypatch.setattr(core.TV, "_reader", lambda self, ws: None)


@pytest.mark.parametrize(("saved", "offered", "stored"), [
    (None, "new-token", "new-token"),  # first pairing: the TV's token is kept
    ("test", "test", "test"),  # same token: config left alone
    ("test", "rotated", "rotated"),
])
def test_connect_saves_the_pairing_token(monkeypatch, config, no_reader, saved, offered, stored):
    config.write_text(json.dumps({"token": saved, "apps": APPS}))
    events, urls = [], []
    ws = FakeWS([{"event": "ms.channel.connect", "data": {"token": offered}}])
    monkeypatch.setattr(core.websocket, "create_connection", lambda url, **kw: urls.append(url) or ws)
    mtime = config.stat().st_mtime_ns
    tv = core.TV(core.load_config(), lambda e, d: events.append(e))
    assert tv.connect() is ws and tv.ws is ws
    assert json.loads(config.read_text())["token"] == stored
    assert ("&token=" in urls[0]) == bool(saved)
    assert events == (["pairing"] if not saved else []) + ["connected"]
    if saved == offered:
        assert config.stat().st_mtime_ns == mtime  # not rewritten


def test_connect_reports_a_refusal_and_keeps_no_socket(monkeypatch, no_reader):
    ws = FakeWS([{"event": "ms.channel.unauthorized"}])
    monkeypatch.setattr(core.websocket, "create_connection", lambda url, **kw: ws)
    events = []
    tv = core.TV(core.load_config(), lambda e, d: events.append(e))
    with pytest.raises(core.PairingRefused, match="Device Connection Manager"):
        tv.connect()
    assert ws.closed and tv.ws is None and "connected" not in events


def test_reconnect_drops_the_old_socket(monkeypatch, no_reader):
    old, new = FakeWS(), FakeWS([{"event": "ms.channel.connect", "data": {}}])
    monkeypatch.setattr(core.websocket, "create_connection", lambda url, **kw: new)
    tv = core.TV(core.load_config())
    tv.ws = old  # pyright: ignore[reportAttributeAccessIssue] - a stand-in socket
    tv.connect()
    assert old.aborted and tv.ws is new


def test_failures_from_a_previous_file_do_not_count(monkeypatch, tmp_path):
    import cast
    n = cast.MAX_POLL_FAILURES - 1
    tv = ScriptedTV([OSError("timed out")] * (2 * n))
    c, _ = watched(monkeypatch, tmp_path, tv)
    try:
        tick(c, n)
        c.next()  # loads fine: the TV is reachable again
        tick(c, n)
        assert c.current is not None and c.current.name == "b.mp4"
    finally:
        c.close()


def http_error(code, reason):
    return urllib.error.HTTPError("http://tv", code, reason, None, None)  # type: ignore[arg-type]


def test_errors_are_described_in_one_line():
    assert core.describe_error(http_error(500, "Internal Server Error")) == "the TV answered HTTP 500 (Internal Server Error)"
    assert core.describe_error(urllib.error.URLError(TimeoutError("timed out"))) == "timed out"
    assert core.describe_error(TimeoutError()) == "TimeoutError"  # never an empty message


@pytest.mark.parametrize(("state", "unreachable", "message"), [
    ("standby", None, "TV is already in standby"),
    ("off", "No route to host", f"TV didn't answer at {HOST} (No route to host); it may already be off"),
])
def test_cli_off_says_why_nothing_happened(monkeypatch, capsys, state, unreachable, message):
    def power_state(self):
        self.unreachable = unreachable
        return state
    monkeypatch.setattr(core.TV, "power_state", power_state)
    monkeypatch.setattr(core.TV, "key", lambda self, k: pytest.fail("no key should be sent"))
    core.cli(["off"])
    assert capsys.readouterr().out.strip() == message


def test_launching_an_uninstalled_app_says_so(monkeypatch):
    def urlopen(req, timeout):
        raise http_error(404, "Not Found")
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    with pytest.raises(SystemExit, match=r"^Netflix isn't installed on the TV any more"):
        core.cli(["launch", "netflix"])


def test_cli_tells_a_refusal_from_an_unreachable_tv(monkeypatch):
    def urlopen(req, timeout):
        raise http_error(503, "Service Unavailable")
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    with pytest.raises(SystemExit, match=r"^The TV at .* refused the request: the TV answered HTTP 503"):
        core.cli(["launch", "netflix"])


def test_cli_apps_says_when_the_list_is_the_saved_one(monkeypatch, capsys):
    def urlopen(url, timeout):
        raise OSError("No route to host")
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    core.cli(["apps"])
    out = capsys.readouterr()
    assert "showing the saved list" in out.err and "Netflix" in out.out


class Sink:
    def __init__(self, fail=None):
        self.data, self.fail = b"", fail

    def write(self, b):
        if self.fail:
            raise self.fail
        self.data += b


def test_stream_reports_why_a_file_could_not_be_served(tmp_path):
    import cast
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"0123456789")
    out = Sink()
    assert cast.stream(out, f, 2, 5) is None and out.data == b"23456"
    assert cast.stream(Sink(BrokenPipeError()), f, 0, 10) is None  # the TV hung up: normal while seeking
    assert cast.stream(Sink(), f, 0, 20) == "clip.mp4 got shorter while it was playing"
    assert (cast.stream(Sink(), tmp_path / "gone.mp4", 0, 1) or "").startswith("couldn't read gone.mp4: No such file")


@pytest.mark.parametrize(("states", "status"), [
    (["PLAYING", "STOPPED"], "Stopped: couldn't read a.mp4: Input/output error"),
    (["PLAYING", "ERROR"], "The TV hit an error playing a.mp4 (couldn't read a.mp4: Input/output error); skipped"),
])
def test_watcher_explains_a_file_this_machine_could_not_serve(monkeypatch, tmp_path, states, status):
    c, statuses = watched(monkeypatch, tmp_path, ScriptedTV(states))
    try:
        c._tick()
        monkeypatch.setattr(c.server, "read_error", "couldn't read a.mp4: Input/output error")
        c._tick()
        assert any(s.startswith(status) for s in statuses), statuses
    finally:
        c.close()


def test_photo_conversion_errors_are_readable(media_server, tmp_path, monkeypatch):
    import cast
    photo = tmp_path / "a.heic"
    photo.write_bytes(b"x")

    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(cast.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match=r"^showing \.heic photos needs ffmpeg: sudo apt install ffmpeg$"):
        media_server.publish(photo)

    def fails(*args, **kwargs):
        return cast.subprocess.CompletedProcess(args, 1, "", "[mjpeg] noise\nInvalid data found when processing input\n")
    monkeypatch.setattr(cast.subprocess, "run", fails)
    with pytest.raises(RuntimeError, match=r"^couldn't convert it to JPEG: Invalid data found when processing input$"):
        media_server.publish(photo)


def test_gui_errors_say_which_action_failed(qtbot, remote, monkeypatch):
    def refuse(self, app):
        raise RuntimeError("Netflix isn't installed on the TV any more")
    monkeypatch.setattr(core.TV, "launch", refuse)
    remote.launch(APPS[0])
    qtbot.waitUntil(lambda: remote.status_text.text().startswith("Couldn't open Netflix: "), timeout=3000)


def test_cast_errors_show_on_the_remote_while_the_cast_window_is_closed(qtbot, remote):
    remote.on_event("cast_error", "Couldn't play a.mp4: port 8199 is in use")
    assert remote.status_text.text() == "Couldn't play a.mp4: port 8199 is in use"


def all_apps_button(w):
    item = w.apps_grid.itemAt(w.apps_grid.count() - 1)  # the last cell of the apps grid
    button = item.widget() if item else None
    assert isinstance(button, QToolButton)
    return button


@pytest.mark.parametrize(("event", "data", "text"), [
    ("off", "No route to host", "Apps appear when the TV is on"),
    ("apps", [], "No apps found"),
])
def test_apps_button_says_why_there_are_no_apps(qtbot, qapp, sent, config, event, data, text):
    config.write_text(json.dumps({"token": "test", "apps": []}))
    w = rq.RemoteWindow(core.load_config())
    qtbot.addWidget(w)
    w.on_event(event, data)
    more = all_apps_button(w)
    assert more.text().strip() == text and not more.isEnabled()


def test_apps_load_once_the_tv_connects_after_being_off(qtbot, qapp, sent, config, monkeypatch):
    config.write_text(json.dumps({"token": "test", "apps": []}))
    w = rq.RemoteWindow(core.load_config())
    qtbot.addWidget(w)
    w.on_event("off", "No route to host")

    def scan(self):
        self.cfg["apps"] = APPS
        return APPS
    monkeypatch.setattr(core.TV, "scan_apps", scan)
    w.on_event("connected", None)  # e.g. turned on with the TV's own remote, then a key press reconnected
    qtbot.waitUntil(lambda: all_apps_button(w).text().strip() == "All apps", timeout=3000)


def test_stream_blames_the_tv_for_socket_errors(tmp_path):
    import cast
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"0123456789")
    assert cast.stream(Sink(TimeoutError("timed out")), f, 0, 10) is None  # not "couldn't read clip.mp4"


def test_connect_fails_fast_but_waits_for_the_allow_prompt(monkeypatch, no_reader):
    timeouts = []

    class WS(FakeWS):
        def settimeout(self, t):
            timeouts.append(t)

    ws = WS([{"event": "ms.channel.connect", "data": {}}])
    monkeypatch.setattr(core.websocket, "create_connection", lambda url, timeout, **kw: timeouts.append(timeout) or ws)
    core.TV(core.load_config()).connect()
    assert timeouts == [8, 35, core.PING_S]  # reach it, its answer (Allow prompt), then the reader's pings


def test_a_bug_while_sending_keeps_the_connection(monkeypatch):
    tv = core.TV(core.load_config())
    ws = FakeWS()
    tv.ws = ws  # pyright: ignore[reportAttributeAccessIssue] - a stand-in socket
    monkeypatch.setattr(core.TV, "connect", lambda self: pytest.fail("no reconnect for a programming error"))
    with pytest.raises(TypeError):
        tv._send({"bad": object()})  # not JSON-serialisable
    assert tv.ws is ws and not ws.aborted


def test_a_token_that_cannot_be_saved_closes_the_socket(monkeypatch, no_reader):
    ws = FakeWS([{"event": "ms.channel.connect", "data": {"token": "new"}}])
    monkeypatch.setattr(core.websocket, "create_connection", lambda url, **kw: ws)

    def full(cfg):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(core, "save_config", full)
    tv = core.TV(core.load_config())
    with pytest.raises(RuntimeError, match=r"^Paired with the TV, but couldn't save its token"):
        tv.connect()
    assert ws.closed and tv.ws is None


@pytest.fixture
def instance_socket(monkeypatch):
    name = f"\0tv-remote-test-{os.getpid()}-{time.monotonic_ns()}"
    monkeypatch.setattr(rq, "INSTANCE_SOCKET", name)
    return name


def test_first_launch_claims_the_instance(instance_socket):
    listener = rq.claim_instance(b"raise")
    assert listener is not None
    listener.close()


def test_second_launch_raises_the_running_remote(instance_socket):
    running = rq.claim_instance(b"raise")
    assert running is not None
    try:
        assert rq.claim_instance(b"cast") is None
        conn, _ = running.accept()
        with conn:
            assert conn.recv(100).startswith(b"cast\n")
    finally:
        running.close()


def test_launch_racing_another_one_gives_a_clear_error(instance_socket, monkeypatch):
    import socket
    monkeypatch.setattr(rq.time, "sleep", lambda s: None)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as other:
        other.bind(instance_socket)  # bound, never listening: the other launch is stuck mid-start
        with pytest.raises(rq.InstanceError, match="doesn't answer"):
            rq.claim_instance(b"raise")


def test_a_photo_that_cannot_be_converted_is_skipped(monkeypatch, tmp_path):
    tv = ScriptedTV([])
    c, files, statuses = caster_with(monkeypatch, tv, tmp_path, names=("a.heic", "b.heic", "c.jpg"))

    def publish(path):
        if path.suffix == ".heic":
            raise RuntimeError("couldn't convert it to JPEG: Invalid data")
        return f"http://x/{path.name}", "image/jpeg", 1
    monkeypatch.setattr(c.server, "publish", publish)
    try:
        c.cast(files)
        assert c.current is not None and c.current.name == "c.jpg" and tv.loaded == ["http://x/c.jpg"]
        assert statuses[-1] == "Skipped a.heic (couldn't convert it to JPEG: Invalid data) and 1 more · 3/3 · c.jpg"
    finally:
        c.close()


def test_the_last_file_failing_to_convert_ends_the_queue(monkeypatch, tmp_path):
    c, files, _ = caster_with(monkeypatch, ScriptedTV([]), tmp_path, names=("a.heic",))

    def publish(path):
        raise RuntimeError("showing .heic photos needs ffmpeg: sudo apt install ffmpeg")
    monkeypatch.setattr(c.server, "publish", publish)
    try:
        with pytest.raises(RuntimeError, match=r"^Couldn't play a\.heic: showing \.heic photos needs ffmpeg"):
            c.cast(files)
        assert c.current is None
    finally:
        c.close()


@pytest.mark.parametrize(("woke", "status"), [(True, "Connected"), (False, "Sent the wake packet")])
def test_power_wakes_a_tv_that_is_off(qtbot, remote, monkeypatch, woke, status):
    calls = []
    monkeypatch.setattr(core.TV, "power_state", lambda self: "standby")
    monkeypatch.setattr(core.TV, "wake", lambda self: calls.append("wake"))
    monkeypatch.setattr(core.TV, "wait_until_on", lambda self, timeout=25: woke)
    monkeypatch.setattr(core.TV, "connect", lambda self: calls.append("connect") or self.on_event("connected", None))
    remote.set_status("Connecting…")  # startup already said "Connected"
    remote.power()
    qtbot.waitUntil(lambda: remote.status_text.text().startswith(status), timeout=3000)
    assert calls == (["wake", "connect"] if woke else ["wake"])
    assert HOST in remote.status_text.text()  # says which address


def test_startup_with_the_tv_unreachable_does_not_connect(qtbot, qapp, sent, monkeypatch):
    def unreachable(self):
        self.unreachable = "No route to host"
        return "off"
    monkeypatch.setattr(core.TV, "power_state", unreachable)
    monkeypatch.setattr(core.TV, "connect", lambda self: pytest.fail("shouldn't connect to an unreachable TV"))
    w = rq.RemoteWindow(core.load_config())
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.status_text.text().startswith("TV is off or unreachable"), timeout=3000)
    assert w.status_text.toolTip() == f"No answer from {HOST}: No route to host"


@pytest.mark.parametrize(("woke", "out"), [(True, "TV is on"), (False, "Sent the wake packet")])
def test_cli_on(monkeypatch, capsys, woke, out):
    monkeypatch.setattr(core.TV, "wake", lambda self: None)
    monkeypatch.setattr(core.TV, "wait_until_on", lambda self, timeout=25: woke)
    core.cli(["on"])
    assert capsys.readouterr().out.startswith(out)


def test_wake_sends_a_magic_packet_to_the_lan_broadcast(monkeypatch):
    sent_to = []

    class Sock:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def setsockopt(self, *args):
            pass

        def sendto(self, data, addr):
            sent_to.append((data, addr))

    monkeypatch.setattr(core.socket, "socket", Sock)
    core.TV(core.load_config()).wake()
    mac = bytes.fromhex(MAC.replace(":", ""))
    assert sent_to == [(b"\xff" * 6 + mac * 16, (HOST.rsplit(".", 1)[0] + ".255", 9))] * 3
    assert len(sent_to[0][0]) == 102


def test_wait_until_on(monkeypatch):
    tv = core.TV(core.load_config())
    states = iter(["off", "standby", "on"])
    monkeypatch.setattr(core.TV, "power_state", lambda self: next(states))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    assert tv.wait_until_on() is True


def test_cast_request_escapes_titles_and_urls(monkeypatch):
    import cast
    requests = []

    class Reply:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def urlopen(req, timeout):
        requests.append(req)
        return Reply(b"<ok/>")
    monkeypatch.setattr(cast.urllib.request, "urlopen", urlopen)
    url = "http://192.0.2.9:8199/tok/Tom%20%26%20Jerry.mp4?a=1&b=2"
    cast.Renderer("192.0.2.1").load(url, "Tom & Jerry <1>", "video/mp4", "video", 42)
    req = requests[0]
    assert req.get_header("Soapaction") == '"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"'
    body = bytes(req.data or b"").decode()
    import xml.etree.ElementTree as ET
    root = ET.fromstring(body)  # well-formed despite the & and <
    args = {el.tag: el.text for el in root.iter() if el.tag in ("CurrentURI", "CurrentURIMetaData")}
    assert args["CurrentURI"] == url
    didl = ET.fromstring(args["CurrentURIMetaData"] or "")  # the DIDL inside is well-formed XML too
    ns = {"d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/", "dc": "http://purl.org/dc/elements/1.1/"}
    assert didl.find("d:item/dc:title", ns).text == "Tom & Jerry <1>"  # type: ignore[union-attr]
    res = didl.find("d:item/d:res", ns)
    assert res is not None and res.text == url
    assert res.get("protocolInfo") == f"http-get:*:video/mp4:{cast.DLNA_FEATURES}" and res.get("size") == "42"


@pytest.mark.parametrize(("xml", "state"), [
    ("<CurrentTransportState>PLAYING</CurrentTransportState><CurrentTransportStatus>OK</CurrentTransportStatus>", "PLAYING"),
    ("<CurrentTransportState>STOPPED</CurrentTransportState><CurrentTransportStatus>ERROR_OCCURRED</CurrentTransportStatus>", "ERROR"),
    ("", ""),
])
def test_renderer_reads_the_transport_state(monkeypatch, xml, state):
    import cast
    r = cast.Renderer("192.0.2.1")
    monkeypatch.setattr(r, "_soap", lambda action, **args: xml)
    assert r.state() == state


def test_renderer_reads_the_position(monkeypatch):
    import cast
    r = cast.Renderer("192.0.2.1")
    monkeypatch.setattr(r, "_soap", lambda action, **a: "<RelTime>0:01:02.500</RelTime><TrackDuration>NOT_IMPLEMENTED</TrackDuration>")
    assert r.position() == (62.5, 0)


@pytest.mark.parametrize(("text", "seconds"), [("1:02:03", 3723), ("0:00:01.25", 1.25), ("NOT_IMPLEMENTED", 0), ("", 0)])
def test_parse_time(text, seconds):
    import cast
    assert cast.parse_time(text) == seconds


def test_cast_server_head_headers_and_deleted_files(media_server, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"0123456789")
    url, _, _ = media_server.publish(f)
    path = url.split(str(media_server.httpd.server_port), 1)[1]
    conn = http.client.HTTPConnection(media_server.ip, media_server.httpd.server_port, timeout=5)
    try:
        conn.request("HEAD", path)
        r = conn.getresponse()
        assert r.status == 200 and r.read() == b"" and r.getheader("Content-Length") == "10"
        assert r.getheader("transferMode.dlna.org") == "Streaming"
        assert r.getheader("contentFeatures.dlna.org") == __import__("cast").DLNA_FEATURES
    finally:
        conn.close()
    f.unlink()
    assert fetch(media_server, url)[0] == 404  # not a 500 or a dead handler


def test_expand_finds_castable_files(tmp_path):
    import cast
    (tmp_path / "Album").mkdir()
    for name in ("Album/b.MP3", "Album/A.flac", "Album/notes.txt", "c.JPG", "readme.md"):
        (tmp_path / name).write_bytes(b"x")
    found = cast.expand([str(tmp_path / "Album"), str(tmp_path / "c.JPG"), str(tmp_path / "readme.md"),
                         str(tmp_path / "missing.mp4")])
    assert [p.name for p in found] == ["A.flac", "b.MP3", "c.JPG"]  # sorted ignoring case; others skipped


def test_find_app_prefers_an_exact_name():
    apps = [{"appId": "1", "name": "Netflix Kids"}, {"appId": "2", "name": "Netflix"}]
    assert core.find_app(apps, "netflix") == apps[1]
    assert core.find_app(apps, "kids") == apps[0]
    assert core.find_app(apps, "hulu") is None
