"""Cast local photos, videos and music to the TV's DLNA renderer (UPnP AVTransport).

A small HTTP server on this machine serves the file that's playing under an unguessable URL, to the
TV's address only; the TV is told to fetch and play it. A file's URL stops working once the queue moves
on or ends. Formats the TV can't decode as images (HEIC, WebP, GIF, ...) are converted to JPEG (GIFs
become their first frame; capped at 3840x2160).
"""

import html
import http.client
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8199  # fixed, so a firewall rule can allow it (ufw only lets the TV reach this one)
DMR_PORT = 9197  # from the TV's SSDP LOCATION http://TV:9197/dmr
LOAD_TIMEOUT_S = 20  # a file still not PLAYING this long after loading is taken as unplayable
MAX_POLL_FAILURES = 10  # watcher polls (1 s apart) in a row the TV may fail to answer before the queue ends
# MIME types exactly as the TV's ConnectionManager (GetProtocolInfo) advertises them, e.g. video/x-mkv,
# not video/x-matroska. They go into both Content-Type and the DIDL protocolInfo, and the TV refuses
# files whose type isn't on its list.
MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-mkv", ".webm": "video/webm",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
    ".ts": "video/vnd.dlna.mpeg-tts", ".m2ts": "video/vnd.dlna.mpeg-tts", ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg", ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/vnd.dlna.adts", ".flac": "audio/x-flac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/x-wav", ".wma": "audio/x-ms-wma",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".bmp": "image/bmp",
}
CONVERT_TO_JPEG = {".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".avif"}
UPNP_CLASS = {"image": "object.item.imageItem.photo", "video": "object.item.videoItem",
              "audio": "object.item.audioItem.musicTrack"}
# OP=01: byte-range seeking (no time seek). CI=0: not transcoded. FLAGS 0170...: streaming and
# background transfer modes, connection stall, DLNA 1.5.
DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in CONVERT_TO_JPEG:
        return "image"
    mime = MIME.get(ext)
    return mime.split("/")[0] if mime else None


def expand(paths):
    """Files as given, plus castable files anywhere inside any folders (recursive, sorted case-insensitively
    by path), skipping everything else and paths that don't exist."""
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted((f for f in p.rglob("*") if f.is_file() and kind_of(f)), key=lambda f: str(f).lower())
        elif p.is_file() and kind_of(p):
            out.append(p)
    return out


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def parse_time(s):
    """h:mm:ss[.fff] -> seconds; 0 for anything else (the TV reports NOT_IMPLEMENTED or "" for photos)."""
    try:
        h, m, sec = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except (ValueError, AttributeError):
        return 0


def byte_range(header, size):
    """(start, end, partial) for a Range header against a file of size bytes; None if unsatisfiable (416).
    Handles bytes=a-b, open-ended bytes=a- and suffix bytes=-n; anything else means the whole file."""
    m = re.match(r"bytes=(\d*)-(\d*)", header or "")
    if not (m and (m[1] or m[2])):
        return 0, size - 1, False
    if m[1]:
        start, end = int(m[1]), int(m[2]) if m[2] else size - 1
    else:  # suffix range: last N bytes
        start, end = max(0, size - int(m[2])), size - 1
    end = min(end, size - 1)
    return (start, end, True) if start <= end else None


def stream(out, path, start, length):
    """Copy length bytes of path from start to out. None when done, or when the TV hung up (it closes
    connections freely while seeking/buffering); otherwise why the file couldn't be read, since all
    the TV sees is a reply cut short."""
    name = Path(path).name
    try:
        with open(path, "rb") as f:
            f.seek(start)
            while length > 0:
                chunk = f.read(min(256 * 1024, length))
                if not chunk:
                    return f"{name} got shorter while it was playing"
                try:
                    out.write(chunk)
                except OSError:  # the TV hung up or stalled: its side, not the file's
                    return None
                length -= len(chunk)
    except OSError as e:  # the drive dropped out, or the file went away
        return f"couldn't read {name}: {e.strerror or e}"
    return None


class MediaServer:
    """Serves published files to the TV only, each under a token that's valid until revoked."""

    def __init__(self, tv_host):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((tv_host, DMR_PORT))  # no packets sent; just picks the interface that routes to the TV
            self.ip = s.getsockname()[0]
            self.allowed = {s.getpeername()[0]}  # the TV's address (tv_host may be a name)
        self.files = {}  # token -> (path, mime)
        self.read_error: str | None = None  # why the published file couldn't be streamed, until the next revoke()
        self.lock = threading.Lock()
        self.tmp = Path(tempfile.mkdtemp(prefix="tv-cast-"))
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_request(self, code="-", size="-"):
                pass  # no access log; errors still go to stderr via log_error

            def do_HEAD(self):
                self.serve(body=False)

            def do_GET(self):
                self.serve(body=True)

            def serve(self, body):
                if self.client_address[0] not in server.allowed:
                    self.send_error(403)  # only the TV may fetch; others on the LAN get nothing
                    return
                token = self.path.lstrip("/").split("/", 1)[0]
                with server.lock:
                    entry = server.files.get(token)
                if not entry:
                    self.send_error(404)  # unknown, or revoked when the queue moved on
                    return
                path, mime = entry
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    self.send_error(404, "File no longer exists")
                    return
                r = byte_range(self.headers.get("Range"), size)
                if r is None:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                start, end, partial = r
                self.send_response(206 if partial else 200)
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                length = end - start + 1
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("transferMode.dlna.org", "Interactive" if mime.startswith("image") else "Streaming")
                self.send_header("contentFeatures.dlna.org", DLNA_FEATURES)
                self.end_headers()
                if body:
                    err = stream(self.wfile, path, start, length)
                    if err:
                        self.log_error("%s", err)
                        with server.lock:
                            if token in server.files:  # not a stream of a file the queue already left
                                server.read_error = err

        try:
            self.httpd = ThreadingHTTPServer((self.ip, PORT), Handler)
        except OSError as e:
            # No fallback to another port: the firewall only lets the TV reach PORT, so the TV couldn't
            # fetch from it and the file would look unplayable.
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise RuntimeError(f"port {PORT} is in use (is another cast running?): {e.strerror or e}") from e
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def publish(self, path):
        """Returns (url, mime, size) for a castable file, converting images the TV can't show."""
        path = Path(path)
        if path.suffix.lower() in CONVERT_TO_JPEG:
            path = self._to_jpeg(path)
        mime = MIME[path.suffix.lower()]
        token = secrets.token_urlsafe(12)
        with self.lock:
            self.files[token] = (path, mime)
        name = urllib.parse.quote(path.name)
        return f"http://{self.ip}:{self.httpd.server_port}/{token}/{name}", mime, path.stat().st_size

    def _to_jpeg(self, src):
        out = self.tmp / f"{secrets.token_hex(6)}.jpg"
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", "-vf",
                                "scale='min(3840,iw)':'min(2160,ih)':force_original_aspect_ratio=decrease",
                                "-q:v", "2", str(out)], capture_output=True, text=True, timeout=60, check=False)
        except FileNotFoundError:
            raise RuntimeError(f"showing {src.suffix} photos needs ffmpeg: sudo apt install ffmpeg") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("converting it to JPEG took over 60 s") from None
        if r.returncode:
            lines = r.stderr.strip().splitlines()
            raise RuntimeError(f"couldn't convert it to JPEG: {lines[-1] if lines else f'ffmpeg exit {r.returncode}'}")
        return out

    def revoke(self):
        """Invalidate every published URL (and delete converted copies); downloads in progress finish."""
        with self.lock:
            entries, self.files = list(self.files.values()), {}
            self.read_error = None
        for path, _ in entries:
            if path.parent == self.tmp:
                path.unlink(missing_ok=True)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Renderer:
    """Minimal UPnP AVTransport client for the TV. Deliberately no RenderingControl: its volume is the
    DLNA renderer's own level (it read 0 and ignored KEY_VOLUP while the speakers played fine), not the
    TV's real volume, so volume goes through the websocket VOL keys instead."""

    def __init__(self, tv_host):
        self.url = f"http://{tv_host}:{DMR_PORT}/upnp/control/AVTransport1"

    def _soap(self, action, **args):
        body = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in {"InstanceID": 0, **args}.items())
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">{body}</u:{action}>'
                    '</s:Body></s:Envelope>')
        req = urllib.request.Request(self.url, envelope.encode(), method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            detail = re.search(r"<errorDescription>(.*?)</errorDescription>", e.read().decode(errors="replace"))
            if detail and detail[1] == "Transport is locked":  # a menu or prompt is open on the TV
                raise RuntimeError("the TV is busy. Close any menu or prompt on the TV, then try again") from None
            raise RuntimeError(f"TV rejected {action}: {detail[1] if detail else e}") from None

    @staticmethod
    def _field(xml, name):
        m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return html.unescape(m[1]) if m else ""

    def load(self, url, title, mime, kind, size):
        protocol = f"http-get:*:{mime}:{DLNA_FEATURES}"
        didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                f'<item id="0" parentID="-1" restricted="1"><dc:title>{html.escape(title)}</dc:title>'
                f'<upnp:class>{UPNP_CLASS[kind]}</upnp:class>'
                f'<res protocolInfo="{protocol}" size="{size}">{html.escape(url)}</res></item></DIDL-Lite>')
        self._soap("SetAVTransportURI", CurrentURI=url, CurrentURIMetaData=didl)

    def play(self):
        self._soap("Play", Speed=1)

    def pause(self):
        self._soap("Pause")

    def stop(self):
        self._soap("Stop")

    def seek(self, seconds):
        self._soap("Seek", Unit="REL_TIME", Target=fmt_time(max(0, seconds)))

    def state(self):
        """CurrentTransportState, or "ERROR" when the TV reports a failed transport (e.g. a file it can't decode)."""
        xml = self._soap("GetTransportInfo")
        if self._field(xml, "CurrentTransportStatus") == "ERROR_OCCURRED":
            return "ERROR"
        return self._field(xml, "CurrentTransportState")

    def position(self):
        xml = self._soap("GetPositionInfo")
        return parse_time(self._field(xml, "RelTime")), parse_time(self._field(xml, "TrackDuration"))


