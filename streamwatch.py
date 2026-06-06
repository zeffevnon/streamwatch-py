#!/usr/bin/env python3
"""
streamwatch.py - Monitor streams and notify when live.
Reads streams.yaml, polls each URL every 60 seconds, fires a Windows toast
notification when a stream goes live. Notifies up to 3 times at 5-minute
intervals before giving up until the next check cycle.
"""

import concurrent.futures
import queue as _qmod
import sys
import threading
import yaml
import subprocess

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
import time
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

__version__ = "1.3"

# --- Config ---
SCRIPT_DIR   = Path(__file__).parent
CONFIG_FILE  = SCRIPT_DIR / "streams.yaml"
STATE_DIR    = SCRIPT_DIR / "state"
STATE_FILE   = STATE_DIR / "state.json"
LOGS_DIR     = SCRIPT_DIR / "logs"
STATE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

POLL_INTERVAL         = 60    # seconds between full poll cycles
REMINDER_INTERVAL     = 300   # seconds between reminders (5 min)
MAX_REMINDERS         = 3
NOTIFICATIONS_ENABLED = True  # toggled by GUI settings


# --- State ---
recording_processes = {}  # name -> Popen
restart_pending     = set()  # names of recordings that died unexpectedly
reminder_counts     = {}  # name -> int
dismiss_counts      = {}  # name -> int (1 = soft dismiss, 2 = hard dismiss)
last_reminder_time  = {}  # name -> float (epoch)
stream_info          = {}  # name -> {title, viewers, start_time} from last live poll
recording_start_times = {}  # name -> float epoch when recording began
recording_output_dirs = {}  # name -> Path of output directory

_event_queue       = None   # set by monitor_loop when running under the GUI
_toast_queue       = _qmod.Queue()   # (name, action_str) posted by toast button callbacks
_pending_toast_actions: dict = {}    # drained from _toast_queue at start of each cycle
_toaster           = None   # lazy-initialised InteractableWindowsToaster
_force_poll        = threading.Event()  # set by GUI refresh button to interrupt _poll_sleep


def _post_event(*args):
    if _event_queue is not None:
        _event_queue.put(args)


# --- Helpers ---

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def output_path_available(output: str | None) -> bool:
    if not output:
        return False
    try:
        p = Path(output)
        if p.exists():
            return True
        if p.drive:
            return Path(p.drive + "\\").exists()
        return True
    except Exception:
        return False


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_live(url):
    """
    Ask yt-dlp for stream metadata without downloading.
    Returns (True, info_dict) if live, (False, {}) if offline.
    info_dict keys: title, thumbnail, viewers, start_time
    """
    try:
        result = subprocess.run(
            ["yt-dlp", "--skip-download", "-j", url],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip().splitlines()[0])
            raw_title   = data.get("title") or data.get("fulltitle") or "Live Stream"
            description = (data.get("description") or "").strip()
            # yt-dlp generates "channel (live) date" as the title for Twitch;
            # the real stream title is in description (single-line for Twitch,
            # multi-paragraph for YouTube — so only swap when single-line).
            if description and "\n" not in description and "(live)" in raw_title.lower():
                title = description
            else:
                title = raw_title
            return True, {
                "title":      title,
                "thumbnail":  data.get("thumbnail"),
                "viewers":    data.get("concurrent_viewers") or data.get("view_count"),
                "start_time": data.get("release_timestamp") or data.get("timestamp"),
            }
    except Exception as e:
        log(f"Error checking {url}: {e}")
    return False, {}


def fetch_thumbnail(thumbnail_url, stream_name):
    """Download thumbnail to state dir, return local path or None."""
    if not thumbnail_url:
        return None
    try:
        dest = STATE_DIR / f"{stream_name}_thumb.jpg"
        with urllib.request.urlopen(thumbnail_url) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return str(dest)
    except Exception:
        return None


def _get_toaster():
    # Windows-only; only called from _notify_windows().
    global _toaster
    if _toaster is None:
        from windows_toasts import InteractableWindowsToaster
        _toaster = InteractableWindowsToaster("streamwatch")
    return _toaster


def _drain_toast_queue():
    """Pull all pending toast actions into _pending_toast_actions (last action wins)."""
    while True:
        try:
            name, action = _toast_queue.get_nowait()
            _pending_toast_actions[name] = action
        except _qmod.Empty:
            break


