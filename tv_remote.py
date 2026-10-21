"""Desktop remote for Samsung Tizen TVs (2016+): keys over the TV's local WebSocket API, apps and
power state over its REST API, power-on by Wake-on-LAN, casting over DLNA (cast.py).

GUI:  tv-remote [tvremote://open | tvremote://cast]   (Qt, in remote_qt.py; cast also opens TV Cast)
CLI:  tv-remote key KEY_VOLUP [KEY_MUTE ...] | apps | launch <name> | text <string> | on | off | info
      tv-remote cast <files or folders...> | mirror
      (key also takes short names: volup -> KEY_VOLUP)
"""

import base64
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
from typing import NoReturn

import websocket

# The launcher runs python -I (no script dir on sys.path), so add it for remote_qt.py and cast.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tv-remote" / "config.json"
# Stand-ins for a TV that isn't set up yet (addresses reserved for documentation: RFC 5737, RFC 7042).
UNSET_HOST, UNSET_MAC = "192.0.2.10", "00:00:5e:00:53:01"
DEFAULTS = {"host": UNSET_HOST, "mac": UNSET_MAC, "name": "Linux Remote", "token": None, "apps": []}
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
WAKE_FAILED = ("Sent the wake packet, but the TV didn't answer at {host}. If it's off, turn on Settings › General › "
               "Network › Expert Settings › Power On with Mobile; if it's on, check its address in the config.")


def describe_error(e):
    """One line for a failure talking to the TV: urllib's reason rather than its wrapper, and never empty."""
    if isinstance(e, urllib.error.HTTPError):
        return f"the TV answered HTTP {e.code} ({e.reason})"
    return str(getattr(e, "reason", None) or e) or type(e).__name__


def load_config():
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        user = {}
    except json.JSONDecodeError as e:
        raise ConfigError(f"{CONFIG} isn't valid JSON ({e}).\nFix it, or delete it to start over "
                          "(the TV will ask to allow this remote again).") from e
    if not isinstance(user, dict):
        raise ConfigError(f"{CONFIG} must hold a JSON object; fix it or delete it to start over.")
    cfg.update(user)
    if cfg.get("host") in (None, "", UNSET_HOST):
        raise ConfigError(f"No TV is set up yet. Put your TV's address in {CONFIG}, e.g.\n"
                          '{"host": "192.168.1.50", "mac": "aa:bb:cc:dd:ee:ff"}\n'
                          "(the MAC address is only needed to turn the TV on; see the README's Setup section).")
    clean_app_lists(cfg)
    return cfg


_save_lock = threading.Lock()  # the GUI saves app lists while a worker thread may save a scan


def save_config(cfg):
    with _save_lock:
        CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)  # a leftover could have looser permissions; O_CREAT keeps those
        # Created 0600, so the pairing token is never readable by others, not even until a chmod.
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(json.dumps(cfg, indent=2))
        tmp.replace(CONFIG)


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
    return CATALOG_BY_KEY[key]["ids"] if key in CATALOG_BY_KEY else []


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
    return CATALOG_BY_KEY[key]["name"]


def key_for_id(app_id):
    """The key of the app with this ID: its CATALOG entry if it has one."""
    return next((app["key"] for app in CATALOG if app_id in app["ids"]), CUSTOM + app_id)


