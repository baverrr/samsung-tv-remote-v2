"""Apple TV (tvOS) client on pyatv: keys, apps, typing and power over the Companion protocol, now playing
and seeking over MRP (tunnelled through AirPlay), and casting over AirPlay. AppleTV offers the same methods
as tv_remote.TV, so the window, the CLI and the ESP32 bridge drive either one.

pyatv is asyncio; AppleTV runs one event loop on a daemon thread, and its blocking methods wait on it
(callers are the remote's worker threads, the CLI and the bridge, never the Qt thread).

Settings live in config.json under "appletv": the box's identifier and last address, the pairing
credentials for Companion and AirPlay, and its app lists (bundle IDs, which the box reports itself).
"""

import asyncio
import concurrent.futures
import contextlib
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import tv_remote as core

if TYPE_CHECKING:
    from pyatv.interface import BaseConfig, PairingHandler

# KEY_* names (what the window, its shortcuts and the bridge send) -> pyatv RemoteControl methods.
KEYS = {
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_ENTER": "select",
    "KEY_RETURN": "menu", "KEY_HOME": "home", "KEY_PLAY": "play_pause", "KEY_PAUSE": "play_pause",
    "KEY_FF": "skip_forward", "KEY_REWIND": "skip_backward", "KEY_VOLUP": "volume_up",
    "KEY_VOLDOWN": "volume_down", "KEY_MENU": "control_center", "KEY_EXIT": "top_menu",
    "KEY_CHUP": "channel_up", "KEY_CHDOWN": "channel_down",
}
# pyatv's own names are accepted too (tv-remote key select, POST /key/play_pause).
NATIVE = set(KEYS.values()) | {"play", "pause", "stop", "next", "previous", "home_hold", "screensaver"}
# Default app buttons, by bundle ID; only the ones the box has are shown.
DEFAULT_FAVORITES = ["com.netflix.Netflix", "com.google.ios.youtube", "com.amazon.aiv.AIVApp",
                     "com.disney.disneyplus", "com.hulu.plus"]
BUNDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SECTION_DEFAULTS = {"identifier": None, "name": None, "host": None, "credentials": {}, "apps": [],
                    "volume_via_samsung": False}
TIMEOUT_S = 15  # one request to the box; connecting includes an mDNS scan of up to 5 s
CAST_UNPATCHED = ("Casting to the Apple TV needs pyatv's AirPlay fix for tvOS 26 and later. Run install.sh "
                  "again: it applies tools/pyatv-airplay.patch")
# The volume keys, and the Samsung TV keys they become with volume_via_samsung.
VOLUME_KEYS = {"volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN"}
VOLUME_UNAVAILABLE = ("The Apple TV isn't controlling the TV's volume. On the Apple TV, set Settings › Remotes and "
                      "Devices › Volume Control to Auto (HDMI-CEC), or choose \"Volume keys control the Samsung TV\" "
                      "in the remote's device menu")
NOT_SET_UP = ("No Apple TV is set up yet. Open the remote and choose Set up Apple TV…, "
              "or run: tv-remote appletv pair")


def section(cfg):
    """The config's "appletv" object, created and made usable whatever the file holds."""
    sec = cfg.get("appletv")
    if not isinstance(sec, dict):
        sec = cfg["appletv"] = {}
    for k, v in SECTION_DEFAULTS.items():
        if v is None:
            if not isinstance(sec.get(k), str):
                sec[k] = None
        elif not isinstance(sec.get(k), type(v)):
            sec[k] = type(v)()
    sec["apps"] = [a for a in sec["apps"] if isinstance(a, dict) and isinstance(a.get("appId"), str)
                   and isinstance(a.get("name"), str)]
    clean_app_lists(sec)
    return sec


def is_set_up(cfg):
    sec = cfg.get("appletv")
    return (isinstance(sec, dict) and bool(sec.get("identifier"))
            and bool((sec.get("credentials") or {}).get("companion")))


def clean_app_lists(sec):
    """Favorites (the remote's buttons, at most MAX_FAVORITES bundle IDs, in order) and menu_apps (All
    apps): every other installed app, like the Apple TV's own home screen."""
    value = sec.get("favorites")
    if isinstance(value, list):
        favorites = list(dict.fromkeys(k for k in value if isinstance(k, str) and BUNDLE_RE.fullmatch(k)))
    else:
        favorites = list(DEFAULT_FAVORITES)
    sec["favorites"] = favorites[:core.MAX_FAVORITES]
    sec["menu_apps"] = [a["appId"] for a in sec.get("apps") or [] if a["appId"] not in sec["favorites"]]
    sec["custom_apps"] = []  # the box lists every app it has, so nothing is added by ID


