"""
Frame Splitter v1.0

Batch-splits stereo/side-by-side frames into separate left and right images.

Crop logic (per image):
    crop_y1 = CROP_Y_OFF + CROP_SIZE
    left    = frame[CROP_Y_OFF:crop_y1,         0:CROP_SIZE]
    right   = frame[CROP_Y_OFF:crop_y1, CROP_SIZE:2 * CROP_SIZE]
    if RESIZE:
        left  = cv2.resize(left,  (RESIZE_TO, RESIZE_TO))
        right = cv2.resize(right, (RESIZE_TO, RESIZE_TO))

Defaults (CROP_Y_OFF=60, CROP_SIZE=960, RESIZE_TO=640) give:
    left  = frame[60:1020,   0:960]   -> resized to 640x640
    right = frame[60:1020, 960:1920]  -> resized to 640x640

Images whose dimensions do not match the expected frame size (default
1920x1080) are skipped and listed in the log with their actual size.

Settings (folders, crop values, toggles, window size) are remembered between
runs in %APPDATA%/FrameSplitter/settings.json.

Requires: opencv-python, numpy, tkinter (no Pillow needed).
Run:  .venv/Scripts/python.exe splitImages.py
"""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
import numpy as np

APP_TITLE = "Frame Splitter"
APP_VERSION = "v1.0"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

DEFAULT_CROP_Y_OFF = 60
DEFAULT_CROP_SIZE = 960
DEFAULT_RESIZE_TO = 640
DEFAULT_EXPECT_W = 1920
DEFAULT_EXPECT_H = 1080
DEFAULT_GEOMETRY = "1020x720"

# --- palette -----------------------------------------------------------------
BG = "#14161a"
SURFACE = "#1c1f26"
SURFACE_ALT = "#232733"
BORDER = "#2e3442"
TEXT = "#e6e9ef"
MUTED = "#8a93a3"
ACCENT = "#4c8dff"
ACCENT_DIM = "#3a6fd0"
OK = "#3ecf8e"
WARN = "#f2b544"
ERR = "#ff6b6b"


# --- settings ----------------------------------------------------------------
def settings_path(app="FrameSplitter"):
    """Location of the remembered-settings file (roaming app data on Windows)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, app, "settings.json")


def load_settings(app="FrameSplitter"):
    """Read the settings file. Missing or damaged files just mean 'defaults'."""
    try:
        with open(settings_path(app), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data, app="FrameSplitter"):
    """Write the settings file, replacing it atomically. Never raises."""
    path = settings_path(app)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass  # remembering settings is a convenience, never a reason to fail


# --- image helpers -----------------------------------------------------------
def imread_unicode(path):
    """cv2.imread that survives non-ASCII paths on Windows."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path, img, ext, params=None):
    """cv2.imwrite that survives non-ASCII paths on Windows."""
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    buf.tofile(path)
    return True


def split_frame(frame, y_off, crop_size, resize_to=None, auto_presplit=True):
    """Return (left, right) crops. Raises ValueError if the frame is too small.

    resize_to=None keeps the crops at their native crop_size.
    """
    h, w = frame.shape[:2]

    if auto_presplit and w == 2 * h:
        # Already-split frames (e.g. 1280x640) just get halved.
        left, right = frame[:, :h].copy(), frame[:, h:].copy()
    else:
        y1 = y_off + crop_size
        if y1 > h or 2 * crop_size > w:
            raise ValueError(
                "image is %dx%d, needs at least %dx%d" % (w, h, 2 * crop_size, y1)
            )
        left = frame[y_off:y1, 0:crop_size]
        right = frame[y_off:y1, crop_size:2 * crop_size]

    if resize_to and left.shape[:2] != (resize_to, resize_to):
        interp = cv2.INTER_AREA if left.shape[0] > resize_to else cv2.INTER_LINEAR
        left = cv2.resize(left, (resize_to, resize_to), interpolation=interp)
        right = cv2.resize(right, (resize_to, resize_to), interpolation=interp)

    return left, right


