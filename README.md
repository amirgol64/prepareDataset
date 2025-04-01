# PrepareDataset

A tool for preparing images for annotation. This script splits images into two smaller images and saves them in a desired folder.

---

## Features

- Accepts images with a size of **1280x640**.
- Splits the images into two smaller images of size **640x640**.
- Allows users to:
  - Select an input directory containing images.
  - Select an output directory to save the split images.
  - Monitor progress with a progress bar.

---

## How It Works

1. **Input Image Requirements**:
   - The input images must have a resolution of **1280x640** or similar.
   - Images are split into two equal parts:
     - **Left Image**: The left half of the original image.
     - **Right Image**: The right half of the original image.

2. **Output**:
   - The split images are saved in the selected output directory.
   - The filenames are appended with `_left` and `_right` to indicate the respective halves.

---

## Usage

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/prepareDataset.git
   cd prepareDataset
   ```


