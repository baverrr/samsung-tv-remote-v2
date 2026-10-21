#!/usr/bin/env python3
"""Sends this machine's sound to the TV while the screen is mirrored to it, except chat/call apps.

GNOME Network Displays creates a virtual output ("gnome_network_displays_...") and, only while a
session is streaming, records that output's monitor and sends it to the TV. Apps keep playing to
the default output (laptop speakers, headset) unless moved, so the TV would get silence. This
watches PipeWire: when the recording starts it makes the virtual output the default and moves the
playing streams there; when it stops it restores the previous default (or, if that output is gone,
the highest-priority real one) and moves whatever is still on the virtual output to it.

Streams from apps in keep_local_apps (Discord, call apps) or with a phone/communication/event/
notification media.role are kept on (or moved to) the output that was the default before mirroring,
so a Discord call keeps playing in the headset and isn't sent to the TV. The lists live in
~/.config/tv-remote/mirror-audio.json (created with the defaults on first use) and are re-read on
every routing pass while mirroring. Runs as the systemd user service tv-mirror-audio.service.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

SINK_PREFIX = "gnome_network_displays"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "tv-mirror-audio.json"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tv-remote" / "mirror-audio.json"
DEFAULT_CONFIG = {
    "_comment": "While mirroring, these stay on this machine's output instead of going to the TV. "
                "Apps: case-insensitive part of the app name, binary, app/Flatpak ID or command. "
                "Roles: media.role values. Both must be lists of strings.",
    "keep_local_apps": ["discord", "vesktop", "webcord", "zoom", "teams", "slack", "signal", "telegram",
                        "skype", "element", "whatsapp", "mumble", "teamspeak"],
    "keep_local_roles": ["phone", "communication", "event", "notification"],
}
RETRY_S = 5  # after a failed check (PipeWire restarting, pactl timing out)


class PactlError(RuntimeError):
    pass


def pactl(*args):
    """Run pactl and return its stdout; raises PactlError if it fails, so "couldn't ask" is never
    mistaken for "nothing there"."""
    try:
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10, check=False)
    except subprocess.TimeoutExpired as e:
        raise PactlError(f"pactl {' '.join(args)}: timed out") from e
    if r.returncode:
        raise PactlError(f"pactl {' '.join(args)}: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def pactl_json(what):
    out = pactl("-f", "json", "list", what)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise PactlError(f"pactl list {what}: unreadable JSON ({e})") from e
    if not isinstance(data, list):
        raise PactlError(f"pactl list {what}: expected a list")
    return data


def log(msg):
    print(msg, flush=True)


def string_list(cfg, key):
    """cfg[key] as lower-case strings, or the default if it isn't a list of non-empty strings
    (a bare string would match letter by letter, and "" would match every stream)."""
    value = cfg.get(key)
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return [v.strip().lower() for v in value]
    log(f"{CONFIG}: {key} must be a list of non-empty strings; using the built-in list")
    return list(DEFAULT_CONFIG[key])


def load_config():
    try:
        user = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        user = {}
    except json.JSONDecodeError as e:
        log(f"{CONFIG} is not valid JSON ({e}); using the built-in lists")
        user = {}
    if not isinstance(user, dict):
        log(f"{CONFIG} must hold a JSON object; using the built-in lists")
        user = {}
    cfg = {**DEFAULT_CONFIG, **user}
    return {"keep_local_apps": string_list(cfg, "keep_local_apps"),
            "keep_local_roles": string_list(cfg, "keep_local_roles")}


def executable(pid):
    """argv[0] of a process: identifies snaps/Flatpaks whose stream names are generic."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
    except (OSError, ValueError):
        return ""


def keep_local(stream, cfg):
    p = stream["properties"]
    if (p.get("media.role") or "").lower() in cfg["keep_local_roles"]:
        return True
    ids = [p.get("application.name"), p.get("application.process.binary"), p.get("application.id"),
           p.get("pipewire.access.portal.app_id")]
    if p.get("application.process.id"):
        ids.append(executable(p["application.process.id"]))
    ids = " ".join(filter(None, ids)).lower()
    return any(app in ids for app in cfg["keep_local_apps"])


def stream_label(stream):
    p = stream["properties"]
    return p.get("application.name") or p.get("node.name") or f"#{stream['index']}"


def mirror_sink(sinks):
    """Name of the Network Displays output whose monitor is being recorded (normally the streaming
    session; any recorder, e.g. a level meter, also counts), else None."""
    mirrors = [s for s in sinks if s["name"].startswith(SINK_PREFIX)]
    if not mirrors:
        return None
    monitors = {s["index"]: s["name"] for s in pactl_json("sources")}
    recording = {monitors.get(o["source"]) for o in pactl_json("source-outputs")}
    for s in mirrors:
        if s["monitor_source"] in recording:
            return s["name"]
    return None