def size_mismatch(frame, expect_w, expect_h):
    """Return a reason string if the frame is not exactly expect_w x expect_h."""
    h, w = frame.shape[:2]
    if w == expect_w and h == expect_h:
        return None
    return "%dx%d (expected %dx%d)" % (w, h, expect_w, expect_h)


def list_images(folder, recursive):
    """Return (abs_path, path_relative_to_folder) pairs, sorted."""
    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(IMAGE_EXTS):
                    full = os.path.join(root, name)
                    found.append((full, os.path.relpath(full, folder)))
    else:
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if os.path.isfile(full) and name.lower().endswith(IMAGE_EXTS):
                found.append((full, name))
    found.sort(key=lambda pair: pair[1].lower())
    return found


def to_photo(bgr, box_w, box_h):
    """Scale a BGR image to fit a box and wrap it in a tk.PhotoImage (via PPM)."""
    h, w = bgr.shape[:2]
    scale = min(box_w / float(w), box_h / float(h))
    disp = cv2.resize(
        bgr,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    ok, buf = cv2.imencode(".ppm", disp)
    if not ok:
        return None
    return tk.PhotoImage(data=buf.tobytes())


def apply_style(root):
    """Dark ttk theme shared by the Frame Splitter and Frame Player."""
    root.configure(bg=BG)
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED,
                    font=("Segoe UI", 9))
    style.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=("Segoe UI Semibold", 16))
    style.configure("Sub.TLabel", background=BG, foreground=MUTED,
                    font=("Segoe UI", 9))
    style.configure("Section.TLabel", background=SURFACE, foreground=ACCENT,
                    font=("Segoe UI Semibold", 9))
    style.configure("Path.TLabel", background=SURFACE_ALT, foreground=TEXT,
                    font=("Consolas", 9), padding=(8, 6))
    style.configure("Status.TLabel", background=BG, foreground=MUTED,
                    font=("Segoe UI", 9))

    style.configure("TButton", background=SURFACE_ALT, foreground=TEXT,
                    borderwidth=0, focusthickness=0, padding=(14, 8))
    style.map("TButton",
              background=[("active", BORDER), ("disabled", SURFACE)],
              foreground=[("disabled", MUTED)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    borderwidth=0, padding=(18, 9),
                    font=("Segoe UI Semibold", 10))
    style.map("Accent.TButton",
              background=[("active", ACCENT_DIM), ("disabled", BORDER)],
              foreground=[("disabled", MUTED)])

    style.configure("Horizontal.TProgressbar", troughcolor=SURFACE_ALT,
                    background=ACCENT, borderwidth=0, thickness=10,
                    lightcolor=ACCENT, darkcolor=ACCENT)

    style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
    style.map("TCheckbutton",
              background=[("active", SURFACE)],
              indicatorcolor=[("selected", ACCENT), ("!selected", SURFACE_ALT)])

    style.configure("TSpinbox", fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=TEXT, arrowcolor=TEXT, borderwidth=0, padding=4)
    style.configure("TCombobox", fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=TEXT, arrowcolor=TEXT, borderwidth=0, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE_ALT)])
    style.configure("TEntry", fieldbackground=SURFACE_ALT, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0, padding=4)


