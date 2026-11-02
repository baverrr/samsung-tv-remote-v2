"""Tests for the Apple TV client (appletv.py), the device choice in tv_remote.py and the bridge driving an
Apple TV. pyatv's connection is replaced by a fake box, so nothing goes on the network; the client's own
event loop thread, key table, app lists, pairing flow and AirPlay renderer run for real.
"""

import asyncio
import http.client
import json
import os
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from pyatv import exceptions as pyatv_errors
from pyatv.const import DeviceState, FeatureState, KeyboardFocusState, PowerState

import appletv
import bridge
import cast
import tv_remote as core

HOST, MAC = "192.0.2.55", "00:00:5e:00:53:55"  # the Samsung TV the tests pretend to have (documentation)
ATV_HOST = "192.0.2.60"  # the Apple TV (documentation address)
ATV = {"identifier": "TEST-ATV-ID", "name": "Den", "host": ATV_HOST,
       "credentials": {"companion": "test-companion", "airplay": "test-airplay"}}
TOKEN = "test-token-0123456789"
BOX_APPS = [("com.netflix.Netflix", "Netflix"), ("com.apple.TVSettings", "Settings"),
            ("com.google.ios.youtube", "YouTube"), ("com.plexapp.plex", "Plex")]


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host": HOST, "mac": MAC, "token": "test", "apps": [], "device": "appletv",
                                "appletv": ATV, "bridge_token": TOKEN}))
    monkeypatch.setattr(core, "CONFIG", path)
    return path


class Recorder:
    """Any async method call is recorded as its name (and arguments)."""

    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        async def call(*args):
            self.log.append(name if not args else (name, *args))
        return call


class FakeBox:
    """The parts of a connected pyatv AppleTV the client uses."""

    def __init__(self):
        self.log = []
        self.remote_control = Recorder(self.log)
        self.apps = SimpleNamespace(app_list=self.app_list, launch_app=self.launch_app)
        self.keyboard = SimpleNamespace(text_focus_state=KeyboardFocusState.Focused, text_set=self.text_set)
        self.power = SimpleNamespace(power_state=PowerState.On, turn_on=self.turn_on, turn_off=self.turn_off)
        self.stream = SimpleNamespace(play_url=self.play_url)
        self.metadata = SimpleNamespace(artwork=self.artwork, app=SimpleNamespace(name="TV"), playing=self.playing)
        self.device_info = SimpleNamespace(model_str="Apple TV 4K (gen 3)", version="27.2")
        self.volume_state = FeatureState.Available  # Unavailable: the box isn't controlling the TV's volume
        self.features = SimpleNamespace(get_feature=lambda name: SimpleNamespace(state=self.volume_state))
        self.now = None
        self.played = None
        self.artwork_calls = 0
        self.closed = False

    async def app_list(self):
        return [SimpleNamespace(identifier=i, name=n) for i, n in BOX_APPS]

    async def launch_app(self, bundle):
        self.log.append(("launch", bundle))

    async def text_set(self, s):
        self.log.append(("text", s))

    async def turn_on(self):
        self.log.append("turn_on")
        self.power.power_state = PowerState.On

    async def turn_off(self):
        self.log.append("turn_off")
        self.power.power_state = PowerState.Off

    async def play_url(self, url):
        self.log.append(("play_url", url))
        self.played = asyncio.Event()
        await self.played.wait()

    async def artwork(self, width=None, height=None):
        self.artwork_calls += 1
        return SimpleNamespace(bytes=b"png")

    async def playing(self):
        return self.now

    def close(self):
        self.closed = True
        return set()


def playing(title="Severance", state=DeviceState.Playing, position=10, total=3000, **kw):
    fields = {"title": title, "artist": None, "album": None, "series_name": None, "season_number": None,
              "episode_number": None, "device_state": state, "position": position, "total_time": total,
              "hash": f"hash-{title}"}
    fields.update(kw)
    return SimpleNamespace(**fields)


@pytest.fixture
def box():
    return FakeBox()


@pytest.fixture
def events():
    return []


@pytest.fixture
def connects():
    return []


@pytest.fixture
def client(monkeypatch, box, events, connects):
    """An AppleTV whose connection is the fake box (connects counts each time it connected), with pyatv's
    AirPlay fix in place."""
    cfg = core.load_config()
    c = appletv.AppleTV(cfg, lambda e, d: events.append((e, d)))

    async def ensure():
        if c.atv is None:
            connects.append(box)
            c.atv = box
        return c.atv
    monkeypatch.setattr(c, "_ensure", ensure)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # power waits
    monkeypatch.setattr(appletv, "can_cast", lambda: True)
    monkeypatch.setattr(appletv, "pin_timing_port", lambda: None)
    yield c
    c.close()


# -- keys


@pytest.mark.parametrize(("name", "method"), [
    ("KEY_ENTER", "select"), ("KEY_RETURN", "menu"), ("KEY_HOME", "home"), ("volup", "volume_up"),
    ("KEY_PLAY", "play_pause"), ("KEY_PAUSE", "play_pause"), ("KEY_FF", "skip_forward"), ("left", "left"),
    ("play_pause", "play_pause"), ("select", "select"), ("KEY_MENU", "control_center"), ("KEY_POWER", "power"), ("power", "power")])
