"""Desktop remote for Samsung Tizen TVs (2016+) and Apple TV boxes (tvOS, in appletv.py).
Samsung: keys over the TV's local WebSocket API, apps and power state over its REST API, power-on by
Wake-on-LAN, casting over DLNA (cast.py). Apple TV: Companion, MRP and AirPlay through pyatv.

GUI:  tv-remote [tvremote://open | tvremote://cast]   (Qt, in remote_qt.py; cast also opens TV Cast)
CLI:  tv-remote [--device samsung|appletv] COMMAND   (default: the device used last)
      tv-remote key KEY_VOLUP [KEY_MUTE ...] | apps | launch <name> | text <string> | on | off | info
      tv-remote cast <files or folders...> | mirror | playing
      tv-remote serve [--token] [port]   (HTTP bridge for e.g. an ESP32 remote, in bridge.py)
      tv-remote appletv scan | pair      (find Apple TVs; pair this remote with one)
      (key also takes short names: volup -> KEY_VOLUP; on an Apple TV also select, play_pause, ...)
"""

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NoReturn, Protocol

import websocket

# The launcher runs python -I (no script dir on sys.path), so add it for remote_qt.py and cast.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

WINDOWS = os.name == "nt"
APP_NAME = "Samsung & Apple TV Remote"  # what the app is called wherever people see it


def config_dir():
    """Where the settings live: %APPDATA%\\tv-remote on Windows, $XDG_CONFIG_HOME/tv-remote elsewhere."""
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "tv-remote"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tv-remote"


CONFIG = config_dir() / "config.json"
# Stand-ins for a TV that isn't set up yet (addresses reserved for documentation: RFC 5737, RFC 7042).
UNSET_HOST, UNSET_MAC = "192.0.2.10", "00:00:5e:00:53:01"
DEFAULTS = {"host": UNSET_HOST, "mac": UNSET_MAC, "name": "Windows Remote" if WINDOWS else "Linux Remote", "token": None, "apps": [],
            "device": "samsung"}
DEVICES = {"samsung": "Samsung TV", "appletv": "Apple TV"}  # "device" in the config: the one in use
# Popular apps and their publicly reported Tizen app IDs (community lists; Samsung publishes none). Some
# apps were given a new ID for 2020+ TVs, so every known ID is listed. 2021+ firmware ignores the websocket
# "installed apps" request, so each ID is probed over REST: only IDs the TV reports count as installed, and
# the TV's own name is shown. An app is identified by its key here, so it works with whichever ID a TV has.
CATALOG = [
    {"key": "netflix", "name": "Netflix", "ids": ["3201907018807", "11101200001"]},
    {"key": "youtube", "name": "YouTube", "ids": ["111299001912"]},
    {"key": "prime", "name": "Prime Video", "ids": ["3201910019365", "3201512006785"]},
    {"key": "disney", "name": "Disney+", "ids": ["3201901017640", "3202009021709", "3202204027038"]},
    {"key": "hulu", "name": "Hulu", "ids": ["3201601007625"]},
    {"key": "max", "name": "HBO Max", "ids": ["3202301029760", "3201601007230"]},
    {"key": "paramount", "name": "Paramount+", "ids": ["3201710014981", "3202110025305"]},
    {"key": "appletv", "name": "Apple TV", "ids": ["3201807016597"]},
    {"key": "peacock", "name": "Peacock", "ids": ["3202006020991"]},
    {"key": "youtubetv", "name": "YouTube TV", "ids": ["3201707014489"]},
    {"key": "tubi", "name": "Tubi", "ids": ["3201504001965"]},
    {"key": "pluto", "name": "Pluto TV", "ids": ["3201808016802"]},
    {"key": "tvplus", "name": "Samsung TV Plus", "ids": ["3202405035480"]},
    {"key": "spotify", "name": "Spotify", "ids": ["3201606009684"]},
    {"key": "sling", "name": "Sling TV", "ids": ["3201707014448"]},
    {"key": "plex", "name": "Plex", "ids": ["3201512006963"]},
    {"key": "crunchyroll", "name": "Crunchyroll", "ids": ["3202302030097"]},
    {"key": "espn", "name": "ESPN", "ids": ["3201708014618"]},
    {"key": "twitch", "name": "Twitch", "ids": ["3202203026841"]},
    {"key": "browser", "name": "Internet", "ids": ["3202010022079", "3201907018784"]},
    {"key": "youtubekids", "name": "YouTube Kids", "ids": ["3201611010983"]},
    {"key": "discovery", "name": "Discovery+", "ids": ["3201803015944"]},
    {"key": "xfinity", "name": "Xfinity Stream", "ids": ["3202006021105", "3201803015836"]},  # unverified
    {"key": "applemusic", "name": "Apple Music", "ids": ["3201908019041"]},
    {"key": "amazonmusic", "name": "Amazon Music", "ids": ["3201710014874"]},
    {"key": "siriusxm", "name": "SiriusXM", "ids": ["111399002220"]},
    {"key": "tiktok", "name": "TikTok", "ids": ["3202008021577"]},
    {"key": "xbox", "name": "Xbox", "ids": ["3202203026799"]},
    {"key": "geforcenow", "name": "GeForce NOW", "ids": ["3202102023078"]},
    {"key": "vudu", "name": "Fandango at Home (Vudu)", "ids": ["111012010001"]},
    {"key": "amc", "name": "AMC", "ids": ["3201811017337"]},
    {"key": "mlb", "name": "MLB", "ids": ["3201603008210"]},
]
CATALOG_BY_KEY = {app["key"]: app for app in CATALOG}
MAX_FAVORITES = 5  # app buttons on the remote; the rest are in All apps
DEFAULT_FAVORITES = ["netflix", "youtube", "prime", "disney", "hulu"]
DEFAULT_MENU = ["max", "paramount", "appletv", "peacock", "youtubetv", "tubi", "pluto", "tvplus", "spotify",
                "sling", "plex", "crunchyroll", "espn", "twitch", "browser"]
