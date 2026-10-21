# Samsung TV Remote

A desktop remote control for Samsung Tizen smart TVs (2016 and later), for Linux. It sends remote keys,
opens apps, turns the TV on and off, casts photos, videos and music from this PC, and mirrors the screen,
all over the local network. There's a Qt window for everyday use and a command line for scripts.

Built for and tested against a 2021 Samsung AU8000 (Tizen, firmware platform 21_KANTSU2E) on
Ubuntu 26.04 with KDE Plasma on Wayland.

<p align="center">
  <img src="docs/remote.png" alt="The remote window: power, status, source, mirror and cast buttons, a d-pad, volume and channel rockers, media keys, app shortcuts and a text box" width="300">
  &nbsp;&nbsp;
  <img src="docs/cast.png" alt="The TV Cast window: pick photos, videos, music or a folder, a now-playing line with a seek bar, playback controls and slideshow settings" width="300">
</p>

## Features

- **Remote keys:** d-pad, OK, Back, Home, Menu, volume, channel, mute, source, info and media keys, with
  keyboard shortcuts. Held keys repeat.
- **Apps:** buttons for the installed streaming apps (Netflix, YouTube, Prime Video, Disney+, …), found by
  asking the TV which known apps it has.
- **Typing:** send text to the TV's on-screen keyboard, e.g. a search box.
- **Power:** off with confirmation (it checks the TV really went to standby, and presses again if the TV
  dropped the first key); on via Wake-on-LAN.
- **TV Cast:** play photos, videos, music or whole folders on the TV over DLNA, with a queue, seek bar,
  next/previous, pause and an optional photo slideshow. HEIC, WebP, GIF, TIFF and AVIF photos are converted
  to JPEG on the fly. Drag and drop files onto the cast window.
- **Screen mirroring:** opens GNOME Network Displays (Miracast). A small background service sends the PC's
  sound to the TV while mirroring, but keeps chat and call apps (Discord, Zoom, Teams, …) on your headset.
- **Desktop integration:** a single window (launching again brings it forward), `tvremote://` links to open
  the remote or the cast window, and a "Cast Files to TV…" action in the app menu.
- **Command line:** every main action, scriptable.

## How it works

| What | How |
|---|---|
| Keys and text | WebSocket on `wss://TV:8002`, channel `samsung.remote.control`. The first connection shows an **Allow** prompt on the TV; the pairing token it returns is saved. |
| Apps | REST on `http://TV:8001/api/v2/applications/<id>`. 2021+ firmware ignores the WebSocket "installed apps" and "launch app" requests, so each known app ID is probed (404 = not installed) and apps are opened with a POST. |
| Power state | `http://TV:8001/api/v2/` reports `PowerState` (on / standby); no answer means off or unreachable. |
| Power on | Wake-on-LAN magic packet to the LAN broadcast address. |
| Casting | The TV's DLNA renderer (UPnP AVTransport on port 9197). A small HTTP server on this PC (port **8199**) serves the file that's playing, only to the TV's address, under an unguessable link that stops working as soon as the queue moves on. |
| Mirroring | GNOME Network Displays (Miracast over Wi-Fi Direct). `mirror_audio.py` watches PipeWire (`pactl subscribe`) and routes app audio while a session is streaming. |

## Requirements

- Linux with Python 3 and a desktop session. Only Python 3.14 on KDE Plasma (Wayland) has been tested.
- Qt for Python (PySide6) from the distribution:
  `sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg`
- `websocket-client` (installed into a virtual environment below).
- Optional:
  - `ffmpeg`, to cast HEIC, WebP, GIF, TIFF and AVIF photos.
  - `gnome-network-displays`, for screen mirroring.
  - `kdialog`, for start-up error pop-ups.
- The TV and this PC on the same LAN. Give the TV a fixed address (a DHCP reservation in the router).

## Install

```sh
git clone https://github.com/jeffbeaver7-afk/samsung-tv-remote-v2.git ~/.local/share/tv-remote
cd ~/.local/share/tv-remote
./install.sh            # add --audio for sound on the TV while mirroring
```

`install.sh` installs for the current user only and needs no root. It:

- creates `venv/` in the checkout, which also sees the distribution's PySide6, and installs `websocket-client`
  into it;
- installs the `tv-remote` launcher to `~/.local/bin`;
- adds the **TV Remote** menu entry, its "Cast Files to TV…" action and the `tvremote://` link handler;
- installs the app icon (`tvremote`);
- with `--audio`, installs and starts the `tv-mirror-audio` user service.

The launcher and service point at the folder you ran the script from. If you move the checkout, run it
again. The templates it fills in are in `packaging/`.