def test_keys_map_onto_the_siri_remote(name, method):
    assert appletv.resolve_key(name) == method


@pytest.mark.parametrize("name", ["KEY_SOURCE", "mute", "KEY_1", "__class__", ""])
def test_keys_the_apple_tv_lacks_are_refused(name):
    with pytest.raises(ValueError, match="no .* key"):
        appletv.resolve_key(name)


def test_a_key_press_reaches_the_box(client, box):
    client.key("KEY_UP")
    client.key("KEY_ENTER")
    assert box.log == ["up", "select"]


def test_power_key_puts_the_box_to_sleep_and_waits_for_it(client, box):
    client.key("KEY_POWER")
    assert box.log == ["turn_off"]


def test_a_dropped_connection_is_made_again_once(client, box, connects):
    calls = []

    async def flaky(atv):
        calls.append(atv)
        if len(calls) == 1:
            raise pyatv_errors.ConnectionLostError("gone")
        return "ok"
    assert client.loop.run(client._do(flaky)) == "ok"
    assert len(connects) == 2 and box.closed


@pytest.mark.parametrize(("error", "kind"), [
    (pyatv_errors.ConnectionFailedError("no route"), ConnectionError),
    (pyatv_errors.AuthenticationError("bad creds"), core.PairingRefused),
    (pyatv_errors.PairingError("timed out"), RuntimeError),
    (pyatv_errors.NotSupportedError("nope"), RuntimeError),
    (pyatv_errors.ProtocolError("odd"), RuntimeError)])
def test_pyatv_errors_become_ones_the_callers_handle(client, error, kind):
    async def fail(atv):
        raise error
    with pytest.raises(kind):
        client.loop.run(client._do(fail))


# -- text, apps, power


def test_text_needs_an_open_text_field(client, box):
    client.text("severance")
    assert box.log == [("text", "severance")]
    box.keyboard.text_focus_state = KeyboardFocusState.Unfocused
    with pytest.raises(RuntimeError, match="open a search box"):
        client.text("x")


def test_apps_come_from_the_box_and_fill_all_apps(client, config):
    apps = client.scan_apps()
    assert [a["name"] for a in apps] == ["Netflix", "Plex", "Settings", "YouTube"]  # sorted by name
    sec = json.loads(config.read_text())["appletv"]
    assert sec["apps"] == apps
    assert sec["favorites"] == appletv.DEFAULT_FAVORITES  # shown when installed (Netflix, YouTube)
    assert set(sec["menu_apps"]) == {"com.apple.TVSettings", "com.plexapp.plex"}  # everything else
    shown = [a for k in client.cfg["favorites"] if (a := core.installed_app(client.cfg, k))]
    assert [a["name"] for a in shown] == ["Netflix", "YouTube"]
    assert core.app_name(client.cfg, "com.plexapp.plex") == "Plex"


def test_apps_are_kept_when_the_box_doesnt_answer(client, monkeypatch, box):
    client.scan_apps()

    async def fail():
        raise pyatv_errors.ConnectionFailedError("asleep")
    monkeypatch.setattr(box.apps, "app_list", fail)
    assert len(client.scan_apps()) == 4 and client.apps_from_cache


def test_launch_opens_the_bundle(client, box):
    client.launch({"appId": "com.plexapp.plex", "name": "Plex"})
    assert box.log == [("launch", "com.plexapp.plex")]


def test_power_state_and_wake(client, box):
    assert client.power_state() == "on"
    box.power.power_state = PowerState.Off
    assert client.power_state() == "standby"  # tvOS sleep, still answering
    client.wake()
    assert client.wait_until_on(timeout=1)
    assert client.turn_off()


def test_an_unreachable_box_counts_as_off(client, monkeypatch):
    async def ensure():
        raise pyatv_errors.ConnectionFailedError("no route to host")
    monkeypatch.setattr(client, "_ensure", ensure)
    assert client.power_state() == "off"
    assert "no route" in client.unreachable


# -- now playing


def test_now_playing_is_reported_with_artwork_once_per_item(client, box, events):
    client.atv = box
    client.loop.loop.call_soon_threadsafe(client._playing, playing(series_name="Severance", season_number=2,
                                                                    episode_number=3, title="Who Is Alive?"))
    client.loop.loop.call_soon_threadsafe(client._playing, playing(series_name="Severance", season_number=2,
                                                                    episode_number=3, title="Who Is Alive?",
                                                                    state=DeviceState.Paused))
    deadline = time.time() + 3
    # Both updates and the artwork: the two updates are queued from this thread, so the artwork
    # (fetched after the first) can arrive before the second update.
    while time.time() < deadline and not (any(e == "artwork" for e, _ in events)
                                          and sum(e == "now_playing" for e, _ in events) >= 2):
        threading.Event().wait(0.01)  # time.sleep is stubbed out
    infos = [d for e, d in events if e == "now_playing"]
    assert infos[0]["subtitle"] == "Severance · S2 E3" and infos[0]["state"] == "playing"
    assert infos[1]["state"] == "paused" and infos[0]["app"] == "TV"
    assert ("artwork", b"png") in events and box.artwork_calls == 1