def resolve_key(name):
    """The pyatv method for a key name: KEY_VOLUP, volup or volume_up ("power" for KEY_POWER, which
    AppleTV.key turns into a confirmed sleep). ValueError if there's none."""
    if name in ("KEY_POWER", "power") or name.upper() == "POWER":
        return "power"
    for candidate in (name, "KEY_" + name.upper()):
        if candidate in KEYS:
            return KEYS[candidate]
    if name in NATIVE:
        return name
    raise ValueError(f"The Apple TV has no {name!r} key")


class NeedsPyatv(RuntimeError):
    """pyatv isn't installed: a setup problem, so never reported as the box being off."""


def _pyatv():
    try:
        import pyatv
    except ImportError as e:
        raise NeedsPyatv("The Apple TV remote needs pyatv. Run install.sh again, or: "
                           "venv/bin/pip install pyatv") from e
    return pyatv


def can_cast():
    """Whether pyatv has the AirPlay fix from tools/pyatv-airplay.patch. Without it tvOS 26+ accepts a
    stream but never fetches it, so casting would sit on "Loading" for nothing."""
    try:
        from pyatv.support import http
    except ImportError:
        return False
    return hasattr(http, "decode_plist_body")  # added by the patch


def pin_timing_port():
    """Make pyatv's AirPlay timing server listen on UDP cast.PORT instead of a random port. The Apple TV
    syncs its clock with that server before it fetches the stream, and a firewall that only allows the
    cast server's port drops the sync packets, so nothing plays (measured 2026-11-01: /play accepted, the
    file never fetched). One port number then covers both: TCP for the media, UDP for the clock."""
    from contextlib import asynccontextmanager

    from pyatv.protocols.airplay import player
    from pyatv.protocols.raop.protocols import TimingServer

    import cast

    if getattr(player.timing_server, "pinned", False):
        return

    @asynccontextmanager
    async def timing_server(rtsp):
        loop = asyncio.get_running_loop()
        try:
            _, server = await loop.create_datagram_endpoint(
                TimingServer, local_addr=(rtsp.connection.local_ip, cast.PORT))
        except OSError:  # in use: a random port still works where no firewall is in the way
            _, server = await loop.create_datagram_endpoint(
                TimingServer, local_addr=(rtsp.connection.local_ip, 0))
        try:
            yield server
        finally:
            server.close()

    setattr(timing_server, "pinned", True)  # noqa: B010 - a function attribute pyright would reject
    player.timing_server = timing_server