class TV:
    def __init__(self, cfg, on_event=lambda event, data: None):
        self.cfg = cfg
        self.on_event = on_event
        self.ws = None
        self.unreachable = None  # why the last power_state() couldn't reach the TV
        self.apps_from_cache = False  # the last scan_apps() got no answers and returned the saved list
        self.lock = threading.Lock()

    def info(self):
        with urllib.request.urlopen(f"http://{self.cfg['host']}:8001/api/v2/", timeout=2) as r:
            return json.load(r)

    def power_state(self):
        """'on', 'standby' (network standby, screen off) or 'off' (unreachable: fully off, or a network
        problem; the reason is kept in self.unreachable). A TV that answers without PowerState counts as 'on'."""
        try:
            info = self.info()
        except (OSError, ValueError) as e:  # unreachable, timed out, or not JSON
            self.unreachable = describe_error(e)
            return "off"
        self.unreachable = None
        device = info.get("device") if isinstance(info, dict) else None
        return (device if isinstance(device, dict) else {}).get("PowerState", "on")

    def connect(self):
        name = base64.b64encode(self.cfg["name"].encode()).decode()
        url = f"wss://{self.cfg['host']}:8002/api/v2/channels/samsung.remote.control?name={name}"
        if self.cfg.get("token"):
            url += f"&token={self.cfg['token']}"
        else:
            self.on_event("pairing", None)
        # The TV uses a self-signed cert. Reaching it gets 8 s, so a key press for a TV that's gone fails
        # quickly; its first message waits up to 35 s, which covers the on-screen "Allow" prompt (shown
        # again even with a token if the TV forgot this remote).
        ws = websocket.create_connection(url, timeout=8, sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False})
        ws.settimeout(35)
        msg = json.loads(ws.recv())
        if msg.get("event") != "ms.channel.connect":
            ws.close()
            raise PairingRefused(f"The TV refused this remote ({msg.get('event')}). Accept the prompt on the TV, or "
                                 "allow it under Settings › General › External Device Manager › Device Connection Manager.")
        token = (msg.get("data") or {}).get("token")
        if token and token != self.cfg.get("token"):
            self.cfg["token"] = token
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

    def key(self, key):
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}})

    def text(self, s):
        """Types into the TV's on-screen keyboard (it must be open, e.g. a search box)."""
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": base64.b64encode(s.encode()).decode(), "DataOfCmd": "base64", "TypeOfRemote": "SendInputString"}})
        self._send({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}})

    def _app_url(self, app_id):
        return f"http://{self.cfg['host']}:8001/api/v2/applications/{app_id}"

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
        text = self.cfg.get("mac") or UNSET_MAC
        try:
            mac = bytes.fromhex(text.replace(":", "").replace("-", ""))
        except (ValueError, AttributeError):
            mac = b""
        if text == UNSET_MAC or len(mac) != 6:
            raise RuntimeError(f'To turn the TV on, put its MAC address in {CONFIG} ("mac": "aa:bb:cc:dd:ee:ff"); '
                               "your router's list of connected devices shows it")
        packet = b"\xff" * 6 + mac * 16
        broadcast = self.cfg["host"].rsplit(".", 1)[0] + ".255"  # assumes an IPv4 host on a /24 LAN
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                s.sendto(packet, (broadcast, 9))

    def wait_until_on(self, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            if self.power_state() == "on":
                return True
            time.sleep(1)
        return False

    def wait_until_off(self, timeout=10):
        """True once the TV reports standby or stops answering after KEY_POWER; False if it stays on
        (the key was lost, or opened a power menu instead)."""
        end = time.time() + timeout
        while time.time() < end:
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

    def close(self):
        ws, self.ws = self.ws, None
        if ws:
            ws.close(timeout=1)  # CLI exit: a clean close frame; the reader exits on the closed socket


def find_app(apps, name):
    name = name.lower()
    exact = [a for a in apps if a["name"].lower() == name]
    return (exact or [a for a in apps if name in a["name"].lower()] or [None])[0]


def start_mirroring():
    """Opens GNOME Network Displays (Miracast over Wi-Fi Direct); the TV is picked there."""
    exe = shutil.which("gnome-network-displays")
    if not exe:
        raise RuntimeError("Mirroring needs GNOME Network Displays: sudo apt install gnome-network-displays")
    subprocess.Popen([exe], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fatal(msg) -> NoReturn:
    if shutil.which("kdialog"):
        subprocess.run(["kdialog", "--title", "TV Remote", "--error", msg], check=False)
    sys.exit(msg)


def cli(args):
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(str(e))
    tv = TV(cfg, lambda e, d: e == "pairing" and print("Accept the connection prompt on the TV…", file=sys.stderr))
    try:
        run_command(tv, cfg, args[0], args[1:])
    except urllib.error.HTTPError as e:  # an OSError too, but the TV did answer
        sys.exit(f"The TV at {cfg['host']} refused the request: {describe_error(e)}")
    except (OSError, websocket.WebSocketException) as e:  # TV off or off the network: one line, not a traceback
        sys.exit(f"Couldn't reach the TV at {cfg['host']}: {describe_error(e)}")
    except (RuntimeError, ValueError) as e:  # reached it, but it failed (pairing refused, nothing to cast, ...)
        sys.exit(str(e))
    time.sleep(0.2)  # let the last frame flush before closing
    tv.close()


def run_command(tv, cfg, cmd, rest):
    if cmd == "key":
        for k in rest:
            tv.key(k if k.startswith("KEY_") else "KEY_" + k.upper())
            time.sleep(0.15)
    elif cmd == "apps":
        apps = tv.scan_apps()
        if tv.apps_from_cache:
            print(f"The TV didn't answer at {cfg['host']}; showing the saved list.", file=sys.stderr)
        favorites = {a["appId"] for k in cfg["favorites"] if (a := installed_app(cfg, k))}
        for a in sorted(apps, key=lambda a: a["name"].lower()):
            print(f"{'★' if a['appId'] in favorites else ' '} {a['name']:<28} {a['appId']}")
    elif cmd == "launch":
        app = find_app(cfg["apps"] or tv.scan_apps(), " ".join(rest))
        if not app:
            sys.exit(f"No installed app matches {' '.join(rest)!r} (see: tv-remote apps)")
        tv.launch(app)
    elif cmd == "text":
        tv.text(" ".join(rest))
    elif cmd == "on":
        tv.wake()
        print("TV is on" if tv.wait_until_on() else WAKE_FAILED.format(host=cfg["host"]))
    elif cmd == "off":
        state = tv.power_state()
        if state == "on":
            print("TV turned off" if tv.turn_off() else TURN_OFF_FAILED)
        elif state == "standby":
            print("TV is already in standby")
        else:
            print(f"TV didn't answer at {cfg['host']} ({tv.unreachable}); it may already be off")
    elif cmd == "info":
        print(json.dumps(tv.info()["device"], indent=2))
    elif cmd == "mirror":
        start_mirroring()
    elif cmd == "cast":
        import cast
        done = threading.Event()

        def on_status(text):
            print(text, file=sys.stderr)
            if caster.current is None:  # every way a queue ends (finished, stopped, unplayable) clears it
                done.set()

        caster = cast.Caster(cfg["host"], on_status)
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
        fatal(f"TV Remote needs Qt for Python ({e.name}):\n"
              "sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg")
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
