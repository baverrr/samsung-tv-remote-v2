"""Re-render docs/remote.png and docs/cast.png offscreen, with the TV stubbed out (nothing is sent).

Run with the app's venv:  venv/bin/python -I tools/screenshots.py
The status line shows a documentation address (192.0.2.20), never a real TV's.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import tv_remote as core

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

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFrame

    import remote_qt as rq

    app = QApplication([])
    app.setPalette(rq.palette())
    app.setStyleSheet(rq.stylesheet())
    window = rq.RemoteWindow(core.load_config())
    window.show()
    docs = APP / "docs"
    docs.mkdir(exist_ok=True)

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
        app.quit()

    QTimer.singleShot(1500, shoot)  # let startup post "Connected" and build the app buttons
    app.exec()
    print(f"wrote {docs / 'remote.png'} and {docs / 'cast.png'}")


if __name__ == "__main__":
    main()