class LoopThread:
    """An asyncio loop on a daemon thread; run() waits for a coroutine on it from any other thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="pyatv").start()

    def run(self, coro, timeout=TIMEOUT_S):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("the Apple TV didn't answer in time") from None
        except Exception as e:
            raise translate(e) from e

    def stop(self):
        """Cancel what's still running on the loop (pyatv leaves background tasks), then stop it, so
        nothing is left to complain about a closed loop at exit."""
        async def drain():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(drain(), self.loop).result(2)
        self.loop.call_soon_threadsafe(self.loop.stop)


def translate(e):
    """pyatv's exceptions as the ones callers already handle: OSError for 'couldn't reach it',
    PairingRefused for credentials it no longer takes, RuntimeError for the rest. Checked before pyatv is
    imported, so NeedsPyatv (and anything else already in those terms) gets through without it."""
    if isinstance(e, (OSError, ValueError, RuntimeError)):
        return e
    from pyatv import exceptions as x

    if isinstance(e, (x.ConnectionFailedError, x.ConnectionLostError, x.OperationTimeoutError,
                      asyncio.TimeoutError)):
        return ConnectionError(str(e) or type(e).__name__)
    if isinstance(e, x.PairingError):  # while pairing: a wrong PIN, a timeout, or a library fault
        return RuntimeError(f"Pairing with the Apple TV failed ({e or type(e).__name__})")
    if isinstance(e, (x.AuthenticationError, x.InvalidCredentialsError, x.NoCredentialsError)):
        return core.PairingRefused(f"The Apple TV refused this remote ({e or type(e).__name__}). "
                                   "Pair it again: Set up Apple TV…, or tv-remote appletv pair")
    if isinstance(e, x.NotSupportedError):
        return RuntimeError(f"The Apple TV doesn't support that ({e})" if str(e) else "The Apple TV doesn't support that")
    return RuntimeError(str(e) or type(e).__name__)


def playing_info(playing, app_name=None):
    """What the window shows about a pyatv Playing: plain values only."""
    state = playing.device_state.name.lower()  # idle, loading, paused, playing, seeking, stopped
    subtitle = playing.artist or playing.series_name or ""
    if playing.series_name and playing.season_number and playing.episode_number:
        subtitle = f"{playing.series_name} · S{playing.season_number} E{playing.episode_number}"
    return {"state": state, "title": playing.title or "", "subtitle": subtitle, "album": playing.album or "",
            "position": playing.position, "total": playing.total_time, "app": app_name or "",
            "hash": playing.hash if playing.title else None}


def make_listener(owner):
    """The object pyatv reports to (on the loop thread): connection, now playing and power changes.
    pyatv keeps only weak references to it, so the AppleTV holds it. One per connection: a late report from
    a connection that was already replaced names that one (self.atv), so it can't drop the new one."""
    from pyatv.const import KeyboardFocusState
    from pyatv.interface import (
        DeviceListener,
        KeyboardListener,
        PowerListener,
        PushListener,
    )

    class Listener(DeviceListener, PushListener, PowerListener, KeyboardListener):
        atv: Any = None  # the connection this listener reports for, set once it's made

        def connection_lost(self, exception):
            owner._lost(self.atv)

        def connection_closed(self):
            owner._lost(self.atv)

        def playstatus_update(self, updater, playstatus):
            owner._playing(playstatus)

        def playstatus_error(self, updater, exception):
            pass  # the updater retries by itself

        def powerstate_update(self, old_state, new_state):
            owner.on_event("power", new_state.name.lower())

        def focusstate_update(self, old_state, new_state):  # a text field opened or closed on the box
            owner.on_event("keyboard", new_state == KeyboardFocusState.Focused)

    return Listener()