CUSTOM = "id:"  # key prefix of an app added by its ID (Advanced), e.g. "id:3201234567890"
APP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # goes into a URL path, so nothing else


class ConfigError(Exception):
    pass


class PairingRefused(RuntimeError):
    """The TV answered but won't take commands from this remote (prompt denied, or blocked)."""


# The TV pings every 6 s and answers our pings at once (measured 2026-10-19), so a link silent for
# PING_S, then for PING_S after our own ping, is dead: the TV vanished (power cut, network drop)
# without closing it. Without this, sends would vanish into the kernel buffer while "Connected" showed.
PING_S = 10
TURN_OFF_FAILED = "Sent the power key twice, but the TV still reports it's on"
# A TV that doesn't answer at its address is looked for on the LAN at most this often (seconds), so a TV
# that's really off doesn't cost a search on every check.
SEARCH_EVERY = 30
WAKE_FAILED = ("Sent the wake packet, but the TV didn't answer at {host}. If it's off, turn on Settings › General › "
               "Network › Expert Settings › Power On with Mobile; if it's on, check its address in the config.")


def now():
    """The clock the power waits run on: monotonic, so a clock change (NTP) can't stretch or cut them."""
    return time.monotonic()


def describe_error(e):
    """One line for a failure talking to the TV: urllib's reason rather than its wrapper, and never empty."""
    if isinstance(e, urllib.error.HTTPError):
        return f"the TV answered HTTP {e.code} ({e.reason})"
    return str(getattr(e, "reason", None) or e) or type(e).__name__


def load_config():
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        user = {}
    except json.JSONDecodeError as e:
        raise ConfigError(f"{CONFIG} isn't valid JSON ({e}).\nFix it, or delete it to start over "
                          "(the TV will ask to allow this remote again).") from e
    if not isinstance(user, dict):
        raise ConfigError(f"{CONFIG} must hold a JSON object; fix it or delete it to start over.")
    cfg.update(user)
    if cfg.get("device") not in DEVICES:
        cfg["device"] = "samsung"
    clean_app_lists(cfg)
    return cfg


SAMSUNG_NOT_SET_UP = (f"No Samsung TV is set up yet. Put your TV's address in {CONFIG}, e.g.\n"
                      '{"host": "192.168.1.50", "mac": "aa:bb:cc:dd:ee:ff"}\n'
                      "(the MAC address is only needed to turn the TV on; see the README's Setup section).\n"
                      "For an Apple TV, choose Set up Apple TV… in the remote, or run: tv-remote appletv pair")


def is_set_up(cfg, device):
    if device == "appletv":
        import appletv
        return appletv.is_set_up(cfg)
    return cfg.get("host") not in (None, "", UNSET_HOST)


def set_up_devices(cfg):
    return [d for d in DEVICES if is_set_up(cfg, d)]


def pick_device(cfg, wanted=None):
    """The device to use: wanted if given (it must be set up), else the one used last, else whichever is
    set up. ConfigError says how to set one up if none is."""
    if wanted is not None:
        if wanted not in DEVICES:
            raise ConfigError(f"Unknown device {wanted!r}: use {' or '.join(DEVICES)}")
        if not is_set_up(cfg, wanted):
            import appletv
            raise ConfigError(appletv.NOT_SET_UP if wanted == "appletv" else SAMSUNG_NOT_SET_UP)
        return wanted
    ready = set_up_devices(cfg)
    if cfg["device"] in ready:
        return cfg["device"]
    if ready:
        return ready[0]
    raise ConfigError(SAMSUNG_NOT_SET_UP)


