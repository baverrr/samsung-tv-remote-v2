# Privacy Policy

**Samsung TV Remote** · Effective 21 October 2026

Samsung TV Remote ("the app") is a desktop program that runs entirely on your own computer
and talks only to the Samsung TV on your local network. It has no accounts, no servers, no
analytics and no advertising. The author never receives any information about you or your use
of the app.

## What the app collects

Nothing is collected or sent to the author or anyone else. There is no telemetry, crash
reporting or update check.

## What stays on your computer

The app keeps a few files in your home folder so that it works the next time you start it.
They're readable only by your user account:

| File | What's in it |
|---|---|
| `~/.config/tv-remote/config.json` | Your TV's network address and MAC address, the name the app pairs as, the **pairing token** the TV issued, and your app-button choices and the list of apps found on the TV. |
| `~/.config/tv-remote/mirror-audio.json` | The apps and sound roles that should stay on your own speakers or headset while mirroring (e.g. Discord, Zoom). |
| `~/.local/state/tv-mirror-audio.json` | Which sound output to switch back to when mirroring stops. |
| A temporary folder (`/tmp/tv-cast-*`) | JPEG copies of photos converted for casting. It's deleted when you stop casting or close the app. |

To remove all of it, delete those files (and the app). Unpairing on the TV itself is under the
TV's own device-connection settings.

## What goes over your network, and to whom

Everything stays on your local network, between your computer and your TV:

- **Remote keys, text and pairing:** sent to the TV over an encrypted WebSocket (port 8002).
  The pairing token is sent to the TV each time you connect.
- **Apps and power state:** the app asks the TV over HTTP (port 8001) which apps it has, and
  opens them.
- **Turning the TV on:** a Wake-on-LAN packet is broadcast on your local network.
- **Casting:** while you cast, the app runs a small web server on port 8199 that serves only
  the file currently playing, and only to your TV's address, under a random link that stops
  working when the file changes. The TV is controlled over DLNA (port 9197). Your file is read
  by the TV; it's not uploaded anywhere.
- **Screen mirroring:** handled by GNOME Network Displays, which sends your screen and sound to
  the TV over Wi-Fi Direct (Miracast). While a session is streaming, the app's audio helper
  reads the names of programs playing sound (from PipeWire and `/proc`) to decide which ones to
  send to the TV. That information never leaves your computer.

Your TV, and Samsung's services on it, have their own privacy practices. The app doesn't
control what the TV does with the commands, app list or media it receives.

## Children

The app doesn't collect personal information from anyone, including children.

## Downloading from GitHub

The source code is hosted on GitHub. Visiting or downloading from GitHub is covered by
[GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement),
not this policy.

## Changes

If the app's handling of data changes, this policy will be updated in the repository, with a
new effective date. Its history is visible in the repository's commits.

## Contact

Questions about this policy: open an issue on the project's GitHub repository.