class Caster:
    """Plays a queue of files on the TV: videos/music advance when they finish, photos on a timer when
    slideshow is on."""

    def __init__(self, tv_host, on_status=lambda text: None, on_busy=lambda busy: None):
        self.host = tv_host
        self.server = None
        self.tv = Renderer(tv_host)
        self.on_status = on_status
        self.on_busy = on_busy  # True while files are being found, converted or loaded on the TV
        self.queue, self.index = [], -1
        self.slide_seconds = 8
        self.slideshow = True
        self.paused = False
        self.lock = threading.RLock()
        self._started_at = 0  # when the current file was loaded (or a photo resumed)
        self._seen_playing = self._near_end = False
        self._poll_failures = 0
        self._stop = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _clear(self):
        """End the queue; its URLs stop working."""
        self.queue, self.index = [], -1
        if self.server:
            self.server.revoke()

    @property
    def current(self):
        # One read of each: callers on other threads don't take the lock, and _clear() or cast() may
        # replace the queue between a bounds check and the subscript.
        queue, index = self.queue, self.index
        return queue[index] if 0 <= index < len(queue) else None

    def _has_next(self):
        return self.index + 1 < len(self.queue)

    def _timed(self):
        """True while a video or track (not a photo) is current: it has a position to seek in."""
        cur = self.current
        return cur is not None and kind_of(cur) != "image"

    def cast(self, paths):
        self.on_busy(True)  # scanning a big folder can take a while too
        try:
            files = expand(paths)
            if not files:
                raise ValueError("Nothing castable there (photos, videos or music)")
            with self.lock:
                self.queue = files
                self._play(0)  # on failure the queue is cleared again
        finally:
            self.on_busy(False)

    def _play(self, i):
        """Load file i on the TV. A file that can't be prepared (e.g. a photo ffmpeg can't convert) is
        skipped for the next one; if that was the last file, or the TV itself fails, the queue ends."""
        with self.lock:
            if self.server is None:
                self.server = MediaServer(self.host)
            skipped, path = [], self.queue[i]
            self.on_busy(True)
            try:
                while True:
                    path = self._reset(i)
                    self.on_status(f"Loading {path.name}…")
                    self.server.revoke()  # only the file being played stays reachable
                    try:
                        url, mime, size = self.server.publish(path)  # converts HEIC/WebP etc. first
                        break
                    except (RuntimeError, OSError) as e:
                        if i + 1 >= len(self.queue):
                            raise
                        skipped.append(f"{path.name} ({e})")
                        i += 1
                try:
                    self.tv.stop()  # the TV won't take a new URI over some playing states
                except RuntimeError:
                    pass
                self.tv.load(url, path.stem, mime, kind_of(path), size)
                self.tv.play()
            except Exception as e:
                # Don't leave a half-loaded file looking like it's playing: end the queue and say which file.
                self._clear()
                raise RuntimeError(f"Couldn't play {path.name}: {e}") from e
            finally:
                self.on_busy(False)
            if skipped:
                more = f" and {len(skipped) - 1} more" if len(skipped) > 1 else ""
                self.on_status(f"Skipped {skipped[0]}{more} · {self.describe()}")
            else:
                self.on_status(self.describe())

    def _reset(self, i):
        """Make file i current with fresh per-file state, so nothing carries over from the last file."""
        self.index = i
        self.paused = False
        self._started_at = time.time()
        self._seen_playing = self._near_end = False
        self._poll_failures = 0  # a hiccup during the last file doesn't count against this one
        return self.queue[i]

    def describe(self):
        cur = self.current
        if not cur:
            return "Nothing casting"
        return f"{self.index + 1}/{len(self.queue)} · {cur.name}"

    def next(self):
        with self.lock:
            if self._has_next():
                self._play(self.index + 1)

    def prev(self):
        with self.lock:
            if self.index > 0:
                self._play(self.index - 1)

    def toggle_pause(self):
        with self.lock:
            if not self.current:
                return
            if self.paused:
                self.tv.play()
            else:
                self.tv.pause()
            self.paused = not self.paused
            self._started_at = time.time()  # photo timer restarts on resume

    def seek_by(self, delta):
        with self.lock:
            if self._timed():
                pos, dur = self.tv.position()
                self._seek(pos + delta, dur)

    def seek_to(self, seconds):
        with self.lock:
            if self._timed():
                self._seek(seconds, self.tv.position()[1])

    def _seek(self, seconds, dur):
        if dur:
            seconds = min(seconds, dur - 1)  # the TV rejects targets past the end
        self.tv.seek(max(0, seconds))

    def stop(self):
        with self.lock:
            self._clear()
            try:
                self.tv.stop()
            except (RuntimeError, OSError) as e:  # the TV refused or didn't answer: say so, don't claim it stopped
                self.on_status(f"Stopped here, but the TV didn't confirm ({e})")
                return
            self.on_status("Stopped")

    def progress(self):
        """(position, duration) in seconds for the current video/track, else None."""
        if not self._timed():
            return None
        try:
            return self.tv.position()
        except (RuntimeError, OSError, http.client.HTTPException):  # the GUI polls again a second later
            return None

    def _watch(self):
        while not self._stop.wait(1):
            self._tick()

    def _tick(self):
        """One watcher poll: advance photos on the slide timer, and videos/tracks when the TV finishes them."""
        try:
            with self.lock:
                cur = self.current
                if not cur or self.paused:
                    return
                if kind_of(cur) == "image":
                    slide_due = self.slideshow and time.time() - self._started_at >= self.slide_seconds
                    if slide_due and self._has_next():
                        self._play(self.index + 1)
                    return
                state = self.tv.state()
                self._poll_failures = 0
                # STOPPED only ends a file after PLAYING was seen: the TV also reports STOPPED or
                # NO_MEDIA_PRESENT while it loads a new URI.
                if state == "PLAYING":
                    self._seen_playing = True
                    pos, dur = self.tv.position()
                    # Within 3 s of the end (polled once a second), or no duration known, counts as
                    # finished, so the STOPPED that follows advances instead of being read as a stop
                    # from the TV's own remote.
                    self._near_end = not dur or pos >= dur - 3
                elif state == "ERROR":  # a codec it can't play, or a stream that broke partway through
                    if self._seen_playing:
                        err = self.server and self.server.read_error  # this machine couldn't serve it?
                        self._skip(f"The TV hit an error playing {cur.name}" + (f" ({err})" if err else ""))
                    else:
                        self._skip(f"The TV couldn't play {cur.name}")
                elif state in ("STOPPED", "NO_MEDIA_PRESENT"):
                    if not self._seen_playing:
                        if time.time() - self._started_at > LOAD_TIMEOUT_S:
                            self._skip(f"The TV couldn't play {cur.name}")  # took the file, never started it
                    elif not self._near_end:  # stopped from the TV's own remote mid-file, or cut short
                        err = self.server and self.server.read_error
                        self._clear()
                        self.on_status(f"Stopped: {err}" if err else "Stopped on TV")
                    elif self._has_next():
                        self._play(self.index + 1)
                    else:
                        self._clear()
                        self.on_status("Finished")
        except Exception as e:  # noqa: BLE001 - the watcher thread must survive; the error is shown
            with self.lock:
                self._poll_failures += 1
                if self._poll_failures >= MAX_POLL_FAILURES and self.current:
                    # e.g. the TV was switched off mid-video: end the queue instead of retrying forever.
                    self._poll_failures = 0
                    self._clear()
                    self.on_status(f"Lost contact with the TV ({e}); casting stopped")
                    return
            self.on_status(f"Cast error: {e}")

    def _skip(self, reason):
        """Move past the current file the TV failed on, or end the queue if it was the last."""
        if self._has_next():
            self._play(self.index + 1)
            self.on_status(f"{reason}; skipped · {self.describe()}")
        else:
            self._clear()
            self.on_status(reason)

    def close(self):
        self._stop.set()
        if self.server:
            self.server.close()
