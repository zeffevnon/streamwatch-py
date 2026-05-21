# streamwatch

A Windows desktop app that watches livestream URLs and notifies you when they go live. Supports YouTube, Twitch, Chaturbate, Kick, and anything else yt-dlp can handle.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Toast notifications** with Record / Dismiss buttons — click Record and it starts immediately
- **Auto-record** mode — recording starts the moment a stream goes live, no click needed
- **Parallel polling** — all streams checked simultaneously, first poll is fast
- **Recording management** — start/stop from the GUI, auto-restart if a CDN drop kills the stream
- **System tray** — close button hides to tray (optional, toggle in Settings); right-click for Show / Quit
- **Run at startup** — optional Windows startup entry (toggle in Settings)
- **One-click updates** — Update All in Maintenance pulls the latest code and upgrades dependencies
- **Quick Tools** — one-off record or download without adding a stream to your list

## Requirements

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [streamlink](https://streamlink.github.io/) (for "Open in Player")
- A media player — PotPlayer is auto-detected, or set a custom path in Settings

Install Python dependencies:

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

Double-click `gui.pyw`, or:

```
pythonw gui.pyw
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

In the **Settings → Maintenance** tab, click **Update streamwatch** to pull the latest changes from GitHub. Also works from the command line:

```
git pull
```

## License

Personal use. Do what you want with it.