def _poll_sleep(seconds):
    """Sleep for `seconds`, but wake early on toast actions or a force-poll request."""
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        if _force_poll.wait(timeout=min(remaining, 1.0)):
            _force_poll.clear()
            return
        _drain_toast_queue()


def show_notification(name, title, thumbnail_path, auto_record=False):
    """Fire a desktop notification with action buttons.

    Routes to the appropriate platform backend. Buttons post directly to
    _toast_queue so the monitor loop processes them within ~1 second.
    """
    if not NOTIFICATIONS_ENABLED:
        return

    primary_label  = "Skip Recording" if auto_record else "Record"
    primary_action = "skip_recording" if auto_record else "record"
    body           = title or "Live now"
    heading        = f"{name} is live!"

    def on_action(action: str):
        if action in ("record", "dismiss", "skip_recording"):
            _toast_queue.put((name, action))

    try:
        if sys.platform == "win32":
            _notify_windows(heading, body, thumbnail_path,
                            primary_label, primary_action, on_action)
        elif sys.platform.startswith("linux"):
            _notify_linux(heading, body, thumbnail_path,
                          primary_label, primary_action, on_action)
        else:
            log(f"No notification backend for platform: {sys.platform}")
    except Exception as e:
        log(f"Notification error: {e}")


def _notify_windows(heading, body, thumbnail_path,
                    primary_label, primary_action, on_action):
    from windows_toasts import (
        Toast, ToastButton, ToastDisplayImage, ToastImagePosition,
    )

    def _on_activated(event):
        on_action(event.arguments)

    images = []
    if thumbnail_path:
        try:
            images = [ToastDisplayImage.fromPath(
                thumbnail_path,
                position=ToastImagePosition.AppLogo,
            )]
        except Exception:
            pass

    toast = Toast(
        text_fields=[heading, body],
        expiration_time=datetime.now() + timedelta(seconds=30),
        actions=[
            ToastButton(primary_label, primary_action),
            ToastButton("Dismiss", "dismiss"),
        ],
        images=images,
        on_activated=_on_activated,
    )
    _get_toaster().show_toast(toast)


_linux_notifier = None


def _get_linux_notifier():
    global _linux_notifier
    if _linux_notifier is None:
        from desktop_notifier import DesktopNotifier
        _linux_notifier = DesktopNotifier(app_name="streamwatch")
    return _linux_notifier


def _notify_linux(heading, body, thumbnail_path,
                  primary_label, primary_action, on_action):
    from desktop_notifier import Button

    notifier = _get_linux_notifier()
    notifier.send_sync(
        title=heading,
        message=body,
        icon=thumbnail_path if thumbnail_path else None,
        buttons=[
            Button(title=primary_label,
                   on_pressed=lambda: on_action(primary_action)),
            Button(title="Dismiss",
                   on_pressed=lambda: on_action("dismiss")),
        ],
        timeout=30,
    )


def start_recording(stream):
    name       = stream["name"]
    url        = stream["url"]
    output_dir = Path(stream["output"])

    if not output_path_available(stream["output"]):
        log(f"Cannot start recording for {name}: output path unavailable ({stream['output']})")
        _post_event("recording_failed", name, "output_unavailable")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = str(output_dir / f"{name}_{timestamp}.%(ext)s")
    log_path    = LOGS_DIR / f"{name}_{timestamp}.log"

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [
                "yt-dlp",
                "--retries", "10",
                "--fragment-retries", "10",
                "-o", output_path,
                url,
            ],
            stdout=log_file,
            stderr=log_file,
            creationflags=_NO_WINDOW,
            **({"start_new_session": True} if sys.platform != "win32" else {}),
        )
    recording_processes[name] = proc
    recording_start_times[name] = time.time()
    recording_output_dirs[name] = output_dir
    restart_pending.discard(name)
    log(f"Recording started: {name}  →  {output_dir}  (log: {log_path.name})")
    _post_event("recording_started", name)