def test_nothing_playing_clears_the_artwork(client, box, events):
    client.atv = box
    client._artwork_hash = "old"
    client._playing(playing(title="", state=DeviceState.Idle))
    assert ("artwork", None) in events


def test_seek_sets_the_position(client, box):
    client.seek(95.7)
    assert box.log == [("set_position", 95)]


# -- AirPlay casting


def test_airplay_renderer_plays_the_cast_url_and_reports_states(client, box):
    r = client.renderer()
    assert r.state() == "STOPPED"
    client.playing = {"state": "playing"}  # another app was playing before the cast
    r.load("http://192.0.2.1:8199/tok/a.mp4", "a", "video/mp4", "video", 1)
    r.play()
    deadline = time.time() + 3
    while box.played is None and time.time() < deadline:
        threading.Event().wait(0.01)
    assert ("play_url", "http://192.0.2.1:8199/tok/a.mp4") in box.log
    assert r.state() == "TRANSITIONING"  # until now playing says it plays
    client.playing = {"state": "playing"}
    assert r.state() == "PLAYING"
    client.playing = {"state": "paused"}
    assert r.state() == "PAUSED_PLAYBACK"
    r.play()  # resume: a remote command, not a new stream
    assert box.log[-1] == "play"
    client.playing, client.playing_at = {"state": "paused", "position": 42, "total": 100}, time.monotonic()
    assert r.position() == (42, 100)  # from what the box pushed: no request on every poll
    client.playing = {"state": "playing", "position": 42, "total": 100}
    client.playing_at = time.monotonic() - 5
    assert 47 <= r.position()[0] < 48 and r.position()[1] == 100  # the clock runs between its reports
    assert box.played is not None
    client.loop.loop.call_soon_threadsafe(box.played.set)  # the video ends
    deadline = time.time() + 3
    while r.state() != "STOPPED" and time.time() < deadline:
        threading.Event().wait(0.01)
    assert r.state() == "STOPPED"


def test_stopping_a_cast_ends_the_stream_even_if_the_box_doesnt_answer(client, box, monkeypatch):
    r = client.renderer()
    r.load("http://192.0.2.1:8199/tok/a.mp4", "a", "video/mp4", "video", 1)
    r.play()
    task = r.task
    assert task is not None

    def silent(method):
        raise RuntimeError("no answer")
    monkeypatch.setattr(r, "_remote", silent)
    with pytest.raises(RuntimeError):
        r.stop()
    deadline = time.time() + 3
    while not task.done() and time.time() < deadline:
        threading.Event().wait(0.01)
    assert task.cancelled() and r.state() == "STOPPED"


def test_a_failed_airplay_stream_reports_an_error(client, box, monkeypatch):
    async def refuse(url):
        raise pyatv_errors.PlaybackError("can't decode")
    monkeypatch.setattr(box.stream, "play_url", refuse)
    r = client.renderer()
    r.load("http://192.0.2.1:8199/tok/a.mkv", "a", "video/x-mkv", "video", 1)
    r.play()
    deadline = time.time() + 3
    while r.state() != "ERROR" and time.time() < deadline:
        threading.Event().wait(0.01)
    assert r.state() == "ERROR"