class AppleTV:
    kind = "appletv"

    def __init__(self, cfg, on_event=lambda event, data: None):
        self.root = cfg  # the whole config (saved as one file)
        self.cfg = section(cfg)
        if not is_set_up(cfg):
            raise core.ConfigError(NOT_SET_UP)
        self.on_event = on_event
        self.host = self.cfg.get("host") or self.cfg.get("name") or "the Apple TV"
        self.unreachable = None
        self.apps_from_cache = False
        self.atv = None
        self.playing = None  # the last now-playing info, for AirPlayRenderer
        self.playing_at = 0.0  # when it came (time.monotonic())
        self.push_error = None  # why now playing couldn't start on this connection, if it couldn't
        self.closing = False
        self._listener = None  # make_listener(), once pyatv is imported
        self._artwork_hash = None
        self._samsung = None  # the Samsung TV client the volume keys go to (volume_via_samsung)
        self._samsung_lock = threading.Lock()  # the window's worker and the bridge's threads may both make it
        self.loop = LoopThread()
        self._connect_lock = asyncio.Lock()  # bound to self.loop.loop on first use (Python 3.10+)

    # -- connection

    async def _find(self):
        pyatv = _pyatv()
        loop = asyncio.get_running_loop()
        ident = self.cfg["identifier"]
        found = []
        if self.cfg.get("host"):  # unicast first: quick, and works where multicast is filtered
            found = await pyatv.scan(loop, identifier=ident, hosts=[self.cfg["host"]], timeout=3)
        if not found:
            found = await pyatv.scan(loop, identifier=ident, timeout=5)
        if not found:
            raise ConnectionError(f"{self.cfg.get('name') or 'The Apple TV'} isn't answering on the network")
        return found[0]

    async def _ensure(self):
        """The connected box, connecting first if need be."""
        async with self._connect_lock:
            if self.atv is not None:
                return self.atv
            pyatv = _pyatv()
            from pyatv.const import KeyboardFocusState, Protocol

            conf = await self._find()
            creds = self.cfg["credentials"]
            for proto, name in ((Protocol.Companion, "companion"), (Protocol.AirPlay, "airplay")):
                if creds.get(name):
                    conf.set_credentials(proto, creds[name])
            try:
                atv = await pyatv.connect(conf, asyncio.get_running_loop())
            except Exception:  # noqa: BLE001 - e.g. "Failed to set up remote control channel" right after
                await asyncio.sleep(1.5)  # another connection (the old window, a CLI run) closed; one more try
                atv = await pyatv.connect(conf, asyncio.get_running_loop())
            self._listener = listener = make_listener(self)
            listener.atv = atv
            atv.listener = listener
            atv.power.listener = listener
            with contextlib.suppress(Exception):  # typing still works without the open/close reports
                atv.keyboard.listener = listener
            self.push_error = None
            try:
                atv.push_updater.listener = listener
                atv.push_updater.start()
            except Exception as e:  # noqa: BLE001 - no MRP: keys, apps and power still work
                self.push_error = str(translate(e))
                if creds.get("airplay"):  # paired for it, so it should have worked: say why it didn't
                    self.on_event("error", f"Now playing isn't available ({self.push_error})")
            self.atv = atv
            self._remember(str(conf.address), conf.name)
            self.on_event("connected", None)
            with contextlib.suppress(Exception):  # a text field already open when the remote connects
                self.on_event("keyboard", atv.keyboard.text_focus_state == KeyboardFocusState.Focused)
            return atv

    async def stream(self, url):
        """Play a URL over AirPlay until it ends."""
        pin_timing_port()
        atv = await self._ensure()
        await atv.stream.play_url(url)

    def _remember(self, host, name):
        self.host = host
        if (self.cfg.get("host"), self.cfg.get("name")) != (host, name):
            self.cfg["host"], self.cfg["name"] = host, name
            try:
                core.save_config(self.root)
            except OSError:
                pass  # only a shortcut for next time

    def _lost(self, which=None):
        """A connection dropped or closed. which is the connection that said so: one that was already
        replaced (a reconnect in _do()) is ignored, so its late report doesn't drop the new connection."""
        if which is not None and which is not self.atv:
            return
        atv, self.atv = self.atv, None
        if atv is not None and not self.closing:
            self.on_event("disconnected", None)

    async def _do(self, fn):
        """fn(atv) on a connected box; a connection that dropped is made again once."""
        atv = await self._ensure()  # first: without pyatv, it raises NeedsPyatv rather than ImportError
        from pyatv import exceptions as x

        try:
            return await fn(atv)
        except (x.ConnectionLostError, ConnectionError):
            self.atv = None
            atv.close()
        return await fn(await self._ensure())

    def connect(self):
        return self.loop.run(self._ensure())

    def close(self):
        self.closing = True
        if self._samsung is not None:
            self._samsung.close()
        atv, self.atv = self.atv, None
        if atv is not None:
            async def go():
                await asyncio.gather(*atv.close(), return_exceptions=True)
            try:
                self.loop.run(go(), timeout=3)
            except (OSError, RuntimeError):
                pass
        self.loop.stop()

    # -- remote

    def resolve_key(self, name):
        return resolve_key(name)

    def key(self, key):
        method = resolve_key(key)
        if method == "power":  # KEY_POWER, power or POWER
            self.turn_off()
        elif method in VOLUME_KEYS:
            self.volume(method)
        else:
            self.remote(method)

    def volume_via_samsung(self):
        """Whether the volume keys go to the Samsung TV: set in the device menu, for an Apple TV whose
        own volume control can't reach the TV (the Siri Remote set to IR, or HDMI-CEC off)."""
        return bool(self.cfg["volume_via_samsung"]) and core.is_set_up(self.root, "samsung")

    def volume(self, method):
        """volume_up or volume_down: on the Samsung TV if so set, else through the Apple TV, which says
        plainly when it isn't controlling the TV's volume (tvOS takes the key and does nothing then)."""
        if self.volume_via_samsung():
            with self._samsung_lock:
                if self._samsung is None:
                    self._samsung = core.TV(self.root, self._samsung_event)
                samsung = self._samsung
            samsung.key(VOLUME_KEYS[method])
            return

        async def go(atv):
            from pyatv.const import FeatureName, FeatureState
            feature = FeatureName.VolumeUp if method == "volume_up" else FeatureName.VolumeDown
            if atv.features.get_feature(feature).state != FeatureState.Available:
                raise RuntimeError(VOLUME_UNAVAILABLE)
            await getattr(atv.remote_control, method)()
        self.loop.run(self._do(go))

    def _samsung_event(self, event, data):
        """The Samsung TV's events while it takes the volume keys: only its "Allow" prompt matters here, as
        the key waits for it."""
        if event == "pairing":
            self.on_event("pairing", None)

    def remote(self, method):
        """A pyatv RemoteControl call: select, play, pause, stop..."""
        async def go(atv):
            await getattr(atv.remote_control, method)()
        self.loop.run(self._do(go))

    def text(self, s):
        """Types into the focused tvOS text field (a search box), replacing what's there."""
        from pyatv.const import KeyboardFocusState

        async def go(atv):
            if atv.keyboard.text_focus_state != KeyboardFocusState.Focused:
                raise RuntimeError("No text field is open on the Apple TV; open a search box first")
            await (atv.keyboard.text_set(s) if s else atv.keyboard.text_clear())
        self.loop.run(self._do(go))

    def typed(self):
        """What the open tvOS text field holds ("" if none is open)."""
        async def go(atv):
            return await atv.keyboard.text_get() or ""
        return self.loop.run(self._do(go))

    def info(self, host=None):
        async def go(atv):
            d = atv.device_info
            return {"device": {"name": self.cfg.get("name"), "model": d.model_str, "version": d.version,
                               "host": self.host, "power": atv.power.power_state.name.lower()}}
        return self.loop.run(self._do(go))

    # -- apps

    def scan_apps(self):
        """The box's installed apps ({"appId": bundle id, "name"}), saved to config; the saved list if it
        doesn't answer."""
        async def go(atv):
            return await atv.apps.app_list()
        try:
            found = [{"appId": a.identifier, "name": a.name or a.identifier}
                     for a in self.loop.run(self._do(go))]
        except (core.PairingRefused, NeedsPyatv):
            raise  # the saved list would hide that the box refused this remote
        except (OSError, RuntimeError):
            self.apps_from_cache = True
            return self.cfg["apps"]
        self.apps_from_cache = False
        found.sort(key=lambda a: a["name"].lower())
        if found != self.cfg["apps"]:
            self.cfg["apps"] = found
            clean_app_lists(self.cfg)
            core.save_config(self.root)
        return found

    def probe_app(self, app_id):
        """The saved app with this bundle ID (adding apps by ID is Samsung only: the box lists all its apps)."""
        return next((a for a in self.cfg["apps"] if a["appId"] == app_id), None)

    def launch(self, app):
        async def go(atv):
            await atv.apps.launch_app(app["appId"])
        self.loop.run(self._do(go))

    # -- power

    def power_state(self):
        """'on', 'standby' or 'off' (unreachable; the reason is kept in self.unreachable)."""
        async def go(atv):
            return atv.power.power_state.name.lower()
        try:
            state = self.loop.run(self._do(go))
        except (core.PairingRefused, NeedsPyatv):
            raise  # it answered and refused (or can't be asked): not "off"
        except (OSError, RuntimeError) as e:
            self.unreachable = core.describe_error(e)
            return "off"
        self.unreachable = None
        return "standby" if state == "off" else "on"  # pyatv's Off is tvOS sleep; Unknown counts as on

    def wake(self):
        async def go(atv):
            await atv.power.turn_on()
        self.loop.run(self._do(go))

    def wait_until_on(self, timeout=25):
        """True once the box reports it's awake. The wake is sent once more halfway: tvOS sometimes
        misses the first one (1 of 3 tries on 2026-11-01), like a Samsung TV dropping KEY_POWER."""
        start = core.now()
        resent = False
        while core.now() - start < timeout:
            if self.power_state() == "on":
                return True
            if not resent and core.now() - start >= timeout / 2:
                resent = True
                with contextlib.suppress(OSError, RuntimeError):
                    self.wake()
            time.sleep(1)
        return False

    def turn_off(self):
        """Puts the box to sleep. True once it reports standby: an asleep box still answers (that's how
        it's woken), so one that stops answering isn't taken as asleep."""
        async def go(atv):
            await atv.power.turn_off()
        self.loop.run(self._do(go))
        end = core.now() + 15
        while core.now() < end:
            time.sleep(1)
            if self.power_state() == "standby":
                return True
        return False

    @property
    def turn_off_failed(self):
        """What to say when turn_off() returned False."""
        if self.unreachable:
            return f"Sent sleep, but then the Apple TV stopped answering ({self.unreachable})"
        return "Sent sleep, but the Apple TV still reports it's on"

    # -- now playing

    def _playing(self, playstatus):
        atv = self.atv
        app = None
        with contextlib.suppress(Exception):  # only a label
            app = atv.metadata.app.name if atv and atv.metadata.app else None
        info = playing_info(playstatus, app)
        self.playing, self.playing_at = info, time.monotonic()
        self.on_event("now_playing", info)
        if info["hash"] != self._artwork_hash:
            self._artwork_hash = info["hash"]
            if info["hash"] and atv:
                asyncio.ensure_future(self._artwork(atv, info["hash"]))
            else:
                self.on_event("artwork", None)

    async def _artwork(self, atv, wanted):
        try:
            art = await atv.metadata.artwork(width=256, height=256)
        except Exception:  # noqa: BLE001 - no artwork is fine
            art = None
        if wanted == self._artwork_hash:  # not a stale answer for an earlier item
            self.on_event("artwork", art.bytes if art else None)

    def now_playing(self):
        async def go(atv):
            return playing_info(await atv.metadata.playing())
        return self.loop.run(self._do(go))

    def seek(self, seconds):
        async def go(atv):
            await atv.remote_control.set_position(max(0, int(seconds)))
        self.loop.run(self._do(go))

    def start_stream(self, url):
        """Starts playing a URL over AirPlay; returns its task, done when playback ends. Connection
        problems are raised here, the stream's own failure is the task's exception."""
        async def start():
            await self._ensure()
            self.playing = None  # whatever played before isn't this stream: TRANSITIONING until it starts
            return asyncio.ensure_future(self.stream(url))
        return self.loop.run(start())

    def cancel(self, task):
        """Cancels a start_stream() task from any thread."""
        self.loop.loop.call_soon_threadsafe(task.cancel)

    def renderer(self):
        return AirPlayRenderer(self)