def kill_recording(proc):
    """Kill a recording process and its entire child tree."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            proc.terminate()
    else:
        import os as _os, signal as _signal
        try:
            _os.killpg(_os.getpgid(proc.pid), _signal.SIGTERM)
        except Exception:
            proc.terminate()


def rename_parts(directory):
    """Rename any *.part files in directory by stripping the .part suffix.

    yt-dlp writes e.g. name.mp4.part while recording; on a clean stop it
    renames it automatically, but a forced kill leaves the .part behind.
    The file is playable as-is — it just needs the correct extension.
    """
    if directory is None:
        return
    directory = Path(directory)
    if not directory.exists():
        return
    for part_file in directory.glob("*.part"):
        dest = part_file.with_suffix("")  # strip .part → name.mp4
        if dest.exists():
            dest = part_file.with_name(part_file.stem + "_recovered" + Path(part_file.stem).suffix)
        try:
            part_file.rename(dest)
            log(f"Renamed: {part_file.name}  →  {dest.name}")
        except Exception as e:
            log(f"Could not rename {part_file.name}: {e}")


def cleanup_old_logs():
    """Delete log files older than 30 days on startup."""
    cutoff = time.time() - 30 * 86400
    for log_file in LOGS_DIR.glob("*.log"):
        if log_file.stat().st_mtime < cutoff:
            log_file.unlink()
            log(f"Deleted old log: {log_file.name}")


def cleanup_legacy_ipc():
    """Remove .bat and .flag files left by the old BurntToast IPC approach."""
    removed = 0
    for pattern in ("*.bat", "*.flag"):
        for f in STATE_DIR.glob(pattern):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    if removed:
        log(f"Removed {removed} legacy IPC file(s) from state/.")


def check_for_updates() -> "tuple[bool, int, str] | None":
    """Check whether the remote git repo has commits we don't have locally.

    Returns (has_update, commits_behind, latest_commit_msg) on success,
    None on any failure (no network, not a git repo, git missing, etc.).
    """
    try:
        result = subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=str(SCRIPT_DIR),
            capture_output=True, text=True, timeout=15,
            creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            return None

        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..@{u}"],
            cwd=str(SCRIPT_DIR),
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            return None

        behind = int(result.stdout.strip() or "0")
        if behind == 0:
            return (False, 0, "")

        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%s", "@{u}"],
            cwd=str(SCRIPT_DIR),
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        latest_msg = result.stdout.strip() if result.returncode == 0 else ""
        return (True, behind, latest_msg)
    except Exception as e:
        log(f"Update check failed: {e}")
        return None


def load_state():
    """Load persisted stream_info cache from state.json.

    reminder_counts and dismiss_counts are intentionally not restored so that
    every session starts fresh: all live streams get polled on the first cycle,
    notifications fire, and stream_info populates immediately.
    """
    global stream_info
    if not STATE_FILE.exists():
        return
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        stream_info = data.get("stream_info", {})
        log(f"State loaded ({len(stream_info)} stream(s) have cached info).")
    except Exception as e:
        log(f"Warning: could not load state: {e}")


def save_state():
    """Persist stream_info cache to state.json."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"stream_info": stream_info}, f)
    except Exception as e:
        log(f"Warning: could not save state: {e}")


def cleanup_finished():
    for name in list(recording_processes):
        retcode = recording_processes[name].poll()
        if retcode is not None:
            del recording_processes[name]
            recording_start_times.pop(name, None)
            output_dir = recording_output_dirs.pop(name, None)
            if retcode == 0:
                log(f"Recording finished: {name}")
                _post_event("recording_stopped", name, True)
            else:
                log(f"Recording died unexpectedly (exit {retcode}): {name} — will auto-restart if still live.")
                rename_parts(output_dir)
                restart_pending.add(name)
                _post_event("recording_stopped", name, False)


# --- Main loop ---

