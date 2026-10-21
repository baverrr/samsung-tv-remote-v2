"""mirror_audio routing against a fake pactl: start, steady state, restore, and pactl failures.

STATE and CONFIG are redirected to tmp_path, so nothing touches the real files or PipeWire.
"""

import json

import pytest

import mirror_audio as ma

HEADSET, SPEAKER, TV = "headset", "speaker", "gnome_network_displays_1"


class FakePulse:
    """Just enough of pactl: sinks, their monitors, recorders, streams and the default output."""

    def __init__(self):
        self.sinks = {HEADSET: 1, SPEAKER: 2}
        self.priority = {HEADSET: 1109, SPEAKER: 1000}
        self.default = HEADSET
        self.recording = False  # a Network Displays session recording the TV sink's monitor
        self.streams = {}  # index -> (sink name, properties)
        self.calls = []
        self.fail = set()  # pactl commands that fail, e.g. {"list sinks"}

    def add_tv(self):
        self.sinks[TV] = 3
        self.priority[TV] = 0

    def play(self, index, sink, **props):
        self.streams[index] = (sink, {"application.name": f"app{index}", **props})

    def __call__(self, *args):
        cmd = " ".join(a for a in args if a not in ("-f", "json"))
        self.calls.append(cmd)
        if any(cmd.startswith(f) for f in self.fail):
            raise ma.PactlError(f"pactl {cmd}: Connection refused")
        if args[:3] == ("-f", "json", "list"):
            return json.dumps(self.listing(args[3]))
        if args[0] == "get-default-sink":
            return self.default + "\n"
        if args[0] == "set-default-sink":
            self.default = args[1]
        elif args[0] == "move-sink-input":
            idx = int(args[1])
            if idx not in self.streams:
                raise ma.PactlError("pactl move-sink-input: No such entity")
            self.streams[idx] = (args[2], self.streams[idx][1])
        return ""

    def listing(self, what):
        if what == "sinks":
            return [{"name": n, "index": i, "monitor_source": f"{n}.monitor",
                     "properties": {"priority.session": str(self.priority[n])}} for n, i in self.sinks.items()]
        if what == "sources":
            return [{"name": f"{n}.monitor", "index": 100 + i} for n, i in self.sinks.items()]
        if what == "source-outputs":
            return [{"source": 100 + self.sinks[TV]}] if self.recording and TV in self.sinks else []
        if what == "sink-inputs":
            return [{"index": i, "sink": self.sinks.get(s, -1), "properties": p} for i, (s, p) in self.streams.items()]
        raise AssertionError(what)

    def sink_of(self, index):
        return self.streams[index][0]


