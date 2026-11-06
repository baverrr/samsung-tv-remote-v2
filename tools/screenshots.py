"""Re-render docs/images/remote.png, cast.png, apps.png and remote-appletv.png offscreen, with the
TV and the Apple TV stubbed out (nothing is sent).

Run with the app's venv:  venv/bin/python -I tools/screenshots.py
The status lines show documentation addresses (192.0.2.20, 192.0.2.21), never a real TV's.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import appletv
import tv_remote as core

ATV_APPS = [("com.netflix.Netflix", "Netflix"), ("com.google.ios.youtube", "YouTube"),
            ("com.amazon.aiv.AIVApp", "Prime Video"), ("com.disney.disneyplus", "Disney+"),
            ("com.apple.TVWatchList", "TV"), ("com.apple.TVMusic", "Music"), ("com.plexapp.plex", "Plex")]
NOW_PLAYING = {"state": "playing", "title": "The Night Sky", "subtitle": "Nature Hour · S1 E4", "album": "",
               "position": 1312, "total": 3120, "app": "TV", "hash": None}
APPS = [("3201907018807", "Netflix"), ("111299001912", "YouTube"), ("3201601007625", "Hulu"),
        ("3201910019365", "Prime Video"), ("3201901017640", "Disney+"), ("3201606009684", "Spotify"),
        ("3201512006963", "Plex")]


def save(pixmap, path):
    if not pixmap.save(str(path)):  # QPixmap.save only reports failure by returning False
        sys.exit(f"couldn't write {path}")


def main():
    core.CONFIG = Path(tempfile.mkdtemp()) / "config.json"  # never the real config
    core.CONFIG.write_text(json.dumps({"host": "192.0.2.20", "mac": "00:00:5e:00:53:20", "token": "x",
                                       "apps": [{"appId": i, "name": n} for i, n in APPS]}))
    core.TV.power_state = lambda self: "on"
    core.TV.connect = lambda self: self.on_event("connected", None)
    core.TV.scan_apps = lambda self: self.cfg["apps"]
    appletv.AppleTV.power_state = lambda self: "on"
    appletv.AppleTV.connect = lambda self: self.on_event("connected", None)
    appletv.AppleTV.scan_apps = lambda self: self.cfg["apps"]
    appletv.AppleTV.typed = lambda self: "the night sky"
    appletv.can_cast = lambda: True  # shown as with tools/pyatv-airplay.patch applied

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFrame

    import remote_qt as rq

    app = QApplication([])
    app.setPalette(rq.palette())
    app.setStyleSheet(rq.stylesheet())
    window = rq.RemoteWindow(core.load_config())
    window.show()
    docs = APP / "docs" / "images"
    docs.mkdir(parents=True, exist_ok=True)

    def shoot():
        # The body, not the window: the offscreen screen is short, so the window would be clipped.
        body = window.findChild(QFrame, "body")
        assert body is not None
        save(body.grab(), docs / "remote.png")
        window.open_cast()
        QTimer.singleShot(500, finish)

    def finish():
        assert window.cast_window is not None
        save(window.cast_window.grab(), docs / "cast.png")
        window.cast_window.close()
        window.edit_apps()
        QTimer.singleShot(500, apps)

    def apps():
        assert window.apps_dialog is not None
        save(window.apps_dialog.grab(), docs / "apps.png")
        window.apps_dialog.reject()
        cfg = core.load_config()
        cfg["appletv"] = {"identifier": "doc", "name": "Living Room", "host": "192.0.2.21",
                          "credentials": {"companion": "x", "airplay": "x"},
                          "apps": [{"appId": i, "name": n} for i, n in ATV_APPS]}
        apple = rq.RemoteWindow(cfg, device="appletv")
        apple.show()
        holder.append(apple)
        apple.post("now_playing", NOW_PLAYING)
        apple.post("keyboard", True)  # a search box open on the TV, with something typed
        QTimer.singleShot(1500, shoot_apple)

    def shoot_apple():
        body = holder[0].findChild(QFrame, "body")
        assert body is not None
        pair = body.parentWidget()  # the Siri Remote and the panel beside it
        assert pair is not None
        save(pair.grab(), docs / "remote-appletv.png")
        app.quit()

    holder = []
    QTimer.singleShot(1500, shoot)  # let startup post "Connected" and build the app buttons
    app.exec()
    print(f"wrote remote.png, cast.png, apps.png and remote-appletv.png in {docs}")


if __name__ == "__main__":
    main()
