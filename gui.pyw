#!/usr/bin/env python3
"""
gui.pyw - streamwatch GUI entry point.
Dark-themed window with Watching / Streams / Recordings / Quick Tools tabs.
Runs the monitor loop in a background thread.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tempfile

_NO_WINDOW = subprocess.CREATE_NO_WINDOW
import time
import tkinter as tk
import webbrowser

import pystray
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont
import yaml

import streamwatch

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SETTINGS_FILE = streamwatch.SCRIPT_DIR / "settings.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_settings(data: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# Status colours
_RED    = "#e05c5c"
_ORANGE = "#e0903c"
_GREEN  = "#5ce05c"
_YELLOW = "#e0c45c"
_GREY   = "#666666"

_ICON_SIZE = 36

# Strips trailing date/time appended by yt-dlp to some stream titles
# e.g. "Stream Title · 2026-04-11 17:33" or "username 2026-04-11 17:33"
_DATE_SUFFIX_RE = re.compile(r'[\s·•]*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\s*$')


def _format_stream_subtitle(info: dict) -> str:
    if not info:
        return ""
    parts = []
    title = info.get("title")
    if title:
        parts.append(_DATE_SUFFIX_RE.sub("", title).strip())
    viewers = info.get("viewers")
    if viewers is not None:
        parts.append(f"{int(viewers):,} viewers")
    return "  ·  ".join(parts)


def _stream_sort_key(stream: dict) -> tuple:
    """Sort order: Recording (most recent first) → Live → Offline → Archived."""
    name = stream["name"]
    if name in streamwatch.recording_processes:
        return (0, -streamwatch.recording_start_times.get(name, 0.0))
    if name in streamwatch.reminder_counts:
        return (1, 0.0)
    if stream.get("archived"):
        return (3, 0.0)
    return (2, 0.0)


def _get_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "chaturbate.com" in u:
        return "Chaturbate"
    if "twitch.tv" in u:
        return "Twitch"
    if "kick.com" in u:
        return "Kick"
    return ""


def _find_player() -> str:
    """Return media player executable path from settings, then common PotPlayer locations."""
    settings = _load_settings()
    custom = settings.get("player_path", "").strip()
    if custom and Path(custom).exists():
        return custom
    candidates = [
        r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
        r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe",
        r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe",
        r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return "potplayer"


# ------------------------------------------------------------------ tray image

def _make_tray_image() -> "Image.Image":
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 3, size - 3], fill=(26, 110, 181, 255))
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), "SW", fill=(255, 255, 255, 255),
              font=font, anchor="mm")
    return img


# ------------------------------------------------------------------ startup shortcut

_STARTUP_DIR      = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / \
                    "Start Menu" / "Programs" / "Startup"
_STARTUP_SHORTCUT = _STARTUP_DIR / "streamwatch.lnk"


def _startup_is_enabled() -> bool:
    return _STARTUP_SHORTCUT.exists()


def _set_startup(enable: bool):
    if enable:
        pythonw  = str(Path(sys.executable).with_name("pythonw.exe"))
        gui_pyw  = str(streamwatch.SCRIPT_DIR / "gui.pyw")
        work_dir = str(streamwatch.SCRIPT_DIR)
        shortcut = str(_STARTUP_SHORTCUT)
        ps = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut}');"
            f"$s.TargetPath = '{pythonw}';"
            f"$s.Arguments = '\"{gui_pyw}\"';"
            f"$s.WorkingDirectory = '{work_dir}';"
            "$s.WindowStyle = 1;"
            "$s.Save()"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1",
                                         delete=False, encoding="utf-8") as f:
            f.write(ps)
            ps_path = f.name
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                capture_output=True, creationflags=_NO_WINDOW,
            )
        finally:
            Path(ps_path).unlink(missing_ok=True)
    else:
        _STARTUP_SHORTCUT.unlink(missing_ok=True)


class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("streamwatch")
        self.geometry("960x600")
        self.minsize(760, 480)

        self._event_queue: queue.Queue       = queue.Queue()
        self._watching_rows: dict[str, dict] = {}
        self._icon_cache:    dict[str, ctk.CTkImage] = {}
        self._editing_name:  str | None      = None

        self._qt_jobs:           dict[str, dict] = {}   # job_id → job metadata
        self._qt_activity_rows:  dict[str, dict] = {}   # job_id → widget refs
        self._activity_polling:  bool            = False

        self._apply_saved_settings()
        self._build_ui()
        self._start_monitor()
        self._poll_events()
        self._poll_watching()
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ tray

    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show streamwatch", self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit_from_tray),
        )
        self._tray = pystray.Icon("streamwatch", _make_tray_image(), "streamwatch", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _show_from_tray(self, *_):
        self.after(0, self._restore_window)

    def _restore_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self, *_):
        self._tray.stop()
        for name, proc in streamwatch.recording_processes.items():
            streamwatch.log(f"Terminating recording: {name}")
            streamwatch.kill_recording(proc)
        self.after(0, self.destroy)

    # ------------------------------------------------------------------ icons

    def _get_icon(self, stream: dict) -> ctk.CTkImage:
        name = stream["name"]
        if name in self._icon_cache:
            return self._icon_cache[name]

        render = _ICON_SIZE * 2
        icon_path = stream.get("icon")
        try:
            if not icon_path or not Path(icon_path).exists():
                raise FileNotFoundError
            img = Image.open(icon_path).convert("RGBA").resize(
                (render, render), Image.LANCZOS)
        except Exception:
            img = Image.new("RGBA", (render, render), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([0, 0, render - 1, render - 1], fill=(65, 65, 75, 255))
            letter = name[0].upper() if name else "?"
            try:
                font = ImageFont.truetype("arial.ttf", render // 2)
            except Exception:
                font = ImageFont.load_default()
            draw.text((render // 2, render // 2), letter,
                      fill=(200, 200, 210, 255), font=font, anchor="mm")

        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse([0, 0, img.width - 1, img.height - 1], fill=255)
        img.putalpha(mask)
        ctk_img = ctk.CTkImage(img, size=(_ICON_SIZE, _ICON_SIZE))
        self._icon_cache[name] = ctk_img
        return ctk_img

    # ------------------------------------------------------------------ build

    def _build_ui(self):
        self._tabs = ctk.CTkTabview(self, anchor="nw",
                                    command=self._on_tab_change)
        self._tabs.pack(fill="both", expand=True, padx=12, pady=12)

        self._tab_watching    = self._tabs.add("Watching")
        self._tab_streams     = self._tabs.add("Streams")
        self._tab_recordings  = self._tabs.add("Recordings")
        self._tab_quick_tools = self._tabs.add("Quick Tools")
        self._tab_settings    = self._tabs.add("Settings")

        self._build_watching_tab()
        self._build_streams_tab()
        self._build_recordings_tab()
        self._build_quick_tools_tab()
        self._build_settings_tab()

    # ------------------------------------------------------------------ Watching tab

    def _build_watching_tab(self):
        header = ctk.CTkFrame(self._tab_watching, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 0))
        ctk.CTkButton(
            header, text="↻  Refresh", width=90, height=26,
            command=self._force_refresh_watching,
            fg_color="#2b2b2b", hover_color="#3d3d3d",
            font=ctk.CTkFont(size=12),
        ).pack(side="right", padx=8)

        self._watching_frame = ctk.CTkScrollableFrame(self._tab_watching)
        self._watching_frame.pack(fill="both", expand=True)
        self._refresh_watching()

    def _force_refresh_watching(self):
        """Interrupt the monitor loop's sleep and trigger an immediate poll."""
        streamwatch._force_poll.set()
        self._refresh_watching()

    def _refresh_watching(self):
        config  = streamwatch.load_config()
        streams = sorted(config.get("streams", []), key=_stream_sort_key)

        # Add/update rows
        for stream in streams:
            name = stream["name"]
            if name not in self._watching_rows:
                self._add_watching_row(name, stream)
            self._update_watching_row(name, stream)

        # Remove rows for deleted streams
        current_names = {s["name"] for s in streams}
        for name in list(self._watching_rows):
            if name not in current_names:
                for key in ("frame", "sep"):
                    self._watching_rows[name][key].destroy()
                del self._watching_rows[name]
                self._icon_cache.pop(name, None)

        # Reorder packed widgets to match sorted order
        for stream in streams:
            name = stream["name"]
            row  = self._watching_rows[name]
            row["frame"].pack_forget()
            row["sep"].pack_forget()
            row["frame"].pack(fill="x", padx=4, pady=4)
            row["sep"].pack(fill="x", padx=4)

    def _add_watching_row(self, name: str, stream: dict):
        frame = ctk.CTkFrame(self._watching_frame, fg_color="transparent")
        frame.pack(fill="x", padx=4, pady=4)

        # Icon
        ctk.CTkLabel(frame, text="", image=self._get_icon(stream),
                     width=_ICON_SIZE).pack(side="left", padx=(6, 10))

        # ⋮ menu button (packed right before name so it anchors far right)
        menu_btn = ctk.CTkButton(frame, text="⋮", width=32,
                                  fg_color="transparent", hover_color="#3d3d3d",
                                  font=ctk.CTkFont(size=18), corner_radius=4)
        menu_btn.configure(command=lambda b=menu_btn, n=name: self._show_row_menu(n, b))
        menu_btn.pack(side="right", padx=(0, 6))

        # Status (second from right)
        status_lbl = ctk.CTkLabel(frame, text="○ Offline",
                                   text_color=_GREY, anchor="w", width=130)
        status_lbl.pack(side="right", padx=(0, 16))

        # Name + platform + subtitle (fills remaining space)
        name_frame = ctk.CTkFrame(frame, fg_color="transparent")
        name_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))

        top_row = ctk.CTkFrame(name_frame, fg_color="transparent")
        top_row.pack(anchor="w")

        ctk.CTkLabel(top_row, text=name, anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        platform = _get_platform(stream.get("url", ""))
        if platform:
            ctk.CTkLabel(top_row, text=platform, anchor="w",
                         text_color="#666666",
                         font=ctk.CTkFont(size=10)).pack(side="left", padx=(8, 0))

        subtitle_lbl = ctk.CTkLabel(name_frame, text="", anchor="w",
                                     text_color="#888888",
                                     font=ctk.CTkFont(size=11))
        subtitle_lbl.pack(anchor="w")

        sep = ctk.CTkFrame(self._watching_frame, height=1, fg_color="#2e2e2e")
        sep.pack(fill="x", padx=4)

        self._watching_rows[name] = {
            "frame": frame, "status": status_lbl,
            "subtitle": subtitle_lbl, "sep": sep,
        }

    def _update_watching_row(self, name: str, stream: dict):
        archived     = stream.get("archived", False)
        recording    = name in streamwatch.recording_processes
        reconnecting = name in streamwatch.restart_pending
        live         = name in streamwatch.reminder_counts and not recording and not reconnecting

        row = self._watching_rows[name]

        if archived and not recording:
            row["status"].configure(text="○ Archived",       text_color=_GREY)
        elif recording:
            row["status"].configure(text="● Recording",      text_color=_RED)
        elif reconnecting:
            row["status"].configure(text="● Reconnecting…",  text_color=_ORANGE)
        elif live:
            row["status"].configure(text="● Live",           text_color=_GREEN)
        else:
            row["status"].configure(text="○ Offline",        text_color=_YELLOW)

        info = streamwatch.stream_info.get(name, {})
        row["subtitle"].configure(text=_format_stream_subtitle(info))

    def _show_row_menu(self, name: str, btn: ctk.CTkButton):
        config = streamwatch.load_config()
        stream = next((s for s in config.get("streams", []) if s["name"] == name), None)
        if not stream:
            return

        archived  = stream.get("archived", False)
        recording = name in streamwatch.recording_processes
        live      = name in streamwatch.reminder_counts and not recording

        menu = tk.Menu(self, tearoff=0,
                       bg="#252525", fg="#d0d0d0",
                       activebackground="#3d3d3d", activeforeground="white",
                       borderwidth=1, relief="flat")
        menu.add_command(label="Open in PotPlayer",
                         command=lambda: self._open_in_potplayer(stream))
        menu.add_command(label="Open in Browser",
                         command=lambda: webbrowser.open(stream["url"]))
        menu.add_command(label="Open Folder",
                         command=lambda: self._open_folder(stream))
        menu.add_separator()
        if live:
            menu.add_command(label="Start Recording",
                             command=lambda: self._start_recording(name, stream))
        if recording:
            menu.add_command(label="Stop Recording",
                             command=lambda: self._stop_recording(name))
        if live or recording:
            menu.add_separator()
        menu.add_command(
            label="Unarchive" if archived else "Archive",
            command=lambda: self._toggle_archive(name),
        )
        try:
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _start_recording(self, name: str, stream: dict):
        streamwatch.start_recording(stream)
        streamwatch.reminder_counts.pop(name, None)
        streamwatch.last_reminder_time.pop(name, None)
        self._refresh_watching()

    def _stop_recording(self, name: str):
        """Terminate a recording, verifying the process actually exits.

        State is cleaned up immediately so the monitor loop cannot auto-restart
        the recording.  The UI update is deferred to a background thread that
        waits for the process to exit — with an error dialog if it times out.
        """
        if name not in streamwatch.recording_processes:
            return

        proc       = streamwatch.recording_processes.pop(name)
        output_dir = streamwatch.recording_output_dirs.pop(name, None)
        streamwatch.restart_pending.discard(name)
        # Hard-dismiss: suppress notifications for the rest of this live session
        streamwatch.dismiss_counts[name] = 2
        streamwatch.reminder_counts[name] = streamwatch.MAX_REMINDERS

        def kill_and_confirm():
            streamwatch.kill_recording(proc)
            try:
                proc.wait(timeout=5)
                streamwatch.rename_parts(output_dir)
                # Confirmed dead — update UI
                streamwatch._post_event("recording_stopped", name, True)
                self.after(0, self._refresh_watching)
            except subprocess.TimeoutExpired:
                self.after(0, lambda: self._show_error(
                    "Stop Recording failed",
                    f"The recording for '{name}' did not exit within 5 seconds "
                    "and may still be running. Check Task Manager.",
                ))

        threading.Thread(target=kill_and_confirm, daemon=True).start()

    def _toggle_archive(self, name: str):
        config    = streamwatch.load_config()
        new_state = None
        for stream in config.get("streams", []):
            if stream["name"] == name:
                stream["archived"] = not stream.get("archived", False)
                new_state = stream["archived"]
                break
        self._save_config(config)
        # Stop any active recording when archiving
        if new_state and name in streamwatch.recording_processes:
            proc = streamwatch.recording_processes.pop(name)
            streamwatch.kill_recording(proc)
            streamwatch.restart_pending.discard(name)
        self._refresh_watching()

    @staticmethod
    def _open_folder(stream: dict):
        folder = Path(stream["output"])
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(folder)])

    def _open_in_potplayer(self, stream: dict):
        """Launch streamlink → PotPlayer in a thread; surface any error in a dialog."""
        def run():
            player = _find_player()
            proc = subprocess.Popen(
                ["streamlink", "--player", player, stream["url"], "best"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=_NO_WINDOW,
            )
            try:
                _, stderr = proc.communicate(timeout=10)
                if proc.returncode != 0:
                    msg = next(
                        (l.strip() for l in stderr.splitlines() if "error:" in l.lower()),
                        stderr.strip() or "streamlink exited with an error.",
                    )
                    self.after(0, lambda m=msg: self._show_error("PotPlayer / streamlink error", m))
            except subprocess.TimeoutExpired:
                pass  # still running after 10 s → stream opened successfully

        threading.Thread(target=run, daemon=True).start()

    def _show_error(self, title: str, message: str):
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("460x130")
        dlg.resizable(False, False)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text=message, wraplength=420,
                     anchor="w", justify="left").pack(padx=20, pady=(20, 8), fill="x")
        ctk.CTkButton(dlg, text="OK", width=80,
                      command=dlg.destroy).pack(pady=(0, 14))

    # ------------------------------------------------------------------ Streams tab

    def _build_streams_tab(self):
        outer = ctk.CTkScrollableFrame(self._tab_streams)
        outer.pack(fill="both", expand=True)

        list_outer = ctk.CTkFrame(outer)
        list_outer.pack(fill="x", padx=4, pady=(4, 8))

        ctk.CTkLabel(list_outer, text="Configured streams",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(6, 2))

        self._streams_scroll = ctk.CTkScrollableFrame(list_outer, height=150)
        self._streams_scroll.pack(fill="x", padx=6, pady=(0, 6))
        self._refresh_streams_list()

        form = ctk.CTkFrame(outer)
        form.pack(fill="x", padx=4, pady=4)

        self._form_title_lbl = ctk.CTkLabel(form, text="Add stream",
                                             font=ctk.CTkFont(size=13, weight="bold"))
        self._form_title_lbl.grid(row=0, column=0, columnspan=3,
                                   sticky="w", padx=8, pady=(6, 4))

        fields = [
            ("Name",          "e.g. MyStreamer",          None),
            ("URL",           "e.g. https://...",         None),
            ("Output folder", r"e.g. P:\streamwatch\...", "dir"),
            ("Icon path",     "optional — absolute path", "file"),
        ]
        self._form_entries: dict[str, ctk.CTkEntry] = {}
        for i, (label, placeholder, browse) in enumerate(fields):
            ctk.CTkLabel(form, text=label, anchor="w").grid(
                row=i + 1, column=0, sticky="w", padx=(8, 6), pady=3)
            entry = ctk.CTkEntry(form, placeholder_text=placeholder)
            entry.grid(row=i + 1, column=1, sticky="ew", padx=(0, 4), pady=3)
            self._form_entries[label] = entry
            if browse == "dir":
                ctk.CTkButton(form, text="Browse…", width=80,
                              command=lambda e=entry: self._browse_output(e)
                              ).grid(row=i + 1, column=2, padx=(0, 8), pady=3)
            elif browse == "file":
                ctk.CTkButton(form, text="Browse…", width=80,
                              command=lambda e=entry: self._browse_icon(e)
                              ).grid(row=i + 1, column=2, padx=(0, 8), pady=3)

        self._auto_record_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Auto-record (skip notification)",
                        variable=self._auto_record_var).grid(
                            row=len(fields) + 1, column=1, sticky="w", pady=4)

        self._form_status = ctk.CTkLabel(form, text="", text_color=_RED, anchor="w")
        self._form_status.grid(row=len(fields) + 2, column=1, sticky="w")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=len(fields) + 3, column=1, sticky="w", pady=(2, 12))

        self._submit_btn = ctk.CTkButton(btn_frame, text="Add Stream",
                                          command=self._add_stream)
        self._submit_btn.pack(side="left", padx=(0, 8))

        self._cancel_btn = ctk.CTkButton(btn_frame, text="Cancel",
                                          fg_color="#444444", hover_color="#555555",
                                          command=self._end_edit)
        self._cancel_btn.pack(side="left")
        self._cancel_btn.pack_forget()

        form.columnconfigure(1, weight=1)

    def _refresh_streams_list(self):
        for w in self._streams_scroll.winfo_children():
            w.destroy()

        config = streamwatch.load_config()
        for stream in config.get("streams", []):
            name  = stream["name"]
            label = name
            if stream.get("auto_record"):
                label += "  [auto]"
            if stream.get("archived"):
                label += "  [archived]"

            row = ctk.CTkFrame(self._streams_scroll, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=2)

            ctk.CTkLabel(row, text=label, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=6)

            btn_frame = ctk.CTkFrame(row, fg_color="transparent")
            btn_frame.pack(side="right", padx=4)

            ctk.CTkButton(btn_frame, text="Remove", width=75,
                          fg_color="#6b2222", hover_color="#8a2c2c",
                          command=lambda n=name: self._remove_stream(n)
                          ).pack(side="right")
            ctk.CTkButton(btn_frame, text="Edit", width=60,
                          fg_color="#2d4a6e", hover_color="#3a5f8a",
                          command=lambda s=stream: self._start_edit(dict(s))
                          ).pack(side="right", padx=(0, 6))

    @staticmethod
    def _browse_output(entry: ctk.CTkEntry):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            entry.delete(0, "end")
            entry.insert(0, folder)

    @staticmethod
    def _browse_icon(entry: ctk.CTkEntry):
        path = filedialog.askopenfilename(
            title="Select icon image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def _add_stream(self):
        name   = self._form_entries["Name"].get().strip()
        url    = self._form_entries["URL"].get().strip()
        output = self._form_entries["Output folder"].get().strip()
        icon   = self._form_entries["Icon path"].get().strip()

        if not name or not url or not output:
            self._form_status.configure(
                text="Name, URL and Output folder are required.", text_color=_RED)
            return

        config  = streamwatch.load_config()
        streams = config.get("streams", [])

        if any(s["name"] == name for s in streams):
            self._form_status.configure(text=f'"{name}" already exists.', text_color=_RED)
            return

        entry: dict = {"name": name, "url": url, "output": output}
        if icon:
            entry["icon"] = icon
        if self._auto_record_var.get():
            entry["auto_record"] = True

        streams.append(entry)
        config["streams"] = streams
        self._save_config(config)

        self._clear_form()
        self._form_status.configure(text="Stream added.", text_color="#5ce05c")
        self.after(2500, lambda: self._form_status.configure(text=""))
        self._refresh_streams_list()
        self._refresh_watching()

    def _start_edit(self, stream: dict):
        self._editing_name = stream["name"]
        self._form_entries["Name"].delete(0, "end")
        self._form_entries["Name"].insert(0, stream.get("name", ""))
        self._form_entries["URL"].delete(0, "end")
        self._form_entries["URL"].insert(0, stream.get("url", ""))
        self._form_entries["Output folder"].delete(0, "end")
        self._form_entries["Output folder"].insert(0, stream.get("output", ""))
        self._form_entries["Icon path"].delete(0, "end")
        self._form_entries["Icon path"].insert(0, stream.get("icon", ""))
        self._auto_record_var.set(stream.get("auto_record", False))
        self._form_status.configure(text="")
        self._form_title_lbl.configure(text="Edit stream")
        self._submit_btn.configure(text="Save Changes", command=self._save_edit)
        self._cancel_btn.pack(side="left")
        self._tabs.set("Streams")

    def _save_edit(self):
        name   = self._form_entries["Name"].get().strip()
        url    = self._form_entries["URL"].get().strip()
        output = self._form_entries["Output folder"].get().strip()
        icon   = self._form_entries["Icon path"].get().strip()

        if not name or not url or not output:
            self._form_status.configure(
                text="Name, URL and Output folder are required.", text_color=_RED)
            return

        config  = streamwatch.load_config()
        streams = config.get("streams", [])

        if name != self._editing_name and any(s["name"] == name for s in streams):
            self._form_status.configure(text=f'"{name}" already exists.', text_color=_RED)
            return

        for i, s in enumerate(streams):
            if s["name"] == self._editing_name:
                updated: dict = {"name": name, "url": url, "output": output}
                if icon:
                    updated["icon"] = icon
                if self._auto_record_var.get():
                    updated["auto_record"] = True
                if s.get("archived"):
                    updated["archived"] = True
                streams[i] = updated
                break

        config["streams"] = streams
        self._save_config(config)
        self._icon_cache.pop(self._editing_name, None)
        self._icon_cache.pop(name, None)
        self._end_edit()
        self._form_status.configure(text="Changes saved.", text_color="#5ce05c")
        self.after(2500, lambda: self._form_status.configure(text=""))
        self._refresh_streams_list()
        self._refresh_watching()

    def _end_edit(self):
        self._editing_name = None
        self._clear_form()
        self._form_title_lbl.configure(text="Add stream")
        self._submit_btn.configure(text="Add Stream", command=self._add_stream)
        self._cancel_btn.pack_forget()
        self._form_status.configure(text="")

    def _clear_form(self):
        for e in self._form_entries.values():
            e.delete(0, "end")
        self._auto_record_var.set(False)

    def _remove_stream(self, name: str):
        config = streamwatch.load_config()
        config["streams"] = [s for s in config.get("streams", []) if s["name"] != name]
        self._save_config(config)
        self._icon_cache.pop(name, None)
        if self._editing_name == name:
            self._end_edit()
        self._refresh_streams_list()
        self._refresh_watching()

    @staticmethod
    def _save_config(config: dict):
        with open(streamwatch.CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True,
                      default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------ Recordings tab

    def _build_recordings_tab(self):
        header = ctk.CTkFrame(self._tab_recordings, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(header, text="Recent recordings",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=4)
        ctk.CTkButton(header, text="Refresh", width=80,
                      command=self._refresh_recordings).pack(side="right", padx=4)

        self._recordings_frame = ctk.CTkScrollableFrame(self._tab_recordings)
        self._recordings_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self._refresh_recordings()

    def _refresh_recordings(self):
        for w in self._recordings_frame.winfo_children():
            w.destroy()

        config = streamwatch.load_config()
        files  = []
        for stream in config.get("streams", []):
            d = Path(stream["output"])
            if d.exists():
                for f in d.iterdir():
                    if f.is_file() and f.suffix.lower() in {
                            ".mp4", ".mkv", ".ts", ".flv", ".avi", ".webm", ".part"}:
                        files.append(f)

        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        if not files:
            ctk.CTkLabel(self._recordings_frame, text="No recordings found.",
                         text_color=_GREY).pack(pady=20)
            return

        for f in files[:200]:
            row = ctk.CTkFrame(self._recordings_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(row, text=f.name, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(
                             side="left", fill="x", expand=True, padx=4)
            ctk.CTkButton(row, text="Open", width=70,
                          command=lambda p=f: os.startfile(str(p))
                          ).pack(side="right", padx=4)
            ctk.CTkFrame(self._recordings_frame, height=1,
                         fg_color="#2e2e2e").pack(fill="x", padx=4)

    # ------------------------------------------------------------------ Quick Tools tab

    def _build_quick_tools_tab(self):
        outer = ctk.CTkScrollableFrame(self._tab_quick_tools)
        outer.pack(fill="both", expand=True)

        # ---- Quick Record ----
        rec = ctk.CTkFrame(outer)
        rec.pack(fill="x", padx=4, pady=(4, 8))

        ctk.CTkLabel(rec, text="Quick Record",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))
        ctk.CTkLabel(rec, text="Record a live stream immediately without adding it to your stream list.",
                     text_color=_GREY, font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=8, pady=(0, 6))

        self._qr_url    = self._tool_row(rec, "URL",    "Live stream URL")
        self._qr_output = self._tool_row(rec, "Output", "Output folder", browse="dir")

        qr_btn_row = ctk.CTkFrame(rec, fg_color="transparent")
        qr_btn_row.pack(anchor="w", padx=8, pady=(4, 10))
        ctk.CTkButton(qr_btn_row, text="Start Recording",
                      command=self._quick_record).pack(side="left")
        self._qr_status = ctk.CTkLabel(qr_btn_row, text="", text_color=_GREY,
                                        font=ctk.CTkFont(size=11))
        self._qr_status.pack(side="left", padx=(12, 0))

        # ---- Video Downloader ----
        dl = ctk.CTkFrame(outer)
        dl.pack(fill="x", padx=4, pady=4)

        ctk.CTkLabel(dl, text="Video Downloader",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))
        ctk.CTkLabel(dl, text="Download a video or VOD to a local folder via yt-dlp.",
                     text_color=_GREY, font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=8, pady=(0, 6))

        self._dl_url    = self._tool_row(dl, "URL",    "Video URL")
        self._dl_output = self._tool_row(dl, "Output", "Output folder", browse="dir")

        dl_btn_row = ctk.CTkFrame(dl, fg_color="transparent")
        dl_btn_row.pack(anchor="w", padx=8, pady=(4, 10))
        ctk.CTkButton(dl_btn_row, text="Download",
                      command=self._quick_download).pack(side="left")
        self._dl_status = ctk.CTkLabel(dl_btn_row, text="", text_color=_GREY,
                                        font=ctk.CTkFont(size=11))
        self._dl_status.pack(side="left", padx=(12, 0))

        # Pre-fill output fields from saved default
        default_output = _load_settings().get("default_output", "")
        if default_output:
            self._qr_output.insert(0, default_output)
            self._dl_output.insert(0, default_output)

        # ---- Activity ----
        act_card = ctk.CTkFrame(outer)
        act_card.pack(fill="x", padx=4, pady=(0, 8))

        ctk.CTkLabel(act_card, text="Activity",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))

        self._activity_list = ctk.CTkFrame(act_card, fg_color="transparent")
        self._activity_list.pack(fill="x", padx=8, pady=(0, 8))

        self._no_activity_lbl = ctk.CTkLabel(
            self._activity_list, text="No recent activity.",
            text_color=_GREY, font=ctk.CTkFont(size=11), anchor="w")
        self._no_activity_lbl.pack(anchor="w", pady=4)

    def _tool_row(self, parent, label: str, placeholder: str,
                  browse: str | None = None) -> ctk.CTkEntry:
        """Helper: label + entry + optional browse button. Returns the entry."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(row, text=label, width=60, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, placeholder_text=placeholder)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if browse == "dir":
            ctk.CTkButton(row, text="Browse…", width=80,
                          command=lambda e=entry: self._browse_output(e)
                          ).pack(side="left")
        return entry

    def _quick_record(self):
        url    = self._qr_url.get().strip()
        output = self._qr_output.get().strip()
        if not url or not output:
            self._qr_status.configure(
                text="URL and output folder are required.", text_color=_RED)
            return

        output_dir = Path(output)
        output_dir.mkdir(parents=True, exist_ok=True)
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(output_dir / f"quick_{ts}.%(ext)s")
        log_path    = streamwatch.LOGS_DIR / f"quick_{ts}.log"

        with open(log_path, "w") as lf:
            proc = subprocess.Popen(
                ["yt-dlp", "--retries", "10", "--fragment-retries", "10",
                 "-o", output_path, url],
                stdout=lf, stderr=lf,
                creationflags=_NO_WINDOW,
            )

        job_id = f"rec_{ts}"
        self._qt_jobs[job_id] = {
            "type": "record", "url": url, "output": output,
            "proc": proc, "started": time.time(), "done": False,
            "exit_code": None, "stopped": False,
        }
        self._qr_status.configure(text="Recording started.", text_color=_GREEN)
        self._qr_url.delete(0, "end")
        self._start_activity_poll()

    def _quick_download(self):
        url    = self._dl_url.get().strip()
        output = self._dl_output.get().strip()
        if not url or not output:
            self._dl_status.configure(
                text="URL and output folder are required.", text_color=_RED)
            return

        output_dir = Path(output)
        output_dir.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = streamwatch.LOGS_DIR / f"download_{ts}.log"

        with open(log_path, "w") as lf:
            proc = subprocess.Popen(
                ["yt-dlp", "-o", str(output_dir / "%(title)s.%(ext)s"), url],
                stdout=lf, stderr=lf,
                creationflags=_NO_WINDOW,
            )

        job_id = f"dl_{ts}"
        self._qt_jobs[job_id] = {
            "type": "download", "url": url, "output": output,
            "proc": proc, "started": time.time(), "done": False,
            "exit_code": None, "stopped": False,
        }
        self._dl_status.configure(text="Download started.", text_color=_GREEN)
        self._dl_url.delete(0, "end")
        self._start_activity_poll()

    # ------------------------------------------------------------------ Activity helpers

    @staticmethod
    def _url_display(url: str) -> str:
        short = url.replace("https://", "").replace("http://", "").replace("www.", "")
        return short[:52] + "…" if len(short) > 53 else short

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m {s:02d}s"

    def _start_activity_poll(self):
        if not self._activity_polling:
            self._activity_polling = True
            self.after(1000, self._refresh_activity)

    def _refresh_activity(self):
        has_active = False
        for job_id, job in list(self._qt_jobs.items()):
            if not job["done"]:
                rc = job["proc"].poll()
                if rc is not None:
                    job["done"]      = True
                    job["exit_code"] = rc
                else:
                    has_active = True
            self._sync_activity_row(job_id, job)

        if has_active:
            self.after(1000, self._refresh_activity)
        else:
            self._activity_polling = False

    def _sync_activity_row(self, job_id: str, job: dict):
        """Create the row if it doesn't exist, then update labels/button."""
        if job_id not in self._qt_activity_rows:
            self._no_activity_lbl.pack_forget()

            row = ctk.CTkFrame(self._activity_list, fg_color="transparent")
            row.pack(fill="x", pady=2)

            icon_color = _RED if job["type"] == "record" else _ORANGE
            icon_text  = "●" if job["type"] == "record" else "↓"
            ctk.CTkLabel(row, text=icon_text, text_color=icon_color,
                         width=14, font=ctk.CTkFont(size=12)).pack(side="left")

            ctk.CTkLabel(row, text=self._url_display(job["url"]),
                         anchor="w", font=ctk.CTkFont(size=11)
                         ).pack(side="left", fill="x", expand=True, padx=(6, 8))

            status_lbl   = ctk.CTkLabel(row, text="", text_color=_GREY,
                                         width=100, anchor="w",
                                         font=ctk.CTkFont(size=11))
            status_lbl.pack(side="left", padx=(0, 8))

            duration_lbl = ctk.CTkLabel(row, text="0s", text_color=_GREY,
                                         width=50, anchor="e",
                                         font=ctk.CTkFont(size=11))
            duration_lbl.pack(side="left", padx=(0, 8))

            action_btn = ctk.CTkButton(row, text="Stop", width=72,
                                        font=ctk.CTkFont(size=11),
                                        fg_color="#6b2222", hover_color="#8a2c2c")
            action_btn.pack(side="left")

            sep = ctk.CTkFrame(self._activity_list, height=1, fg_color="#2e2e2e")
            sep.pack(fill="x", pady=(2, 0))

            self._qt_activity_rows[job_id] = {
                "frame": row, "status_lbl": status_lbl,
                "duration_lbl": duration_lbl, "action_btn": action_btn, "sep": sep,
            }

        widgets  = self._qt_activity_rows[job_id]
        elapsed  = time.time() - job["started"]
        widgets["duration_lbl"].configure(text=self._fmt_duration(elapsed))

        if job["done"]:
            if job["stopped"]:
                widgets["status_lbl"].configure(text="Stopped", text_color=_GREY)
            elif job["exit_code"] == 0:
                widgets["status_lbl"].configure(text="Done", text_color=_GREEN)
            else:
                widgets["status_lbl"].configure(text="Failed", text_color=_RED)
            widgets["action_btn"].configure(
                text="Remove", fg_color="#444444", hover_color="#555555",
                command=lambda jid=job_id: self._remove_activity_row(jid))
        else:
            label = "Recording…" if job["type"] == "record" else "Downloading…"
            color = _RED        if job["type"] == "record" else _ORANGE
            widgets["status_lbl"].configure(text=label, text_color=color)
            widgets["action_btn"].configure(
                text="Stop", fg_color="#6b2222", hover_color="#8a2c2c",
                command=lambda jid=job_id: self._stop_qt_job(jid))

    def _stop_qt_job(self, job_id: str):
        job = self._qt_jobs.get(job_id)
        if not job or job["done"]:
            return
        job["stopped"] = True

        def kill_and_confirm():
            streamwatch.kill_recording(job["proc"])
            try:
                job["proc"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            job["done"]      = True
            job["exit_code"] = -1
            self.after(0, lambda: self._sync_activity_row(job_id, job))

        threading.Thread(target=kill_and_confirm, daemon=True).start()

    def _remove_activity_row(self, job_id: str):
        if job_id in self._qt_activity_rows:
            for key in ("frame", "sep"):
                self._qt_activity_rows[job_id][key].destroy()
            del self._qt_activity_rows[job_id]
        self._qt_jobs.pop(job_id, None)
        if not self._qt_jobs:
            self._no_activity_lbl.pack(anchor="w", pady=4)

    # ------------------------------------------------------------------ Settings tab

    def _apply_saved_settings(self):
        """Apply persisted settings to the running streamwatch constants."""
        s = _load_settings()
        if "max_reminders" in s:
            streamwatch.MAX_REMINDERS = s["max_reminders"]
        if "reminder_interval_min" in s:
            streamwatch.REMINDER_INTERVAL = s["reminder_interval_min"] * 60
        if "poll_interval" in s:
            streamwatch.POLL_INTERVAL = s["poll_interval"]
        if "notifications_enabled" in s:
            streamwatch.NOTIFICATIONS_ENABLED = s["notifications_enabled"]

    def _build_settings_tab(self):
        outer = ctk.CTkScrollableFrame(self._tab_settings)
        outer.pack(fill="both", expand=True)
        s = _load_settings()

        # ---- Media Player ----
        mp_card = ctk.CTkFrame(outer)
        mp_card.pack(fill="x", padx=4, pady=(4, 8))
        ctk.CTkLabel(mp_card, text="Media Player",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))
        ctk.CTkLabel(mp_card,
                     text="Used by 'Open in Player' in the Watching tab. Leave blank to auto-detect PotPlayer.",
                     text_color=_GREY, font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=8, pady=(0, 6))

        player_row = ctk.CTkFrame(mp_card, fg_color="transparent")
        player_row.pack(fill="x", padx=8, pady=(0, 10))
        ctk.CTkLabel(player_row, text="Executable", width=100, anchor="w").pack(side="left")
        self._settings_player_entry = ctk.CTkEntry(
            player_row,
            placeholder_text=r"e.g. C:\Program Files\...\PotPlayerMini64.exe")
        self._settings_player_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if s.get("player_path"):
            self._settings_player_entry.insert(0, s["player_path"])
        ctk.CTkButton(player_row, text="Browse…", width=80,
                      command=self._browse_player_exe).pack(side="left")

        # ---- Quick Tools Defaults ----
        qt_card = ctk.CTkFrame(outer)
        qt_card.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(qt_card, text="Quick Tools",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))

        output_row = ctk.CTkFrame(qt_card, fg_color="transparent")
        output_row.pack(fill="x", padx=8, pady=(0, 10))
        ctk.CTkLabel(output_row, text="Default output", width=100, anchor="w").pack(side="left")
        self._settings_output_entry = ctk.CTkEntry(
            output_row,
            placeholder_text="Default folder for Quick Record and Downloader")
        self._settings_output_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if s.get("default_output"):
            self._settings_output_entry.insert(0, s["default_output"])
        ctk.CTkButton(output_row, text="Browse…", width=80,
                      command=lambda e=self._settings_output_entry: self._browse_output(e)
                      ).pack(side="left")

        # ---- Windows ----
        win_card = ctk.CTkFrame(outer)
        win_card.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(win_card, text="Windows",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))

        startup_row = ctk.CTkFrame(win_card, fg_color="transparent")
        startup_row.pack(fill="x", padx=8, pady=(0, 4))
        self._startup_var = ctk.BooleanVar(value=_startup_is_enabled())
        ctk.CTkCheckBox(startup_row, text="Run at Windows startup",
                        variable=self._startup_var,
                        command=self._toggle_startup).pack(side="left")
        self._startup_status = ctk.CTkLabel(startup_row, text="",
                                             text_color=_GREY,
                                             font=ctk.CTkFont(size=11))
        self._startup_status.pack(side="left", padx=(12, 0))
        ctk.CTkFrame(win_card, height=8, fg_color="transparent").pack()

        # ---- Notifications ----
        notif_card = ctk.CTkFrame(outer)
        notif_card.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(notif_card, text="Notifications",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))

        self._settings_notif_var = ctk.BooleanVar(
            value=s.get("notifications_enabled", True))
        ctk.CTkCheckBox(notif_card, text="Enable toast notifications",
                        variable=self._settings_notif_var).pack(
                            anchor="w", padx=8, pady=(0, 4))

        def _notif_row(parent, label: str, default: int, unit: str):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=2)
            ctk.CTkLabel(row, text=label, width=160, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=60)
            entry.insert(0, str(default))
            entry.pack(side="left")
            ctk.CTkLabel(row, text=unit, text_color=_GREY,
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 0))
            return entry

        self._settings_max_rem   = _notif_row(
            notif_card, "Max reminders",
            s.get("max_reminders", streamwatch.MAX_REMINDERS),
            "per live session")
        self._settings_rem_intv  = _notif_row(
            notif_card, "Reminder interval",
            s.get("reminder_interval_min", streamwatch.REMINDER_INTERVAL // 60),
            "minutes")
        self._settings_poll_intv = _notif_row(
            notif_card, "Poll interval",
            s.get("poll_interval", streamwatch.POLL_INTERVAL),
            "seconds")
        ctk.CTkFrame(notif_card, height=8, fg_color="transparent").pack()

        # ---- Maintenance ----
        maint_card = ctk.CTkFrame(outer)
        maint_card.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(maint_card, text="Maintenance",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(8, 4))

        maint_btn_row = ctk.CTkFrame(maint_card, fg_color="transparent")
        maint_btn_row.pack(anchor="w", padx=8, pady=(0, 6))
        ctk.CTkButton(maint_btn_row, text="Update yt-dlp",
                      command=self._update_ytdlp).pack(side="left", padx=(0, 8))
        ctk.CTkButton(maint_btn_row, text="Update streamlink",
                      command=self._update_streamlink).pack(side="left", padx=(0, 8))
        ctk.CTkButton(maint_btn_row, text="Update streamwatch",
                      command=self._update_streamwatch).pack(side="left")

        self._maint_status = ctk.CTkLabel(maint_card, text="",
                                           text_color=_GREY,
                                           font=ctk.CTkFont(size=11), anchor="w",
                                           wraplength=500)
        self._maint_status.pack(anchor="w", padx=8, pady=(0, 8))

        # ---- Save ----
        save_row = ctk.CTkFrame(outer, fg_color="transparent")
        save_row.pack(anchor="w", padx=4, pady=(0, 12))
        ctk.CTkButton(save_row, text="Save Settings",
                      command=self._on_save_settings).pack(side="left", padx=4)
        self._settings_status = ctk.CTkLabel(save_row, text="",
                                              text_color=_GREY,
                                              font=ctk.CTkFont(size=11))
        self._settings_status.pack(side="left", padx=(8, 0))

    def _browse_player_exe(self):
        path = filedialog.askopenfilename(
            title="Select media player executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self._settings_player_entry.delete(0, "end")
            self._settings_player_entry.insert(0, path)

    def _on_save_settings(self):
        try:
            max_rem      = int(self._settings_max_rem.get())
            rem_interval = int(self._settings_rem_intv.get())
            poll_interval = int(self._settings_poll_intv.get())
        except ValueError:
            self._settings_status.configure(
                text="Reminders and intervals must be whole numbers.", text_color=_RED)
            return

        data = {
            "player_path":            self._settings_player_entry.get().strip(),
            "default_output":         self._settings_output_entry.get().strip(),
            "notifications_enabled":  self._settings_notif_var.get(),
            "max_reminders":          max_rem,
            "reminder_interval_min":  rem_interval,
            "poll_interval":          poll_interval,
        }
        _save_settings(data)

        # Apply immediately to the running monitor
        streamwatch.MAX_REMINDERS         = max_rem
        streamwatch.REMINDER_INTERVAL     = rem_interval * 60
        streamwatch.POLL_INTERVAL         = poll_interval
        streamwatch.NOTIFICATIONS_ENABLED = data["notifications_enabled"]

        self._settings_status.configure(text="Settings saved.", text_color=_GREEN)
        self.after(2500, lambda: self._settings_status.configure(text=""))

    def _update_ytdlp(self):
        self._maint_status.configure(text="Updating yt-dlp…", text_color=_GREY)

        def run():
            try:
                result = subprocess.run(
                    ["yt-dlp", "-U"], capture_output=True, text=True, timeout=60,
                    creationflags=_NO_WINDOW)
                lines = [l.strip() for l in
                         (result.stdout + result.stderr).splitlines() if l.strip()]
                msg = "\n".join(lines[-3:]) or "Done."
                color = _GREEN if result.returncode == 0 else _RED
                self.after(0, lambda m=msg, c=color: self._maint_status.configure(
                    text=m, text_color=c))
            except Exception as e:
                self.after(0, lambda: self._maint_status.configure(
                    text=f"Error: {e}", text_color=_RED))

        threading.Thread(target=run, daemon=True).start()

    def _update_streamlink(self):
        self._maint_status.configure(text="Updating streamlink…", text_color=_GREY)

        def run():
            try:
                result = subprocess.run(
                    ["pip", "install", "--upgrade", "streamlink"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=_NO_WINDOW)
                lines = [l.strip() for l in
                         (result.stdout + result.stderr).splitlines() if l.strip()]
                msg = "\n".join(lines[-3:]) or "Done."
                color = _GREEN if result.returncode == 0 else _RED
                self.after(0, lambda m=msg, c=color: self._maint_status.configure(
                    text=m, text_color=c))
            except Exception as e:
                self.after(0, lambda: self._maint_status.configure(
                    text=f"Error: {e}", text_color=_RED))

        threading.Thread(target=run, daemon=True).start()

    def _toggle_startup(self):
        enable = self._startup_var.get()
        try:
            _set_startup(enable)
            if _startup_is_enabled() == enable:
                msg   = "Will start at login." if enable else "Startup entry removed."
                color = _GREEN
            else:
                raise RuntimeError("shortcut not created")
        except Exception as e:
            self._startup_var.set(not enable)
            msg, color = f"Failed: {e}", _RED
        self._startup_status.configure(text=msg, text_color=color)
        self.after(3000, lambda: self._startup_status.configure(text=""))

    def _update_streamwatch(self):
        self._maint_status.configure(text="Pulling latest changes…", text_color=_GREY)

        def run():
            try:
                result = subprocess.run(
                    ["git", "pull"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(streamwatch.SCRIPT_DIR),
                    creationflags=_NO_WINDOW,
                )
                lines = [l.strip() for l in
                         (result.stdout + result.stderr).splitlines() if l.strip()]
                msg   = "\n".join(lines[-3:]) or "Done."
                color = _GREEN if result.returncode == 0 else _RED
                self.after(0, lambda m=msg, c=color: self._maint_status.configure(
                    text=m, text_color=c))
            except Exception as e:
                self.after(0, lambda: self._maint_status.configure(
                    text=f"Error: {e}", text_color=_RED))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ monitor thread

    def _start_monitor(self):
        threading.Thread(
            target=streamwatch.monitor_loop,
            args=(self._event_queue,),
            daemon=True,
        ).start()

    def _poll_events(self):
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(500, self._poll_events)

    def _handle_event(self, event: tuple):
        kind = event[0]
        if kind in ("stream_live", "recording_started", "stream_offline",
                    "recording_stopped", "recording_skipped"):
            self._refresh_watching()
        if kind == "recording_stopped":
            self._refresh_recordings()

    def _on_tab_change(self):
        if self._tabs.get() == "Recordings":
            self._refresh_recordings()

    def _poll_watching(self):
        """Refresh the Watching tab periodically so subtitles stay current.

        Event-driven refreshes handle state changes, but subtitle data loaded
        from state.json on startup (or updated mid-session without a state
        change) would never render without this ticker.
        """
        self._refresh_watching()
        self.after(5000, self._poll_watching)

    # ------------------------------------------------------------------ shutdown

    def _on_close(self):
        """Hide to system tray — use Quit from the tray menu to fully exit."""
        self.withdraw()


if __name__ == "__main__":
    app = App()
    app.mainloop()
