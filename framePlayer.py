"""
Frame Player v0.7

VLC-style frame player for building datasets from paired MKV recordings.
You browse the *detected* movie (name.mkv, boxes drawn in) and save the
matching frame from the *raw* movie (name_raw.mkv) as _left / _right crops.

Frames are addressed by index. Both files of a pair were verified to hold the
same frames in the same order, so frame i of one is frame i of the other.

OpenCV cannot seek these files (wrong frame, up to 35 s per seek, and it skips
the first GOP), so decoding goes through PyAV: on open, the packets are demuxed
(no decoding) to list every frame's pts; a frame is then fetched exactly by
seeking to the keyframe before it and decoding forward to its pts.

Requires: av, opencv-python, numpy, tkinter.
Run:  .venv/Scripts/python.exe framePlayer.py [movie.mkv]
"""

import bisect
import os
import re
import sys
import time
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, ttk

import av
import cv2

from splitImages import (
    ACCENT, BG, BORDER, DEFAULT_CROP_SIZE, DEFAULT_RESIZE_TO, ERR, MUTED, OK,
    SURFACE_ALT, TEXT, WARN, apply_style, imwrite_unicode, load_settings,
    save_settings as write_settings, split_frame, to_photo,
)

APP_TITLE = "Frame Player"
APP_VERSION = "v0.7"
SETTINGS_APP = "FramePlayer"

RAW_SUFFIX = "_raw"
VIDEO_EXTS = (".mkv",)
DEFAULT_SIZE = (1440, 920)
# 1920x1536 frames: a 960 crop is one half; 288 centres it vertically.
DEFAULT_Y_OFF = 288
SAVE_EXT = ".png"
FRAMES_DIR_SUFFIX = "_frames"
LEFT_COLOR = (255, 141, 76)      # BGR, same as the Frame Splitter preview
RIGHT_COLOR = (142, 207, 62)
BAD_COLOR = (107, 107, 255)
MARK_COLOR = OK                  # saved-frame ticks on the seek bar
RAW_TAG_COLOR = (68, 181, 242)   # BGR of WARN: "RAW" label on the raw view
SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
MAX_POSITIONS = 200              # remembered per-movie frame positions


# --- pairing -----------------------------------------------------------------
def find_pair(path):
    """Return (detected_path, raw_path) for either file of a pair.

    A missing partner comes back as None.
    """
    root, ext = os.path.splitext(path)
    if root.endswith(RAW_SUFFIX):
        detected, raw = root[:-len(RAW_SUFFIX)] + ext, path
    else:
        detected, raw = path, root + RAW_SUFFIX + ext
    return (detected if os.path.isfile(detected) else None,
            raw if os.path.isfile(raw) else None)


