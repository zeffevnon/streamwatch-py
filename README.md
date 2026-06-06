# streamwatch

A desktop app for Windows and Linux that watches livestream URLs and notifies you when they go live. Supports YouTube, Twitch, Chaturbate, Kick, and anything else yt-dlp can handle.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

## Features

- **Desktop notifications** with Record / Dismiss buttons — click Record and it starts immediately
- **Auto-record** mode — recording starts the moment a stream goes live, no click needed
- **Parallel polling** — all streams checked simultaneously, first poll is fast
- **Recording management** — start/stop from the GUI, auto-restart if a CDN drop kills the stream
- **Output path resilience** — if a stream's output folder is on an unavailable network drive, the app starts cleanly and shows ⚠ Path missing in the Watching tab instead of crashing
- **System tray** — close button hides to tray (optional, toggle in Settings); right-click for Show / Quit
- **Run at startup** — optional startup entry (toggle in Settings); creates a `.lnk` on Windows or a `.desktop` file on Linux
- **Auto-update banner** — checks for new commits on launch and shows a one-click Update & Restart stripe if an update is available
- **One-click updates** — Update All in Maintenance pulls the latest code and upgrades dependencies
- **Quick Tools** — one-off record or download without adding a stream to your list

## Requirements

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [streamlink](https://streamlink.github.io/) (for "Open in Player")
- A media player — PotPlayer is auto-detected, or set a custom path in Settings

Python dependencies (`customtkinter`, `Pillow`, `PyYAML`, `pystray`, platform notification lib) are installed automatically on first launch. If you prefer to install them manually:

```
pip install -r requirements.txt
```

## Setup

1. Copy `streams.yaml.example` → `streams.yaml` and add your streams:

```yaml
streams:
- name: MyStreamer
  url: https://www.youtube.com/@MyStreamer/live
  output: D:\recordings\MyStreamer
  # icon: C:\path\to\profile.jpg   # optional
  # auto_record: true              # start recording immediately
```

2. Copy `settings.json.example` → `settings.json` and adjust paths if needed (or just configure everything through the Settings tab after launch).

## Running

Double-click `streamwatch.bat` (Windows) or run `./streamwatch.sh` (Linux), or launch directly:

```
pythonw gui.pyw    # Windows
python3 gui.pyw    # Linux
```

The app minimizes to the system tray when you close the window. Use **Quit** from the tray icon to fully exit.

## Tabs

| Tab | What it does |
|---|---|
| **Watching** | Live status for all streams, subtitle with title + viewer count, ⋮ menu per stream |
| **Streams** | Add / edit / archive streams |
| **Recordings** | Browse and open recorded files |
| **Quick Tools** | Record or download a URL without adding it to your list |
| **Settings** | Player path, notifications, poll intervals, startup toggle, updates |

## Updating

When new commits are available, a banner appears at the top of the window on next launch. Click **Update & Restart** to apply in one step.

You can also go to **Settings → Maintenance → Update All**, or from the command line:

```
git pull && pip install --upgrade -r requirements.txt
```

## License

Personal use. Do what you want with it.