def monitor_loop(event_queue=None):
    global _event_queue
    _event_queue = event_queue
    config = load_config()
    load_state()
    cleanup_old_logs()
    cleanup_legacy_ipc()
    for stream in config.get("streams", []):
        try:
            rename_parts(stream.get("output"))
        except Exception as e:
            log(f"Skipping rename_parts for {stream['name']}: {e}")
    log(f"streamwatch started — watching {len(config.get('streams', []))} stream(s).")
    log("Ctrl+C to stop.\n")

    while True:
        config = load_config()  # reload each cycle so edits take effect immediately
        cleanup_finished()
        _drain_toast_queue()
        now = time.time()

        # ── Phase 1: fast synchronous actions; collect streams that need polling ──
        needs_poll = []  # streams whose is_live() result is needed this cycle

        for stream in config.get("streams", []):
            name = stream["name"]

            if stream.get("archived"):
                continue

            if name in recording_processes:
                if _pending_toast_actions.pop(name, None) == "skip_recording":
                    log(f"Recording skipped by user: {name}")
                    kill_recording(recording_processes[name])
                    del recording_processes[name]
                    restart_pending.discard(name)
                    _post_event("recording_skipped", name)
                    d = dismiss_counts.get(name, 0) + 1
                    dismiss_counts[name] = d
                    if d >= 2:
                        log(f"Dismissed (hard): {name} — no more notifications this session.")
                        reminder_counts[name] = MAX_REMINDERS
                    else:
                        log(f"Dismissed (soft): {name} — will notify once more.")
                        reminder_counts[name] = MAX_REMINDERS - 1
                    last_reminder_time[name] = time.time()
                continue

            if name in restart_pending:
                needs_poll.append(stream)
                continue

            action = _pending_toast_actions.pop(name, None)
            if action == "record":
                start_recording(stream)
                reminder_counts.pop(name, None)
                last_reminder_time.pop(name, None)
                continue
            elif action == "dismiss":
                d = dismiss_counts.get(name, 0) + 1
                dismiss_counts[name] = d
                if d >= 2:
                    log(f"Dismissed (hard): {name} — no more notifications this session.")
                    reminder_counts[name] = MAX_REMINDERS
                else:
                    log(f"Dismissed (soft): {name} — will notify once more.")
                    reminder_counts[name] = MAX_REMINDERS - 1
                last_reminder_time[name] = time.time()
                continue

            count     = reminder_counts.get(name, 0)
            last_time = last_reminder_time.get(name, 0)

            if count >= MAX_REMINDERS:
                # Still poll to detect when the stream goes offline,
                # but don't send any more notifications.
                needs_poll.append(stream)
                continue

            due = (count == 0) or ((now - last_time) >= REMINDER_INTERVAL)
            if not due:
                continue

            needs_poll.append(stream)

        # ── Phase 2: parallel is_live() checks ──
        poll_cache: dict[str, tuple] = {}
        if needs_poll:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(needs_poll)) as pool:
                futures = {
                    pool.submit(is_live, s["url"]): s["name"]
                    for s in needs_poll
                }
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    try:
                        poll_cache[name] = future.result()
                    except Exception as e:
                        log(f"Error checking {name}: {e}")
                        poll_cache[name] = (False, {})

        # ── Phase 3: process results ──
        for stream in config.get("streams", []):
            name = stream["name"]
            if name not in poll_cache:
                continue

            live, info = poll_cache[name]

            if name in restart_pending:
                if live:
                    stream_info[name] = info
                    log(f"Auto-restarting recording: {name}")
                    start_recording(stream)
                else:
                    restart_pending.discard(name)
                continue

            if live:
                stream_info[name] = info
                title         = info.get("title", "Live Stream")
                thumbnail_url = info.get("thumbnail")

                if stream.get("auto_record"):
                    log(f"{name} is live — '{title}' (auto_record)")
                    start_recording(stream)
                    custom_icon   = stream.get("icon")
                    fetched_thumb = None if custom_icon else fetch_thumbnail(thumbnail_url, name)
                    show_notification(name, title, custom_icon or fetched_thumb, auto_record=True)
                    if fetched_thumb:
                        try:
                            Path(fetched_thumb).unlink()
                        except Exception:
                            pass
                    reminder_counts.pop(name, None)
                    last_reminder_time.pop(name, None)
                else:
                    count = reminder_counts.get(name, 0)
                    if count < MAX_REMINDERS:
                        count += 1
                        log(f"{name} is live — '{title}'  (notification {count}/{MAX_REMINDERS})")
                        _post_event("stream_live", name, title)
                        custom_icon   = stream.get("icon")
                        fetched_thumb = None if custom_icon else fetch_thumbnail(thumbnail_url, name)
                        show_notification(name, title, custom_icon or fetched_thumb)
                        if fetched_thumb:
                            try:
                                Path(fetched_thumb).unlink()
                            except Exception:
                                pass
                        reminder_counts[name]    = count
                        last_reminder_time[name] = now
            else:
                if name in reminder_counts:
                    log(f"{name} is offline — resetting counters.")
                    _post_event("stream_offline", name)
                reminder_counts.pop(name, None)
                dismiss_counts.pop(name, None)
                last_reminder_time.pop(name, None)
                restart_pending.discard(name)
                stream_info.pop(name, None)

        save_state()
        _poll_sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        monitor_loop()
    except KeyboardInterrupt:
        log("Stopping streamwatch.")
        for name, proc in recording_processes.items():
            log(f"Terminating recording: {name}")
            kill_recording(proc)
