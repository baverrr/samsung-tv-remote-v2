"""HTTP bridge for devices that can't speak the TV's WebSocket API, e.g. an ESP32 touch screen running
ESPHome: `tv-remote serve` listens on the LAN and runs each request through the remote's client for the
device in use (tv_remote.TV, or appletv.AppleTV with --device appletv), so the device gets the same pairing,
Wi-Fi fallback, app list and confirmed power-off as the desktop remote.

  POST /key/<key>        a remote key: KEY_VOLUP or volup (an Apple TV also takes select, play_pause, ...)
  POST /launch/<app>     an app key (netflix, or an Apple TV bundle ID) or the TV's name for an installed
                         app (URL-encoded)
  POST /power/on         Wake-on-LAN (an Apple TV: power on over Companion); answers at once (202)
  POST /power/off        KEY_POWER until the TV reports standby, in the background (202)
  GET  /status           {"power": "on" | "standby" | "off"}

Every request needs "Authorization: Bearer <bridge_token>"; the token is generated into config.json on
the first run (`tv-remote serve --token` prints it). If "bridge_clients" lists addresses, only those may
connect. Replies are JSON: {"ok": true, ...} or {"error": "..."}.
"""

import hmac
import json
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import websocket

import tv_remote as core

PORT = 8198  # next to the cast server's 8199
KEY_RE = re.compile(r"[A-Za-z0-9_]{1,40}")


def ensure_token(cfg):
    """The bridge token from the config, generated and saved first if there isn't a usable one."""
    token = cfg.get("bridge_token")
    if not isinstance(token, str) or len(token) < 16:
        token = cfg["bridge_token"] = secrets.token_urlsafe(24)
        core.save_config(cfg)
    return token


def find_launchable(tv, name):
    """An installed app by key (netflix, id:<appId>, an Apple TV bundle ID) or by the TV's name for it, or None."""
    cfg = tv.cfg
    if tv.kind == "samsung" and (name in core.CATALOG_BY_KEY or name.startswith(core.CUSTOM)):
        return core.installed_app(cfg, name)
    return core.installed_app(cfg, name) or core.find_app(cfg["apps"] or tv.scan_apps(), name)


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Bridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tv, address):
        self.tv = tv
        self.token = ensure_token(tv.root)
        self.turning_off = threading.Lock()  # held while a power-off runs, so repeated presses don't stack
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: ESPHome's client reuses the connection

    @property
    def bridge(self) -> Bridge:
        return cast(Bridge, self.server)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        if int(self.headers.get("Content-Length") or 0):  # nothing takes a body; don't leave it unread
            self.rfile.read(int(self.headers["Content-Length"]))
        try:
            self._check_client()
            status, body = self._route(method, urllib.parse.urlsplit(self.path).path.strip("/").split("/"))
        except HTTPError as e:
            status, body = e.status, {"error": str(e)}
        except urllib.error.HTTPError as e:  # an OSError too, but the TV did answer
            status, body = 502, {"error": f"The TV refused the request: {core.describe_error(e)}"}
        except (OSError, websocket.WebSocketException) as e:
            status, body = 502, {"error": f"Couldn't reach the TV at {self.bridge.tv.host}: {core.describe_error(e)}"}
        except (RuntimeError, ValueError) as e:  # reached it, but it failed (e.g. pairing refused)
            status, body = 502, {"error": str(e)}
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check_client(self):
        clients = self.bridge.tv.root.get("bridge_clients")
        if clients and self.client_address[0] not in clients:
            raise HTTPError(403, "This address isn't in bridge_clients")
        expected = f"Bearer {self.bridge.token}".encode()
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected):
            raise HTTPError(401, "Missing or wrong bridge token")

    def _route(self, method, parts):
        tv = self.bridge.tv
        match method, parts:
            case "POST", ["key", key]:
                if not KEY_RE.fullmatch(key):
                    raise HTTPError(400, f"{key!r} isn't a key name")
                try:
                    key = tv.resolve_key(key)
                except ValueError as e:  # a key the Apple TV doesn't have
                    raise HTTPError(400, str(e)) from None
                tv.key(key)
                return 200, {"ok": True, "key": key}
            case "POST", ["launch", name]:
                name = urllib.parse.unquote(name)
                app = find_launchable(tv, name)
                if not app:
                    raise HTTPError(404, f"No installed app matches {name!r}")
                tv.launch(app)
                return 200, {"ok": True, "app": app["name"]}
            case "POST", ["power", "on"]:
                tv.wake()
                return 202, {"ok": True}
            case "POST", ["power", "off"]:
                if self.bridge.turning_off.acquire(blocking=False):
                    threading.Thread(target=self._turn_off, daemon=True).start()
                return 202, {"ok": True}
            case "GET", ["status"]:
                return 200, {"power": tv.power_state()}
        raise HTTPError(404, f"No such request: {method} /{'/'.join(parts)}")

    def _turn_off(self):
        try:
            if self.bridge.tv.power_state() == "on" and not self.bridge.tv.turn_off():
                print(core.TURN_OFF_FAILED, file=sys.stderr)
        except (OSError, websocket.WebSocketException, RuntimeError) as e:
            print(f"Power off failed: {core.describe_error(e)}", file=sys.stderr)
        finally:
            self.bridge.turning_off.release()

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} {format % args}", file=sys.stderr)


def serve(tv, args):
    """tv-remote serve [--token] [port]: run the bridge until stopped, or print the token."""
    if "--token" in args:
        print(ensure_token(tv.root))
        return
    ports = [a for a in args if a.isdigit()]
    port = int(ports[0]) if ports else PORT
    listen = tv.root.get("bridge_listen") or "0.0.0.0"
    try:
        server = Bridge(tv, (listen, port))
    except OSError as e:  # not the TV's fault, so not cli()'s "Couldn't reach the TV"
        raise RuntimeError(f"Couldn't listen on {listen}:{port}: {e.strerror or e}") from None
    print(f"TV bridge listening on {listen}:{port} (token: tv-remote serve --token)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