class AirPlayRenderer:
    """cast.Caster's renderer for the Apple TV: plays the cast server's URL over AirPlay. pyatv's play_url
    only returns when playback ends, so it runs as a task; state() reads it and now playing, in the
    DLNA transport states Caster already understands."""

    kinds = frozenset({"video", "audio"})  # pyatv has no AirPlay photo support

    def __init__(self, client):
        self.client = client
        self.url = None
        self.task = None

    def load(self, url, title, mime, kind, size):
        self.url = url

    def play(self):
        if self.url is None:  # resume after pause
            self._remote("play")
            return
        if not can_cast():
            raise RuntimeError(CAST_UNPATCHED)
        url, self.url = self.url, None
        self.task = self.client.start_stream(url)

    def pause(self):
        self._remote("pause")

    def stop(self):
        task, self.task = self.task, None
        if task is None or task.done():
            return
        try:
            self._remote("stop")  # AirPlay's own stop while it holds the stream
        finally:  # even if the box didn't answer: the stream mustn't outlive the cast
            self.client.cancel(task)

    def seek(self, seconds):
        self.client.seek(seconds)

    def _remote(self, method):
        self.client.remote(method)

    @property
    def error(self):
        """Why the last stream failed, for Caster's status line; None if it didn't."""
        task = self.task
        if task is None or not task.done() or task.cancelled() or task.exception() is None:
            return None
        from pyatv import exceptions as x

        if isinstance(task.exception(), x.AuthenticationError):  # tvOS answered 403 to /play
            return ("the Apple TV refused AirPlay from this remote: set its Settings › AirPlay and HomeKit › "
                    "Allow Access to Anyone on the Same Network, then Pair Apple TV again")
        return core.describe_error(task.exception())

    def state(self):
        task = self.task
        if task is None:
            return "STOPPED"
        if task.done():
            return "ERROR" if not task.cancelled() and task.exception() else "STOPPED"
        if self.client.push_error:  # no now playing: AirPlay holding the stream is all there is to go on
            return "PLAYING"
        state = (self.client.playing or {}).get("state") or ""
        return {"playing": "PLAYING", "paused": "PAUSED_PLAYBACK"}.get(state, "TRANSITIONING")

    def position(self):
        """From the last now-playing report, which the box pushes on every change (play, pause, seek), with
        the time since added while it plays: no request to the box on every poll."""
        info = self.client.playing
        if self.client.push_error or not info:
            return 0, 0
        pos, total = info.get("position") or 0, info.get("total") or 0
        if info.get("state") == "playing":
            pos += time.monotonic() - self.client.playing_at
        return (min(pos, total) if total else pos), total


