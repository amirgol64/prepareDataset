# Frame Player — plan

A VLC-style frame player for building datasets from paired MKV recordings.
You watch the **detected** movie (with boxes drawn on it) and press **S** to
save the matching frame from the **raw** movie, split into `_left` / `_right`
crops exactly like `splitImages.py` does.

## Decisions (from Q&A)

| # | Topic | Decision |
|---|---|---|
| 1 | Naming | `name.mkv` = detected, `name_raw.mkv` = raw |
| 2 | Opening | Opening either file finds its partner |
| 3 | Raw missing | Movie still plays; saving disabled, S shows a warning |
| 4 | Pair check | On load compare frame count, FPS, resolution; warn if they differ |
| 5 | Multiple pairs | Side panel lists every pair in the folder; click to switch |
| 6 | Video | 1920x1536, ~20 fps |
| 7 | Codec | H.264, variable frame rate ~19 fps, keyframe every 250 frames |
| 8 | Samples | `test_files/20261102_1347.mkv` + `_raw.mkv` (~300 MB each, git-ignored) |
| 9 | Format | PNG |
| 10 | Output folder | Default `<movie folder>/<movie>_frames/`, changeable in UI, remembered |
| 11 | File names | `<movie>_f000123_left.png`, `<movie>_f000123_right.png` — frame numbers start at 1 to match the `Frame_number` burned into the video (verified at frame 101) |
| 12 | Crop | Same logic as `splitImages.py`: Y offset + crop size spinboxes, left/right boxes drawn live over the video. The 1920x1536 frame is one image whose **left and right halves** are processed separately (overlay: `Detected L` / `Detected R`) — default crop = the two 960-wide halves |
| 12c | Save set | Left + right crops only; optional resize (default 640), toggle in UI |
| 13 | Detected frame | Not saved |
| 14 | Re-save | Already-saved frame is skipped with a status message |
| 15 | Saved tracking | Markers on seek bar + list of saved frames (click to jump) + Ctrl+Z / Delete removes |
| 16 | Playback | Space play/pause; `[` / `]` speed 0.25x–4x |
| 17 | Seeking | Draggable seek bar + go-to-frame box (Ctrl+G) |
| 18 | Held keys | Keep stepping while held; drop display updates if decoding falls behind |
| 19 | Display | Detected by default; Tab toggles to raw |
| 20 | Info bar | Frame n / total, timecode, saved count, speed, FPS, DETECTED/RAW label |
| 21 | Remember | Last folder/movie, frame position per movie, crop settings, output folder, window size |
| 22 | Structure | New `framePlayer.py`; imports shared helpers from `splitImages.py`; both stay usable |
| 23 | Dependencies | `.venv` + `requirements.txt`: av (PyAV), opencv-python, numpy. PyAV approved after OpenCV seeking failed |
| 24 | Look | Same dark theme/palette as `splitImages.py` |
| 25 | Demo movie | 10 s sample pair committed under `samples/` for anyone to test |
| 26 | Docs | README updated with UI screenshots of the working app in `docs/images/` |

## Keyboard

| Key | Action |
|---|---|
| ← / → | 1 frame back / forward |
| Shift + ← / → | 10 frames |
| Ctrl + Shift + ← / → | 100 frames |
| Space | Play / pause |
| `[` / `]` | Slower / faster |
| S | Save raw frame as `_left` / `_right` |
| Ctrl+Z / Delete | Remove last saved / current frame's saved files |
| Tab | Toggle detected ↔ raw view |
| Ctrl+G | Focus go-to-frame box (1-based number, Enter jumps, Esc leaves) |
| Home / End | First / last frame |
| Ctrl+O | Open movie |

## Steps

Each step ends with a working app you can try before we move on.

### Step 0 — Environment ✅
- Create `.venv`, add `requirements.txt` (opencv-python, numpy).
- `.gitignore`: `*.mkv` and `/test_files/` (done).

### Step 1 findings (`tools/probe_video.py`, sample `20261102_1347`)
- H.264, 1920x1536, **variable frame rate** ~19 fps (frame gaps 31–92 ms).
  OpenCV reports fps=1000 and frame count=237 545 (the ms time base) — both wrong.
- Real count: **4306 frames in both files**; timestamps match per index within ±24 ms;
  content matches at offset 0 for all 394 frames checked → **pair by frame index**.
- Sequential decode: 13–17 ms/frame (full file scan with `grab()` ≈ 17 s).
- **OpenCV seeking is broken:** `POS_FRAMES` returned the wrong frame 39/40 times
  (always frame 22) and took up to 35 s; `POS_MSEC` took > 5 min for 30 seeks.
- PyAV: **4556 frames** in both files (OpenCV silently skipped the first 250-frame GOP),
  index by demuxing packets in 0.45 s, keyframe every 250 frames.
  Full-file check: all 910 sampled frames pair at offset 0.
- `VideoReader` (PyAV): exact 55/55 vs sequential decode, incl. backward and across
  keyframes; random access avg 245 ms (max ~1 s), forward step ~9 ms.
  Backward stepping will need a frame cache (step 2).

### Step 1 — Probe + open a movie ✅
- Script prints codec, size, FPS, frame count of both sample files.
- **Seek accuracy test:** read frame N by sequential decoding vs. by
  `CAP_PROP_POS_FRAMES` seek at random N; compare pixels. Report result.
  If seeks are off → stop and discuss PyAV.
