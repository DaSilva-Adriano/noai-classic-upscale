# SPDX-License-Identifier: GPL-3.0-or-later
"""
NOAI Classic Upscale
====================
Copyright (C) 2026 Adriano

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Batch-upscale videos with FFmpeg using only classic interpolators
(bilinear, bicubic, and lanczos). No denoise, sharpen, unsharp, eq, or
other enhancement filters — interpolation only.

Not NVIDIA VSR. This is a non-AI baseline scaler (bilinear / bicubic /
lanczos).

Requirements
  - Python 3.8+ with tkinter (standard library)
  - FFmpeg (default root: C:\\VSR\\ffmpeg-9.0.1-full_build)

Run
  launch.bat          (double-click on Windows)
  bash launch.sh      (Git Bash)
  python noai_classic_upscale.py

If `python` opens the Microsoft Store stub, use launch.bat / launch.sh,
or a real interpreter such as:
  C:\\Users\\<you>\\AppData\\Roaming\\uv\\python\\cpython-3.12-windows-x86_64-none\\python.exe noai_classic_upscale.py
"""

from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# FFmpeg defaults
# ---------------------------------------------------------------------------

FFMPEG_ROOT = Path(r"C:\VSR\ffmpeg-9.0.1-full_build")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

RESOLUTIONS = [
    ("4K (3840x2160)", 3840, 2160),
    ("1080p (1920x1080)", 1920, 1080),
    ("1440p (2560x1440)", 2560, 1440),
]
PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]
METHODS_ORDER = ("bilinear", "bicubic", "lanczos")
OVERWRITE_POLICIES = ("Ask", "Overwrite", "Skip")

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
PROGRESS_OVERLAY_RE = re.compile(
    r"frame=\s*\d+.*fps=|fps=\s*[\d.]+.*time=",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Path / command helpers
# ---------------------------------------------------------------------------

def resolve_ffmpeg(root: Path) -> Path | None:
    """Prefer <root>\\bin\\ffmpeg.exe, else search one level under root."""
    if not root:
        return None
    preferred = root / "bin" / "ffmpeg.exe"
    if preferred.is_file():
        return preferred
    direct = root / "ffmpeg.exe"
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    try:
        children = list(root.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_file() and child.name.lower() == "ffmpeg.exe":
            return child
        if child.is_dir():
            for cand in (child / "ffmpeg.exe", child / "bin" / "ffmpeg.exe"):
                if cand.is_file():
                    return cand
    return None


def win_quote(arg: str) -> str:
    if not arg:
        return '""'
    needs_quotes = (
        any(c in arg for c in ' \t"')
        or "\\" in arg
        or "/" in arg
        or arg.startswith("scale=")
    )
    if needs_quotes:
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def format_cmd(argv: list[str]) -> str:
    return " ".join(win_quote(a) for a in argv)


def output_path_for(src: Path, method: str) -> Path:
    return src.with_name(f"{src.stem}-{method}{src.suffix}")


def build_vf(width: int, height: int, method: str, pad: bool) -> str:
    flags = f"{method}+accurate_rnd+full_chroma_int"
    if not pad:
        return f"scale={width}:{height}:flags={flags}"
    return (
        f"scale={width}:{height}:flags={flags}"
        f":force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def build_argv(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    method: str,
    width: int,
    height: int,
    crf: int,
    preset: str,
    pad: bool,
    overwrite: bool,
) -> list[str]:
    argv = [str(ffmpeg)]
    argv.append("-y" if overwrite else "-n")
    argv.extend(
        [
            "-nostdin",
            "-progress",
            "pipe:1",
            "-i",
            str(src),
            "-vf",
            build_vf(width, height, method, pad),
            "-c:v",
            "libx265",
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "hvc1",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )
    return argv


def hms_to_seconds(text: str) -> float | None:
    parts = text.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None


def seconds_to_hms(sec: float) -> str:
    if sec < 0 or sec != sec:  # NaN
        sec = 0.0
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def is_mp4(path: Path) -> bool:
    return path.suffix.lower() == ".mp4"


# ---------------------------------------------------------------------------
# Windows drag-and-drop (WM_DROPFILES) — no extra packages
# ---------------------------------------------------------------------------

def _try_enable_file_drop(window: tk.Misc, on_files) -> object | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4
    GA_ROOT = 2
    LONG_PTR = ctypes.c_ssize_t
    LRESULT = LONG_PTR

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragAcceptFiles.restype = None
    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        set_long = user32.SetWindowLongPtrW
        call_proc = user32.CallWindowProcW
    else:
        set_long = user32.SetWindowLongW
        call_proc = user32.CallWindowProcW

    set_long.restype = LONG_PTR
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
    call_proc.restype = LRESULT
    call_proc.argtypes = [
        LONG_PTR,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]

    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class DropHook:
        def __init__(self):
            self.hwnd = 0
            self.old_proc = 0
            self._wndproc = None
            self.alive = False

        def attach(self) -> bool:
            window.update_idletasks()
            inner = int(window.winfo_id())
            root_hwnd = user32.GetAncestor(inner, GA_ROOT) or inner
            self.hwnd = int(root_hwnd)
            if not self.hwnd:
                return False
            shell32.DragAcceptFiles(self.hwnd, True)
            self._wndproc = WNDPROC(self._dispatch)
            new_ptr = ctypes.cast(self._wndproc, ctypes.c_void_p).value
            self.old_proc = set_long(self.hwnd, GWLP_WNDPROC, new_ptr)
            self.alive = True
            return True

        def detach(self) -> None:
            if not self.alive:
                return
            try:
                if self.old_proc and self.hwnd:
                    set_long(self.hwnd, GWLP_WNDPROC, self.old_proc)
                if self.hwnd:
                    shell32.DragAcceptFiles(self.hwnd, False)
            except OSError:
                pass
            self.alive = False

        def _handle_drop(self, hdrop) -> None:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            files: list[str] = []
            for i in range(count):
                nchars = shell32.DragQueryFileW(hdrop, i, None, 0)
                buf = ctypes.create_unicode_buffer(nchars + 1)
                shell32.DragQueryFileW(hdrop, i, buf, nchars + 1)
                files.append(buf.value)
            shell32.DragFinish(hdrop)
            if files:
                window.after(0, lambda paths=files: on_files(paths))

        def _dispatch(self, hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    self._handle_drop(wparam)
                except Exception:
                    try:
                        shell32.DragFinish(wparam)
                    except Exception:
                        pass
                return 0
            return call_proc(self.old_proc, hwnd, msg, wparam, lparam)

    hook = DropHook()
    try:
        if hook.attach():
            return hook
    except Exception:
        try:
            hook.detach()
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Settings snapshot (frozen when Start is pressed)
# ---------------------------------------------------------------------------

@dataclass
class RunSettings:
    ffmpeg: Path
    methods: tuple[str, ...]
    width: int
    height: int
    crf: int
    preset: str
    pad: bool
    overwrite_policy: str  # Ask / Overwrite / Skip


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class NoaiClassicUpscaleApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("NOAI Classic Upscale")
        self.root.minsize(860, 640)
        self.root.geometry("960x740")

        self.files: list[Path] = []
        self.ui_queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.current_proc: subprocess.Popen | None = None
        self.proc_lock = threading.Lock()
        self.running = False
        self.overwrite_all = False
        self.skip_all = False
        self.drop_hook = None

        self.ffmpeg_var = tk.StringVar()
        self.bilinear_var = tk.BooleanVar(value=True)
        self.bicubic_var = tk.BooleanVar(value=True)
        self.lanczos_var = tk.BooleanVar(value=True)
        self.res_var = tk.StringVar(value=RESOLUTIONS[0][0])
        self.crf_var = tk.StringVar(value="12")
        self.preset_var = tk.StringVar(value="medium")
        self.pad_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.StringVar(value="Ask")
        self.progress_var = tk.StringVar(value="Idle")
        self.status_var = tk.StringVar(value="idle")
        self.bar_var = tk.DoubleVar(value=0.0)

        self._build_style()
        self._build_ui()
        self._resolve_ffmpeg_on_launch()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._attach_dnd)
        self.root.after(50, self._pump_ui)

    # ----- UI -----

    def _build_style(self) -> None:
        style = ttk.Style()
        if sys.platform == "win32":
            try:
                style.theme_use("vista")
            except tk.TclError:
                pass
        style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 8))
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#555555")

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ff = ttk.LabelFrame(main, text="FFmpeg", padding=8)
        ff.pack(fill=tk.X, **pad)
        ttk.Label(ff, text="ffmpeg.exe").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.ffmpeg_entry = ttk.Entry(ff, textvariable=self.ffmpeg_var)
        self.ffmpeg_entry.grid(row=0, column=1, sticky=tk.EW)
        ttk.Button(ff, text="Browse…", command=self._browse_ffmpeg).grid(
            row=0, column=2, padx=(8, 0)
        )
        ff.columnconfigure(1, weight=1)

        files_fr = ttk.LabelFrame(main, text="Queue", padding=8)
        files_fr.pack(fill=tk.BOTH, expand=False, **pad)
        btns = ttk.Frame(files_fr)
        btns.pack(fill=tk.X, pady=(0, 6))
        self.add_btn = ttk.Button(btns, text="Add files…", command=self._add_files)
        self.add_btn.pack(side=tk.LEFT)
        self.remove_btn = ttk.Button(
            btns, text="Remove selected", command=self._remove_selected
        )
        self.remove_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.clear_btn = ttk.Button(btns, text="Clear list", command=self._clear_list)
        self.clear_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.queue_count_var = tk.StringVar(value="0 files")
        ttk.Label(btns, textvariable=self.queue_count_var).pack(side=tk.RIGHT)

        list_wrap = ttk.Frame(files_fr)
        list_wrap.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL)
        self.listbox = tk.Listbox(
            list_wrap,
            selectmode=tk.EXTENDED,
            height=8,
            activestyle="dotbox",
            font=("Consolas", 9),
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.configure(command=self.listbox.yview)
        self.listbox.bind("<Delete>", lambda e: self._remove_selected())
        self.listbox.bind("<Control-a>", self._select_all)
        self.listbox.bind("<Control-A>", self._select_all)
        ttk.Label(
            files_fr,
            text="Select one or many .mp4 files (Ctrl/Shift). Drag-and-drop onto this window is also supported.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(6, 0))

        opts = ttk.LabelFrame(main, text="Options", padding=8)
        opts.pack(fill=tk.X, **pad)

        row1 = ttk.Frame(opts)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Methods:").pack(side=tk.LEFT)
        self.bilinear_chk = ttk.Checkbutton(
            row1, text="Bilinear", variable=self.bilinear_var
        )
        self.bilinear_chk.pack(side=tk.LEFT, padx=(8, 0))
        self.bicubic_chk = ttk.Checkbutton(
            row1, text="Bicubic", variable=self.bicubic_var
        )
        self.bicubic_chk.pack(side=tk.LEFT, padx=(8, 0))
        self.lanczos_chk = ttk.Checkbutton(
            row1, text="Lanczos", variable=self.lanczos_var
        )
        self.lanczos_chk.pack(side=tk.LEFT, padx=(8, 0))

        row2 = ttk.Frame(opts)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Target resolution:").pack(side=tk.LEFT)
        self.res_combo = ttk.Combobox(
            row2,
            textvariable=self.res_var,
            values=[r[0] for r in RESOLUTIONS],
            state="readonly",
            width=22,
        )
        self.res_combo.pack(side=tk.LEFT, padx=(8, 16))
        ttk.Label(row2, text="CRF:").pack(side=tk.LEFT)
        self.crf_entry = ttk.Entry(row2, textvariable=self.crf_var, width=6)
        self.crf_entry.pack(side=tk.LEFT, padx=(8, 16))
        ttk.Label(row2, text="Preset:").pack(side=tk.LEFT)
        self.preset_combo = ttk.Combobox(
            row2,
            textvariable=self.preset_var,
            values=PRESETS,
            state="readonly",
            width=12,
        )
        self.preset_combo.pack(side=tk.LEFT, padx=(8, 0))

        row3 = ttk.Frame(opts)
        row3.pack(fill=tk.X, pady=2)
        self.pad_chk = ttk.Checkbutton(
            row3,
            text="Keep aspect ratio and pad to target (black bars)",
            variable=self.pad_var,
        )
        self.pad_chk.pack(side=tk.LEFT)
        ttk.Label(row3, text="Existing files:").pack(side=tk.LEFT, padx=(24, 0))
        self.overwrite_combo = ttk.Combobox(
            row3,
            textvariable=self.overwrite_var,
            values=OVERWRITE_POLICIES,
            state="readonly",
            width=12,
        )
        self.overwrite_combo.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(
            opts,
            text="Default scaling stretches to exact WxH (matching 4K frames for A/B). Interpolation only.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(4, 0))

        actions = ttk.Frame(main)
        actions.pack(fill=tk.X, padx=10, pady=8)
        self.start_btn = ttk.Button(
            actions, text="Start", style="Start.TButton", command=self._start
        )
        self.start_btn.pack(side=tk.LEFT, ipadx=24, ipady=4)
        self.stop_btn = ttk.Button(
            actions, text="Stop / Cancel", command=self._stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(12, 0), ipady=4)

        prog = ttk.Frame(main)
        prog.pack(fill=tk.X, padx=10, pady=(0, 4))
        self.bar = ttk.Progressbar(
            prog, variable=self.bar_var, maximum=100.0, mode="determinate"
        )
        self.bar.pack(fill=tk.X)
        ttk.Label(prog, textvariable=self.progress_var).pack(anchor=tk.W, pady=(4, 0))
        self.status_label = ttk.Label(
            prog, textvariable=self.status_var, style="Status.TLabel"
        )
        self.status_label.pack(anchor=tk.W)

        log_fr = ttk.LabelFrame(main, text="Log", padding=6)
        log_fr.pack(fill=tk.BOTH, expand=True, **pad)
        log_wrap = ttk.Frame(log_fr)
        log_wrap.pack(fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_wrap, orient=tk.VERTICAL)
        self.log = tk.Text(
            log_wrap,
            height=12,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
            background="#111111",
            foreground="#DDDDDD",
            insertbackground="#FFFFFF",
        )
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.configure(command=self.log.yview)

    def _option_widgets(self) -> list[tk.Misc]:
        return [
            self.ffmpeg_entry,
            self.add_btn,
            self.remove_btn,
            self.clear_btn,
            self.listbox,
            self.bilinear_chk,
            self.bicubic_chk,
            self.lanczos_chk,
            self.res_combo,
            self.crf_entry,
            self.preset_combo,
            self.pad_chk,
            self.overwrite_combo,
        ]

    def _set_running_ui(self, running: bool) -> None:
        self.running = running
        state = tk.DISABLED if running else tk.NORMAL
        combo_state = "disabled" if running else "readonly"
        for w in self._option_widgets():
            if w in (self.res_combo, self.preset_combo, self.overwrite_combo):
                w.configure(state=combo_state)
            elif w is self.listbox:
                w.configure(state=state)
            else:
                try:
                    w.configure(state=state)
                except tk.TclError:
                    pass
        self.start_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    # ----- ffmpeg path -----

    def _resolve_ffmpeg_on_launch(self) -> None:
        found = resolve_ffmpeg(FFMPEG_ROOT)
        if found:
            self.ffmpeg_var.set(str(found))
            self._log(f"FFmpeg: {found}")
        else:
            self.ffmpeg_var.set(str(FFMPEG_ROOT / "bin" / "ffmpeg.exe"))
            self._log(
                "FFmpeg not found under "
                f"{FFMPEG_ROOT}. Browse to ffmpeg.exe."
            )
            self._set_status("idle")
            self.root.after(
                200,
                lambda: messagebox.showwarning(
                    "FFmpeg not found",
                    "ffmpeg.exe was not found under:\n"
                    f"{FFMPEG_ROOT}\n\n"
                    "Use Browse… to select ffmpeg.exe.",
                    parent=self.root,
                ),
            )

    def _browse_ffmpeg(self) -> None:
        initial = self.ffmpeg_var.get().strip()
        initdir = str(Path(initial).parent) if initial else str(FFMPEG_ROOT)
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Select ffmpeg.exe",
            initialdir=initdir,
            filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.ffmpeg_var.set(path)
            self._log(f"FFmpeg set to: {path}")

    # ----- queue -----

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Select MP4 files",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if paths:
            self._ingest_paths(paths)

    def _ingest_paths(self, paths) -> None:
        added = 0
        skipped = 0
        existing = {p.resolve() for p in self.files}
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_file() and is_mp4(child):
                        key = child.resolve()
                        if key not in existing:
                            self.files.append(child)
                            existing.add(key)
                            added += 1
                continue
            if not is_mp4(p):
                skipped += 1
                continue
            key = p.resolve() if p.exists() else p
            try:
                key = p.resolve()
            except OSError:
                key = p
            if key in existing or p in self.files:
                continue
            self.files.append(p)
            existing.add(key)
            added += 1
        self._refresh_list()
        if added:
            self._log(f"Added {added} file(s).")
        if skipped:
            self._log(f"Skipped {skipped} non-MP4 path(s).")

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for p in self.files:
            self.listbox.insert(tk.END, str(p))
        n = len(self.files)
        self.queue_count_var.set(f"{n} file{'s' if n != 1 else ''}")

    def _remove_selected(self) -> None:
        if self.running:
            return
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            del self.files[i]
        self._refresh_list()

    def _clear_list(self) -> None:
        if self.running:
            return
        self.files.clear()
        self._refresh_list()

    def _select_all(self, _event=None):
        self.listbox.selection_set(0, tk.END)
        return "break"

    # ----- status / log -----

    def _log(self, msg: str) -> None:
        self.ui_queue.put(("log", msg))

    def _set_status(self, text: str) -> None:
        self.ui_queue.put(("status", text))

    def _set_progress(self, text: str, pct: float | None = None) -> None:
        self.ui_queue.put(("progress", (text, pct)))

    def _append_log(self, msg: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg.rstrip() + "\n")
        # keep the widget from growing without bound
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 8000:
            self.log.delete("1.0", "2000.0")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _apply_status(self, text: str) -> None:
        self.status_var.set(text)
        colors = {
            "idle": "#444444",
            "running": "#0B57D0",
            "done": "#0B7A3B",
            "failed": "#B00020",
            "cancelled": "#8A5A00",
        }
        key = text.split()[0].lower() if text else "idle"
        self.status_label.configure(foreground=colors.get(key, "#444444"))

    def _pump_ui(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self._apply_status(payload)
                elif kind == "progress":
                    text, pct = payload
                    self.progress_var.set(text)
                    if pct is None:
                        self.bar_var.set(0.0)
                    else:
                        self.bar_var.set(max(0.0, min(100.0, pct)))
                elif kind == "running":
                    self._set_running_ui(bool(payload))
        except queue.Empty:
            pass
        self.root.after(50, self._pump_ui)

    # ----- start / stop -----

    def _parse_resolution(self) -> tuple[int, int] | None:
        label = self.res_var.get()
        for name, w, h in RESOLUTIONS:
            if name == label:
                return w, h
        return None

    def _snapshot_settings(self) -> RunSettings | None:
        ffmpeg = Path(self.ffmpeg_var.get().strip().strip('"'))
        if not ffmpeg.is_file():
            searched = resolve_ffmpeg(FFMPEG_ROOT)
            if searched and searched.is_file():
                ffmpeg = searched
                self.ffmpeg_var.set(str(ffmpeg))
            else:
                messagebox.showerror(
                    "FFmpeg missing",
                    "ffmpeg.exe was not found.\nBrowse to it and try again.",
                    parent=self.root,
                )
                return None
        methods = []
        if self.bilinear_var.get():
            methods.append("bilinear")
        if self.bicubic_var.get():
            methods.append("bicubic")
        if self.lanczos_var.get():
            methods.append("lanczos")
        if not methods:
            messagebox.showerror(
                "No method selected",
                "Enable Bilinear, Bicubic, Lanczos, or any combination.",
                parent=self.root,
            )
            return None
        res = self._parse_resolution()
        if not res:
            messagebox.showerror("Resolution", "Choose a target resolution.", parent=self.root)
            return None
        try:
            crf = int(self.crf_var.get().strip())
        except ValueError:
            messagebox.showerror("CRF", "CRF must be an integer (typical range 0–51).", parent=self.root)
            return None
        if crf < 0 or crf > 51:
            messagebox.showerror("CRF", "CRF must be between 0 and 51.", parent=self.root)
            return None
        preset = self.preset_var.get().strip()
        if preset not in PRESETS:
            messagebox.showerror("Preset", "Choose a valid x265 preset.", parent=self.root)
            return None
        policy = self.overwrite_var.get().strip()
        if policy not in OVERWRITE_POLICIES:
            policy = "Ask"
        return RunSettings(
            ffmpeg=ffmpeg,
            methods=tuple(methods),
            width=res[0],
            height=res[1],
            crf=crf,
            preset=preset,
            pad=bool(self.pad_var.get()),
            overwrite_policy=policy,
        )

    def _start(self) -> None:
        if self.running:
            return
        settings = self._snapshot_settings()
        if settings is None:
            return
        if not self.files:
            messagebox.showerror("Queue empty", "Add one or more .mp4 files.", parent=self.root)
            return
        jobs: list[tuple[Path, str]] = []
        for src in list(self.files):
            for method in METHODS_ORDER:
                if method in settings.methods:
                    jobs.append((src, method))
        if not jobs:
            return
        self.cancel_event.clear()
        self.overwrite_all = settings.overwrite_policy == "Overwrite"
        self.skip_all = settings.overwrite_policy == "Skip"
        self._set_running_ui(True)
        self._apply_status("running")
        self.progress_var.set(f"Starting… 0 of {len(jobs)}")
        self.bar_var.set(0.0)
        self._append_log("-" * 72)
        self._append_log(
            f"Queue: {len(self.files)} file(s), {len(jobs)} job(s)  |  "
            f"{settings.width}x{settings.height}  CRF {settings.crf}  "
            f"preset {settings.preset}  "
            f"{'pad' if settings.pad else 'stretch'}"
        )
        self._append_log(f"FFmpeg: {settings.ffmpeg}")
        self.worker = threading.Thread(
            target=self._run_jobs, args=(jobs, settings), daemon=True
        )
        self.worker.start()

    def _stop(self) -> None:
        if not self.running:
            return
        self.cancel_event.set()
        with self.proc_lock:
            proc = self.current_proc
        if proc is not None and proc.poll() is None:
            self._log("Stopping current FFmpeg process…")
            _kill_process(proc)

    def _run_jobs(self, jobs: list[tuple[Path, str]], settings: RunSettings) -> None:
        total = len(jobs)
        failures = 0
        completed = 0
        cancelled = False
        try:
            for idx, (src, method) in enumerate(jobs, start=1):
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                dst = output_path_for(src, method)
                label = (
                    f"File {idx} of {total}  ·  {method}  ·  {src.name}"
                )
                self._set_progress(label, 0.0)
                self._log("")
                self._log(f"=== [{idx}/{total}] {method} ===")
                self._log(f"Input : {src}")
                self._log(f"Output: {dst}")

                if not src.is_file():
                    self._log(f"ERROR: input not found: {src}")
                    failures += 1
                    continue

                overwrite = True
                if dst.exists():
                    decision = self._decide_overwrite(dst)
                    if decision == "cancel":
                        cancelled = True
                        break
                    if decision == "skip":
                        self._log(f"Skipping existing output: {dst}")
                        completed += 1
                        self._set_progress(f"{label}  ·  skipped", 100.0)
                        continue
                    overwrite = True

                argv = build_argv(
                    settings.ffmpeg,
                    src,
                    dst,
                    method,
                    settings.width,
                    settings.height,
                    settings.crf,
                    settings.preset,
                    settings.pad,
                    overwrite=overwrite,
                )
                self._log(format_cmd(argv))
                rc, cancelled_job = self._run_ffmpeg(argv, label, idx, total, method)
                if cancelled_job or self.cancel_event.is_set():
                    cancelled = True
                    if dst.exists():
                        self._log(
                            f"Cancelled. Partial output may remain: {dst}"
                        )
                    break
                if rc != 0:
                    self._log(f"FAILED ({method}) exit code {rc}: {src.name}")
                    failures += 1
                else:
                    self._log(f"OK ({method}) -> {dst.name}")
                    completed += 1
        except Exception as exc:
            self._log(f"ERROR: {exc!r}")
            failures += 1
        finally:
            with self.proc_lock:
                self.current_proc = None
            if cancelled:
                self._set_status("cancelled")
                self._set_progress("Cancelled", None)
                self._log("Cancelled by user.")
            elif failures:
                self._set_status("failed")
                self._set_progress(
                    f"Done with errors  ·  {completed} ok, {failures} failed, {total} total",
                    100.0,
                )
                self._log(f"Finished with {failures} failure(s).")
            else:
                self._set_status("done")
                self._set_progress(f"Done  ·  {completed} of {total} job(s)", 100.0)
                self._log("All jobs finished.")
            self.ui_queue.put(("running", False))

    def _decide_overwrite(self, dst: Path) -> str:
        """Return 'overwrite', 'skip', or 'cancel'."""
        if self.overwrite_all:
            return "overwrite"
        if self.skip_all:
            return "skip"
        answer = self._ask_overwrite_dialog(dst)
        if answer == "overwrite_all":
            self.overwrite_all = True
            return "overwrite"
        if answer == "skip_all":
            self.skip_all = True
            return "skip"
        if answer == "overwrite":
            return "overwrite"
        if answer == "skip":
            return "skip"
        return "cancel"

    def _ask_overwrite_dialog(self, dst: Path) -> str:
        box: queue.Queue = queue.Queue()

        def show() -> None:
            win = tk.Toplevel(self.root)
            win.title("Output exists")
            win.transient(self.root)
            win.resizable(False, False)
            result = {"value": "skip"}

            ttk.Label(
                win,
                text="Output already exists:\n" + str(dst),
                wraplength=520,
                justify=tk.LEFT,
            ).pack(padx=16, pady=(16, 8), anchor=tk.W)
            ttk.Label(
                win,
                text="Overwrite this file?",
            ).pack(padx=16, pady=(0, 12), anchor=tk.W)

            btnrow = ttk.Frame(win)
            btnrow.pack(padx=16, pady=(0, 16))

            def choose(value: str) -> None:
                result["value"] = value
                win.destroy()

            ttk.Button(btnrow, text="Overwrite", command=lambda: choose("overwrite")).pack(
                side=tk.LEFT, padx=3
            )
            ttk.Button(btnrow, text="Skip", command=lambda: choose("skip")).pack(
                side=tk.LEFT, padx=3
            )
            ttk.Button(
                btnrow, text="Overwrite all", command=lambda: choose("overwrite_all")
            ).pack(side=tk.LEFT, padx=3)
            ttk.Button(btnrow, text="Skip all", command=lambda: choose("skip_all")).pack(
                side=tk.LEFT, padx=3
            )

            win.protocol("WM_DELETE_WINDOW", lambda: choose("skip"))
            win.grab_set()
            win.update_idletasks()
            try:
                x = self.root.winfo_rootx() + 80
                y = self.root.winfo_rooty() + 80
                win.geometry(f"+{x}+{y}")
            except tk.TclError:
                pass
            win.wait_window()
            box.put(result["value"])

        self.root.after(0, show)
        try:
            return box.get(timeout=3600)
        except queue.Empty:
            return "skip"

    def _run_ffmpeg(
        self,
        argv: list[str],
        label: str,
        idx: int,
        total: int,
        method: str,
    ) -> tuple[int, bool]:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            self._log(f"Could not start FFmpeg: {exc}")
            return 1, False

        with self.proc_lock:
            self.current_proc = proc

        duration = {"sec": None}
        last = {"time": "00:00:00", "speed": "—", "pct": 0.0}

        def emit_progress() -> None:
            dur = duration["sec"]
            pct = last["pct"]
            if dur and dur > 0:
                t = hms_to_seconds(last["time"])
                if t is not None:
                    pct = min(99.5, 100.0 * t / dur)
                    last["pct"] = pct
                dur_txt = seconds_to_hms(dur)
                text = (
                    f"File {idx} of {total}  ·  {method}  ·  "
                    f"{last['time']} / {dur_txt}  ·  {last['speed']}"
                )
            else:
                text = (
                    f"File {idx} of {total}  ·  {method}  ·  "
                    f"{last['time']}  ·  {last['speed']}"
                )
            self._set_progress(text, pct)

        def read_stdout() -> None:
            assert proc.stdout is not None
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    if line.startswith("out_time=") and not line.startswith("out_time_"):
                        val = line.split("=", 1)[1].strip()
                        if val and val != "N/A":
                            # 00:00:01.234567 → 00:00:01
                            last["time"] = val.split(".")[0]
                            emit_progress()
                    elif line.startswith("speed="):
                        val = line.split("=", 1)[1].strip()
                        last["speed"] = val if val else "—"
                        emit_progress()
                    elif line.startswith("progress=") and line.endswith("end"):
                        last["pct"] = 100.0
                        emit_progress()
            except (ValueError, OSError):
                pass
            finally:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

        def read_stderr() -> None:
            assert proc.stderr is not None
            buf = b""
            try:
                while True:
                    chunk = proc.stderr.read(512)
                    if not chunk:
                        break
                    buf += chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                    while b"\n" in buf:
                        line_b, buf = buf.split(b"\n", 1)
                        text = line_b.decode("utf-8", "replace").strip()
                        if not text:
                            continue
                        m = DURATION_RE.search(text)
                        if m and duration["sec"] is None:
                            try:
                                duration["sec"] = (
                                    int(m.group(1)) * 3600
                                    + int(m.group(2)) * 60
                                    + float(m.group(3))
                                )
                            except ValueError:
                                pass
                        if PROGRESS_OVERLAY_RE.search(text):
                            continue
                        self._log(text)
                rest = buf.decode("utf-8", "replace").strip()
                if rest and not PROGRESS_OVERLAY_RE.search(rest):
                    self._log(rest)
            except (ValueError, OSError):
                pass
            finally:
                try:
                    proc.stderr.close()
                except OSError:
                    pass

        t_out = threading.Thread(target=read_stdout, daemon=True)
        t_err = threading.Thread(target=read_stderr, daemon=True)
        t_out.start()
        t_err.start()
        rc = proc.wait()
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        with self.proc_lock:
            if self.current_proc is proc:
                self.current_proc = None
        cancelled = self.cancel_event.is_set()
        if cancelled:
            return rc if rc is not None else 1, True
        return rc, False

    # ----- dnd / close -----

    def _attach_dnd(self) -> None:
        self.drop_hook = _try_enable_file_drop(self.root, self._ingest_paths)
        if self.drop_hook:
            self._log("Drag-and-drop enabled.")
        else:
            self._log("Drag-and-drop not available; use Add files…")

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(
                "Cancel and exit?",
                "A job is running. Stop FFmpeg and quit?",
                parent=self.root,
            ):
                return
            self._stop()
        if self.drop_hook is not None:
            try:
                self.drop_hook.detach()
            except Exception:
                pass
        self.root.destroy()


def _kill_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "NOAI.ClassicUpscale"
            )
        except Exception:
            pass
    root = tk.Tk()
    NoaiClassicUpscaleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