def local_output(sinks, preferred):
    """preferred if it still exists, else the highest-priority real (non-Network Displays) output."""
    names = {s["name"] for s in sinks}
    if preferred in names:
        return preferred
    real = [s for s in sinks if not s["name"].startswith(SINK_PREFIX)]
    if not real:
        return None
    best = max(real, key=lambda s: int(s["properties"].get("priority.session") or 0))
    return best["name"]


def load_state():
    """{"active_sink": ..., "previous": ...} while mirroring, else {}."""
    try:
        state = json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        log(f"{STATE} unreadable ({e}); the output to restore will be picked by priority")
        return {}
    if not (isinstance(state, dict) and all(v is None or isinstance(v, str) for v in state.values())):
        log(f"{STATE} has an unexpected shape; ignoring it")
        return {}
    return state


def save_state(state):
    # Written to a temp file and renamed, so a crash mid-write can't leave a truncated file.
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


class Router:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = load_state()  # survives restarts mid-session

    def move(self, stream, sink):
        try:
            pactl("move-sink-input", str(stream["index"]), sink)
        except PactlError as e:  # usually the stream ended in the meantime
            log(f"  couldn't move {stream_label(stream)}: {e}")
            return False
        return True

    def route(self, sinks, tv_sink, local_sink):
        """TV for everything except keep-local streams, which go to (or stay on) local_sink."""
        cfg = load_config()
        index = {s["name"]: s["index"] for s in sinks}
        if local_sink not in index:
            log(f"  no local output to keep chat apps on ({local_sink!r} is gone)")
        for stream in pactl_json("sink-inputs"):
            local = keep_local(stream, cfg)
            target = local_sink if local else tv_sink
            if target in index and stream["sink"] != index[target] and self.move(stream, target):
                log(f"  {stream_label(stream)} -> {'this machine' if local else 'TV'}")

    def check(self):
        # Every pactl call raises on failure, so a failed check changes nothing and is retried.
        with self.lock:
            sinks = pactl_json("sinks")
            sink = mirror_sink(sinks)
            active = self.state.get("active_sink")
            if sink and sink != active:
                current = pactl("get-default-sink").strip()
                # If the default is already a Network Displays output (restart mid-session, or a new
                # session replacing one), keep the saved previous output instead of recording the TV.
                saved = self.state.get("previous") if current.startswith(SINK_PREFIX) else current
                previous = local_output(sinks, saved)
                # Record the output to restore before switching: if a step below fails after the default
                # has moved to the TV, the retry sees a Network Displays default and must find it here.
                self.state = {"active_sink": active, "previous": previous}
                save_state(self.state)
                pactl("set-sink-mute", sink, "0")
                pactl("set-sink-volume", sink, "100%")  # full level; turn it up/down with the TV's volume
                pactl("set-default-sink", sink)
                self.state = {"active_sink": sink, "previous": previous}
                save_state(self.state)
                log(f"Mirroring started: sound -> TV ({sink}); chat apps and the restore target: {previous}")
                self.route(sinks, sink, previous)
            elif sink:
                # New streams follow the default (the TV); send keep-local ones back, and pull over
                # any other app that pinned its own output.
                previous = local_output(sinks, self.state.get("previous"))
                self.route(sinks, sink, previous)
            elif active:
                target = local_output(sinks, self.state.get("previous"))
                if target is None:
                    log("Mirroring stopped, but there's no other output to send sound to")
                else:
                    pactl("set-default-sink", target)
                    mirror_indexes = {s["index"] for s in sinks if s["name"].startswith(SINK_PREFIX)}
                    for stream in pactl_json("sink-inputs"):
                        if stream["sink"] in mirror_indexes:
                            self.move(stream, target)
                    note = "" if target == self.state.get("previous") else " (the previous output is gone)"
                    log(f"Mirroring stopped: sound -> {target}{note}")
                self.state = {}
                save_state(self.state)


def main():
    router = Router()
    pending = threading.Event()
    pending.set()  # initial check, on the debounce thread so its failures are handled the same way

    def debounce():
        while True:
            pending.wait()
            # Coalesce the burst of events a session start/stop produces.
            while pending.wait(0.4):
                pending.clear()
            try:
                router.check()
            except Exception:  # noqa: BLE001 - if this thread died, the main loop would keep the service "running" but routing nothing (systemd wouldn't restart it); log and retry
                traceback.print_exc()
                log(f"check failed; retrying in {RETRY_S} s")
                time.sleep(RETRY_S)
                pending.set()

    threading.Thread(target=debounce, daemon=True).start()
    proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
    # sink: virtual output added/removed; source-output: recording started/stopped;
    # sink-input: new app streams; server: default output changed.
    for line in proc.stdout or ():
        if any(k in line for k in (" sink #", " source-output #", " sink-input #", " server")):
            pending.set()
    sys.exit(f"pactl subscribe exited ({proc.wait()})")  # systemd restarts us


if __name__ == "__main__":
    main()