- `framePlayer.py`: window, Ctrl+O file dialog, partner lookup (decision 1–3),
  pair check (4), show first frame, info bar (20).

### Step 2 — Frame stepping ✅
- ← / →, with a frame reader that keeps the decoder position and only seeks
  when jumping backwards or far ahead (sequential reads are much faster than seeks).
- Done: display-size LRU cache (long side 1280, 600 MB ≈ 160 frames); a backward
  cache miss keeps the 100 frames before the target. Measured: forward step ~8 ms,
  backward step ~0 ms with one ~1 s refill per 100 frames, random jump ~250–500 ms;
  all frames pixel-exact vs sequential decode. Held keys coalesce (only the latest
  target is decoded), which already covers decision 18.

### Step 3 — Jumps, seek bar, go-to ✅
- Shift / Ctrl+Shift jumps, held-key coalescing (18), seek bar + go-to box (17).
- Done: seek bar is a Canvas (ready for saved-frame markers in step 5); click/drag
  seeks, drags coalesce like held keys and skip the back-fill. Arrow keys are ignored
  while the go-to box has focus. UI test with real keystrokes/click: Shift×3 +
  Ctrl+Shift×2 + ←×5 → 226, go-to 1000 + → → 1001, click at 75% → 3417,
  End + Shift+← → 4546 — each matches the burned-in Frame_number.

### Step 4 — Save with S ✅
- Crop panel (Y offset, crop size, resize toggle/size) with live overlay (12).
- S reads the same frame index from the raw file, runs `split_frame`, writes PNGs (9–11, 14).
- Disabled with warning when raw is missing (3).
- Done: side panel with Crop card (Y offset 288 / crop 960 = the two halves, centred
  vertically; resize 640 toggle; live formula) and Output card (default
  `<movie>_frames/` next to the movie, Change… / Default / Open). Overlay draws LEFT /
  RIGHT boxes (red when the crop doesn't fit) and SAVED on saved frames.
  Saved count is read from the output folder on load. Tests: saved crops pixel-equal
  to an independent raw decode (with and without resize), raw ≠ detected, re-save
  skipped, bad crop and missing raw refused.
- Bug found + fixed in the real-key test: S pressed right after a burst of arrow keys
  saved the frame still on screen (220) instead of the target (226). S now catches up
  to the target frame first.

### Step 5 — Saved-frame tracking ✅
- Saved list built from the output folder on load, seek-bar markers, click to jump,
  Ctrl+Z / Delete (15).
- Done: "Saved frames (N)" card with frame number + time, current frame highlighted,
  click or ↑/↓ jumps. Green ticks on the seek bar. Del deletes the current frame's
  _left/_right files; Ctrl+Z removes the newest save of this session and jumps to it
  (the undo history resets when the movie or output folder changes). Deletes are
  permanent (not the Recycle Bin) — S recreates the same files.
  Tested with real keys: 5 saves → Ctrl+Z → Del → exactly the expected 3 files left.
- Also fixed: default window (1440x920) ran under the taskbar on a 1080p screen;
  it is now capped to the screen size.

### Step 6 — Playback + view toggle ✅
- Space play/pause timed to FPS × speed, `[` `]` speed, Tab detected/raw (16, 19).
- Done: playback is paced by the wall clock against each frame's own timestamp
  (variable frame rate) and skips frames rather than slowing when decoding can't keep
  up. Speeds 0.25 / 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2 / 3 / 4x. Arrow keys / seek / go-to
  while playing continue playback from the new spot; it stops at the last frame.
  Measured: 1x ratio 0.99, 0.25x 0.24, 4x 3.98 (draws ~42 fps, skips the rest).
  Tab shows the raw movie with a RAW tag (info bar: SHOWING RAW); S always saves from
  raw regardless of the view.

### Step 7 — Folder pair list + remembered settings
- Side panel of all pairs in the folder (5).
- Settings in `%APPDATA%/FramePlayer/settings.json`, incl. per-movie position (21).

### Step 8 — Sample movie in the repo
- Cut a 10 s pair from the test files: `samples/sample.mkv` + `samples/sample_raw.mkv`
  (same start frame in both, so the pair stays in sync).
- Re-encode (H.264, CRF ~23) to keep each file small, target < 10 MB
  (GitHub rejects files > 100 MB and warns > 50 MB). Cutting tool: ffmpeg via the
  `imageio-ffmpeg` pip package (bundles the binary), installed in `.venv` as a dev-only
  dependency — not needed to run the app.
- Re-run the seek accuracy test on the sample after re-encoding.
- `.gitignore` exceptions: `!/samples/*.mkv`, `!/docs/images/*.png`.

### Step 9 — README + screenshots
- `docs/images/`: screenshots of the working app — movie loaded with crop overlay,
  detected vs raw view (Tab), saved-frame list + seek-bar markers, the saved
  `_left` / `_right` output, and (optional) a short GIF of frame stepping + S.
- README: both tools (Frame Splitter + Frame Player), install (`.venv` +
  `requirements.txt`), "try it with `samples/`", keyboard table, output naming,
  settings location, screenshots.

## Shared code with `splitImages.py`
Reused as-is: `split_frame`, `imwrite_unicode`, `to_photo`, palette constants.
Small refactor: `settings_path()` / `load_settings()` / `save_settings()` take an
app-folder name so the two apps keep separate settings files.