class Client(Protocol):
    """What the window, CLI and bridge use of a device client: TV (Samsung) or appletv.AppleTV. cfg is
    this device's part of the config (all of it for Samsung), root the whole file save_config() writes.
    Device-only extras (TV.locate, AppleTV.now_playing and seek) are reached after checking kind."""

    kind: str
    cfg: dict
    root: dict
    host: str
    unreachable: str | None  # why the last power_state() said "off"
    apps_from_cache: bool  # the last scan_apps() couldn't ask and returned the saved list

    @property
    def turn_off_failed(self) -> str: ...  # what to say when turn_off() returned False
    def info(self, host=None) -> Any: ...
    def power_state(self) -> str: ...  # "on", "standby" or "off"
    def connect(self) -> Any: ...
    def resolve_key(self, name) -> str: ...
    def key(self, key) -> None: ...
    def text(self, s) -> None: ...
    def scan_apps(self) -> list: ...
    def probe_app(self, app_id) -> Any: ...
    def launch(self, app) -> None: ...
    def wake(self) -> None: ...
    def wait_until_on(self, timeout=25) -> bool: ...
    def turn_off(self) -> bool: ...
    def renderer(self) -> Any: ...  # for cast.Caster: cast.Renderer or appletv.AirPlayRenderer
    def close(self) -> None: ...


def make_client(cfg, device, on_event=lambda event, data: None) -> Client:
    """The client for a device: TV (Samsung) or appletv.AppleTV, which offer the same methods."""
    if device == "appletv":
        import appletv
        return appletv.AppleTV(cfg, on_event)
    return TV(cfg, on_event)


_save_lock = threading.Lock()  # the GUI saves app lists while a worker thread may save a scan


def save_config(cfg):
    with _save_lock:
        CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)  # a leftover could have looser permissions; O_CREAT keeps those
        # Created 0600, so the pairing token is never readable by others, not even until a chmod. (Windows
        # ignores the mode: there the user profile's own permissions keep %APPDATA% private.)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg, indent=2))
        for attempt in range(5):
            try:
                tmp.replace(CONFIG)
                return
            except PermissionError:  # Windows: antivirus or the search indexer has the file open for a moment
                if not WINDOWS or attempt == 4:
                    raise
                time.sleep(0.1)


# -- which apps the remote shows: "favorites" (its buttons, in order) and "menu_apps" (All apps) hold app
# keys: a CATALOG key, or CUSTOM + an app ID for apps added by ID, whose TV names are in "custom_apps".

def clean_app_lists(cfg):
    """Make the app lists usable whatever the config file holds: wrong types and unknown keys are dropped,
    duplicates removed, favorites capped at MAX_FAVORITES and never repeated in the menu. A config from
    before these lists keeps every installed app it showed, with the default favorites."""
    custom = []
    for a in cfg.get("custom_apps") if isinstance(cfg.get("custom_apps"), list) else []:
        if (isinstance(a, dict) and isinstance(a.get("appId"), str) and APP_ID_RE.fullmatch(a["appId"])
                and isinstance(a.get("name"), str) and all(c["appId"] != a["appId"] for c in custom)):
            custom.append({"appId": a["appId"], "name": a["name"]})
    cfg["custom_apps"] = custom
    known = set(CATALOG_BY_KEY) | {CUSTOM + a["appId"] for a in custom}

    def keys(name, default):
        value = cfg.get(name)
        if not isinstance(value, list):
            return list(default)
        return list(dict.fromkeys(k for k in value if isinstance(k, str) and k in known))

    first_run = not isinstance(cfg.get("menu_apps"), list)
    favorites = keys("favorites", DEFAULT_FAVORITES)[:MAX_FAVORITES]
    menu = [k for k in keys("menu_apps", DEFAULT_MENU) if k not in favorites]
    if first_run:  # nothing the remote listed before disappears
        menu += [k for k in CATALOG_BY_KEY if k not in favorites and k not in menu and installed_app(cfg, k)]
    cfg["favorites"], cfg["menu_apps"] = favorites, menu


def app_ids(key):
    """The IDs an app key can have on a TV."""
    if key.startswith(CUSTOM):
        return [key.removeprefix(CUSTOM)]
    return CATALOG_BY_KEY[key]["ids"] if key in CATALOG_BY_KEY else [key]  # an Apple TV bundle ID is its own key


def installed_app(cfg, key):
    """The app as the last scan found it on the TV ({"appId", "name"}), or None if it isn't installed."""
    ids = app_ids(key)
    return next((a for a in cfg.get("apps") or [] if isinstance(a, dict) and a.get("appId") in ids), None)


def app_name(cfg, key):
    """The TV's name for an installed app, otherwise ours."""
    if app := installed_app(cfg, key):
        return app["name"]
    if key.startswith(CUSTOM):
        return next((a["name"] for a in cfg["custom_apps"] if CUSTOM + a["appId"] == key), key)
    return CATALOG_BY_KEY[key]["name"] if key in CATALOG_BY_KEY else key