def discover(timeout=6, hosts=()):
    """Apple TVs on the LAN: [{"name", "model", "host", "identifier"}]. Multicast (mDNS) first; if that
    finds none, the given addresses are asked directly: Wi-Fi often drops multicast, so a box can be
    reachable while it's invisible to a search."""
    pyatv = _pyatv()
    from pyatv.const import DeviceModel, OperatingSystem, Protocol

    async def go():
        loop = asyncio.get_running_loop()
        protocols = {Protocol.Companion, Protocol.AirPlay}
        found = await pyatv.scan(loop, timeout=timeout, protocol=protocols)
        wanted = [h for h in hosts if h]
        if wanted and not any(c.get_service(Protocol.Companion) for c in found):
            found = await pyatv.scan(loop, timeout=3, protocol=protocols, hosts=wanted)
        return found

    loop = LoopThread()
    try:
        confs = loop.run(go(), timeout=timeout + 10)
    finally:
        loop.stop()
    out = []
    for c in confs:
        if c.device_info.operating_system != OperatingSystem.TvOS or c.get_service(Protocol.Companion) is None:
            continue
        model = c.device_info.model_str if c.device_info.model != DeviceModel.Unknown else "Apple TV"
        out.append({"name": c.name, "model": model, "host": str(c.address), "identifier": c.identifier})
    return sorted(out, key=lambda d: d["name"].lower())