# --- worker ------------------------------------------------------------------
class SplitWorker(threading.Thread):
    """Runs the batch split off the UI thread, reporting through a queue."""

    def __init__(self, files, out_dir, cfg, events, cancel_event):
        threading.Thread.__init__(self, daemon=True)
        self.files = files
        self.out_dir = out_dir
        self.cfg = cfg
        self.events = events
        self.cancel = cancel_event

    def run(self):
        cfg = self.cfg
        ext = cfg["ext"]
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 95] if ext == ".jpg" else []
        done = skipped = failed = 0

        for idx, (src, rel) in enumerate(self.files):
            if self.cancel.is_set():
                self.events.put(("cancelled", (done, skipped, failed)))
                return

            self.events.put(("progress", (idx, rel, done, skipped, failed)))

            frame = imread_unicode(src)
            if frame is None:
                failed += 1
                self.events.put(("log", ("err", "unreadable: %s" % rel)))
                continue

            if cfg["check_size"]:
                reason = size_mismatch(frame, cfg["expect_w"], cfg["expect_h"])
                if reason:
                    skipped += 1
                    self.events.put(
                        ("log", ("warn", "SKIPPED  %s  -  %s" % (rel, reason))))
                    continue

            try:
                left, right = split_frame(
                    frame,
                    cfg["y_off"],
                    cfg["crop_size"],
                    cfg["resize_to"],
                    cfg["auto_presplit"],
                )
            except ValueError as exc:
                skipped += 1
                self.events.put(
                    ("log", ("warn", "SKIPPED  %s  -  %s" % (rel, exc))))
                continue

            sub = os.path.dirname(rel)
            dest_dir = os.path.join(self.out_dir, sub) if sub else self.out_dir
            if sub:
                os.makedirs(dest_dir, exist_ok=True)

            stem = os.path.splitext(os.path.basename(rel))[0]
            left_path = os.path.join(dest_dir, stem + "_left" + ext)
            right_path = os.path.join(dest_dir, stem + "_right" + ext)

            try:
                wrote = imwrite_unicode(left_path, left, ext, params) and imwrite_unicode(
                    right_path, right, ext, params
                )
            except OSError as exc:
                wrote = False
                self.events.put(("log", ("err", "write failed %s (%s)" % (rel, exc))))

            if wrote:
                done += 1
            else:
                failed += 1

        self.events.put(("progress", (len(self.files), "", done, skipped, failed)))
        self.events.put(("finished", (done, skipped, failed)))