def key_for_id(app_id):
    """The key of the app with this ID: its CATALOG entry if it has one."""
    return next((app["key"] for app in CATALOG if app_id in app["ids"]), CUSTOM + app_id)


class TV:
    kind = "samsung"
    turn_off_failed = TURN_OFF_FAILED  # what to say when turn_off() returned False

    def __init__(self, cfg, on_event=lambda event, data: None):
        self.cfg = cfg
        self.root = cfg  # what save_config() writes (the Apple TV client's cfg is a part of it)
        self.on_event = on_event
        self.ws = None
        self.host = cfg["host"]  # where the TV is now: the configured (wired) address, or where locate() found it
        self.searched = 0.0  # when locate() last searched the LAN
        self.unreachable = None  # why the last power_state() couldn't reach the TV
        self.apps_from_cache = False  # the last scan_apps() got no answers and returned the saved list
        self.lock = threading.Lock()

    def info(self, host=None):
        with urllib.request.urlopen(f"http://{host or self.host}:8001/api/v2/", timeout=2) as r:
            return json.load(r)

    def power_state(self):
        """'on', 'standby' (network standby, screen off) or 'off' (unreachable: fully off, or a network
        problem; the reason is kept in self.unreachable). A TV that answers without PowerState counts as 'on'.
        A TV that doesn't answer at self.host is looked for elsewhere (locate()), e.g. on Wi-Fi once its
        cable is out."""
        try:
            info = self.info()
        except (OSError, ValueError) as e:  # unreachable, timed out, or not JSON
            self.unreachable = describe_error(e)
            info = self.locate()
            if info is None:
                return "off"
        self.unreachable = None
        device = self._remember(info)
        return device.get("PowerState", "on")

    def _is_this_tv(self, info):
        device = info.get("device") if isinstance(info, dict) else None
        if not isinstance(device, dict):
            return False
        if self.cfg.get("tv_id"):
            return device.get("id") == self.cfg["tv_id"]
        return bool(self.cfg.get("wifi_mac")) and str(device.get("wifiMac", "")).lower() == self.cfg["wifi_mac"].lower()

    def locate(self):
        """The TV's info from wherever it answers now, with self.host moved there; None if it's nowhere.
        Tries its other saved address (wired "host", last Wi-Fi "wifi_host"), then, at most every
        SEARCH_EVERY s, every address on the LAN for a TV with its id. Needs the id (or Wi-Fi MAC) saved
        by an earlier contact, so it never picks up some other TV."""
        if not (self.cfg.get("tv_id") or self.cfg.get("wifi_mac")):
            return None
        tried = {self.host}
        for host in (self.cfg["host"], self.cfg.get("wifi_host")):
            if isinstance(host, str) and host and host not in tried:
                tried.add(host)
                if found := self._try(host):
                    return found
        if time.monotonic() - self.searched < SEARCH_EVERY:
            return None
        self.searched = time.monotonic()
        prefix = self.cfg["host"].rsplit(".", 1)[0]  # assumes an IPv4 host on a /24 LAN, as wake() does
        hosts = [h for i in range(1, 255) if (h := f"{prefix}.{i}") not in tried]

        def listening(host):
            try:
                socket.create_connection((host, 8001), timeout=0.4).close()
                return True
            except OSError:
                return False

        with ThreadPoolExecutor(64) as ex:
            open_hosts = [h for h, ok in zip(hosts, ex.map(listening, hosts), strict=True) if ok]
        return next((found for h in open_hosts if (found := self._try(h))), None)

    def _try(self, host):
        try:
            info = self.info(host)
        except (OSError, ValueError):
            return None
        if not self._is_this_tv(info):
            return None
        self.host = host
        return info

    def _remember(self, info):
        """The device part of a reply, after saving what locate() needs to find this TV again: its id,
        Wi-Fi MAC (for wake() too) and, while it's on Wi-Fi, that address."""
        device = info.get("device") if isinstance(info, dict) else None
        if not isinstance(device, dict):
            return {}
        seen = {"tv_id": device.get("id"), "wifi_mac": str(device.get("wifiMac") or "").lower() or None}
        if device.get("networkType") == "wireless":
            seen["wifi_host"] = self.host
        seen = {k: v for k, v in seen.items() if isinstance(v, str) and v and self.cfg.get(k) != v}
        if seen:
            self.cfg.update(seen)
            try:
                save_config(self.cfg)
            except OSError:
                pass  # only a shortcut for next time
        return device

    def connect(self):
        try:
            return self._connect()
        except OSError:  # the TV may have moved, e.g. to Wi-Fi: reconnect wherever it answers now
            old = self.host
            if self.locate() is None or self.host == old:
                raise
            return self._connect()

    def _open_tls(self):
        """A TLS connection to the TV's port 8002, and the SHA-256 of the certificate it showed. The TV's
        certificate is self-signed, so no CA can vouch for it: _connect() pins the one it paired with."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # checked by fingerprint instead, before the token is sent
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # Python's default already, but said outright
        sock = socket.create_connection((self.host, 8002), timeout=8)
        try:
            tls = ctx.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise
        der = tls.getpeercert(binary_form=True) or b""
        return tls, hashlib.sha256(der).hexdigest()

    def _connect(self):
        name = base64.b64encode(self.cfg["name"].encode()).decode()
        url = f"wss://{self.host}:8002/api/v2/channels/samsung.remote.control?name={name}"
        # Reaching the TV gets 8 s, so a key press for a TV that's gone fails quickly.
        sock, fingerprint = self._open_tls()
        pinned = self.cfg.get("cert_sha256")
        # The token goes only to the TV it came from: one showing another certificate (another TV, a reset,
        # or something on the network posing as it) is asked to pair afresh, with the "Allow" prompt.
        sent_token = bool(self.cfg.get("token")) and (not pinned or hmac.compare_digest(pinned, fingerprint))
        if sent_token:
            url += f"&token={self.cfg['token']}"
        else:
            self.on_event("pairing", None)
        # The first message waits up to 35 s, which covers the on-screen "Allow" prompt (shown again even
        # with a token if the TV forgot this remote).
        try:
            ws = websocket.create_connection(url, timeout=8, socket=sock)
        except BaseException:
            sock.close()
            raise
        ws.settimeout(35)
        msg = json.loads(ws.recv())
        if msg.get("event") != "ms.channel.connect":
            ws.close()
            raise PairingRefused(f"The TV refused this remote ({msg.get('event')}). Accept the prompt on the TV, or "
                                 "allow it under Settings › General › External Device Manager › Device Connection Manager.")
        token = (msg.get("data") or {}).get("token")
        # A new token is pinned to the certificate it came over; so is a token paired before pinning was.
        if (token and token != self.cfg.get("token")) or (fingerprint != pinned and (token or sent_token)):
            self.cfg["token"], self.cfg["cert_sha256"] = token or self.cfg["token"], fingerprint
            try:
                save_config(self.cfg)
            except OSError as e:  # don't leave this socket open with nothing reading it
                ws.close()
                raise RuntimeError(f"Paired with the TV, but couldn't save its token to {CONFIG} ({e}); "
                                   "after the remote restarts, the TV will ask to allow it again") from e
        ws.settimeout(PING_S)
        old, self.ws = self.ws, ws
        if old is not None:
            old.abort()  # e.g. power() reconnecting after a wake: wakes its reader, which closes it
        threading.Thread(target=self._reader, args=(ws,), daemon=True).start()
        self.on_event("connected", None)
        return ws

    def _reader(self, ws):
        pinged = False
        while True:
            try:
                opcode, data = ws.recv_data(control_frame=True)  # pings/pongs count as signs of life
            except websocket.WebSocketTimeoutException:
                if pinged:  # silent through a whole ping interval: the TV is gone
                    break
                try:
                    ws.ping()
                except (websocket.WebSocketException, OSError):
                    break
                pinged = True
                continue
            except (websocket.WebSocketException, OSError):  # closed or dropped
                break
            pinged = False
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                break
            if opcode == websocket.ABNF.OPCODE_TEXT:
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue  # one garbled message isn't a dead connection
                if isinstance(msg, dict):
                    self.on_event(msg.get("event"), msg.get("data"))
        ws.shutdown()  # free the socket (other threads only abort(), which wakes us up)
        if self.ws is ws:
            self.ws = None
            self.on_event("disconnected", None)

    def _send(self, payload):
        data = json.dumps(payload)  # first: a bug here isn't a dead connection
        with self.lock:
            for attempt in (1, 2):
                ws = self.ws or self.connect()  # a failed connect is raised straight away
                try:
                    ws.send(data)
                    return
                except (websocket.WebSocketException, OSError):
                    # A failed send drops the socket (e.g. the TV slept); the second attempt
                    # reconnects, and a second failure is raised.
                    self.ws = None
                    ws.abort()  # its reader closes it; no "disconnected" since self.ws moved on
                    if attempt == 2:
                        raise

    @staticmethod
    def resolve_key(name):
        return name if name.startswith("KEY_") else "KEY_" + name.upper()

    def key(self, key):
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}})

    def text(self, s):
        """Types into the TV's on-screen keyboard (it must be open, e.g. a search box)."""
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": base64.b64encode(s.encode()).decode(), "DataOfCmd": "base64", "TypeOfRemote": "SendInputString"}})
        self._send({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}})

    def _app_url(self, app_id):
        return f"http://{self.host}:8001/api/v2/applications/{app_id}"

    def probe_app(self, app_id):
        """{"appId", "name"} if the TV has this app, None if it says it doesn't (404). Other failures raise."""
        if not APP_ID_RE.fullmatch(app_id):
            raise ValueError(f"{app_id!r} isn't an app ID (letters, digits, dots, dashes, underscores)")
        try:
            with urllib.request.urlopen(self._app_url(app_id), timeout=3) as r:
                return {"appId": app_id, "name": str(json.load(r)["name"])}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def scan_apps(self):
        """Installed apps from CATALOG and the apps added by ID (missing IDs answer 404), saved to config.
        An ID whose probe failed any other way (timeout while the TV wakes, odd reply) keeps its cached
        entry, so a slow TV can't make app buttons disappear; a scan that finds nothing keeps the whole
        cached list."""
        unknown = object()

        def probe(app_id):
            try:
                return self.probe_app(app_id)
            except (OSError, ValueError, KeyError, TypeError):  # timeouts, odd replies
                return unknown

        ids = [i for app in CATALOG for i in app["ids"]] + [a["appId"] for a in self.cfg.get("custom_apps") or []]
        ids = list(dict.fromkeys(ids))
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(probe, ids))
        cached = {a.get("appId"): a for a in self.cfg.get("apps") or [] if isinstance(a, dict)}
        found = []
        for app_id, result in zip(ids, results, strict=True):
            if result is unknown:
                if app_id in cached:
                    found.append(cached[app_id])
            elif isinstance(result, dict):
                found.append(result)
        self.apps_from_cache = not any(isinstance(r, dict) for r in results)
        if self.apps_from_cache:
            return self.cfg.get("apps") or []
        if found != self.cfg.get("apps"):
            self.cfg["apps"] = found
            save_config(self.cfg)
        return found

    def launch(self, app):
        # The websocket ed.apps.launch event is silently ignored on 2021+ firmware (this AU8000); a REST
        # POST to /api/v2/applications/<id> works.
        req = urllib.request.Request(self._app_url(app["appId"]), method="POST")
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"{app['name']} isn't installed on the TV any more "
                                   "(tv-remote apps refreshes the list)") from None
            raise

    def wake(self):
        """Magic packets for the configured MAC and the Wi-Fi one the TV reported, so it wakes on either."""
        macs = []
        for text in (self.cfg.get("mac"), self.cfg.get("wifi_mac")):
            try:
                mac = bytes.fromhex(text.replace(":", "").replace("-", ""))
            except (ValueError, AttributeError):
                continue
            if text != UNSET_MAC and len(mac) == 6 and mac not in macs:
                macs.append(mac)
        if not macs:
            raise RuntimeError(f'To turn the TV on, put its MAC address in {CONFIG} ("mac": "aa:bb:cc:dd:ee:ff"); '
                               "your router's list of connected devices shows it")
        broadcast = self.cfg["host"].rsplit(".", 1)[0] + ".255"  # assumes an IPv4 host on a /24 LAN
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                for mac in macs:
                    s.sendto(b"\xff" * 6 + mac * 16, (broadcast, 9))

    def wait_until_on(self, timeout=25):
        end = now() + timeout
        while now() < end:
            if self.power_state() == "on":
                return True
            time.sleep(1)
        return False

    def wait_until_off(self, timeout=10):
        """True once the TV reports standby or stops answering after KEY_POWER; False if it stays on
        (the key was lost, or opened a power menu instead)."""
        end = now() + timeout
        while now() < end:
            time.sleep(1)
            if self.power_state() != "on":
                return True
        return False

    def turn_off(self):
        """KEY_POWER, sent once more if the TV still reports on: it sometimes drops the first one (2 of 4
        tries on 2026-10-19). A press that works reaches standby within ~3 s, so the retry can't turn
        it back on. True once the TV reports off."""
        for timeout in (6, 10):
            self.key("KEY_POWER")
            if self.wait_until_off(timeout):
                return True
        return False

    def renderer(self):
        return None  # cast.Caster's default: the TV's DLNA renderer

    def close(self):
        ws, self.ws = self.ws, None
        if ws:
            ws.close(timeout=1)  # CLI exit: a clean close frame; the reader exits on the closed socket


