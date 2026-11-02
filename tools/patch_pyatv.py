"""Applies tools/pyatv-airplay.patch to the pyatv in this Python's site-packages without the `patch`
command, which Windows doesn't have (install.ps1 runs this with the venv's Python; install.sh uses
`patch`). All or nothing: the files it changes are put back if any part doesn't apply. Applied once:
a pyatv that already has the fix is left alone.

  python -I tools/patch_pyatv.py [site-packages folder]
"""

import logging
import shutil
import sys
import sysconfig
import tempfile
from pathlib import Path

import patch_ng

PATCH = Path(__file__).with_name("pyatv-airplay.patch")
MARK = "def decode_plist_body"  # what the patch adds to pyatv/support/http.py (appletv.can_cast looks for it)


def is_patched(site):
    try:
        return MARK in (Path(site) / "pyatv" / "support" / "http.py").read_text(encoding="utf-8")
    except OSError:
        return False


def apply(site):
    """'patched' or 'already'; RuntimeError (with every file as it was) if it doesn't apply."""
    site = Path(site)
    if not (site / "pyatv").is_dir():
        raise RuntimeError(f"pyatv isn't installed in {site}")
    if is_patched(site):
        return "already"
    patches = patch_ng.fromfile(str(PATCH))
    if not patches:
        raise RuntimeError(f"couldn't read {PATCH.name}")
    # git's a/ and b/ prefixes off, as `patch -p1` would. patch_ng drops them itself, but not for the
    # first file after the patch's header comment, so they're dropped here for every file.
    for item in patches.items:
        item.source, item.target = (n[2:] if n[:2] in (b"a/", b"b/") else n for n in (item.source, item.target))
    files = [site / item.target.decode() for item in patches.items]
    with tempfile.TemporaryDirectory() as tmp:
        saved = {}
        for i, f in enumerate(files):
            if f.exists():
                saved[f] = Path(tmp) / str(i)
                shutil.copy2(f, saved[f])
        try:
            ok = patches.apply(strip=0, root=str(site))
        except Exception as e:  # noqa: BLE001 - patch_ng raises a mix; any of them means "didn't apply"
            ok, why = False, e
        else:
            why = None
        if not ok or not is_patched(site):
            for f in files:
                if f in saved:
                    shutil.copy2(saved[f], f)
                else:
                    f.unlink(missing_ok=True)
            raise RuntimeError(f"{PATCH.name} didn't apply" + (f" ({why})" if why else ""))
    return "patched"


def main(args):
    logging.disable(logging.WARNING)  # patch_ng logs each hunk it can't place; the summary below says it
    site = args[0] if args else sysconfig.get_paths()["purelib"]
    try:
        result = apply(site)
    except (OSError, RuntimeError) as e:
        print(f"Note: {e}, so AirPlay casting to an Apple TV stays off.", file=sys.stderr)
        return 1
    if result == "patched":
        print(f"pyatv: patched for AirPlay casting ({Path(site) / 'pyatv'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