def test_caster_leaves_photos_out_for_airplay(client, tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    statuses = []
    played = []

    class Renderer:
        kinds = appletv.AirPlayRenderer.kinds

        def __getattr__(self, name):
            return lambda *a: played.append(name)

    c = cast.Caster("127.0.0.1", statuses.append, renderer=Renderer())
    monkeypatch.setattr(c, "server", SimpleNamespace(publish=lambda p: ("http://x/" + p.name, "video/mp4", 1),
                                                     revoke=lambda: None, close=lambda: None))
    try:
        c.cast([str(tmp_path)])
        assert [p.name for p in c.queue] == ["b.mp4"]
        assert any("Left out 1 photos" in s for s in statuses)
        with pytest.raises(ValueError, match="can't show photos"):
            c.cast([str(tmp_path / "a.jpg")])
    finally:
        c.close()


# -- config and device choice


def test_an_old_samsung_config_loads_unchanged(config):
    config.write_text(json.dumps({"host": HOST, "mac": MAC, "token": "test", "apps": []}))
    cfg = core.load_config()
    assert cfg["device"] == "samsung" and "appletv" not in cfg
    assert core.pick_device(cfg) == "samsung"
    assert isinstance(core.make_client(cfg, "samsung"), core.TV)


def test_the_device_used_last_is_picked():
    cfg = core.load_config()
    assert core.set_up_devices(cfg) == ["samsung", "appletv"]
    assert core.pick_device(cfg) == "appletv"
    assert core.pick_device(cfg, "samsung") == "samsung"


def test_an_apple_tv_that_isnt_paired_falls_back_to_the_samsung(config):
    config.write_text(json.dumps({"host": HOST, "device": "appletv", "appletv": {"identifier": "x"}}))
    cfg = core.load_config()
    assert core.pick_device(cfg) == "samsung"
    with pytest.raises(core.ConfigError, match="No Apple TV is set up"):
        core.pick_device(cfg, "appletv")
    with pytest.raises(core.ConfigError, match="Unknown device"):
        core.pick_device(cfg, "roku")


def test_only_an_apple_tv_is_enough(config):
    config.write_text(json.dumps({"appletv": ATV}))
    cfg = core.load_config()
    assert core.pick_device(cfg) == "appletv"


@pytest.mark.parametrize("junk", [None, "x", [], {"credentials": "x", "apps": "y", "favorites": 3}])
def test_a_broken_apple_tv_section_is_made_usable(junk):
    cfg = {"appletv": junk}
    sec = appletv.section(cfg)
    assert sec["credentials"] == {} and sec["apps"] == [] and sec["menu_apps"] == []
    assert sec["favorites"] == appletv.DEFAULT_FAVORITES
    assert not appletv.is_set_up(cfg)


def test_cli_device_flag(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(appletv.AppleTV, "key", lambda self, k: sent.append(("atv", k)))
    monkeypatch.setattr(core.TV, "key", lambda self, k: sent.append(("samsung", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    core.cli(["key", "select"])
    core.cli(["--device", "samsung", "key", "mute"])
    core.cli(["--device=appletv", "key", "volup"])
    assert sent == [("atv", "select"), ("samsung", "KEY_MUTE"), ("atv", "volume_up")]


def test_cli_mirror_is_samsung_only(monkeypatch):
    with pytest.raises(SystemExit, match="only for Samsung"):
        core.cli(["mirror"])


# -- pairing


class FakeHandler:
    def __init__(self, ok=True):
        self.ok = ok
        self.name = None  # what this remote is called on the TV
        self.pins = []
        self.closed = False
        self.service = SimpleNamespace(credentials="new-creds")

    async def begin(self):
        pass

    def pin(self, pin):
        self.pins.append(pin)

    async def finish(self):
        pass

    @property
    def has_paired(self):
        return self.ok

    async def close(self):
        self.closed = True


def fake_pyatv(monkeypatch, handler):
    conf = SimpleNamespace(address=ATV_HOST, name="Living Room", get_service=lambda proto: object())
    import pyatv

    async def scan(loop, **kw):
        return [conf]

    async def pair(conf, protocol, loop, **kw):
        handler.name = kw.get("name")
        return handler
    monkeypatch.setattr(pyatv, "scan", scan)
    monkeypatch.setattr(pyatv, "pair", pair)


def test_pairing_saves_both_credentials_and_switches_to_the_apple_tv(monkeypatch, config):
    config.write_text(json.dumps({"host": HOST, "device": "samsung"}))
    handler = FakeHandler()
    fake_pyatv(monkeypatch, handler)
    cfg = core.load_config()
    p = appletv.Pairing("NEW-ID", "Linux Remote")
    try:
        for step in p.STEPS:
            assert p.begin(step)
            p.pin(" 12 34 ")
        p.save(cfg)
    finally:
        p.close()
    saved = json.loads(config.read_text())
    assert handler.pins == [1234, 1234] and handler.closed and handler.name == "Linux Remote"
    assert saved["device"] == "appletv" and saved["host"] == HOST  # the Samsung settings stay
    assert saved["appletv"]["identifier"] == "NEW-ID" and saved["appletv"]["name"] == "Living Room"
    assert saved["appletv"]["credentials"] == {"companion": "new-creds", "airplay": "new-creds"}
    if os.name != "nt":  # Windows has no such modes: the user profile keeps %APPDATA% private
        assert (config.stat().st_mode & 0o777) == 0o600


def test_pairing_another_box_drops_the_old_boxs_credentials(monkeypatch, config):
    config.write_text(json.dumps({"host": HOST, "appletv": {
        "identifier": "OLD-ID", "credentials": {"companion": "old", "airplay": "old"}}}))
    fake_pyatv(monkeypatch, FakeHandler())
    cfg = core.load_config()
    p = appletv.Pairing("NEW-ID")
    try:
        p.begin("companion")
        p.pin("1234")  # AirPlay skipped, as for a box that doesn't offer it
        p.save(cfg)
    finally:
        p.close()
    assert json.loads(config.read_text())["appletv"]["credentials"] == {"companion": "new-creds"}


def test_a_wrong_pin_is_refused(monkeypatch):
    fake_pyatv(monkeypatch, FakeHandler(ok=False))
    p = appletv.Pairing("NEW-ID")
    try:
        p.begin("companion")
        with pytest.raises(core.PairingRefused, match="didn't accept that PIN"):
            p.pin("0000")
        with pytest.raises(ValueError, match="4-digit PIN"):
            p.pin("abc")
    finally:
        p.close()


# -- the bridge on an Apple TV


class RecordingAppleTV(appletv.AppleTV):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls = []

    def key(self, key):
        self.calls.append(key)

    def launch(self, app):
        self.calls.append("launch:" + app["name"])

    def power_state(self):
        return "standby"


@pytest.fixture
def server():
    cfg = core.load_config()
    tv = RecordingAppleTV(cfg)
    tv.cfg["apps"] = [{"appId": i, "name": n} for i, n in BOX_APPS]
    s = bridge.Bridge(tv, ("127.0.0.1", 0))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()
    tv.close()


def request(server, method, path):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        c.request(method, path, headers={"Authorization": f"Bearer {TOKEN}"})
        r = c.getresponse()
        return r.status, json.loads(r.read())
    finally:
        c.close()


def test_bridge_sends_apple_tv_keys(server):
    assert request(server, "POST", "/key/select") == (200, {"ok": True, "key": "select"})
    assert request(server, "POST", "/key/volup") == (200, {"ok": True, "key": "volume_up"})
    status, body = request(server, "POST", "/key/KEY_SOURCE")
    assert status == 400 and "no 'KEY_SOURCE' key" in body["error"]
    assert server.tv.calls == ["select", "volume_up"]


def test_bridge_launches_by_bundle_id_or_name(server):
    assert request(server, "POST", "/launch/com.plexapp.plex") == (200, {"ok": True, "app": "Plex"})
    assert request(server, "POST", "/launch/youtube") == (200, {"ok": True, "app": "YouTube"})
    assert request(server, "GET", "/status") == (200, {"power": "standby"})


def test_a_pairing_fault_isnt_blamed_on_the_tv():
    e = appletv.translate(pyatv_errors.PairingError("takes 2 positional arguments"))
    assert not isinstance(e, core.PairingRefused)
    assert str(e) == "Pairing with the Apple TV failed (takes 2 positional arguments)"


def test_stopping_the_loop_cancels_what_runs_on_it():
    loop = appletv.LoopThread()
    started = threading.Event()

    async def forever():
        started.set()
        await asyncio.sleep(3600)
    task = asyncio.run_coroutine_threadsafe(forever(), loop.loop)
    started.wait(2)
    loop.stop()
    deadline = time.time() + 2
    while not task.done() and time.time() < deadline:
        threading.Event().wait(0.01)
    assert task.cancelled()


def test_a_missed_wake_is_sent_again(client, box, monkeypatch):
    box.power.power_state = PowerState.Off
    wakes = []

    async def turn_on():  # the box ignores the first wake
        wakes.append(1)
        if len(wakes) == 2:
            box.power.power_state = PowerState.On
    monkeypatch.setattr(box.power, "turn_on", turn_on)
    clock = iter(range(100))
    monkeypatch.setattr(core, "now", lambda: next(clock))
    client.wake()
    assert client.wait_until_on(timeout=10)
    assert len(wakes) == 2



def test_a_refused_airplay_stream_says_how_to_fix_it(client, box, monkeypatch):
    async def refuse(url):  # tvOS answered 403: AirPlay limited to the Home
        raise pyatv_errors.AuthenticationError("not authenticated")
    monkeypatch.setattr(box.stream, "play_url", refuse)
    r = client.renderer()
    r.load("http://192.0.2.1:8199/tok/a.mp4", "a", "video/mp4", "video", 1)
    r.play()
    deadline = time.time() + 3
    while r.state() != "ERROR" and time.time() < deadline:
        threading.Event().wait(0.01)
    assert r.state() == "ERROR"
    assert "Allow Access to Anyone on the Same Network" in r.error


def test_casting_needs_the_pyatv_airplay_fix(client, box, monkeypatch):
    monkeypatch.setattr(appletv, "can_cast", lambda: False)
    r = client.renderer()
    r.load("http://192.0.2.1:8199/tok/a.mp4", "a", "video/mp4", "video", 1)
    with pytest.raises(RuntimeError, match="pyatv-airplay.patch"):
        r.play()
    assert not any(isinstance(e, tuple) and e[0] == "play_url" for e in box.log)


def test_can_cast_looks_for_the_patched_pyatv(monkeypatch):
    from pyatv.support import http
    monkeypatch.delattr(http, "decode_plist_body", raising=False)
    assert not appletv.can_cast()
    monkeypatch.setattr(http, "decode_plist_body", lambda body: body, raising=False)
    assert appletv.can_cast()


def test_the_timing_server_listens_on_the_cast_port(monkeypatch):
    """The Apple TV syncs its clock with it before fetching a stream, through the cast port's firewall rule;
    when that port is taken it still opens (on any port)."""
    from pyatv.protocols.airplay import player
    monkeypatch.setattr(player, "timing_server", player.timing_server)  # undone after the test
    appletv.pin_timing_port()
    appletv.pin_timing_port()  # once is enough
    rtsp: Any = SimpleNamespace(connection=SimpleNamespace(local_ip="127.0.0.1"))

    async def port_of():
        async with player.timing_server(rtsp) as server:
            return server.port

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    monkeypatch.setattr(cast, "PORT", free)
    assert asyncio.run(port_of()) == free
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("127.0.0.1", free))
        assert asyncio.run(port_of()) not in (free, 0)


# -- the real connection path (pyatv's scan and connect faked, the client's own code run)


@pytest.fixture
def wired(monkeypatch, box):
    """pyatv.scan finds the box (at a new address) and pyatv.connect hands back the fake box. Returns a
    namespace logging the scans, the connects and the credentials set."""
    import pyatv

    net = SimpleNamespace(scans=[], connects=0, creds={}, fail_connect=[], unicast=True, push_fails=None)
    conf = SimpleNamespace(address="192.0.2.61", name="Den",
                           set_credentials=lambda proto, c: net.creds.__setitem__(proto.name.lower(), c))
    box.push_updater = SimpleNamespace(listener=None, start=lambda: push_start())

    def push_start():
        if net.push_fails:
            raise net.push_fails
        box.log.append("push")

    async def scan(loop, **kw):
        net.scans.append("unicast" if kw.get("hosts") else "multicast")
        return [conf] if net.unicast or not kw.get("hosts") else []

    async def connect(c, loop):
        net.connects += 1
        if net.fail_connect:
            raise net.fail_connect.pop(0)
        return box

    async def no_wait(seconds):
        pass
    monkeypatch.setattr(pyatv, "scan", scan)
    monkeypatch.setattr(pyatv, "connect", connect)
    monkeypatch.setattr(appletv.asyncio, "sleep", no_wait)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(appletv, "can_cast", lambda: True)
    monkeypatch.setattr(appletv, "pin_timing_port", lambda: None)
    return net


@pytest.fixture
def live(wired, events):
    c = appletv.AppleTV(core.load_config(), lambda e, d: events.append((e, d)))
    yield c
    c.close()


def test_connecting_sets_both_credentials_listens_and_remembers_the_address(live, wired, box, events, config):
    assert live.connect() is box
    assert wired.scans == ["unicast"]  # the saved address is asked first
    assert wired.creds == {"companion": "test-companion", "airplay": "test-airplay"}
    assert box.listener is live._listener and box.power.listener is live._listener
    assert box.push_updater.listener is live._listener and "push" in box.log
    assert box.keyboard.listener is live._listener
    assert events == [("connected", None), ("keyboard", True)] and live.push_error is None  # a field is open
    assert live.host == "192.0.2.61" and json.loads(config.read_text())["appletv"]["host"] == "192.0.2.61"
    live.connect()
    assert wired.connects == 1  # already connected


def test_a_box_not_at_its_saved_address_is_searched_for(live, wired):
    wired.unicast = False
    live.connect()
    assert wired.scans == ["unicast", "multicast"]


def test_a_box_nowhere_on_the_network_is_unreachable(live, wired, monkeypatch):
    import pyatv

    async def nothing(loop, **kw):
        return []
    monkeypatch.setattr(pyatv, "scan", nothing)
    with pytest.raises(ConnectionError, match="isn't answering"):
        live.connect()
    assert live.power_state() == "off" and "isn't answering" in live.unreachable


def test_a_failed_connect_is_tried_once_more(live, wired, box):
    wired.fail_connect = [pyatv_errors.ConnectionFailedError("Failed to set up remote control channel")]
    assert live.connect() is box and wired.connects == 2


def test_a_second_failed_connect_is_reported(live, wired):
    wired.fail_connect = [pyatv_errors.ConnectionFailedError("a"), pyatv_errors.ConnectionFailedError("b")]
    with pytest.raises(ConnectionError, match="b"):
        live.connect()


def test_a_refused_pairing_is_reported_not_taken_for_off(live, wired):
    wired.fail_connect = [pyatv_errors.AuthenticationError("bad creds")] * 4
    with pytest.raises(core.PairingRefused, match="Pair it again"):
        live.power_state()
    with pytest.raises(core.PairingRefused):
        live.scan_apps()  # not the saved list, which would hide it


def test_now_playing_failing_to_start_is_said_and_casting_goes_on(live, wired, box, events):
    wired.push_fails = pyatv_errors.NotSupportedError("no MRP")
    live.connect()
    assert live.push_error and ("error", f"Now playing isn't available ({live.push_error})") in events
    r = live.renderer()
    r.load("http://192.0.2.1:8199/tok/a.mp4", "a", "video/mp4", "video", 1)
    r.play()
    assert r.state() == "PLAYING" and r.position() == (0, 0)  # AirPlay holds the stream: that's all there is
    r.stop()


def test_now_playing_failing_without_airplay_paired_is_expected(wired, box, events, config):
    cfg = core.load_config()
    del cfg["appletv"]["credentials"]["airplay"]
    wired.push_fails = pyatv_errors.NotSupportedError("no MRP")
    c = appletv.AppleTV(cfg, lambda e, d: events.append((e, d)))
    try:
        c.connect()
    finally:
        c.close()
    assert events == [("connected", None), ("keyboard", True)]  # no "Now playing isn't available"


def test_a_lost_connection_is_reported_unless_closing(live, wired, box, events):
    live.connect()
    live._listener.connection_lost(Exception("gone"))
    assert live.atv is None and events[-1] == ("disconnected", None)
    live.connect()
    live._listener.powerstate_update(PowerState.On, PowerState.Off)
    assert events[-1] == ("power", "off")
    live.closing = True
    live._listener.connection_closed()
    assert events[-1] == ("power", "off")


def test_sleep_counts_only_once_the_box_reports_standby(client, box, monkeypatch):
    assert client.turn_off() and box.power.power_state == PowerState.Off

    async def no_answer():
        box.log.append("turn_off")
    monkeypatch.setattr(box.power, "turn_off", no_answer)  # still on: tvOS ignored it
    box.power.power_state = PowerState.On
    assert not client.turn_off()
    assert client.turn_off_failed == "Sent sleep, but the Apple TV still reports it's on"

    calls = []

    async def gone(fn):  # the sleep command goes through, then nothing answers
        calls.append(fn)
        if len(calls) > 1:
            raise pyatv_errors.ConnectionFailedError("no route to host")
        return await fn(box)
    monkeypatch.setattr(client, "_do", gone)
    assert not client.turn_off()  # stopped answering: not taken as asleep
    assert "stopped answering" in client.turn_off_failed and "no route" in client.turn_off_failed


# -- discover and the appletv CLI


def found_box(name, ident, os="TvOS", companion=True, model="AppleTV4KGen3"):
    from pyatv.const import DeviceModel, OperatingSystem
    return SimpleNamespace(name=name, identifier=ident, address="192.0.2.70",
                           device_info=SimpleNamespace(operating_system=getattr(OperatingSystem, os),
                                                       model=getattr(DeviceModel, model),
                                                       model_str="Apple TV 4K (gen 3)"),
                           get_service=lambda proto: object() if companion else None)


def test_discover_keeps_apple_tvs_sorted_and_asks_addresses_only_if_multicast_finds_none(monkeypatch):
    import pyatv

    scans = []
    multicast = [found_box("Kitchen", "K"), found_box("den", "D"), found_box("HomePod", "H", os="TvOS",
                 companion=False), found_box("Mac", "M", os="MacOS")]

    async def scan(loop, **kw):
        scans.append(kw.get("hosts"))
        return [found_box("Den", "D2", model="Unknown")] if kw.get("hosts") else multicast
    monkeypatch.setattr(pyatv, "scan", scan)
    assert [(b["name"], b["identifier"]) for b in appletv.discover(timeout=1, hosts=["192.0.2.70"])] == [
        ("den", "D"), ("Kitchen", "K")]
    assert scans == [None]
    multicast = []
    assert appletv.discover(timeout=1, hosts=["", "192.0.2.70"]) == [
        {"name": "Den", "model": "Apple TV", "host": "192.0.2.70", "identifier": "D2"}]
    assert scans == [None, None, ["192.0.2.70"]]


@pytest.fixture
def two_boxes(monkeypatch):
    boxes = [{"name": "Den", "model": "Apple TV 4K", "host": "192.0.2.70", "identifier": "D"},
             {"name": "Kitchen", "model": "Apple TV HD", "host": "192.0.2.71", "identifier": "K"}]
    monkeypatch.setattr(appletv, "discover", lambda hosts=(): boxes)
    return boxes


def test_cli_appletv_scan_lists_the_boxes(two_boxes, capsys):
    core.cli(["appletv", "scan"])
    out = capsys.readouterr().out
    assert "1. Den" in out and "2. Kitchen" in out


def test_cli_appletv_pair_pairs_the_picked_box_and_skips_a_missing_step(two_boxes, monkeypatch, config, capsys):
    made = []

    class FakePairing:
        STEPS = appletv.Pairing.STEPS

        def __init__(self, identifier, name, host):
            made.append(identifier)
            self.pins = []

        def begin(self, step):
            return step == "companion"  # the box doesn't offer AirPlay

        def pin(self, pin):
            self.pins.append(pin)

        def save(self, cfg):
            made.append(("saved", self.pins))

        def close(self):
            made.append("closed")
    answers = iter(["2", "1234"])
    monkeypatch.setattr(appletv, "Pairing", FakePairing)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    core.cli(["appletv", "pair"])
    assert made == ["K", ("saved", ["1234"]), "closed"]
    assert "doesn't offer airplay; skipped" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["3", "x", KeyboardInterrupt])
def test_cli_appletv_pair_saves_nothing_on_a_bad_pick_or_ctrl_c(two_boxes, monkeypatch, config, answer):
    before = config.read_text()
    saved = []

    class FakePairing:
        STEPS = appletv.Pairing.STEPS

        def __init__(self, *a):
            pass

        def begin(self, step):
            return True

        def pin(self, pin):
            pass

        def save(self, cfg):
            saved.append(cfg)

        def close(self):
            pass

    def ask(prompt=""):
        if "which one" in prompt:
            if answer is KeyboardInterrupt:
                return "1"
            return answer
        raise KeyboardInterrupt
    monkeypatch.setattr(appletv, "Pairing", FakePairing)
    monkeypatch.setattr("builtins.input", ask)
    with pytest.raises(SystemExit, match="No Apple TV picked|nothing was saved"):
        core.cli(["appletv", "pair"])
    assert not saved and config.read_text() == before


# -- typing


def test_typing_replaces_the_field_reads_it_and_clears_it(client, box):
    box.keyboard.text_get = lambda: asyncio.sleep(0, "sev")
    box.keyboard.text_clear = lambda: asyncio.sleep(0, box.log.append("clear"))
    assert client.typed() == "sev"
    client.text("severance")
    client.text("")
    assert box.log == [("text", "severance"), "clear"]
    box.keyboard.text_focus_state = KeyboardFocusState.Unfocused
    with pytest.raises(RuntimeError, match="No text field is open"):
        client.text("x")


def test_a_text_field_opening_or_closing_is_reported(live, wired, box, events):
    live.connect()
    live._listener.focusstate_update(KeyboardFocusState.Focused, KeyboardFocusState.Unfocused)
    assert events[-1] == ("keyboard", False)
    live._listener.focusstate_update(KeyboardFocusState.Unfocused, KeyboardFocusState.Focused)
    assert events[-1] == ("keyboard", True)


# -- volume


def test_volume_goes_through_the_apple_tv_when_it_controls_the_tv(client, box):
    client.key("KEY_VOLUP")
    client.key("volume_down")
    assert box.log == ["volume_up", "volume_down"]


def test_volume_says_why_when_the_apple_tv_cannot_change_it(client, box):
    box.volume_state = FeatureState.Unavailable  # Volume Control set to IR, or HDMI-CEC off
    with pytest.raises(RuntimeError, match="Volume Control to Auto"):
        client.key("KEY_VOLUP")
    assert box.log == []


def test_volume_can_go_to_the_samsung_tv(client, box, monkeypatch):
    sent = []
    monkeypatch.setattr(core.TV, "key", lambda self, k: sent.append(k))
    client.cfg["volume_via_samsung"] = True
    client.key("KEY_VOLUP")
    client.key("volume_down")
    client.key("KEY_ENTER")  # only volume goes there
    assert sent == ["KEY_VOLUP", "KEY_VOLDOWN"] and box.log == ["select"]


def test_volume_stays_on_the_apple_tv_without_a_samsung_tv(client, box, monkeypatch):
    monkeypatch.setattr(core, "is_set_up", lambda cfg, device: device == "appletv")
    client.cfg["volume_via_samsung"] = True
    client.key("KEY_VOLUP")
    assert box.log == ["volume_up"]


def test_the_volume_setting_survives_a_broken_value(config):
    cfg = json.loads(config.read_text())
    cfg["appletv"]["volume_via_samsung"] = "yes"
    config.write_text(json.dumps(cfg))
    assert appletv.section(core.load_config())["volume_via_samsung"] is False


def test_without_pyatv_the_remote_says_so_instead_of_crashing(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pyatv(name, *args, **kwargs):
        if name == "pyatv" or name.startswith("pyatv."):
            raise ImportError("No module named 'pyatv'", name="pyatv")
        return real(name, *args, **kwargs)
    c = appletv.AppleTV(core.load_config())
    monkeypatch.setattr(builtins, "__import__", no_pyatv)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    try:
        with pytest.raises(appletv.NeedsPyatv):
            c.power_state()  # not ImportError, and not "off"
        with pytest.raises(SystemExit, match="needs pyatv"):
            core.cli(["key", "select"])  # one line, not a traceback
    finally:
        c.close()


def test_a_late_report_from_a_replaced_connection_doesnt_drop_the_new_one(live, wired, events, monkeypatch):
    import pyatv
    live.connect()
    old = live._listener
    newer: Any = FakeBox()
    newer.push_updater = SimpleNamespace(listener=None, start=lambda: None)

    async def connect(c, loop):
        return newer
    monkeypatch.setattr(pyatv, "connect", connect)
    live.atv = None  # as _do() does when a request finds the connection gone
    assert live.connect() is newer
    old.connection_closed()  # the old connection's close, arriving after the new one is up
    assert live.atv is newer and ("disconnected", None) not in events
    live._listener.connection_lost(Exception("gone"))
    assert live.atv is None and events[-1] == ("disconnected", None)


def test_any_spelling_of_the_power_key_sleeps_the_box(client, box):
    client.key("POWER")
    assert box.log == ["turn_off"]


def test_volume_on_the_samsung_tv_shows_its_allow_prompt(client, events, monkeypatch):
    made = []

    def key(self, k):
        made.append(self)
        self.on_event("pairing", None)  # no token yet: the Samsung TV asks to allow this remote
    monkeypatch.setattr(core.TV, "key", key)
    client.cfg["volume_via_samsung"] = True
    client.key("KEY_VOLUP")
    client.key("KEY_VOLDOWN")
    assert ("pairing", None) in events
    assert len(made) == 2 and made[0] is made[1]  # one Samsung TV client, kept


def test_bridge_power_key_sleeps_the_box_in_the_background(server, monkeypatch):
    slept = threading.Event()
    monkeypatch.setattr(server.tv, "power_state", lambda: "on")
    monkeypatch.setattr(server.tv, "turn_off", lambda: slept.set() or True)
    assert request(server, "POST", "/key/power") == (202, {"ok": True})  # answers at once, like /power/off
    assert slept.wait(3) and server.tv.calls == []