def find_app(apps, name):
    name = name.lower()
    exact = [a for a in apps if a["name"].lower() == name]
    return (exact or [a for a in apps if name in a["name"].lower()] or [None])[0]


def find_launchable(tv, name):
    """An installed app by key (netflix, id:<appId>, an Apple TV bundle ID) or by the TV's name for it, or
    None. The CLI's launch and the bridge's /launch both look apps up this way."""
    cfg = tv.cfg
    if not cfg["apps"]:  # never scanned (a fresh config): look first, rather than say it isn't installed
        tv.scan_apps()  # saves what it finds in cfg["apps"]
    if tv.kind == "samsung" and (name in CATALOG_BY_KEY or name.startswith(CUSTOM)):
        return installed_app(cfg, name)
    return installed_app(cfg, name) or find_app(cfg["apps"], name)


MIRROR_HINT = ("Pick the TV in Windows' Cast panel, then Allow on the TV" if WINDOWS else
               "Pick the TV in Network Displays, then Allow on the TV")


def start_mirroring():
    """Opens GNOME Network Displays (Miracast over Wi-Fi Direct); the TV is picked there. On Windows,
    which has Miracast built in (sound included), it opens Windows' own Cast panel (Win+K) instead."""
    if WINDOWS:
        press_win_k()
        return
    # A local build patched to survive a shared window being resized, if there is one (tools/build-gnd.sh)
    patched = Path.home() / ".local" / "opt" / "gnd" / "bin" / "gnome-network-displays"
    exe = str(patched) if os.access(patched, os.X_OK) else shutil.which("gnome-network-displays")
    if not exe:
        raise RuntimeError("Mirroring needs GNOME Network Displays: sudo apt install gnome-network-displays")
    subprocess.Popen([exe], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def press_win_k():
    """Win+K: opens the Cast panel on Windows 10 and 11."""
    import ctypes
    user32 = ctypes.windll.user32  # pyright: ignore[reportAttributeAccessIssue] - Windows only
    vk_lwin, vk_k, keyup = 0x5B, 0x4B, 0x0002
    for vk, flags in ((vk_lwin, 0), (vk_k, 0), (vk_k, keyup), (vk_lwin, keyup)):
        user32.keybd_event(vk, 0, flags, 0)


def fatal(msg) -> NoReturn:
    if WINDOWS:  # pythonw has no console: a message box is the only way this is seen
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME, 0x10)  # pyright: ignore[reportAttributeAccessIssue]
    elif shutil.which("kdialog"):
        subprocess.run(["kdialog", "--title", APP_NAME, "--error", msg], check=False)
    sys.exit(msg)


