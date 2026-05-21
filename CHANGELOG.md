# Changelog

## [1.1] — 2026-05-21

### Added
- System tray icon — close button hides to tray (optional, toggle in Settings); right-click for Show / Quit
- Windows Settings card: run at startup, close to tray, desktop icon, Start Menu shortcut toggles
- **Update All** button — pulls latest code from GitHub then upgrades all dependencies; prompts to restart if changes were found
- Settings auto-save on change — no more Save button
- Version display in Maintenance section
- `requirements.txt` for one-command dependency install

### Changed
- Media player simplified: defaults to system default (`os.startfile`); set a custom exe in Settings to use streamlink instead
- Menu label dynamically reflects the configured player ("Open in VLC", "Open in PotPlayer", etc.)
- Import ordering cleaned up

## [1.0] — 2026-05-21

First release.

### Features
- Monitor YouTube, Twitch, Chaturbate, Kick, and any yt-dlp-supported URL
- Toast notifications with **Record / Dismiss** action buttons
- **Auto-record** mode — recording starts immediately when a stream goes live
- Parallel polling via `ThreadPoolExecutor` — all streams checked at once
- Up to 3 configurable reminders per live session at adjustable intervals
- Soft/hard dismiss logic — two dismisses suppress notifications for the rest of the session
- Auto-restart recordings that die unexpectedly (CDN drops, etc.)
- `.part` file cleanup on recording stop so files are always playable
- **System tray** — close button hides to tray; right-click for Show / Quit
- **Run at startup** toggle in Settings (creates/removes a Windows Startup shortcut)
- **Self-update** button in Settings — runs `git pull` and shows output inline
- Full GUI with Watching / Streams / Recordings / Quick Tools / Settings tabs
- Quick Record and Video Downloader for one-off URLs
- Recordings tab with file browser and direct open
- Settings: player path, default output folder, notification tuning, yt-dlp/streamlink update buttons