class Pairing:
    """Pairs this remote with one Apple TV: Companion (keys, apps, power) then AirPlay (now playing,
    casting). Each step shows a PIN on the TV; pin() takes it. Blocking, like AppleTV."""

    STEPS = ("companion", "airplay")

    def __init__(self, identifier, remote_name="Linux Remote", host=None):
        self.identifier = identifier
        self.host = host  # where discover() found it: asked directly first, as multicast may not get through
        self.remote_name = remote_name
        self.loop = LoopThread()
        self.conf: BaseConfig | None = None
        self.handler: PairingHandler | None = None
        self.credentials = {}
        self.step = None

    def begin(self, step):
        """Start pairing one protocol; the TV shows a PIN."""
        pyatv = _pyatv()
        from pyatv.const import Protocol

        protocol = {"companion": Protocol.Companion, "airplay": Protocol.AirPlay}[step]

        async def go():
            loop = asyncio.get_running_loop()
            if self.conf is None:
                found = []
                if self.host:
                    found = await pyatv.scan(loop, identifier=self.identifier, hosts=[self.host], timeout=3)
                found = found or await pyatv.scan(loop, identifier=self.identifier, timeout=6)
                if not found:
                    raise ConnectionError("That Apple TV isn't answering on the network any more")
                self.conf = found[0]
            if self.conf.get_service(protocol) is None:
                return False
            self.handler = await pyatv.pair(self.conf, protocol, loop, name=self.remote_name)
            await self.handler.begin()
            return True

        self.step = step
        return self.loop.run(go(), timeout=30)

    def pin(self, pin):
        """Finish the current step with the PIN from the TV; its credentials are kept."""
        digits = re.sub(r"\D", "", str(pin))
        if not digits:
            raise ValueError("Type the 4-digit PIN shown on the TV")

        async def go():
            handler, self.handler = self.handler, None
            if handler is None:
                raise RuntimeError("Start pairing first")
            try:
                handler.pin(int(digits))
                await handler.finish()
                if not handler.has_paired:
                    raise core.PairingRefused("The Apple TV didn't accept that PIN; try again")
                return handler.service.credentials
            finally:
                await handler.close()

        self.credentials[self.step] = self.loop.run(go(), timeout=30)

    def save(self, cfg):
        """Store the box and its credentials in the config and make the Apple TV the device in use."""
        sec = section(cfg)
        if sec.get("identifier") != self.identifier:  # a different box: its apps and keys aren't this one's
            sec["apps"] = []
            sec["credentials"] = {}
            sec.pop("favorites", None)
            clean_app_lists(sec)
        sec["identifier"] = self.identifier
        sec["credentials"] = {**sec["credentials"], **self.credentials}
        if self.conf is not None:
            sec["host"], sec["name"] = str(self.conf.address), self.conf.name
        cfg["device"] = "appletv"
        core.save_config(cfg)

    def close(self):
        if self.handler is not None:
            handler, self.handler = self.handler, None
            try:
                self.loop.run(handler.close(), timeout=3)
            except (OSError, RuntimeError):
                pass
        self.loop.stop()