def take_device(args):
    """--device NAME (or --device=NAME) out of the arguments: (device or None, the other arguments)."""
    device, rest, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a == "--device" and i + 1 < len(args):
            device, i = args[i + 1], i + 2
            continue
        if a.startswith("--device="):
            device = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return device, rest


def cli(args):
    if WINDOWS and sys.stdout:  # redirected output is cp1252 there: "★" and "·" mustn't crash a listing
        sys.stdout.reconfigure(errors="replace")  # pyright: ignore[reportAttributeAccessIssue]
    wanted, args = take_device(args)
    if not args:
        sys.exit(__doc__)
    try:
        cfg = load_config()
        if args[0] == "appletv":
            appletv_command(cfg, args[1:])
            return
        tv = make_client(cfg, pick_device(cfg, wanted),
                         lambda e, d: e == "pairing" and print("Accept the connection prompt on the TV…", file=sys.stderr))
    except ConfigError as e:
        sys.exit(str(e))
    except (OSError, RuntimeError, ValueError) as e:  # pairing or scanning an Apple TV
        sys.exit(describe_error(e))
    try:
        run_command(tv, tv.cfg, args[0], args[1:])
    except urllib.error.HTTPError as e:  # an OSError too, but the TV did answer
        sys.exit(f"The TV at {tv.host} refused the request: {describe_error(e)}")
    except (OSError, websocket.WebSocketException) as e:  # TV off or off the network: one line, not a traceback
        sys.exit(f"Couldn't reach {DEVICE_NOUN[tv.kind]} at {tv.host}: {describe_error(e)}")
    except (RuntimeError, ValueError) as e:  # reached it, but it failed (pairing refused, nothing to cast, ...)
        sys.exit(str(e))
    finally:
        time.sleep(0.2)  # let the last frame flush before closing
        tv.close()


