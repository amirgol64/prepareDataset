from PIL import Image
import os
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk

# Global variables
input_dir = ""
output_dir = ""

# Function to select the input directory
def select_input_dir():
    global input_dir
    input_dir = filedialog.askdirectory(title="Select Input Directory")
    if input_dir:
        input_label.config(text=f"Input Directory: {input_dir}")

# Function to select the output directory
def select_output_dir():
    global output_dir
    output_dir = filedialog.askdirectory(title="Select Output Directory")
    if output_dir:
        output_label.config(text=f"Output Directory: {output_dir}")

# Function to start splitting images
def start_splitting():
    global input_dir, output_dir

    if not input_dir or not output_dir:
        status_label.config(text="Error: Please select both input and output directories.")
        return

    # Get the list of image files in the input directory
    image_files = [f for f in os.listdir(input_dir) if f.endswith(".jpg") or f.endswith(".png")]

    # Total number of images to process
    total_images = len(image_files)
    if total_images == 0:
        status_label.config(text="Error: No images found in the input directory.")
        return

    # Create the output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Initialize progress bar
    progress_bar["value"] = 0
    progress_bar["maximum"] = total_images

    # Iterate through each image in the input directory
    for idx, filename in enumerate(image_files):
        # Open the image
        img_path = os.path.join(input_dir, filename)
        img = Image.open(img_path)

        # Check if the image has the expected size (1280x640)
        if img.size == (1280, 640):
            # Split into two 640x640 images
            left_img = img.crop((0, 0, 640, 640))
            right_img = img.crop((640, 0, 1280, 640))

            # Save the images
            left_img.save(os.path.join(output_dir, f"{filename}_left.png"))
            right_img.save(os.path.join(output_dir, f"{filename}_right.png"))

        else:
            left_img = img.crop((0, 60, 960, 1020))
            right_img = img.crop((960, 60, 1920, 1020))

            left_img = left_img.resize((640, 640))
            right_img = right_img.resize((640, 640))

            left_img.save(os.path.join(output_dir, f"{filename}_left.png"))
            right_img.save(os.path.join(output_dir, f"{filename}_right.png"))

        # Update progress bar
        progress_bar["value"] = idx + 1
        progress_percentage = ((idx + 1) / total_images) * 100
        status_label.config(text=f"Processed {idx + 1}/{total_images} images ({progress_percentage:.2f}%)")
        root.update_idletasks()

    status_label.config(text="Splitting completed!")

# GUI setup
root = tk.Tk()
root.title("Image Splitter_v1.1")
root.geometry("600x400")

# Input directory selection
input_button = tk.Button(root, text="Select Input Directory", command=select_input_dir)
input_button.pack(pady=10)
input_label = tk.Label(root, text="Input Directory: Not selected", wraplength=500)
input_label.pack()

# Output directory selection
output_button = tk.Button(root, text="Select Output Directory", command=select_output_dir)
output_button.pack(pady=10)
output_label = tk.Label(root, text="Output Directory: Not selected", wraplength=500)
output_label.pack()

# Start splitting button
start_button = tk.Button(root, text="Start Splitting", command=start_splitting)
start_button.pack(pady=20)

# Progress bar
progress_bar = ttk.Progressbar(root, orient="horizontal", length=500, mode="determinate")
progress_bar.pack(pady=10)

# Status label
status_label = tk.Label(root, text="Status: Waiting to start...")
status_label.pack()

# Run the GUI
root.mainloop()
