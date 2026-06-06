# Changelog

## [1.3] — 2026-06-06

### Added
- **Self-installing dependencies** — on first launch, if any required packages are missing, a setup window appears and runs `pip install -r requirements.txt` automatically; the app relaunches itself once the install completes. No manual pip step needed for new users.

## [1.2] — 2026-06-06

### Added
- **Linux support** — notifications via `desktop-notifier`, process-tree kill via `os.killpg`, file/folder/URL opening via `xdg-open`, `.desktop` shortcuts for startup and application menus
- **`streamwatch.bat` / `streamwatch.sh`** launchers at project root — double-click on Windows, `./streamwatch.sh` on Linux; no need to invoke Python manually
- **Auto-update banner** — checks for new upstream commits 3 seconds after launch; shows a dismissible stripe above the tabs with commit count and latest message; one-click Update & Restart flow; toggle in Settings → Windows
- **Output path resilience** — if a stream's output directory is on an unavailable network drive at startup, the app no longer crashes; affected streams show `⚠ Path missing` with the broken path as the subtitle in the Watching tab, and recording attempts are skipped with a clear log entry

### Changed
- `requirements.txt` uses PEP 508 platform markers — `windows-toasts` installs on Windows only, `desktop-notifier` on Linux only
- `settings.json.example` updated with `close_to_tray` and `auto_update_check` keys

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