DEVICE_NOUN = {"samsung": "the TV", "appletv": "the Apple TV"}


def appletv_command(cfg, rest):
    """tv-remote appletv scan | pair [address]: find Apple TVs, or pair this remote with one (PINs typed
    here). An address (or the one saved last) is asked directly when the network search finds nothing."""
    import appletv
    sub = rest[0] if rest else "pair"
    if sub not in ("scan", "pair"):
        sys.exit("usage: tv-remote appletv scan | pair [address]")
    print("Looking for Apple TVs…", file=sys.stderr)
    boxes = appletv.discover(hosts=[*rest[1:2], appletv.section(cfg).get("host")])
    if not boxes:
        sys.exit("No Apple TV answered. Check it's on and on this network; if it is, give its address "
                 "(Settings › Network on the Apple TV): tv-remote appletv pair <address>")
    for i, b in enumerate(boxes, 1):
        print(f"{i}. {b['name']}  ·  {b['model']}  ·  {b['host']}")
    if sub == "scan":
        return
    box = boxes[0]
    if len(boxes) > 1:
        choice = input(f"Pair with which one (1-{len(boxes)})? ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(boxes):
            sys.exit("No Apple TV picked.")
        box = boxes[int(choice) - 1]
    pairing = appletv.Pairing(box["identifier"], cfg.get("name") or DEFAULTS["name"], box["host"])
    try:
        for step, what in zip(pairing.STEPS, ("remote keys and apps", "now playing and casting"), strict=True):
            if not pairing.begin(step):
                print(f"{box['name']} doesn't offer {step}; skipped", file=sys.stderr)
                continue
            pairing.pin(input(f"PIN shown on the TV ({what}): "))
        pairing.save(cfg)
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nPairing cancelled; nothing was saved.")
    finally:
        pairing.close()
    print(f"Paired with {box['name']}. It's now the device in use; --device samsung reaches the Samsung TV.")


def run_command(tv, cfg, cmd, rest):
    if cmd == "key":
        for k in rest:
            tv.key(tv.resolve_key(k))
            time.sleep(0.15)
    elif cmd == "apps":
        apps = tv.scan_apps()
        if tv.apps_from_cache:
            print(f"{DEVICE_NOUN[tv.kind].capitalize()} didn't answer at {tv.host}; showing the saved list.",
                  file=sys.stderr)
        favorites = {a["appId"] for k in cfg["favorites"] if (a := installed_app(cfg, k))}
        for a in sorted(apps, key=lambda a: a["name"].lower()):
            print(f"{'★' if a['appId'] in favorites else ' '} {a['name']:<28} {a['appId']}")
    elif cmd == "launch":
        app = find_launchable(tv, " ".join(rest))
        if not app:
            sys.exit(f"No installed app matches {' '.join(rest)!r} (see: tv-remote apps)")
        tv.launch(app)
    elif cmd == "text":
        tv.text(" ".join(rest))
    elif cmd == "on":
        tv.wake()
        if tv.kind == "appletv":
            print("Apple TV is on" if tv.wait_until_on() else "Sent power on, but the Apple TV still reports it's asleep")
        else:
            print("TV is on" if tv.wait_until_on() else WAKE_FAILED.format(host=tv.host))
    elif cmd == "off":
        state = tv.power_state()
        apple = tv.kind == "appletv"
        if state == "on":
            if tv.turn_off():
                print("Apple TV is asleep" if apple else "TV turned off")
            else:
                print(tv.turn_off_failed)
        elif state == "standby":
            print("Apple TV is already asleep" if apple else "TV is already in standby")
        else:
            print(f"{'Apple TV' if apple else 'TV'} didn't answer at {tv.host} ({tv.unreachable}); "
                  "it may already be off")
    elif cmd == "info":
        print(json.dumps(tv.info()["device"], indent=2))
    elif cmd == "playing":
        if tv.kind != "appletv":
            sys.exit("Now playing is only reported by an Apple TV")
        info = tv.now_playing()
        if info["state"] in ("idle", "stopped") and not info["title"]:
            print("Nothing playing")
        else:
            where = f"{info['position'] or 0}/{info['total']} s" if info["total"] else ""
            print(" · ".join(x for x in (info["state"], info["title"], info["subtitle"], where) if x))
    elif cmd == "mirror":
        if tv.kind == "appletv":
            sys.exit("Mirroring is only for Samsung TVs (Miracast); Linux can't mirror to an Apple TV")
        start_mirroring()
    elif cmd == "serve":
        import bridge
        bridge.serve(tv, rest)
    elif cmd == "cast":
        import cast
        if tv.kind == "appletv":
            import appletv
            if not appletv.can_cast():
                sys.exit(appletv.CAST_UNPATCHED)
        # Find where the device is now first (the saved address may be old: a TV that moved to Wi-Fi, an
        # Apple TV given a new address), as the cast server only serves to that address.
        if tv.kind == "appletv":
            tv.connect()
        else:
            tv.power_state()  # moves tv.host to wherever it answers (locate())
        done = threading.Event()

        def on_status(text):
            print(text, file=sys.stderr)
            if caster.current is None:  # every way a queue ends (finished, stopped, unplayable) clears it
                done.set()

        caster = cast.Caster(tv.host, on_status, renderer=tv.renderer())
        try:
            caster.cast(rest)
            try:
                done.wait()
            except KeyboardInterrupt:
                caster.stop()
        finally:
            caster.close()
    else:
        sys.exit(__doc__)


def gui(cast_window=False):
    try:
        import remote_qt
    except ImportError as e:
        fatal(f"{APP_NAME} needs Qt for Python ({e.name}):\n" + (
              "run install.ps1 again" if WINDOWS else
              "sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg"))
    try:
        remote_qt.run(cast_window)
    except (ConfigError, remote_qt.InstanceError) as e:
        fatal(str(e))


if __name__ == "__main__":
    # tvremote:// links (the Homepage tile) open the remote; tvremote://cast also opens TV Cast.
    links = [a for a in sys.argv[1:] if a.startswith("tvremote:")]
    args = [a for a in sys.argv[1:] if a not in links]
    if args:
        cli(args)
    else:
        gui(cast_window=any(link.rstrip("/").endswith("cast") for link in links))