@pytest.fixture
def pulse(monkeypatch, tmp_path):
    fake = FakePulse()
    monkeypatch.setattr(ma, "pactl", fake)
    monkeypatch.setattr(ma, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(ma, "CONFIG", tmp_path / "mirror-audio.json")
    return fake


def start(pulse):
    pulse.add_tv()
    pulse.recording = True
    router = ma.Router()
    router.check()
    return router


def test_start_routes_to_tv_but_keeps_chat_local(pulse):
    pulse.play(5, HEADSET)
    pulse.play(6, HEADSET, **{"application.name": "Discord"})
    start(pulse)
    assert pulse.default == TV
    assert pulse.sink_of(5) == TV
    assert pulse.sink_of(6) == HEADSET
    assert json.loads(ma.STATE.read_text()) == {"active_sink": TV, "previous": HEADSET}


def test_steady_state_changes_nothing(pulse):
    pulse.play(5, HEADSET)
    router = start(pulse)
    pulse.calls.clear()
    router.check()
    assert not [c for c in pulse.calls if c.startswith(("set-", "move-"))]


def test_stop_restores_previous_output(pulse):
    pulse.play(5, HEADSET)
    router = start(pulse)
    pulse.recording = False  # Network Displays keeps its sink while not streaming
    router.check()
    assert pulse.default == HEADSET
    assert pulse.sink_of(5) == HEADSET
    assert json.loads(ma.STATE.read_text()) == {}


def test_open_but_not_streaming_does_nothing(pulse):
    pulse.add_tv()
    ma.Router().check()
    assert pulse.default == HEADSET
    assert not ma.STATE.exists()


def test_failed_listing_keeps_the_restore_target(pulse):
    """A pactl hiccup mid-session must not look like "mirroring stopped" and lose the headset."""
    pulse.play(5, HEADSET)
    router = start(pulse)
    pulse.fail = {"list sinks"}
    with pytest.raises(ma.PactlError):
        router.check()
    pulse.fail = set()
    router.check()  # the retry, with pactl working again
    assert router.state == {"active_sink": TV, "previous": HEADSET}
    pulse.recording = False
    router.check()
    assert pulse.default == HEADSET


def test_failed_switch_is_retried(pulse):
    pulse.add_tv()
    pulse.recording = True
    pulse.fail = {"set-default-sink"}
    router = ma.Router()
    with pytest.raises(ma.PactlError):
        router.check()
    assert router.state == {"active_sink": None, "previous": HEADSET}  # not switched, restore target kept
    pulse.fail = set()
    router.check()
    assert pulse.default == TV


def test_stop_falls_back_when_previous_output_is_gone(pulse):
    pulse.play(5, HEADSET)
    router = start(pulse)
    del pulse.sinks[HEADSET]  # headset unplugged during the session
    pulse.streams[5] = (TV, pulse.streams[5][1])
    pulse.recording = False
    router.check()
    assert pulse.default == SPEAKER
    assert pulse.sink_of(5) == SPEAKER


def test_restart_mid_session_with_lost_state_picks_a_real_output(pulse):
    pulse.add_tv()
    pulse.recording = True
    pulse.default = TV  # we had switched it, then the state file was lost
    router = ma.Router()
    router.check()
    assert router.state == {"active_sink": TV, "previous": HEADSET}


def test_restart_mid_session_keeps_saved_previous(pulse):
    ma.STATE.write_text(json.dumps({"active_sink": TV, "previous": SPEAKER}))
    pulse.add_tv()
    pulse.recording = True
    pulse.default = TV
    router = ma.Router()
    router.check()
    assert not [c for c in pulse.calls if c.startswith("set-default-sink")]
    pulse.recording = False
    router.check()
    assert pulse.default == SPEAKER


@pytest.mark.usefixtures("pulse")
def test_corrupt_state_file_is_ignored():
    ma.STATE.write_text('["not", "a dict"]')
    assert ma.Router().state == {}


def test_vanished_stream_does_not_abort_routing(pulse):
    pulse.play(5, HEADSET)
    pulse.play(6, HEADSET)
    pulse.add_tv()
    pulse.recording = True
    original = pulse.listing

    def listing(what):  # stream 5 ends between the listing and the move
        out = original(what)
        if what == "sink-inputs":
            pulse.streams.pop(5, None)
        return out

    pulse.listing = listing
    ma.Router().check()
    assert pulse.sink_of(6) == TV


@pytest.mark.parametrize(("props", "expected"), [
    ({"media.role": "Phone"}, True),
    ({"application.name": "Discord"}, True),
    ({"pipewire.access.portal.app_id": "com.discordapp.Discord"}, True),
    ({"application.name": "Firefox"}, False),
    ({}, False),
])
@pytest.mark.usefixtures("pulse")
def test_keep_local(props, expected):
    assert ma.keep_local({"properties": props}, ma.load_config()) is expected


@pytest.mark.usefixtures("pulse")
def test_keep_local_matches_snap_command(monkeypatch):
    monkeypatch.setattr(ma, "executable", lambda _pid: "/snap/discord/123/usr/share/discord/Discord")
    stream = {"properties": {"application.name": "Chromium", "application.process.id": "42"}}
    assert ma.keep_local(stream, ma.load_config())


@pytest.mark.parametrize("bad", [
    {"keep_local_apps": "discord"},  # a string would match letter by letter
    {"keep_local_apps": ["discord", ""]},  # "" matches everything
    {"keep_local_roles": "phone"},
    ["discord"],
])
@pytest.mark.usefixtures("pulse")
def test_bad_config_falls_back_to_defaults(bad):
    ma.CONFIG.write_text(json.dumps(bad))
    cfg = ma.load_config()
    assert not ma.keep_local({"properties": {"application.name": "Firefox"}}, cfg)
    assert cfg["keep_local_apps"] == ma.DEFAULT_CONFIG["keep_local_apps"]


@pytest.mark.usefixtures("pulse")
def test_config_roles_are_case_insensitive():
    ma.CONFIG.write_text(json.dumps({"keep_local_roles": ["Phone"]}))
    assert ma.keep_local({"properties": {"media.role": "phone"}}, ma.load_config())


@pytest.mark.usefixtures("pulse")
def test_missing_config_is_created():
    ma.load_config()
    assert json.loads(ma.CONFIG.read_text())["keep_local_apps"]


def test_failure_after_the_switch_still_restores_the_previous_output(pulse, monkeypatch):
    """The default moved to the TV, then saving the state failed: the retry must not forget the headset."""
    pulse.play(5, HEADSET)
    pulse.add_tv()
    pulse.recording = True
    router = ma.Router()
    save, calls = ma.save_state, []

    def flaky_save(state):
        calls.append(state)
        if len(calls) == 2:  # the write after set-default-sink
            raise OSError(28, "No space left on device")
        save(state)
    monkeypatch.setattr(ma, "save_state", flaky_save)
    with pytest.raises(OSError):
        router.check()
    assert pulse.default == TV
    router.check()  # the retry
    pulse.recording = False
    router.check()
    assert pulse.default == HEADSET and pulse.sink_of(5) == HEADSET


def test_restart_after_a_failed_switch_keeps_the_restore_target(pulse, monkeypatch):
    pulse.add_tv()
    pulse.recording = True
    pulse.fail = {"set-sink-volume"}
    with pytest.raises(ma.PactlError):
        ma.Router().check()
    assert json.loads(ma.STATE.read_text()) == {"active_sink": None, "previous": HEADSET}