# --- app ---------------------------------------------------------------------
class App:
    PREVIEW_W, PREVIEW_H = 520, 300
    THUMB = 150

    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.files = []
        self.preview_src = None      # BGR image used for the preview
        self.photos = {}             # keep PhotoImage refs alive
        self.settings = load_settings()

        self.in_dir = tk.StringVar(value=self._pref_dir("in_dir"))
        self.out_dir = tk.StringVar(value=self._pref_dir("out_dir"))
        self.y_off = tk.IntVar(value=self._pref_int("y_off", DEFAULT_CROP_Y_OFF, 0, 4000))
        self.crop_size = tk.IntVar(
            value=self._pref_int("crop_size", DEFAULT_CROP_SIZE, 1, 8000))
        self.check_size = tk.BooleanVar(value=self._pref_bool("check_size", True))
        self.expect_w = tk.IntVar(
            value=self._pref_int("expect_w", DEFAULT_EXPECT_W, 1, 20000))
        self.expect_h = tk.IntVar(
            value=self._pref_int("expect_h", DEFAULT_EXPECT_H, 1, 20000))
        self.do_resize = tk.BooleanVar(value=self._pref_bool("do_resize", True))
        self.resize_to = tk.IntVar(
            value=self._pref_int("resize_to", DEFAULT_RESIZE_TO, 1, 8000))
        self.recursive = tk.BooleanVar(value=self._pref_bool("recursive", False))
        self.auto_presplit = tk.BooleanVar(value=self._pref_bool("auto_presplit", True))
        self.out_format = tk.StringVar(value=self._pref_choice("out_format",
                                                               ("PNG", "JPG"), "PNG"))
        self.status = tk.StringVar(value="Select a folder of images to begin.")
        self.count_text = tk.StringVar(value="No folder selected")
        self.tally = tk.StringVar(value="")

        self._build_style()
        self._build_ui()
        self._apply_restored_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.pump_job = self.root.after(60, self._pump_events)

    # -- remembered settings --
    def _pref_int(self, key, default, lo, hi):
        """Saved integer, falling back to the default if absent or out of range."""
        try:
            value = int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if lo <= value <= hi else default

    def _pref_bool(self, key, default):
        value = self.settings.get(key)
        return value if isinstance(value, bool) else default

    def _pref_choice(self, key, allowed, default):
        value = self.settings.get(key)
        return value if value in allowed else default

    def _pref_dir(self, key):
        """Saved folder, but only if it still exists on disk."""
        value = self.settings.get(key)
        return value if isinstance(value, str) and os.path.isdir(value) else ""

    def _apply_restored_settings(self):
        """Bring the UI in line with the values restored from disk."""
        geometry = self.settings.get("window")
        if isinstance(geometry, str) and geometry:
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                pass
        self.on_size_toggle()
        self.on_resize_toggle()
        if os.path.isdir(self.in_dir.get()):
            self.rescan()

    def _var_int(self, var, key, default):
        """Current value of an int var, or the last saved one if the field is blank."""
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return self._pref_int(key, default, 0, 20000)

    def _collect_settings(self):
        in_dir = self.in_dir.get()
        out_dir = self.out_dir.get()
        return {
            "in_dir": in_dir if os.path.isdir(in_dir) else "",
            "out_dir": out_dir if not out_dir.startswith("—") else "",
            "y_off": self._var_int(self.y_off, "y_off", DEFAULT_CROP_Y_OFF),
            "crop_size": self._var_int(self.crop_size, "crop_size", DEFAULT_CROP_SIZE),
            "check_size": self.check_size.get(),
            "expect_w": self._var_int(self.expect_w, "expect_w", DEFAULT_EXPECT_W),
            "expect_h": self._var_int(self.expect_h, "expect_h", DEFAULT_EXPECT_H),
            "do_resize": self.do_resize.get(),
            "resize_to": self._var_int(self.resize_to, "resize_to", DEFAULT_RESIZE_TO),
            "recursive": self.recursive.get(),
            "auto_presplit": self.auto_presplit.get(),
            "out_format": self.out_format.get(),
            "window": "%dx%d" % (self.root.winfo_width(), self.root.winfo_height()),
        }

    def on_close(self):
        save_settings(self._collect_settings())
        self.cancel_event.set()
        self.root.after_cancel(self.pump_job)
        self.root.destroy()

    # -- styling --
    def _build_style(self):
        apply_style(self.root)

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        ttk.Label(card, text=title.upper(), style="Section.TLabel").pack(anchor="w")
        return card

    def _spin(self, parent, var, lo, hi, width=10):
        spin = ttk.Spinbox(parent, from_=lo, to=hi, increment=10, width=width,
                           textvariable=var, command=self.refresh_preview)
        spin.bind("<FocusOut>", lambda _e: self.refresh_preview())
        spin.bind("<Return>", lambda _e: self.refresh_preview())
        return spin

    # -- layout --
    def _build_ui(self):
        self.root.title("%s %s" % (APP_TITLE, APP_VERSION))
        self.root.geometry(DEFAULT_GEOMETRY)
        self.root.minsize(940, 660)

        header = ttk.Frame(self.root, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,
                  text="Split side-by-side frames into left / right crops",
                  style="Sub.TLabel").pack(anchor="w")

        body = ttk.Frame(self.root, padding=(20, 4, 20, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=360)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left_col = ttk.Frame(body)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right_col = ttk.Frame(body)
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.rowconfigure(0, weight=1)
        right_col.columnconfigure(0, weight=1)

        self._build_folders(left_col)
        self._build_params(left_col)
        self._build_preview(right_col)
        self._build_footer()

    def _build_folders(self, parent):
        card = self._card(parent, "Folders")
        card.pack(fill="x", pady=(0, 12))

        ttk.Button(card, text="Select input folder…",
                   command=self.pick_input).pack(fill="x", pady=(10, 4))
        ttk.Label(card, textvariable=self.in_dir, style="Path.TLabel",
                  anchor="w", wraplength=310).pack(fill="x")

        ttk.Button(card, text="Select output folder…",
                   command=self.pick_output).pack(fill="x", pady=(10, 4))
        ttk.Label(card, textvariable=self.out_dir, style="Path.TLabel",
                  anchor="w", wraplength=310).pack(fill="x")

        ttk.Checkbutton(card, text="Include subfolders", variable=self.recursive,
                        command=self.rescan).pack(anchor="w", pady=(10, 0))
        ttk.Label(card, textvariable=self.count_text,
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        if not self.in_dir.get():
            self.in_dir.set("— not selected —")
        if not self.out_dir.get():
            self.out_dir.set("— not selected —")

    def _build_params(self, parent):
        card = self._card(parent, "Crop settings")
        card.pack(fill="x")

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(1, weight=1)

        rows = (
            ("Y offset", self.y_off, 0, 4000),
            ("Crop size", self.crop_size, 1, 8000),
        )
        for row, (label, var, lo, hi) in enumerate(rows):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=4)
            spin = self._spin(grid, var, lo, hi, width=10)
            spin.grid(row=row, column=1, sticky="e", pady=4)

        ttk.Label(grid, text="Output format", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        combo = ttk.Combobox(grid, values=("PNG", "JPG"), state="readonly",
                             width=8, textvariable=self.out_format)
        combo.grid(row=2, column=1, sticky="e", pady=4)

        # --- expected frame size gate ---
        size_row = ttk.Frame(card, style="Card.TFrame")
        size_row.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(size_row, text="Only process frames of size",
                        variable=self.check_size,
                        command=self.on_size_toggle).pack(anchor="w")

        size_fields = ttk.Frame(card, style="Card.TFrame")
        size_fields.pack(fill="x", pady=(6, 0))
        self.expect_w_spin = self._spin(size_fields, self.expect_w, 1, 20000, width=7)
        self.expect_w_spin.pack(side="left", padx=(22, 6))
        ttk.Label(size_fields, text="x", style="Muted.TLabel").pack(side="left")
        self.expect_h_spin = self._spin(size_fields, self.expect_h, 1, 20000, width=7)
        self.expect_h_spin.pack(side="left", padx=(6, 0))
        ttk.Label(size_fields, text="px  (others are skipped)",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        # --- optional resize of the output crops ---
        resize_row = ttk.Frame(card, style="Card.TFrame")
        resize_row.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(resize_row, text="Resize output crops to",
                        variable=self.do_resize,
                        command=self.on_resize_toggle).pack(anchor="w")

        resize_fields = ttk.Frame(card, style="Card.TFrame")
        resize_fields.pack(fill="x", pady=(6, 0))
        self.resize_spin = self._spin(resize_fields, self.resize_to, 1, 8000, width=7)
        self.resize_spin.pack(side="left", padx=(22, 6))
        ttk.Label(resize_fields, text="px  (square, e.g. 640)",
                  style="Muted.TLabel").pack(side="left")

        ttk.Checkbutton(card, text="Auto-halve pre-split frames (width = 2 x height)",
                        variable=self.auto_presplit,
                        command=self.refresh_preview).pack(anchor="w", pady=(12, 0))

        self.formula = ttk.Label(card, text="", style="Muted.TLabel",
                                 font=("Consolas", 8), justify="left")
        self.formula.pack(anchor="w", pady=(10, 0))
        self._update_formula()

    def _build_preview(self, parent):
        card = self._card(parent, "Preview")
        card.grid(row=0, column=0, sticky="nsew")

        self.preview_label = tk.Label(card, bg=SURFACE_ALT, fg=MUTED,
                                      text="\nNo preview\n",
                                      width=self.PREVIEW_W, height=self.PREVIEW_H)
        self.preview_label.pack(pady=(10, 4))
        self.preview_caption = ttk.Label(card, text="", style="Muted.TLabel")
        self.preview_caption.pack(anchor="w")

        thumbs = ttk.Frame(card, style="Card.TFrame")
        thumbs.pack(fill="x", pady=(12, 0))
        self.thumb_labels = {}
        for col, side in enumerate(("left", "right")):
            box = ttk.Frame(thumbs, style="Card.TFrame")
            box.grid(row=0, column=col, padx=(0, 12))
            lbl = tk.Label(box, bg=SURFACE_ALT, fg=MUTED, text="—",
                           width=self.THUMB, height=self.THUMB)
            lbl.pack()
            ttk.Label(box, text=side.upper(), style="Muted.TLabel").pack(anchor="w",
                                                                        pady=(4, 0))
            self.thumb_labels[side] = lbl

        log_card = self._card(parent, "Log")
        log_card.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        parent.rowconfigure(1, weight=0)

        self.log = tk.Text(log_card, height=6, bg=SURFACE_ALT, fg=MUTED,
                           insertbackground=TEXT, relief="flat", wrap="none",
                           font=("Consolas", 9), padx=8, pady=6)
        self.log.pack(fill="both", expand=True, pady=(10, 0))
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("info", foreground=MUTED)
        self.log.configure(state="disabled")

    def _build_footer(self):
        footer = ttk.Frame(self.root, padding=(20, 12, 20, 16))
        footer.pack(fill="x")

        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        row = ttk.Frame(footer)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, textvariable=self.status, style="Status.TLabel").pack(side="left")
        ttk.Label(row, textvariable=self.tally, style="Status.TLabel").pack(
            side="left", padx=(16, 0))

        self.cancel_btn = ttk.Button(row, text="Cancel", command=self.cancel_run,
                                     state="disabled")
        self.cancel_btn.pack(side="right", padx=(8, 0))
        self.start_btn = ttk.Button(row, text="Start splitting", style="Accent.TButton",
                                    command=self.start_run)
        self.start_btn.pack(side="right")
        self.open_btn = ttk.Button(row, text="Open output", command=self.open_output,
                                   state="disabled")
        self.open_btn.pack(side="right", padx=(0, 8))

    # -- actions --
    def pick_input(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if not folder:
            return
        self.in_dir.set(folder)
        if not os.path.isdir(self.out_dir.get()):
            self.out_dir.set(os.path.join(folder, "split"))
        self.rescan()

    def pick_output(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.out_dir.set(folder)

    def rescan(self):
        folder = self.in_dir.get()
        if not os.path.isdir(folder):
            return
        self.files = list_images(folder, self.recursive.get())
        self.count_text.set("%d image%s found" % (len(self.files),
                                                  "" if len(self.files) == 1 else "s"))
        self.preview_src = None
        if self.files:
            self.preview_src = imread_unicode(self.files[0][0])
            self.status.set("Ready — %d images queued." % len(self.files))
        else:
            self.status.set("No images found in that folder.")
        self.refresh_preview()

    def on_size_toggle(self):
        state = "normal" if self.check_size.get() else "disabled"
        self.expect_w_spin.configure(state=state)
        self.expect_h_spin.configure(state=state)
        self.refresh_preview()

    def on_resize_toggle(self):
        self.resize_spin.configure(
            state="normal" if self.do_resize.get() else "disabled")
        self.refresh_preview()

    def _read_params(self):
        try:
            y_off = max(0, int(self.y_off.get()))
            crop = max(1, int(self.crop_size.get()))
            resize = max(1, int(self.resize_to.get())) if self.do_resize.get() else None
            expect = (max(1, int(self.expect_w.get())), max(1, int(self.expect_h.get())))
        except (tk.TclError, ValueError):
            return None
        return y_off, crop, resize, expect

    def _update_formula(self):
        params = self._read_params()
        if not params:
            return
        y_off, crop, resize, expect = params
        text = ("left  = frame[%d:%d, %d:%d]\n"
                "right = frame[%d:%d, %d:%d]" %
                (y_off, y_off + crop, 0, crop,
                 y_off, y_off + crop, crop, 2 * crop))
        text += "\nresize -> %dx%d" % (resize, resize) if resize else "\nno resize"
        if self.check_size.get():
            text += "   |   only %dx%d frames" % expect
        self.formula.configure(text=text)

    def refresh_preview(self):
        self._update_formula()
        params = self._read_params()
        if self.preview_src is None or not params:
            return
        y_off, crop, resize, expect = params
        frame = self.preview_src
        h, w = frame.shape[:2]

        overlay = frame.copy()
        y1 = y_off + crop
        cv2.rectangle(overlay, (0, y_off), (crop, y1), (255, 141, 76), 6)
        cv2.rectangle(overlay, (crop, y_off), (2 * crop, y1), (142, 207, 62), 6)

        photo = to_photo(overlay, self.PREVIEW_W, self.PREVIEW_H)
        if photo:
            self.photos["src"] = photo
            self.preview_label.configure(image=photo, text="", width=0, height=0)
        name = os.path.basename(self.files[0][1]) if self.files else ""
        caption = "%s  •  %d x %d" % (name, w, h)
        mismatch = size_mismatch(frame, *expect) if self.check_size.get() else None
        if mismatch:
            caption += "  •  would be SKIPPED (expected %d x %d)" % expect
        self.preview_caption.configure(text=caption)

        try:
            left, right = split_frame(frame, y_off, crop, resize,
                                      self.auto_presplit.get())
        except ValueError as exc:
            for side in ("left", "right"):
                self.thumb_labels[side].configure(image="", text="too small",
                                                  width=18, height=8)
                self.photos.pop(side, None)
            self.status.set("Preview: %s" % exc)
            return

        for side, img in (("left", left), ("right", right)):
            thumb = to_photo(img, self.THUMB, self.THUMB)
            if thumb:
                self.photos[side] = thumb
                self.thumb_labels[side].configure(image=thumb, text="",
                                                  width=0, height=0)

    def start_run(self):
        if self.worker and self.worker.is_alive():
            return
        if not os.path.isdir(self.in_dir.get()):
            self.status.set("Select an input folder first.")
            return
        if not self.files:
            self.rescan()
            if not self.files:
                self.status.set("Nothing to process.")
                return
        params = self._read_params()
        if not params:
            self.status.set("Crop settings must be whole numbers.")
            return
        y_off, crop, resize, expect = params

        out_dir = self.out_dir.get()
        if not out_dir or out_dir.startswith("—"):
            out_dir = os.path.join(self.in_dir.get(), "split")
            self.out_dir.set(out_dir)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            self.status.set("Cannot create output folder: %s" % exc)
            return

        self._clear_log()
        self._append_log("info", "%d images -> %s" % (len(self.files), out_dir))
        if self.check_size.get():
            self._append_log("info", "size filter: only %dx%d frames" % expect)
        self.progress.configure(maximum=len(self.files), value=0)
        self.tally.set("0 split • 0 skipped • 0 failed")
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")
        self.cancel_event.clear()

        cfg = {
            "y_off": y_off,
            "crop_size": crop,
            "resize_to": resize,
            "auto_presplit": self.auto_presplit.get(),
            "check_size": self.check_size.get(),
            "expect_w": expect[0],
            "expect_h": expect[1],
            "ext": ".png" if self.out_format.get() == "PNG" else ".jpg",
        }
        self.worker = SplitWorker(list(self.files), out_dir, cfg,
                                  self.events, self.cancel_event)
        self.worker.start()

    def cancel_run(self):
        self.cancel_event.set()
        self.status.set("Cancelling…")
        self.cancel_btn.configure(state="disabled")

    def open_output(self):
        folder = self.out_dir.get()
        if os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606 - Windows shell open

    # -- log + event pump --
    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, tag, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pump_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    idx, name, done, skipped, failed = payload
                    self.progress.configure(value=idx)
                    total = len(self.files) or 1
                    pct = idx * 100.0 / total
                    if name:
                        self.status.set("%d / %d  (%.0f%%)  —  %s"
                                        % (idx + 1, total, pct, name))
                    self.tally.set("%d split • %d skipped • %d failed"
                                   % (done, skipped, failed))
                elif kind == "log":
                    self._append_log(payload[0], payload[1])
                elif kind in ("finished", "cancelled"):
                    self._on_done(kind, payload)
        except queue.Empty:
            pass
        self.pump_job = self.root.after(60, self._pump_events)

    def _on_done(self, kind, payload):
        done, skipped, failed = payload
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.open_btn.configure(state="normal")
        verb = "Done" if kind == "finished" else "Cancelled"
        summary = "%s — %d split (%d files), %d skipped, %d failed" % (
            verb, done, done * 2, skipped, failed)
        self.status.set(summary)
        self.tally.set("%d split • %d skipped • %d failed" % (done, skipped, failed))
        self._append_log("ok" if kind == "finished" and not failed else "warn", summary)
        if kind == "cancelled":
            self.progress.configure(value=0)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
