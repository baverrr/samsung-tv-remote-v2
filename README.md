# Samsung TV Remote (and Apple TV Remote)

[![Tests](https://github.com/baverrr/samsung-tv-remote-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/baverrr/samsung-tv-remote-v2/actions/workflows/tests.yml)
[![CodeQL](https://github.com/baverrr/samsung-tv-remote-v2/actions/workflows/codeql.yml/badge.svg)](https://github.com/baverrr/samsung-tv-remote-v2/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A desktop remote control for Linux and Windows, in two versions: one for Samsung TVs and one for Apple TV.

- **Samsung TV:** for Samsung Tizen smart TVs (2016 and later). It sends remote keys, opens apps, turns the
  TV on and off, casts photos, videos and music from this PC, and mirrors the screen.
- **Apple TV:** for Apple TV boxes (tvOS), with its own window drawn as a silver Siri Remote. It sends remote
  keys, opens any app on the box, types from this PC's keyboard into the TV as you type, sleeps and wakes
  it, shows **what's playing** (title, show, artwork and a seek bar), and casts videos and music from this
  PC over **AirPlay**.

> [!TIP]
> **You only need one of them.** The Apple TV remote works without any Samsung TV, and the Samsung
> remote without an Apple TV. Set up just the device you have:
>
> - **Only an Apple TV?** The first start offers **Set up Apple TV…**; pair it, and the remote opens
>   straight to the Siri Remote from then on. Nothing ever tries to reach a Samsung TV, and the command
>   line and the ESP32 bridge use the Apple TV without being told.
> - **Only a Samsung TV?** Put its address in the settings file and the remote is the Samsung one, as it
>   always was.
> - **Both?** Switch between them from the device menu at the bottom of the window; the remote reopens on
>   the one you used last.

Everything goes over the local network. There's a Qt window for everyday use and a command line for scripts.

Built for and tested against a 2021 Samsung AU8000 (Tizen, firmware platform 21_KANTSU2E) and an
Apple TV 4K (3rd generation, tvOS 27) on Ubuntu 26.04 with KDE Plasma on Wayland. Windows 10 and 11 are
supported too; there the tests and the installer run on every push, on GitHub's Windows runners.

<p align="center">
  <img src="docs/remote.png" alt="The remote window: power, status, source, mirror and cast buttons, a d-pad, volume and channel rockers, media keys, app shortcuts and a text box" width="300">
  &nbsp;&nbsp;
  <img src="docs/cast.png" alt="The TV Cast window: pick photos, videos, music or a folder, a now-playing line with a seek bar, playback controls and slideshow settings" width="300">
  &nbsp;&nbsp;
  <img src="docs/apps.png" alt="The Edit apps window: a list of apps with a tick for the five shown on the remote and whether each is installed, buttons to move, remove and add apps, and Save" width="300">
</p>

<p align="center">
  <img src="docs/remote-appletv.png" alt="The Apple TV remote: a silver Siri Remote with a power key, a black clickpad, Back, TV, Play/Pause and keyboard keys and a volume key, beside a panel with the status and Control Center key, a now-playing card with artwork, title, episode and a seek bar, skip back, AirPlay cast and skip ahead, a keyboard box showing a text field open on the TV, app shortcuts and the device menu" width="546">
</p>

> [!IMPORTANT]
> **Disclaimer.** This is an independent project. It isn't made, endorsed, sponsored or
> supported by Samsung Electronics or Apple Inc. "Samsung", "Tizen", "Apple TV", "tvOS", "AirPlay",
> "Siri" and related names are trademarks of their owners, used here only to say which devices the
> remote works with.
>
> - **Use at your own risk.** The remote is provided as is, without warranty (see the
>   [Terms and Conditions](TERMS.md)). Samsung and Apple can change their devices' software at any
>   time, which may stop some or all features from working.
> - **Use it only with TVs you own or are allowed to control.**
> - **Cast or mirror only content you have the right to show.**

## Features

**Both versions**

- **Switch devices in the window:** the device menu at the bottom (**Samsung TV ▾** / **Apple TV ▾**) switches
  the remote between your TVs, or sets up an Apple TV. The remote opens on the one you used last.
- **ESP32 bridge:** the same HTTP bridge drives either device (`tv-remote serve --device appletv`).

**Apple TV**

- **Siri Remote window:** switching to the Apple TV swaps the Samsung layout for a silver Siri Remote: power
  at the top, the black clickpad (its ring clicks up, down, left and right, its centre selects), Back and
  TV, Play/Pause beside the volume key, and a keyboard key where the real remote has Mute. A panel beside it
  holds the status, Control Center, what's playing, skip back and ahead, AirPlay cast, the keyboard and the
  apps. No keys the Apple TV doesn't have (channel, source, info).
- **Now playing:** title, show and episode or artist, the app, artwork, and a seek bar that follows the box
  and seeks when you drag it. It hides when nothing is playing.
- **Apps:** the box reports every app it has, so there's no list to maintain. Pick five for the buttons
  (**All apps › Edit apps…**); All apps has the rest, like the Apple TV's home screen.
- **Keyboard:** type on this PC and the text appears in the Apple TV's open text field (a search, a sign-in)
  as you type, Backspace and paste included. When the TV opens a text field the keyboard box says so, shows
  what the field already holds and takes your typing; Enter or Esc gives the keys back to the remote, and T
  or the remote's keyboard key brings you back. The × clears the TV's field.
- **Power:** sleep (confirmed) and wake over the network. No Wake-on-LAN or MAC address needed.
- **AirPlay cast:** play videos and music from this PC on the Apple TV, with the same queue, seek bar and
  controls as TV Cast.
- **Pairing:** once, from **Set up Apple TV…** or `tv-remote appletv pair`: the TV shows a PIN twice.

**Samsung TV**

- **Remote keys:** d-pad, OK, Back, Home, Menu, volume, channel, mute, source, info and media keys, with
  keyboard shortcuts. Held keys repeat.
- **Apps:** five app buttons plus an **All apps** menu, both your choice (**All apps › Edit apps…**, or
  right-click an app button). It starts with Netflix, YouTube, Prime Video, Disney+ and Hulu on the remote
  and 15 more popular apps in All apps. You can add about 30 popular apps, or any app by its ID under Advanced.
  The TV is asked which of them it has, and the ones it doesn't have stay hidden.
- **Typing:** send text to the TV's on-screen keyboard, e.g. a search box.
- **Power:** off with confirmation (it checks the TV really went to standby, and presses again if the TV
  dropped the first key); on via Wake-on-LAN.
- **TV Cast:** play photos, videos, music or whole folders on the TV over DLNA, with a queue, seek bar,
  next/previous, pause and an optional photo slideshow. HEIC, WebP, GIF, TIFF and AVIF photos are converted
  to JPEG on the fly. Drag and drop files onto the cast window.
- **Screen mirroring:** opens GNOME Network Displays (Miracast); on Windows, Windows' own Cast panel
  (Win+K), which sends the sound too. Share the whole screen or just one window,
  e.g. a browser: with the [patched build](#mirroring-one-window) you can resize the window or take a video
  full screen and the TV keeps playing, scaled to fit. A small background service sends the PC's sound to
  the TV while mirroring, but keeps chat and call apps (Discord, Zoom, Teams, …) on your headset.
- **Desktop integration:** a single window (launching again brings it forward), `tvremote://` links to open
  the remote or the cast window, and a "Cast Files to TV…" action in the app menu.
- **Command line:** every main action, scriptable.
- **ESP32 bridge:** `tv-remote serve` takes remote keys, app launches and power over plain HTTP, so an
  ESPHome device (e.g. a touch screen on the wall) can be a remote too. See [ESP32 bridge](#esp32-bridge).

## How it works

```mermaid
flowchart LR
    W[Remote window / CLI / ESP32 bridge] --> C{device in use}
    C -->|samsung| S[tv_remote.TV]
    C -->|appletv| A[appletv.AppleTV]
    S -->|WebSocket 8002, REST 8001, WoL| TV[Samsung TV]
    S -->|DLNA 9197| TV
    A -->|pyatv: Companion| ATV[Apple TV]
    A -->|pyatv: AirPlay + MRP| ATV
    TV & ATV -.->|fetch the cast file| F[cast server :8199]
```

Both clients offer the same methods (keys, text, apps, power, cast renderer), so the window, the CLI and the
bridge don't care which device they're driving.

**Apple TV**, through [pyatv](https://pyatv.dev):

| What | How |
|---|---|
| Finding it | mDNS (Bonjour). The box is remembered by its identifier, so a new DHCP address doesn't matter: the last address is tried first, then the LAN is searched. |
| Pairing | Companion, then AirPlay, each with a 4-digit PIN shown on the TV. The credentials are saved in `config.json`. |
| Keys, apps, typing, power | The Companion protocol: Siri Remote buttons, the box's app list and app launch, its text field, sleep and wake. |
| Now playing | MRP, tunnelled through AirPlay: the box pushes changes (play, pause, seek, new item); the window's clock runs in between. Artwork is fetched once per item. |
| AirPlay cast | The same cast server as TV Cast (TCP port **8199**, only to the Apple TV's address); the box is told to play the URL over AirPlay, and pause and seek go through MRP. Before it fetches the file the box syncs its clock with this PC on UDP port **8199**. tvOS 26 and later need an AirPlay fix that isn't in a pyatv release yet, so `install.sh` applies it (`tools/pyatv-airplay.patch`, from [pyatv#2846](https://github.com/postlund/pyatv/pull/2846)). |

**Samsung TV:**

| What | How |
|---|---|
| Keys and text | WebSocket on `wss://TV:8002`, channel `samsung.remote.control`. The first connection shows an **Allow** prompt on the TV; the pairing token it returns is saved. |
| Apps | REST on `http://TV:8001/api/v2/applications/<id>`. 2021+ firmware ignores the WebSocket "installed apps" and "launch app" requests, so each known app ID is probed (404 = not installed) and apps are opened with a POST. |
| Power state | `http://TV:8001/api/v2/` reports `PowerState` (on / standby); no answer means off or unreachable. |
| Power on | Wake-on-LAN magic packet to the LAN broadcast address. |
| Casting | The TV's DLNA renderer (UPnP AVTransport on port 9197). A small HTTP server on this PC (port **8199**) serves the file that's playing, only to the TV's address, under an unguessable link that stops working as soon as the queue moves on. |
| ESP32 bridge | `tv-remote serve`: a small HTTP server on port **8198**. Each request needs a bearer token and goes through the same TV client as the window, so the pairing token, Wi-Fi fallback, app list and confirmed power-off are shared. |
| Mirroring | GNOME Network Displays (Miracast over Wi-Fi Direct): the patched build in `~/.local/opt/gnd` if there is one, otherwise the system copy. `mirror_audio.py` watches PipeWire (`pactl subscribe`) and routes app audio while a session is streaming. On Windows, Mirror screen presses Win+K, Windows' built-in Miracast Cast panel. |

## Requirements

**Linux**

- Linux with Python 3.10 or newer and a desktop session. The tests run on Python 3.10–3.14 on every push;
  day-to-day use has only been on Python 3.14 with KDE Plasma (Wayland).
- Qt for Python (PySide6) from the distribution:
  `sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg`
- `websocket-client`, and `pyatv` for the Apple TV (both installed into a virtual environment below).
- Optional:
  - `ffmpeg`, to cast HEIC, WebP, GIF, TIFF and AVIF photos.
  - `gnome-network-displays`, for screen mirroring (and Docker, to build the patched copy that mirrors one
    window reliably).
  - `kdialog`, for start-up error pop-ups.

**Windows**

- Windows 10 or 11 with Python 3.10 or newer, from [python.org](https://www.python.org/downloads/windows/)
  or `winget install Python.Python.3.13`. `install.ps1` installs everything else (PySide6 too) into a
  virtual environment.
- Optional: `ffmpeg` (`winget install ffmpeg`), to cast HEIC, WebP, GIF, TIFF and AVIF photos. Mirroring is
  built into Windows.

**Both**

- The TV and this PC on the same LAN. Give the TV a fixed address (a DHCP reservation in the router).

## Install

### Linux

```sh
git clone https://github.com/baverrr/samsung-tv-remote-v2.git ~/.local/share/tv-remote
cd ~/.local/share/tv-remote
./install.sh            # add --audio for sound on the TV while mirroring, --bridge for the ESP32 bridge
```

`install.sh` installs for the current user only and needs no root. It:

- creates `venv/` in the checkout, which also sees the distribution's PySide6, and installs `websocket-client`
  and `pyatv` into it, with `tools/pyatv-airplay.patch` applied for AirPlay casting to tvOS 26 and later
  (it needs `patch`: `sudo apt install patch`; without it the remote works but AirPlay Cast stays off);
- installs the `tv-remote` launcher to `~/.local/bin`;
- adds the **TV Remote** menu entry, its "Cast Files to TV…" action and the `tvremote://` link handler;
- installs the app icon (`tvremote`);
- with `--audio`, installs and starts the `tv-mirror-audio` user service;
- with `--bridge`, installs and starts the `tv-remote-bridge` user service (see [ESP32 bridge](#esp32-bridge)).

The launcher and service point at the folder you ran the script from. If you move the checkout, run it
again. The templates it fills in are in `packaging/`.

### Mirroring one window

<p align="center">
  <img src="docs/mirroring.svg" alt="A browser window on the PC is sent to the TV over Miracast. Whatever its size, it is scaled to fit the TV's 1920×1080 picture with black bars: a wide window gets bars above and below, a tall one bars at the sides, and a 16:9 window fills the screen." width="760">
</p>

GNOME Network Displays 0.99 freezes the TV picture and sound when a window you're sharing changes size,
which includes a video in it going full screen. `tools/build-gnd.sh` builds a copy with
`tools/gnd-window-resize.patch` applied and installs it to `~/.local/opt/gnd`; **Mirror screen** and
`tv-remote mirror` then use it instead of the system copy:

```sh
tools/build-gnd.sh      # needs Docker; builds the installed gnome-network-displays version
```

It builds in an Ubuntu container matching your release, so no development packages are installed on your
system. Run it again after `gnome-network-displays` is upgraded. To go back to the system copy, delete
`~/.local/opt/gnd`.

What the patch changes:

- **The fix:** the PipeWire screen capture no longer drives the stream's clock. That clock stops while the
  compositor switches the stream to a new window size, and everything timed by it (picture and sound)
  waited for it forever.
- Square pixels before the encoder, so a window of any shape is fitted with black bars instead of being
  stretched, and the encoder isn't restarted on every resize.
- The capture no longer pauses the whole stream while it waits for a first frame at the new size, and
  frames are copied out of PipeWire's buffers as they arrive.
- A keyframe (a full picture) every second. The stock build sends one only every 10 seconds when the TV
  says it can ask for one, so after a few packets are lost on Wi-Fi the picture can stay frozen or smeared
  for several seconds; now it recovers within a second.
- A crash when GNOME Network Displays closes (`free(): invalid pointer`) is fixed.

**Firewall:** if you use a default-deny firewall, let the TV reach the cast server (`<TV address>` is your
TV's IP):

```sh
sudo ufw allow from <TV address> to any port 8199 proto tcp
```

An Apple TV also needs UDP on the same port, for the clock sync it does before playing (without it, AirPlay
Cast sits on "Loading"):

```sh
sudo ufw allow from <Apple TV address> to any port 8199 proto udp
```

### Windows

In PowerShell:

```powershell
git clone https://github.com/baverrr/samsung-tv-remote-v2.git "$env:LOCALAPPDATA\tv-remote"
cd "$env:LOCALAPPDATA\tv-remote"
powershell -ExecutionPolicy Bypass -File .\install.ps1     # add -Bridge for the ESP32 bridge
```

(No Git? Download the repository as a ZIP from GitHub and unpack it there.) `install.ps1` installs for the
current user only. It:

- creates `venv\` in the checkout and installs PySide6, `websocket-client` and `pyatv` into it, with the
  AirPlay fix applied by `tools/patch_pyatv.py` (Windows has no `patch` command; it uses `patch-ng`, all or
  nothing, once);
- adds **TV Remote** to the Start menu (it opens without a console window), with an icon made from
  `tv-remote.svg`;
- registers the `tvremote://` link handler;
- installs the `tv-remote` command (`%LOCALAPPDATA%\Programs\tv-remote\bin`, added to your PATH);
- with `-Bridge`, registers the **TV Remote bridge** scheduled task, which starts the bridge when you sign in;
- adds firewall rules that let the TV and network remotes reach this PC from the local network (TCP 8199,
  UDP 8199 for the Apple TV's clock sync, and TCP 8198 with `-Bridge`) if you run it as administrator.
  Otherwise it prints them to run once in an administrator PowerShell; or allow Python on **Private
  networks** when Windows asks the first time you cast.

Mirroring needs nothing more: **Mirror screen** opens Windows' Cast panel, where you pick the TV. Windows
sends the sound with it, so there's no audio helper. Run `install.ps1` again after moving the checkout.

### Updating

```sh
git -C ~/.local/share/tv-remote pull
```

On Windows, `git pull` in the checkout, then run `install.ps1` again.

Close and reopen the remote afterwards (launching it again only brings the open window forward). If
`gnome-network-displays` was upgraded, also run `tools/build-gnd.sh` from the checkout again.

## Setup

Settings live in `~/.config/tv-remote/config.json`, saved readable only by you (on Windows,
`%APPDATA%\tv-remote\config.json`, private to your Windows account).

**Set up only the devices you have.** Each section below stands on its own: with just an Apple TV, follow
[Apple TV](#apple-tv) and skip the Samsung part entirely (the device menu then lists Samsung TV as "not set
up"). With both, `device` in the file is the one the remote opens with (the last one used), and
`--device samsung` or `--device appletv` picks one on the command line.

### Apple TV

Open the remote and choose **Set up Apple TV…** (in the device menu at the bottom, or offered on the first
start when nothing is set up), or run:

```sh
tv-remote appletv pair
```

Pick your Apple TV from the list. It shows a PIN on screen twice: the first allows remote keys and apps, the
second now playing and AirPlay casting. Type each one in. The remote then switches to the Apple TV.

The Apple TV and this PC must be on the same LAN, and multicast (mDNS) must get between them. To pair again,
for example after removing the remote under the Apple TV's **Settings › Remotes and Devices**, use **Pair
Apple TV again…**. The pairing is saved under `appletv` in `config.json`: the box's identifier, name and last
address, the Companion and AirPlay credentials, and your app buttons (`favorites`, as bundle IDs).

### Samsung TV

Create `config.json` with your TV's details; the remote adds the pairing token and the app list to it:

```json
{
  "host": "192.168.1.50",
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "Linux Remote"
}
```

- **`host`:** the TV's IP address.
- **`mac`:** the TV's MAC address, used for Wake-on-LAN.
- **`name`:** what the TV calls this remote ("Linux Remote", or "Windows Remote" on Windows).

Until `host` is set (or an Apple TV is paired), the remote says how to set one up instead of starting.

**If the TV moves to Wi-Fi** (its cable is out, or it was switched over), the remote finds it there by
itself. Once it has reached the TV, it saves the TV's ID (`tv_id`), Wi-Fi MAC (`wifi_mac`) and Wi-Fi
address (`wifi_host`). When the TV doesn't answer at `host`, it tries `wifi_host`. If that fails too, it
checks every address on the LAN for a TV with that ID, at most every 30 seconds. `host` stays your wired
address, so the remote goes back to it when the cable is back in. Power-on sends the wake packet to both MACs.

On the first connection the TV asks to **Allow** the remote. If you denied it, allow it under
Settings › General › External Device Manager › Device Connection Manager.

For power-on, enable Settings › General › Network › Expert Settings › **Power On with Mobile**.

Your app choices are saved in the same file: `favorites` (the remote's buttons, in order), `menu_apps`
(All apps) and `custom_apps` (apps added by ID). The **Edit apps** window is the easy way to change them.

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

On the Apple TV remote, Enter is Select, H is TV (home), Space and P are Play/Pause, [ and ] skip back
and ahead, C opens Control Center, and T goes to the keyboard, which types on the TV as you type. M, Page
Up/Down, S and I do nothing (the Siri Remote has no such keys).

### Command line

```sh
tv-remote key volup mute            # keys, short (volup) or full (KEY_VOLUP) names
tv-remote apps                      # installed apps and their IDs (★ = on the remote)
tv-remote launch netflix            # open an app (exact name first, then partial)
tv-remote text "stranger things"    # type into the TV's on-screen keyboard
tv-remote on | off                  # power
tv-remote info                      # the TV's device details (JSON)
tv-remote cast ~/Pictures/Trip      # cast files or folders; Ctrl+C stops
tv-remote mirror                    # start screen mirroring
tv-remote serve                     # run the ESP32 bridge (port 8198); --token prints its token
```

Every command drives the device used last; `--device samsung` or `--device appletv` picks one. For the
Apple TV:

```sh
tv-remote appletv scan              # Apple TVs on the network
tv-remote appletv pair              # pair with one (type the PINs it shows)
tv-remote --device appletv key select menu play_pause   # KEY_* names work too
tv-remote --device appletv launch plex
tv-remote --device appletv playing  # what's on: state, title, show, position
tv-remote --device appletv cast ~/Videos/trip.mp4       # AirPlay; videos and music
```

Errors come back as one line saying what failed, e.g. "Couldn't reach the TV at …" or "Netflix isn't
installed on the TV any more".

### Mirroring tips

- **Whole screen or one window:** the screen-sharing prompt (KDE's or GNOME's) decides what the TV shows.
  Pick a screen to mirror everything, or a window (e.g. your browser) to show only that. A shared window
  keeps streaming while it's covered by other windows or minimized.
- **"Error" when connecting:** the TV may still be holding a previous mirroring session. Press **Home** on
  the TV (or switch source), then pick it again; restarting the TV clears a stuck Wi-Fi Direct session.
  Several TVs can show up in the list, so check you picked the right one.
- **Choppy picture:** first check the signal to the TV. The PC talks to the TV directly, not through your
  router, so a weak signal (worse than about -70 dBm) makes the picture freeze now and then, and the TV may
  drop the connection. While mirroring:

  ```sh
  for i in $(iw dev | awk '/Interface p2p-/{print $2}'); do iw dev "$i" station dump | grep 'signal:'; done
  ```

  Around -60 dBm is good. If it's weaker, move the PC closer to the TV or out from behind things in the way.

  Wi-Fi power saving on this PC's Wi-Fi card also makes the radio doze, and the TV link shares that radio.
  To turn it off for good with NetworkManager:

  ```sh
  printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/zz-wifi-powersave-off.conf
  sudo nmcli general reload conf
  ```

  It applies the next time Wi-Fi reconnects. To undo, delete that file and reload again.

  Some Wi-Fi drivers have power saving of their own that this doesn't reach. Realtek cards on the `rtw89`
  driver (e.g. the RTL8852BE) stall for a moment now and then, which shows as a freeze on the TV, and log
  `timed out to flush queues` (`journalctl -k`). To turn their power saving off:

  ```sh
  printf 'options rtw89_core disable_ps_mode=y\noptions rtw89_pci disable_aspm_l1=y disable_aspm_l1ss=y disable_clkreq=y\n' | sudo tee /etc/modprobe.d/rtw89-no-powersave.conf
  ```

  It applies after a reboot. To undo, delete that file and reboot. The [patched build](#mirroring-one-window)
  also gets the picture back within a second after a stall, instead of up to 10.

## ESP32 bridge

<p align="center">
  <img src="docs/esp32-remote.svg" alt="The TV remote screen on a 320×240 ESP32 touch display: a 3×3 block with Back, Up, Home, Left, OK, Right, Source, Down and Info; Power, Mute, Vol + / − and Ch + / − on the right; and Play, Pause, Netflix and YouTube along the bottom" width="480">
</p>

Microcontrollers like the ESP32 can't easily keep the TV's TLS WebSocket open. `tv-remote serve` gives
them plain HTTP on the LAN instead and does the TV side itself:

| Request | Does |
|---|---|
| `POST /key/<key>` | Sends a remote key: `volup` or `KEY_VOLUP` |
| `POST /launch/<app>` | Opens an installed app by key (`netflix`) or by the TV's name for it (URL-encoded) |
| `POST /power/on` | Wake-on-LAN. Answers at once (`202`) |
| `POST /power/off` | Presses power until the TV reports standby, in the background (`202`); pressing again meanwhile does nothing |
| `GET /status` | `{"power": "on"}`, `"standby"` or `"off"` |

The bridge drives the device in use when it starts; run it with `--device appletv` (or `samsung`) to fix one.
For an Apple TV, `/key/` also takes its own names (`select`, `menu`, `play_pause`, `control_center`), a key with
no Apple TV equivalent (mute, source, info) answers `400`, `/launch/` takes a bundle ID (`com.netflix.Netflix`) or an app name, and
`/power/on` wakes it over the network.

Replies are JSON: `{"ok": true, ...}`, or `{"error": "..."}` with `400` (bad key name), `401` (token),
`403` (address not allowed), `404` (unknown request or app) or `502` (the TV didn't answer or refused).

**Set it up:**

1. Run `./install.sh --bridge`. This starts the `tv-remote-bridge` user service
   (`journalctl --user -u tv-remote-bridge` for its log). On Windows, `install.ps1 -Bridge` registers the
   **TV Remote bridge** scheduled task instead, which starts it when you sign in.
2. Get the token with `tv-remote serve --token`. It's generated into `config.json` (`bridge_token`) the
   first time. Every request needs the header `Authorization: Bearer <token>`.
3. Optionally, limit which devices can connect. In `config.json`, `"bridge_clients": ["192.0.2.30"]`
   allows only those addresses, and `"bridge_listen"` picks the address to listen on (default: all).
4. With a default-deny firewall, let the device reach the port:

   ```sh
   sudo ufw allow from <ESP32 address> to any port 8198 proto tcp
   ```

The token travels unencrypted, like everything on plain HTTP. That's fine on a home LAN, but don't
forward the port to the internet.

**An ESPHome example:**

```yaml
http_request:
  timeout: 3s

script:
  - id: tv_send
    mode: queued
    parameters:
      path: string
    then:
      - http_request.post:
          url: !lambda return std::string("http://192.0.2.10:8198") + path;
          request_headers:
            Authorization: "Bearer change-me"

button:
  - platform: template
    name: TV Volume Up
    on_press:
      - script.execute: {id: tv_send, path: /key/volup}
```

The touch remote in the picture above is a "Cheap Yellow Display" (ESP32-2432S028R) build. It also
sends the IR power code (`remote_transmitter.transmit_samsung: {data: 0xE0E040BF}`) through an IR LED,
which turns on TVs that ignore Wake-on-LAN.

## Project layout

| File | What it is |
|---|---|
| `tv_remote.py` | Samsung TV client (WebSocket keys, REST apps and power, Wake-on-LAN), config, device choice, CLI, entry point |
| `appletv.py` | Apple TV client on pyatv (Companion, MRP, AirPlay), pairing, discovery, the AirPlay cast renderer |
| `remote_qt.py` | The Qt (PySide6) remotes (Samsung and Apple TV), cast window, Apple TV setup, design tokens and stylesheet |
| `cast.py` | Casting: the file server, the DLNA renderer client and the play queue (any renderer) |
| `bridge.py` | The ESP32 bridge: `tv-remote serve`'s HTTP server |
| `mirror_audio.py` | PipeWire audio routing while mirroring (standard library only, runs as a user service) |
| `test_gui.py`, `test_appletv.py`, `test_bridge.py`, `test_mirror_audio.py`, `test_windows.py` | Tests (the TV, the Apple TV, PipeWire and the Windows APIs are faked; nothing touches the network) |
| `install.sh`, `packaging/` | Per-user install: launcher, menu entry, icon, audio and bridge service templates |
| `install.ps1` | The Windows install: venv, Start menu shortcut, `tvremote://` links, `tv-remote` command, bridge task, firewall rules |
| `tools/patch_pyatv.py`, `tools/make_icon.py` | Used by `install.ps1`: applies the pyatv patch without `patch`, renders the `.ico` |
| `.gitattributes` | Keeps the patch and shell scripts at LF line endings on Windows checkouts |
| `tools/screenshots.py` | Re-renders the screenshots in `docs/` offscreen |
| `tools/build-gnd.sh`, `tools/gnd-window-resize.patch` | Builds GNOME Network Displays patched for one-window mirroring |
| `tools/pyatv-airplay.patch` | pyatv's unreleased AirPlay fix for tvOS 26+ ([pyatv#2846](https://github.com/postlund/pyatv/pull/2846)), applied by `install.sh` and `install.ps1` |
| `docs/` | README screenshots (`*.png`, including `remote-appletv.png`), the mirroring diagram (`mirroring.svg`) and the ESP32 remote screen (`esp32-remote.svg`) |
| `icons/` | Lucide icons used by the windows, and the Siri Remote's play/pause key (`play-pause.svg`, drawn for this project) |
| `tv-remote.svg` | App icon |

## Development

```sh
./venv/bin/pip install pytest pytest-qt ruff   # once (pyatv comes with install.sh)
QT_QPA_PLATFORM=offscreen ./venv/bin/python -I -m pytest -q
./venv/bin/ruff check .
pyright                                        # uses pyrightconfig.json
```

On Windows: `.\venv\Scripts\pip install pytest pytest-qt ruff`, then `$env:QT_QPA_PLATFORM = "offscreen";
.\venv\Scripts\python -I -m pytest -q`.

The tests run offscreen (no windows appear) against stubs, so they never talk to a real TV or change your
audio, and they use a temporary config.

## Known limitations

- **Wake-on-LAN is unreliable on some models.** On the AU8000 it's tested on, the TV drops off the network in
  standby and ignores magic packets, so turning it on usually needs the physical remote, or an IR LED on
  an [ESP32 remote](#esp32-bridge).
- **App IDs come from community lists.** Samsung doesn't publish them, and some apps have a different ID on
  2020 and later TVs. `CATALOG` in `tv_remote.py` lists every ID known for each app, and the TV is asked which
  one it has, so a wrong ID only means the app shows as "Not on this TV". Apps outside the catalog can be
  added by ID (Edit apps › Add apps… › Advanced) once the TV confirms it has them.
- **No volume over DLNA:** the renderer's own volume isn't the TV's real volume, so casting uses the remote's
  VOL keys instead.
- **Casting needs the remote running:** files stream from this PC, so closing the remote stops casting.
- **One-window mirroring needs the patched build:** the system GNOME Network Displays freezes when a shared
  window is resized (see [Mirroring one window](#mirroring-one-window)). Whole-screen mirroring works with
  either.
- **One TV of each kind:** settings hold one Samsung TV and one Apple TV.
- **Windows: tested on GitHub's runners so far,** not yet against a TV from a Windows PC. Windows mirroring
  is Windows' own Cast panel, so one-window mirroring and the audio routing of the Linux version don't apply.
- **Apple TV: AirPlay Cast relies on a patched pyatv** until a pyatv release includes
  [pyatv#2846](https://github.com/postlund/pyatv/pull/2846). If the patch isn't applied (for example a venv
  set up by hand), the AirPlay cast button is greyed out and says why; run `install.sh` again.
- **Apple TV: no photos over AirPlay.** pyatv streams video and audio only, so AirPlay Cast leaves photos out
  (and says so). Videos must be in a format the Apple TV plays (MP4/MOV with H.264 or HEVC; not MKV).
- **Apple TV: no mute or mirroring.** Mute isn't in the protocols the box offers (the Siri Remote mutes the TV
  over HDMI-CEC or IR), and Linux can't mirror the screen to an Apple TV.
- **Apple TV: typing replaces** the text in the box's open text field rather than adding to it: the keyboard
  sends the whole text after each pause in typing (120 ms), which is what tvOS's remote protocol takes.

## License

[MIT](LICENSE), copyright 2026 baverrr. The Lucide icons keep their ISC License
(`icons/LICENSE-lucide`).

## Privacy and terms

The remote collects nothing and talks only to your TV and Apple TV on your own network. What it keeps on your
computer, and what goes over the network, is in the [Privacy Policy](PRIVACY.md). Use of the app
is covered by the [Terms and Conditions](TERMS.md).

## Credits

Icons are from [Lucide](https://lucide.dev) (ISC License; see `icons/LICENSE-lucide`).
