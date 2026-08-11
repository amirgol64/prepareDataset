# PrepareDataset — Frame Splitter v1.0

A desktop tool for preparing images for annotation. It batch-splits side-by-side
(stereo) frames into separate **left** and **right** images, with a live preview
of the crop before you run the batch.

---

## Features

- Crops a configurable region out of each frame and splits it into two square halves.
- Optional resize of both halves to a square target (e.g. **640x640**).
- Live preview of the crop region and of the resulting left/right images.
- Recursive mode: walk subfolders and mirror the folder structure in the output.
- Frames that do not match the expected input size are skipped and listed in the log.
- PNG or JPG output (JPG written at quality 95).
- Unicode-safe file reading and writing (works with non-ASCII paths).
- Runs the batch on a background thread with a progress bar and a cancel button.

---

## How It Works

Per image, with the defaults `CROP_Y_OFF=60`, `CROP_SIZE=960`, `RESIZE_TO=640`:

```python
crop_y1 = CROP_Y_OFF + CROP_SIZE
left    = frame[CROP_Y_OFF:crop_y1,         0:CROP_SIZE]
right   = frame[CROP_Y_OFF:crop_y1, CROP_SIZE:2 * CROP_SIZE]
if RESIZE:
    left  = cv2.resize(left,  (RESIZE_TO, RESIZE_TO))
    right = cv2.resize(right, (RESIZE_TO, RESIZE_TO))
```

So a **1920x1080** frame yields:

| Half  | Source region              | Output    |
| ----- | -------------------------- | --------- |
| left  | `frame[60:1020,   0:960]`  | 640 x 640 |
| right | `frame[60:1020, 960:1920]` | 640 x 640 |

**Already-split frames** (width exactly twice the height, e.g. 1280x640) are
detected automatically and simply halved down the middle — no cropping applied.

### Settings

| Setting                | Default     | Meaning                                           |
| ---------------------- | ----------- | ------------------------------------------------- |
| Crop Y offset          | `60`        | Top edge of the crop region, in pixels            |
| Crop size              | `960`       | Width and height of each half before resizing     |
| Expected input size    | `1920x1080` | Frames of any other size are skipped              |
| Resize to              | `640`       | Square output size; disable to keep the crop size |
| Output format          | `PNG`       | `PNG` or `JPG`                                    |
| Include subfolders     | off         | Recurse into the input folder                     |

### Output

Split images are written to the selected output directory, with `_left` and
`_right` appended to the original filename stem:

```
frame_0421.png  ->  frame_0421_left.png
                    frame_0421_right.png
```

Supported input extensions: `.png`, `.jpg`, `.jpeg`, `.bmp`, `.webp`, `.tif`, `.tiff`.

---

## Requirements

- Python 3.8+
- `opencv-python`
- `numpy`
- `tkinter` (bundled with the standard Python installer on Windows)

No Pillow needed — previews are encoded through OpenCV as PPM.

---

## Usage

1. Clone the repository:

   ```bash
   git clone https://github.com/amirgol64/prepareDataset.git
   cd prepareDataset
   ```

2. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install opencv-python numpy
   ```

3. Run the app:

   ```bash
   .venv/Scripts/python.exe splitImages.py
   ```

4. In the window: select an input folder, select an output folder, adjust the
   crop settings if needed, check the preview, then press **Start splitting**.