def list_pairs(folder):
    """[(stem, detected_path or None, raw_path or None)] for every movie in folder."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    pairs = {}
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO_EXTS or not os.path.isfile(os.path.join(folder, name)):
            continue
        is_raw = stem.endswith(RAW_SUFFIX)
        base = stem[:-len(RAW_SUFFIX)] if is_raw else stem
        entry = pairs.setdefault(base, [None, None])
        entry[1 if is_raw else 0] = os.path.join(folder, name)
    return [(stem, det, raw) for stem, (det, raw) in
            sorted(pairs.items(), key=lambda item: item[0].lower())]


def saved_indices(folder, stem):
    """0-based frame indices of `stem` whose _left file is in `folder`."""
    found = set()
    if not os.path.isdir(folder):
        return found
    pattern = re.compile(r"^%s_f(\d{6})_left%s$" % (re.escape(stem), re.escape(SAVE_EXT)))
    for name in os.listdir(folder):
        match = pattern.match(name)
        if match:
            found.add(int(match.group(1)) - 1)
    return found


# --- reader ------------------------------------------------------------------
class VideoReader:
    """Exact frame-by-index access to one video file."""

    # Decoding forward beats seeking when the target is this close (frames).
    FORWARD_LIMIT = 30
    # On a cache miss, also keep this many frames before the target.
    BACK_FILL = 100
    # Memory budget for display-size frames.
    CACHE_MB = 600

    def __init__(self, path, display_max=1280):
        self.path = path
        self.pts, self.key_idx = self._index(path)
        if not self.pts:
            raise ValueError("no video frames in %s" % os.path.basename(path))
        self._pts_index = {t: i for i, t in enumerate(self.pts)}

        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self.width = self.stream.codec_context.width
        self.height = self.stream.codec_context.height
        self.time_base = float(self.stream.time_base)
        span = (self.pts[-1] - self.pts[0]) * self.time_base
        # Variable frame rate: report the average, never the container's value.
        self.fps = (len(self.pts) - 1) / span if span > 0 else 0.0

        self._decoder = None    # live decode generator
        self._last = None       # index of the frame it produced last
        self._cache = OrderedDict()   # index -> display-size BGR image (LRU)
        self.display_size = None
        self.set_display_size(display_max)

    @staticmethod
    def _index(path):
        """Sorted pts of every frame, and the frame indices of keyframes."""
        container = av.open(path)
        try:
            stream = container.streams.video[0]
            packets = [(p.pts, p.is_keyframe) for p in container.demux(stream)
                       if p.pts is not None]
        finally:
            container.close()
        pts = sorted(t for t, _ in packets)
        keys = sorted(t for t, k in packets if k)
        lookup = {t: i for i, t in enumerate(pts)}
        return pts, [lookup[t] for t in keys]

    def __len__(self):
        return len(self.pts)

    def seconds(self, index):
        """Time of a frame, measured from the first frame."""
        return (self.pts[index] - self.pts[0]) * self.time_base

    def index_at(self, seconds):
        """Last frame that starts at or before `seconds` (from the first frame)."""
        pts = self.pts[0] + seconds / self.time_base
        return self.clamp(bisect.bisect_right(self.pts, pts) - 1)

    def _keyframe_of(self, index):
        return self.key_idx[max(0, bisect.bisect_right(self.key_idx, index) - 1)]

    def clamp(self, index):
        return max(0, min(index, len(self.pts) - 1))

    def _decode_to(self, index, on_skipped=None):
        """Position the decoder on frame `index` and return its av.VideoFrame.

        Frames decoded on the way are passed to on_skipped(i, frame).
        """
        target = self.pts[index]
        forward = (self._decoder is not None and self._last is not None
                   and self._last < index
                   and (index - self._last <= self.FORWARD_LIMIT
                        or self._keyframe_of(index) == self._keyframe_of(self._last)))
        if not forward:
            self.container.seek(target, stream=self.stream, backward=True,
                                any_frame=False)
            self._decoder = self.container.decode(self.stream)

        for frame in self._decoder:
            if frame.pts is None or frame.pts < target:
                if on_skipped and frame.pts in self._pts_index:
                    on_skipped(self._pts_index[frame.pts], frame)
                continue
            if frame.pts != target:
                self._decoder = None
                raise RuntimeError("frame %d: decoder skipped from pts %d to %d"
                                   % (index, target, frame.pts))
            self._last = index
            return frame

        self._decoder = None
        raise RuntimeError("frame %d: end of stream before pts %d" % (index, target))

    def get(self, index):
        """Decode frame `index` at full resolution as a BGR ndarray."""
        return self._decode_to(self.clamp(index)).to_ndarray(format="bgr24")

    # -- display cache --
    def set_display_size(self, max_side):
        """Size cached display frames so their long side is at most max_side."""
        scale = min(1.0, max_side / float(max(self.width, self.height)))
        size = (max(1, int(self.width * scale)), max(1, int(self.height * scale)))
        if size != self.display_size:
            self.display_size = size
            self._cache.clear()
        frame_bytes = size[0] * size[1] * 3
        self._cache_frames = max(self.BACK_FILL + 10,
                                 self.CACHE_MB * 1024 * 1024 // frame_bytes)

    def _to_display(self, frame):
        w, h = self.display_size
        return frame.to_ndarray(width=w, height=h, format="bgr24")

    def _remember(self, index, image):
        self._cache[index] = image
        self._cache.move_to_end(index)
        while len(self._cache) > self._cache_frames:
            self._cache.popitem(last=False)

    def get_display(self, index, backward=False):
        """Frame `index` scaled to the display size, served from cache if possible.

        Moving backward, a cache miss decodes from the nearest keyframe anyway,
        so the frames just before the target are kept too and the next steps
        back are free. Moving forward they would only cost conversion time.
        """
        index = self.clamp(index)
        image = self._cache.get(index)
        if image is not None:
            self._cache.move_to_end(index)
            return image

        keep_from = index - self.BACK_FILL if backward else index

        def keep(i, frame):
            if i >= keep_from and i not in self._cache:
                self._remember(i, self._to_display(frame))

        image = self._to_display(self._decode_to(index, keep))
        self._remember(index, image)
        return image

    def close(self):
        self.container.close()


def pair_mismatches(det, raw):
    """Human-readable differences that would break index pairing."""
    problems = []
    if len(det) != len(raw):
        problems.append("frame count %d vs %d" % (len(det), len(raw)))
    if (det.width, det.height) != (raw.width, raw.height):
        problems.append("size %dx%d vs %dx%d"
                        % (det.width, det.height, raw.width, raw.height))
    if abs(det.fps - raw.fps) > 0.1:
        problems.append("fps %.2f vs %.2f" % (det.fps, raw.fps))
    return problems


def timecode(seconds):
    minutes, sec = divmod(max(0.0, seconds), 60)
    return "%02d:%06.3f" % (minutes, sec)


# --- app ---------------------------------------------------------------------
class App:
    SEEK_H = 22          # seek bar height (px)
    SEEK_PAD = 10        # space left/right of the track for the handle
    SIDE_W = 270         # side panel width (px)
    MOVIES_CHARS = 24    # movie list width (characters)

    def __init__(self, root):
        self.root = root
        self.settings = load_settings(SETTINGS_APP)
        positions = self.settings.get("positions")
        # movie path -> last frame index
        self.positions = ({k: v for k, v in positions.items() if isinstance(v, int)}
                          if isinstance(positions, dict) else {})
        self.movies = []            # list_pairs() of the current folder
        self.det = None             # VideoReader for the detected movie
        self.raw = None             # VideoReader for the raw movie, or None
        self.index = 0              # frame on screen (0-based)
        self.target = 0             # frame the keys asked for
        self.render_pending = False
        self.playing = False
        self.speed = self.settings.get("speed") if self.settings.get("speed") in SPEEDS else 1.0
        self.play_job = None
        self.play_anchor = None     # (wall clock, movie seconds) playback started from
        self.view_raw = False       # Tab: show the raw movie instead of detected
        self.frame = None           # BGR image currently shown
        self.photo = None           # keep the PhotoImage alive
        self.redraw_job = None

        self.det_text = tk.StringVar(value="No movie loaded")
        self.raw_text = tk.StringVar(value="")
        self.status = tk.StringVar(value="Open a movie with Ctrl+O.")
        self.goto_text = tk.StringVar(value="")
        self.info = {key: tk.StringVar(value="—") for key in
                     ("frame", "time", "fps", "speed", "source", "saved")}
        self.y_off = tk.IntVar(value=self._pref_int("y_off", DEFAULT_Y_OFF, 0, 8000))
        self.crop_size = tk.IntVar(value=self._pref_int("crop_size", DEFAULT_CROP_SIZE, 1, 8000))
        self.do_resize = tk.BooleanVar(value=self._pref_bool("do_resize", True))
        self.resize_to = tk.IntVar(value=self._pref_int("resize_to", DEFAULT_RESIZE_TO, 1, 8000))
        out_dir = self.settings.get("out_dir")
        # Output folder picked by the user, if any (created on the first save).
        self.out_custom = out_dir if isinstance(out_dir, str) and out_dir else None
        self.movies_title = tk.StringVar(value="MOVIES IN FOLDER")
        self.saved = set()          # 0-based indices already in the output folder
        self.saved_order = []       # self.saved sorted, mirrors the list box rows
        self.undo_stack = []        # indices saved this session, newest last
        self.saved_title = tk.StringVar(value="SAVED FRAMES")
        self.out_text = tk.StringVar(value="—")

        apply_style(self.root)
        self._build_ui()
        self.root.bind("<Control-o>", lambda _e: self.pick_movie())
        self.root.bind("<Control-g>", lambda _e: self.focus_goto())
        for keys, delta in (("<Left>", -1), ("<Right>", 1),
                            ("<Shift-Left>", -10), ("<Shift-Right>", 10),
                            ("<Control-Shift-Left>", -100),
                            ("<Control-Shift-Right>", 100)):
            self.root.bind(keys, self._nav_key(delta))
        self.root.bind("<Home>", self._nav_key(-10 ** 9))
        self.root.bind("<End>", self._nav_key(10 ** 9))
        self.root.bind("<s>", self._key(self.save_frame))
        self.root.bind("<S>", self._key(self.save_frame))
        self.root.bind("<Delete>", self._key(self.delete_current))
        self.root.bind("<Control-z>", self._key(self.undo_save))
        self.root.bind("<space>", self._key(self.toggle_play))
        self.root.bind("<bracketleft>", self._key(lambda: self.change_speed(-1)))
        self.root.bind("<bracketright>", self._key(lambda: self.change_speed(1)))
        self.root.bind("<Tab>", self._key(self.toggle_view))
        self.root.bind("<Prior>", self._key(lambda: self.switch_movie(-1)))
        self.root.bind("<Next>", self._key(lambda: self.switch_movie(1)))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _key(self, action):
        """Bind a player shortcut that stays out of the way of text fields."""
        def handler(event):
            if isinstance(event.widget, (tk.Entry, ttk.Entry)):   # incl. Spinbox
                return None
            action()
            return "break"
        return handler

    def _nav_key(self, delta):
        return self._key(lambda: self.step(delta))

    # -- layout --
    def _build_ui(self):
        self.root.title("%s %s" % (APP_TITLE, APP_VERSION))
        # Never start larger than the screen minus room for the taskbar.
        width = min(DEFAULT_SIZE[0], self.root.winfo_screenwidth() - 40)
        height = min(DEFAULT_SIZE[1], self.root.winfo_screenheight() - 90)
        self.root.geometry("%dx%d+20+10" % (width, height))
        self._restore_window()
        self.root.minsize(800, 600)

        header = ttk.Frame(self.root, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Button(header, text="Open movie…", style="Accent.TButton",
                   command=self.pick_movie).pack(side="right")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, textvariable=self.det_text, style="Sub.TLabel").pack(anchor="w")
        self.raw_label = ttk.Label(header, textvariable=self.raw_text, style="Sub.TLabel")
        self.raw_label.pack(anchor="w")

        footer = ttk.Frame(self.root, padding=(20, 8, 20, 12))
        footer.pack(side="bottom", fill="x")

        self.seekbar = tk.Canvas(footer, height=self.SEEK_H, bg=BG, bd=0,
                                 highlightthickness=0, cursor="hand2")
        self.seekbar.pack(fill="x", pady=(0, 8))
        self.seekbar.bind("<Configure>", lambda _e: self._draw_seekbar())
        self.seekbar.bind("<Button-1>", self._on_seek)
        self.seekbar.bind("<B1-Motion>", self._on_seek)

        info = ttk.Frame(footer)
        info.pack(fill="x")
        goto = ttk.Frame(info)
        goto.pack(side="right")
        ttk.Label(goto, text="GO TO FRAME  (Ctrl+G)", style="Status.TLabel").pack(anchor="w")
        self.goto_entry = ttk.Entry(goto, width=10, textvariable=self.goto_text,
                                    font=("Consolas", 12))
        self.goto_entry.pack(anchor="w")
        self.goto_entry.bind("<Return>", self._on_goto)
        self.goto_entry.bind("<KP_Enter>", self._on_goto)
        self.goto_entry.bind("<Escape>", lambda _e: self.root.focus_set())
        for key, title in (("frame", "Frame"), ("time", "Time"), ("fps", "FPS"),
                           ("speed", "Speed"), ("source", "Showing"),
                           ("saved", "Saved")):
            cell = ttk.Frame(info)
            cell.pack(side="left", padx=(0, 28))
            ttk.Label(cell, text=title.upper(), style="Status.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=self.info[key],
                      font=("Consolas", 12)).pack(anchor="w")
        self.status_label = ttk.Label(footer, textvariable=self.status,
                                      style="Status.TLabel")
        self.status_label.pack(anchor="w", pady=(8, 0))

        self._build_side()
        self._build_movies()

        self.video = tk.Label(self.root, bg="#000000", fg=MUTED, bd=0,
                              highlightthickness=1, highlightbackground=BORDER,
                              text="No movie", font=("Segoe UI", 11))
        self.video.pack(fill="both", expand=True, padx=12)
        self.video.bind("<Configure>", lambda _e: self._schedule_redraw())
        self.video.bind("<Button-1>", lambda _e: self.root.focus_set())

    def _build_movies(self):
        left = ttk.Frame(self.root, padding=(20, 0, 0, 12))
        left.pack(side="left", fill="y")
        card = self._card(left, textvariable=self.movies_title)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="Click to open  •  PgUp / PgDn", style="Muted.TLabel").pack(
            anchor="w", pady=(4, 8))
        holder = ttk.Frame(card, style="Card.TFrame")
        holder.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical")
        self.movie_list = tk.Listbox(
            holder, width=self.MOVIES_CHARS, bg=SURFACE_ALT, fg=TEXT,
            selectbackground=ACCENT, selectforeground="#ffffff", highlightthickness=0,
            bd=0, activestyle="none", font=("Consolas", 10), exportselection=False,
            yscrollcommand=scroll.set)
        scroll.configure(command=self.movie_list.yview)
        scroll.pack(side="right", fill="y")
        self.movie_list.pack(side="left", fill="both", expand=True)
        self.movie_list.bind("<<ListboxSelect>>", self._on_movie_select)
        self.movie_list.bind("<space>", lambda _e: (self.toggle_play(), "break")[1])

    def _build_side(self):
        side = ttk.Frame(self.root, padding=(0, 0, 20, 0))
        side.pack(side="right", fill="y")

        crop = self._card(side, "Crop  —  S saves")
        crop.pack(fill="x")
        grid = ttk.Frame(crop, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        ttk.Label(grid, text="Y offset", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self._spin(grid, self.y_off, 0, 8000).grid(row=0, column=1, sticky="e", pady=3)
        ttk.Label(grid, text="Crop size", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        self._spin(grid, self.crop_size, 1, 8000).grid(row=1, column=1, sticky="e", pady=3)
        ttk.Checkbutton(grid, text="Resize to", variable=self.do_resize,
                        command=self._on_resize_toggle).grid(row=2, column=0, sticky="w")
        self.resize_spin = self._spin(grid, self.resize_to, 1, 8000)
        self.resize_spin.grid(row=2, column=1, sticky="e", pady=3)
        grid.columnconfigure(0, weight=1)
        self.formula = ttk.Label(crop, style="Muted.TLabel", font=("Consolas", 9),
                                 justify="left")
        self.formula.pack(anchor="w", pady=(10, 0))

        out = self._card(side, "Output folder")
        out.pack(fill="x", pady=(12, 0))
        ttk.Label(out, textvariable=self.out_text, style="Path.TLabel",
                  wraplength=self.SIDE_W - 30).pack(fill="x", pady=(10, 8))
        row = ttk.Frame(out, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="Change…", command=self.pick_output).pack(side="left")
        ttk.Button(row, text="Default", command=self.reset_output).pack(side="left", padx=6)
        ttk.Button(row, text="Open", command=self.open_output).pack(side="left")
        ttk.Label(out, text="File names:  <movie>_f000123_left.png / _right.png",
                  style="Muted.TLabel", wraplength=self.SIDE_W - 30).pack(anchor="w", pady=(8, 0))

        saved = self._card(side, textvariable=self.saved_title)
        saved.pack(fill="both", expand=True, pady=(12, 12))
        ttk.Label(saved, text="Click to jump  •  Del removes current  •  Ctrl+Z undo",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 8))
        holder = ttk.Frame(saved, style="Card.TFrame")
        holder.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical")
        self.saved_list = tk.Listbox(
            holder, bg=SURFACE_ALT, fg=TEXT, selectbackground=ACCENT,
            selectforeground="#ffffff", highlightthickness=0, bd=0, activestyle="none",
            font=("Consolas", 10), exportselection=False, yscrollcommand=scroll.set)
        scroll.configure(command=self.saved_list.yview)
        scroll.pack(side="right", fill="y")
        self.saved_list.pack(side="left", fill="both", expand=True)
        self.saved_list.bind("<<ListboxSelect>>", self._on_saved_select)
        # Space in a list box would select the active row; keep it for playback.
        self.saved_list.bind("<space>", lambda _e: (self.toggle_play(), "break")[1])

        self._on_resize_toggle()

    def _card(self, parent, title="", textvariable=None):
        card = ttk.Frame(parent, style="Card.TFrame", padding=14, width=self.SIDE_W)
        ttk.Label(card, text=title.upper(), textvariable=textvariable,
                  style="Section.TLabel").pack(anchor="w")
        return card

    def _spin(self, parent, var, lo, hi):
        spin = ttk.Spinbox(parent, from_=lo, to=hi, increment=10, width=7,
                           textvariable=var, command=self._on_crop_change)
        spin.bind("<FocusOut>", lambda _e: self._on_crop_change())
        spin.bind("<Return>", lambda _e: (self._on_crop_change(), self.root.focus_set()))
        spin.bind("<Escape>", lambda _e: self.root.focus_set())
        return spin

    def _set_status(self, text, color=MUTED):
        self.status.set(text)
        self.status_label.configure(foreground=color)

    # -- loading --
    def pick_movie(self):
        path = filedialog.askopenfilename(
            title="Open movie (detected or _raw)",
            filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        det_path, raw_path = find_pair(os.path.abspath(path))
        if det_path is None:
            # Only the raw file exists: browse it, nothing to save from.
            det_path, raw_path = raw_path, None
        if det_path is None:
            self._set_status("File not found: %s" % path, ERR)
            return

        self.pause()
        if self.det is not None:
            self.positions[self.det.path] = self.index
        self._set_status("Indexing %s…" % os.path.basename(det_path))
        self.root.update_idletasks()
        self._close_readers()
        try:
            self.det = VideoReader(det_path)
            self.raw = VideoReader(raw_path) if raw_path else None
        except (av.FFmpegError, ValueError, OSError) as exc:
            self._close_readers()
            self._set_status("Cannot open movie: %s" % exc, ERR)
            return

        self.det_text.set("Detected  %s   •   %dx%d   •   %d frames   •   %s"
                          % (det_path, self.det.width, self.det.height,
                             len(self.det), timecode(self.det.seconds(len(self.det) - 1))))
        if self.raw is None:
            self.raw_text.set("Raw  %s not found — saving disabled"
                              % (os.path.splitext(os.path.basename(det_path))[0]
                                 + RAW_SUFFIX + ".mkv"))
            self.raw_label.configure(foreground=WARN)
            self._set_status("Loaded without a raw partner.", WARN)
        else:
            problems = pair_mismatches(self.det, self.raw)
            self.raw_text.set("Raw       %s" % raw_path)
            if problems:
                self.raw_label.configure(foreground=WARN)
                self._set_status("Pair mismatch: %s — saved frames may not match."
                                 % "; ".join(problems), WARN)
            else:
                self.raw_label.configure(foreground=MUTED)
                self._set_status("Pair OK — %d frames in both files." % len(self.det), OK)

        self.root.title("%s — %s" % (APP_TITLE, os.path.basename(det_path)))
        self.index = self.target = 0
        self.view_raw = False
        self._refresh_output()
        self._on_crop_change()
        self._refresh_movies()
        resume = self.positions.pop(det_path, 0)    # re-added on save: newest last
        resume = self.det.clamp(resume if isinstance(resume, int) else 0)
        self.show(resume)
        if resume:
            self._set_status("%s  Resumed at frame %d." % (self.status.get(), resume + 1),
                             self.status_label.cget("foreground"))
        self.save_settings()

    def _close_readers(self):
        for reader in (self.det, self.raw):
            if reader is not None:
                reader.close()
        self.det = self.raw = None

    # -- playback --
    def toggle_play(self):
        if self.det is None:
            return
        if self.playing:
            self.pause()
            return
        if self.index >= len(self.det) - 1:
            self.show(0)
        self.playing = True
        self.play_anchor = None
        self._update_info()
        self._play_tick()

    def pause(self):
        self.playing = False
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job = None
        if self.det is not None:
            self._update_info()

    def change_speed(self, direction):
        pos = SPEEDS.index(self.speed) + direction
        self.speed = SPEEDS[max(0, min(pos, len(SPEEDS) - 1))]
        self.play_anchor = None
        if self.det is not None:
            self._update_info()

    def _play_tick(self):
        """Show the frame that is due by the wall clock, then sleep until the next.

        Pacing follows each frame's own timestamp (the movies have a variable
        frame rate). If decoding falls behind, frames are skipped, not slowed.
        """
        self.play_job = None
        if not self.playing or self.det is None:
            return
        if self.target != self.index:           # the user navigated meanwhile
            self.show(self.target)
            self.play_anchor = None
        now = time.perf_counter()
        if self.play_anchor is None:
            self.play_anchor = (now, self.det.seconds(self.index))
        start_wall, start_media = self.play_anchor
        due = self.det.index_at(start_media + (now - start_wall) * self.speed)
        if due > self.index:
            self.show(due)
        last = len(self.det) - 1
        if self.index >= last:
            self.pause()
            self._set_status("End of movie.")
            return
        next_at = start_wall + (self.det.seconds(self.index + 1) - start_media) / self.speed
        delay_ms = int((next_at - time.perf_counter()) * 1000)
        self.play_job = self.root.after(max(1, delay_ms), self._play_tick)

    # -- view --
    def _reader(self):
        """The movie on screen: raw when toggled with Tab, else detected."""
        return self.raw if self.view_raw and self.raw is not None else self.det

    def toggle_view(self):
        if self.det is None:
            return
        if self.raw is None:
            self._set_status("No %s file — only the detected movie can be shown." % RAW_SUFFIX,
                             WARN)
            return
        self.view_raw = not self.view_raw
        self.show(self.target)

    # -- navigation --
    def step(self, delta):
        if self.det is not None:
            self.go_to(self.target + delta)

    def go_to(self, index):
        """Show frame `index`. Requests that arrive while a frame is still
        decoding (key repeat, seek-bar drag) only move the target, so the
        display jumps to the latest one instead of lagging behind."""
        if self.det is None:
            return
        self.play_anchor = None     # playback continues from the new position
        self.target = self.det.clamp(index)
        if not self.render_pending:
            self.render_pending = True
            self.root.after_idle(self._render_target)

    def _render_target(self):
        self.render_pending = False
        if self.det is not None and self.target != self.index:
            self.show(self.target)

    def focus_goto(self):
        self.goto_entry.focus_set()
        self.goto_entry.select_range(0, "end")

    def _on_goto(self, _event=None):
        if self.det is None:
            return "break"
        try:
            number = int(self.goto_text.get().strip())
        except ValueError:
            self._set_status("Go to: enter a frame number 1–%d." % len(self.det), WARN)
            return "break"
        self.go_to(number - 1)      # the box uses the 1-based overlay numbers
        self.root.focus_set()
        return "break"

    def _on_seek(self, event):
        if self.det is None:
            return
        width = self.seekbar.winfo_width() - 2 * self.SEEK_PAD
        fraction = (event.x - self.SEEK_PAD) / float(max(1, width))
        self.go_to(round(min(1.0, max(0.0, fraction)) * (len(self.det) - 1)))

    def _draw_seekbar(self):
        bar = self.seekbar
        bar.delete("all")
        w, h, pad = bar.winfo_width(), self.SEEK_H, self.SEEK_PAD
        mid = h // 2
        bar.create_line(pad, mid, w - pad, mid, fill=SURFACE_ALT, width=6,
                        capstyle="round")
        if self.det is None or len(self.det) < 2:
            return
        span = (w - 2 * pad) / float(len(self.det) - 1)
        x = pad + span * self.index
        bar.create_line(pad, mid, x, mid, fill=ACCENT, width=6, capstyle="round")
        for i in self.saved_order:
            mx = pad + span * i
            bar.create_line(mx, 1, mx, h - 1, fill=MARK_COLOR, width=2)
        r = mid - 3
        bar.create_oval(x - r, mid - r, x + r, mid + r, fill=TEXT, outline="")

    # -- display --
    def show(self, index):
        index = self.det.clamp(index)
        # Small steps back get the back-fill; long jumps (seek drags) don't.
        backward = 0 < self.index - index <= VideoReader.BACK_FILL
        try:
            self.frame = self._reader().get_display(index, backward=backward)
        except (av.FFmpegError, RuntimeError) as exc:
            self.target = self.index
            self._set_status(str(exc), ERR)
            return
        self.index = self.target = index
        self._redraw()
        self._update_info()
        self._draw_seekbar()
        self._select_current_in_list()

    def _update_info(self):
        total = len(self.det)
        # Numbered from 1 to match the frame number burned into the video.
        self.info["frame"].set("%d / %d" % (self.index + 1, total))
        self.info["time"].set("%s / %s" % (timecode(self.det.seconds(self.index)),
                                           timecode(self.det.seconds(total - 1))))
        self.info["fps"].set("%.2f" % self.det.fps)
        self.info["speed"].set("%.2fx  %s" % (self.speed, "PLAY" if self.playing else "PAUSE"))
        self.info["source"].set("RAW" if self._reader() is self.raw else "DETECTED")
        self.info["saved"].set(str(len(self.saved)))

    def _schedule_redraw(self):
        if self.redraw_job:
            self.root.after_cancel(self.redraw_job)
        self.redraw_job = self.root.after(50, self._redraw)

    def _redraw(self):
        self.redraw_job = None
        if self.frame is None:
            return
        box_w = max(1, self.video.winfo_width() - 2)
        box_h = max(1, self.video.winfo_height() - 2)
        self.photo = to_photo(self._with_overlay(self.frame), box_w, box_h)
        if self.photo:
            self.video.configure(image=self.photo, text="")

    def _with_overlay(self, image):
        """Draw the left / right crop boxes (scaled to the display frame)."""
        params = self._crop_params()
        if params is None or self.det is None:
            return image
        y_off, crop, _resize = params
        scale = image.shape[1] / float(self.det.width)
        bad = self._crop_problem(params) is not None
        out = image.copy()
        y0, y1 = int(y_off * scale), int((y_off + crop) * scale)
        for x0, x1, color, name in ((0, crop, LEFT_COLOR, "LEFT"),
                                    (crop, 2 * crop, RIGHT_COLOR, "RIGHT")):
            color = BAD_COLOR if bad else color
            p0, p1 = (int(x0 * scale), y0), (int(x1 * scale) - 1, y1)
            cv2.rectangle(out, p0, p1, color, 2)
            cv2.putText(out, name, (p0[0] + 8, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color, 2, cv2.LINE_AA)
        if self.index in self.saved:
            cv2.putText(out, "SAVED", (out.shape[1] - 110, 34), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (142, 207, 62), 2, cv2.LINE_AA)
        if self._reader() is self.raw:
            cv2.putText(out, "RAW", (12, 34), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, RAW_TAG_COLOR, 2, cv2.LINE_AA)
        return out

    # -- crop --
    def _crop_params(self):
        try:
            y_off = max(0, int(self.y_off.get()))
            crop = max(1, int(self.crop_size.get()))
            resize = max(1, int(self.resize_to.get())) if self.do_resize.get() else None
        except (tk.TclError, ValueError):
            return None
        return y_off, crop, resize

    def _crop_problem(self, params):
        """Why the crop doesn't fit the movie's frames, or None if it does."""
        if self.det is None:
            return None
        y_off, crop, _resize = params
        if y_off + crop > self.det.height or 2 * crop > self.det.width:
            return ("crop needs %dx%d, frames are %dx%d"
                    % (2 * crop, y_off + crop, self.det.width, self.det.height))
        return None

    def _on_crop_change(self):
        params = self._crop_params()
        if params is None:
            return
        y_off, crop, resize = params
        text = ("left  = frame[%d:%d, %d:%d]\nright = frame[%d:%d, %d:%d]\n"
                % (y_off, y_off + crop, 0, crop, y_off, y_off + crop, crop, 2 * crop))
        text += "resize -> %dx%d" % (resize, resize) if resize else "no resize"
        self.formula.configure(text=text)
        problem = self._crop_problem(params)
        if problem:
            self._set_status("Crop does not fit: %s." % problem, ERR)
        self._redraw()

    def _on_resize_toggle(self):
        self.resize_spin.configure(state="normal" if self.do_resize.get() else "disabled")
        self._on_crop_change()

    # -- output --
    def movie_stem(self):
        return os.path.splitext(os.path.basename(self.det.path))[0]

    def output_dir(self):
        if self.out_custom:
            return self.out_custom
        if self.det is None:
            return ""
        return os.path.join(os.path.dirname(self.det.path),
                            self.movie_stem() + FRAMES_DIR_SUFFIX)

    def frame_paths(self, index):
        """Left / right file names; numbered from 1 like the video overlay."""
        base = os.path.join(self.output_dir(), "%s_f%06d" % (self.movie_stem(), index + 1))
        return base + "_left" + SAVE_EXT, base + "_right" + SAVE_EXT

    def _refresh_output(self):
        """Re-read which frames of this movie are already in the output folder."""
        self.out_text.set(self.output_dir() or "—")
        self.saved = (saved_indices(self.output_dir(), self.movie_stem())
                      if self.det is not None else set())
        self.undo_stack = []        # undo only covers saves into this folder
        self._sync_saved()

    def pick_output(self):
        folder = filedialog.askdirectory(title="Select output folder",
                                         initialdir=self.output_dir() or None)
        if folder:
            self.out_custom = os.path.normpath(folder)
            self._refresh_output()

    def reset_output(self):
        self.out_custom = None
        self._refresh_output()

    def open_output(self):
        folder = self.output_dir()
        if os.path.isdir(folder):
            os.startfile(folder)
        else:
            self._set_status("Output folder does not exist yet — save a frame first.", WARN)

    def save_frame(self):
        """S: save the raw frame at the current index as _left / _right crops."""
        if self.det is None:
            return
        if self.raw is None:
            self._set_status("Saving disabled: no %s file for this movie." % RAW_SUFFIX, WARN)
            return
        params = self._crop_params()
        if params is None:
            self._set_status("Crop values are not valid numbers.", ERR)
            return
        problem = self._crop_problem(params)
        if problem:
            self._set_status("Not saved: crop does not fit (%s)." % problem, ERR)
            return

        # S can arrive before queued arrow keys were drawn: catch up first, so
        # the saved frame is the one the keys asked for and the one on screen.
        if self.target != self.index:
            self.show(self.target)
            if self.target != self.index:
                return      # decode failed; show() reported it
        index = self.index
        left_path, right_path = self.frame_paths(index)
        if os.path.exists(left_path) and os.path.exists(right_path):
            self._set_status("Frame %d already saved — skipped." % (index + 1), WARN)
            return

        y_off, crop, resize = params
        try:
            frame = self.raw.get(index)
            left, right = split_frame(frame, y_off, crop, resize, auto_presplit=False)
            os.makedirs(self.output_dir(), exist_ok=True)
            ok = (imwrite_unicode(left_path, left, SAVE_EXT)
                  and imwrite_unicode(right_path, right, SAVE_EXT))
        except (av.FFmpegError, RuntimeError, ValueError, OSError) as exc:
            self._set_status("Frame %d not saved: %s" % (index + 1, exc), ERR)
            return
        if not ok:
            self._set_status("Frame %d not saved: PNG encoding failed." % (index + 1), ERR)
            return

        self.saved.add(index)
        self.undo_stack.append(index)
        self._sync_saved()
        self._set_status("Saved frame %d  →  %s  (+ _right)"
                         % (index + 1, os.path.basename(left_path)), OK)

    # -- saved frames --
    def _sync_saved(self):
        """Bring list, seek-bar ticks, counter and overlay in line with self.saved."""
        self.saved_order = sorted(self.saved)
        self.saved_title.set("SAVED FRAMES  (%d)" % len(self.saved_order))
        self.saved_list.delete(0, "end")
        for i in self.saved_order:
            label = "%06d" % (i + 1)
            if self.det is not None:
                label += "   %s" % timecode(self.det.seconds(i))
            self.saved_list.insert("end", label)
        self._select_current_in_list()
        if self.det is not None:
            self._update_info()
            self._draw_seekbar()
            self._redraw()

    def _select_current_in_list(self):
        """Highlight the current frame in the list if it is a saved one."""
        self.saved_list.selection_clear(0, "end")
        pos = bisect.bisect_left(self.saved_order, self.index)
        if pos < len(self.saved_order) and self.saved_order[pos] == self.index:
            self.saved_list.selection_set(pos)
            self.saved_list.see(pos)

    def _on_saved_select(self, _event=None):
        picked = self.saved_list.curselection()
        if picked and self.det is not None:
            self.go_to(self.saved_order[picked[0]])

    def _remove_saved(self, index):
        """Delete the _left / _right files of one frame. True if anything was removed."""
        removed = False
        for path in self.frame_paths(index):
            try:
                os.remove(path)
                removed = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._set_status("Could not delete %s: %s" % (os.path.basename(path), exc), ERR)
                return False
        self.saved.discard(index)
        if index in self.undo_stack:
            self.undo_stack.remove(index)
        self._sync_saved()
        return removed

    def delete_current(self):
        """Del: remove the current frame's saved files."""
        if self.det is None:
            return
        if self.target != self.index:
            self.show(self.target)
        if self.index not in self.saved:
            self._set_status("Frame %d is not saved — nothing to delete." % (self.index + 1), WARN)
            return
        if self._remove_saved(self.index):
            self._set_status("Deleted saved frame %d." % (self.index + 1), OK)

    def undo_save(self):
        """Ctrl+Z: remove the most recent save of this session and jump to it."""
        if not self.undo_stack:
            self._set_status("Nothing to undo — no frames saved in this session.", WARN)
            return
        index = self.undo_stack[-1]
        if self._remove_saved(index):
            self.go_to(index)
            self._set_status("Undone: frame %d removed." % (index + 1), OK)

    # -- movies in folder --
    def _refresh_movies(self):
        """List every movie pair in the current movie's folder."""
        self.movies = list_pairs(os.path.dirname(self.det.path)) if self.det else []
        self.movie_list.delete(0, "end")
        current = None
        for row, (stem, det_path, raw_path) in enumerate(self.movies):
            if raw_path is None:
                note = "no raw"
            else:
                folder = self.out_custom or os.path.join(os.path.dirname(det_path or raw_path),
                                                         stem + FRAMES_DIR_SUFFIX)
                count = len(saved_indices(folder, stem))
                note = "%d saved" % count if count else ""
            self.movie_list.insert("end", "%s  %s" % (stem, note) if note else stem)
            if raw_path is None:
                self.movie_list.itemconfigure(row, foreground=WARN)
            if self.det is not None and self.det.path in (det_path, raw_path):
                current = row
        self.movies_title.set("MOVIES IN FOLDER  (%d)" % len(self.movies))
        if current is not None:
            self.movie_list.selection_set(current)
            self.movie_list.see(current)

    def _on_movie_select(self, _event=None):
        picked = self.movie_list.curselection()
        if not picked:
            return
        _stem, det_path, raw_path = self.movies[picked[0]]
        path = det_path or raw_path
        if self.det is None or self.det.path != path:
            self.load(path)

    def switch_movie(self, direction):
        """PgUp / PgDn: previous / next movie in the folder."""
        if not self.movies or self.det is None:
            return
        paths = [det or raw for _stem, det, raw in self.movies]
        row = paths.index(self.det.path) if self.det.path in paths else 0
        row = max(0, min(row + direction, len(paths) - 1))
        if paths[row] != self.det.path:
            self.load(paths[row])

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

    def _var_int(self, var, key, default):
        """Current value of an int var, or the last saved one if the field is blank."""
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return self._pref_int(key, default, 0, 20000)

    def _restore_window(self):
        geometry = self.settings.get("window")
        if not (isinstance(geometry, str)
                and re.match(r"^\d+x\d+[+-]-?\d+[+-]-?\d+$", geometry)):
            return
        width, height = (int(v) for v in re.match(r"^(\d+)x(\d+)", geometry).groups())
        # A window saved on a bigger monitor would not fit: keep the default.
        if (width <= self.root.winfo_screenwidth()
                and height <= self.root.winfo_screenheight()):
            self.root.geometry(geometry)
        if self.settings.get("zoomed") is True:
            self.root.state("zoomed")

    def save_settings(self):
        if self.det is not None:
            self.positions[self.det.path] = self.index
        # Keep the newest positions only; dict order is insertion order.
        while len(self.positions) > MAX_POSITIONS:
            self.positions.pop(next(iter(self.positions)))
        zoomed = self.root.state() == "zoomed"
        if zoomed:
            geometry = self.settings.get("window", "")
        else:
            geometry = self.root.geometry()
        write_settings({
            "last_movie": self.det.path if self.det is not None else
                          self.settings.get("last_movie", ""),
            "positions": self.positions,
            "y_off": self._var_int(self.y_off, "y_off", DEFAULT_Y_OFF),
            "crop_size": self._var_int(self.crop_size, "crop_size", DEFAULT_CROP_SIZE),
            "do_resize": self.do_resize.get(),
            "resize_to": self._var_int(self.resize_to, "resize_to", DEFAULT_RESIZE_TO),
            "out_dir": self.out_custom or "",
            "speed": self.speed,
            "window": geometry,
            "zoomed": zoomed,
        }, SETTINGS_APP)

    def on_close(self):
        self.pause()
        self.save_settings()
        self._close_readers()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    if len(sys.argv) > 1:
        root.after(100, lambda: app.load(sys.argv[1]))
    elif os.path.isfile(app.settings.get("last_movie") or ""):
        root.after(100, lambda: app.load(app.settings["last_movie"]))
    root.mainloop()


if __name__ == "__main__":
    main()