**Firewall:** if you use a default-deny firewall, let the TV reach the cast server (`<TV address>` is your
TV's IP):

```sh
sudo ufw allow from <TV address> to any port 8199 proto tcp
```

## Setup

Settings live in `~/.config/tv-remote/config.json`. The remote writes it when it first pairs with the TV
(or you can create it first). It holds the pairing token, so it's saved readable only by you. Set your
TV's details there:

```json
{
  "host": "192.168.1.50",
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "Linux Remote"
}
```

- **`host`:** the TV's IP address.
- **`mac`:** the TV's MAC address, used for Wake-on-LAN.
- **`name`:** what the TV calls this remote.

The code's defaults are placeholders (192.0.2.10 and 00:00:5e:00:53:01, addresses reserved for documentation), so set `host` and `mac`.

On the first connection the TV asks to **Allow** the remote. If you denied it, allow it under
Settings › General › External Device Manager › Device Connection Manager.

For power-on, enable Settings › General › Network › Expert Settings › **Power On with Mobile**.

Chat apps kept off the TV while mirroring are listed in `~/.config/tv-remote/mirror-audio.json`. It's
created with defaults (Discord, Zoom, Teams, Slack, Signal, …) and re-read while mirroring, so edits
apply without a restart.

## Usage

### Window

Open **TV Remote** from the app menu, or run `tv-remote`. `tvremote://open` and `tvremote://cast` links open
the remote, or the remote and the cast window.

| Key | Action |
|---|---|
| Arrow keys | Move |
| Enter | OK |
| Esc or Backspace | Back |
| H | Home |
| + / − | Volume |
| M | Mute |
| Page Up / Down | Channel |
| Space / P | Pause / Play |
| [ / ] | Rewind / Fast-forward |
| S | Source |
| I | Info |
| T | Type text |
| Tab | Move between on-screen keys |
| F11 | Full screen |
| ? | List the shortcuts |

### Command line

```sh
tv-remote key volup mute            # keys, short (volup) or full (KEY_VOLUP) names
tv-remote apps                      # installed apps and their IDs
tv-remote launch netflix            # open an app (exact name first, then partial)
tv-remote text "stranger things"    # type into the TV's on-screen keyboard
tv-remote on | off                  # power
tv-remote info                      # the TV's device details (JSON)
tv-remote cast ~/Pictures/Trip      # cast files or folders; Ctrl+C stops
tv-remote mirror                    # start screen mirroring
```

Errors come back as one line saying what failed, e.g. "Couldn't reach the TV at …" or "Netflix isn't
installed on the TV any more".

## Project layout

| File | What it is |
|---|---|
| `tv_remote.py` | TV client (WebSocket keys, REST apps and power, Wake-on-LAN), config, CLI, entry point |
| `remote_qt.py` | The Qt (PySide6) remote and cast windows, design tokens and stylesheet |
| `cast.py` | DLNA casting: the file server, the renderer client and the play queue |
| `mirror_audio.py` | PipeWire audio routing while mirroring (standard library only, runs as a user service) |
| `test_gui.py`, `test_mirror_audio.py` | Tests (the TV and PipeWire are faked; nothing touches the network) |
| `install.sh`, `packaging/` | Per-user install: launcher, menu entry, icon, audio service templates |
| `tools/screenshots.py` | Re-renders the screenshots in `docs/` offscreen |
| `icons/` | Lucide icons used by the windows |
| `tv-remote.svg` | App icon |

## Development

```sh
./venv/bin/pip install pytest pytest-qt ruff   # once
QT_QPA_PLATFORM=offscreen ./venv/bin/python -I -m pytest -q
./venv/bin/ruff check .
pyright                                        # uses pyrightconfig.json
```

The tests run offscreen (no windows appear) against stubs, so they never talk to a real TV or change your
audio, and they use a temporary config.

## Known limitations

- **Wake-on-LAN is unreliable on some models.** On the AU8000 it's tested on, the TV drops off the network in
  standby and ignores magic packets, so turning it on usually needs the physical remote.
- **The app list is a catalog** of known Tizen app IDs (`CATALOG` in `tv_remote.py`). Apps not in it don't
  appear, and a few IDs are unverified.
- **No volume over DLNA:** the renderer's own volume isn't the TV's real volume, so casting uses the remote's
  VOL keys instead.
- **Casting needs the remote running:** files stream from this PC, so closing the remote stops casting.
- **One TV:** settings hold a single TV.

## Credits

Icons are from [Lucide](https://lucide.dev) (ISC License; see `icons/LICENSE-lucide`).
