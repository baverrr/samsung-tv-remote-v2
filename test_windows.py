"""Tests for the Windows code paths: the config folder, mirroring through Windows' Cast panel, the error
message box, saving while Windows holds the file, revoking a cast file that's still being sent, and the
two helpers install.ps1 runs (tools/patch_pyatv.py and tools/make_icon.py). They run everywhere: the
Windows APIs are faked, so Linux CI checks these paths too, and the Windows job checks them for real
where it can (the instance pipe, in test_gui.py).
"""

import ctypes
import json
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import cast
import tv_remote as core

TOOLS = Path(__file__).resolve().parent / "tools"
sys.path.insert(0, str(TOOLS))


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(core, "WINDOWS", True)


@pytest.fixture
def user32(monkeypatch):
    """ctypes.windll.user32, recording each call as (name, args)."""
    calls = []

    class User32:
        def __getattr__(self, name):
            return lambda *args: calls.append((name, args))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=User32()), raising=False)
    return calls


def test_the_config_lives_in_appdata_on_windows(windows, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert core.config_dir() == tmp_path / "Roaming" / "tv-remote"


def test_the_config_follows_xdg_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert core.config_dir() == tmp_path / "tv-remote"
    monkeypatch.setenv("XDG_CONFIG_HOME", "")  # empty counts as unset (XDG spec)
    assert core.config_dir() == Path.home() / ".config" / "tv-remote"


def test_mirroring_on_windows_opens_the_cast_panel(windows, user32):
    core.start_mirroring()
    win, k, up = 0x5B, 0x4B, 0x0002
    assert user32 == [("keybd_event", (win, 0, 0, 0)), ("keybd_event", (k, 0, 0, 0)),
                      ("keybd_event", (k, 0, up, 0)), ("keybd_event", (win, 0, up, 0))]  # Win+K


def test_a_fatal_error_on_windows_is_a_message_box(windows, user32):
    with pytest.raises(SystemExit, match="broken"):
        core.fatal("broken")
    assert user32 == [("MessageBoxW", (None, "broken", core.APP_NAME, 0x10))]


def test_saving_waits_out_windows_holding_the_file(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    real, tries = Path.replace, []

    def busy(self, target):
        tries.append(target)
        if len(tries) < 3:
            raise PermissionError("in use by another process")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", busy)
    core.save_config({"token": "secret"})
    assert len(tries) == 3 and json.loads(core.CONFIG.read_text(encoding="utf-8")) == {"token": "secret"}


def test_saving_gives_up_if_the_file_stays_held(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    def busy(self, target):
        raise PermissionError("in use by another process")
    monkeypatch.setattr(Path, "replace", busy)
    with pytest.raises(PermissionError):
        core.save_config({})


def test_a_converted_photo_still_being_sent_is_deleted_later(tmp_path, monkeypatch):
    jpg = tmp_path / "abc.jpg"
    jpg.write_bytes(b"x")
    server: Any = SimpleNamespace(lock=threading.Lock(), files={"t": (jpg, "image/jpeg")}, read_error="old",
                             leftovers=[], tmp=tmp_path)
    real = Path.unlink

    def held(self, missing_ok=False):
        raise PermissionError("being sent")
    monkeypatch.setattr(Path, "unlink", held)
    cast.MediaServer.revoke(server)  # Windows won't delete it while the TV downloads it
    assert server.files == {} and server.read_error is None and server.leftovers == [jpg] and jpg.exists()
    monkeypatch.setattr(Path, "unlink", real)
    cast.MediaServer.revoke(server)
    assert server.leftovers == [] and not jpg.exists()


# -- install.ps1's helpers


@pytest.fixture
def site(tmp_path):
    """A copy of the installed (unpatched) pyatv, to patch."""
    pyatv = pytest.importorskip("pyatv")
    src = Path(pyatv.__file__).parent
    shutil.copytree(src, tmp_path / "pyatv", ignore=shutil.ignore_patterns("__pycache__"))
    if (tmp_path / "pyatv" / "support" / "http.py").read_text(encoding="utf-8").count("def decode_plist_body"):
        pytest.skip("this pyatv already has the AirPlay fix")
    return tmp_path


def test_the_pyatv_patch_applies_once(site, capsys):
    pytest.importorskip("patch_ng")
    import patch_pyatv
    assert patch_pyatv.main([str(site)]) == 0
    assert "patched for AirPlay casting" in capsys.readouterr().out
    assert patch_pyatv.is_patched(site)
    assert patch_pyatv.apply(site) == "already"
    assert not list(site.rglob("*.orig"))


def test_a_pyatv_the_patch_doesnt_fit_is_left_as_it_was(site, capsys):
    pytest.importorskip("patch_ng")
    import patch_pyatv
    drifted = site / "pyatv" / "protocols" / "raop" / "protocols" / "airplayv2.py"
    drifted.write_text(drifted.read_text(encoding="utf-8").replace("import", "imp0rt"), encoding="utf-8")
    before = {p: p.read_bytes() for p in site.rglob("*.py")}
    assert patch_pyatv.main([str(site)]) == 1
    assert "casting to an Apple TV stays off" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in site.rglob("*.py")} == before  # every file put back


def test_the_icon_has_every_windows_size(tmp_path):
    pytest.importorskip("PySide6.QtSvg")
    import make_icon
    from PySide6.QtWidgets import QApplication

    out = tmp_path / "tv-remote.ico"
    app = QApplication.instance()
    if app is None:
        make_icon.main([str(out)])
    else:  # pytest-qt's QApplication is already running: render with it
        out.write_bytes(make_icon.ico([make_icon.render(s) for s in make_icon.SIZES]))
    data = out.read_bytes()
    assert data[:4] == b"\0\0\1\0" and int.from_bytes(data[4:6], "little") == len(make_icon.SIZES)
    sides = [data[6 + 16 * i] or 256 for i in range(len(make_icon.SIZES))]
    assert sides == list(make_icon.SIZES)
    first = int.from_bytes(data[6 + 12:6 + 16], "little")
    assert data[first:first + 8] == b"\x89PNG\r\n\x1a\n"


def test_stylesheet_icon_paths_survive_windows_backslashes(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from pathlib import PureWindowsPath

    import remote_qt as rq
    monkeypatch.setattr(rq, "ICONS", PureWindowsPath(r"C:\Users\someone\tv-remote\icons"))
    urls = [u for u in rq.stylesheet().split("url(")[1:]]
    assert urls and all(u.startswith("C:/Users/someone/tv-remote/icons/") for u in urls)  # QSS eats "\"
