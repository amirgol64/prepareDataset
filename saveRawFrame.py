import cv2
import os
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk

# Global variables
video1_path = ""
video2_path = ""
capture = None
capture2 = None
frame_idx = 1
total_frames = 0
canvas = None
frame_image = None
save_folder = ""  # Folder to save frames

# Function to open video file
def open_video():
    global video1_path, video2_path, capture, capture2, frame_idx, total_frames, canvas
    file_path = filedialog.askopenfilename(filetypes=[("MP4 files", "*.mp4")])
    if file_path:
        video1_path = file_path
        video2_path = file_path.replace(".mp4", "_raw.mp4")  # Assuming a naming pattern
        print(f"video1_path: {video1_path}")
        print(f"video2_path: {video2_path}")
        if not os.path.exists(video2_path):
            print(f"Error: Corresponding video file {video2_path} not found!")
            return
        
        capture = cv2.VideoCapture(video1_path)
        if not capture.isOpened():
            print(f"Error: Unable to open video file {video1_path}")
            return
        
        capture2 = cv2.VideoCapture(video2_path)
        frame_idx = 0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # Get frame dimensions and update canvas size
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        canvas.config(width=frame_width, height=frame_height)
        
        show_frame()

# Function to show current frame
def show_frame():
    global frame_idx, frame_image, canvas
    if capture is None or not capture.isOpened():
        print("Error: Video capture is not initialized or failed to open.")
        return
    
    if frame_idx >= total_frames:
        print(f"Error: Frame index {frame_idx} is out of range. Total frames: {total_frames}")
        return
    
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = capture.read()
    
    if not ret:
        print(f"Error: Failed to read frame at index {frame_idx}")
        return
    
    # Convert the frame to RGB format for Tkinter
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    frame_image = ImageTk.PhotoImage(Image.fromarray(frame))
    
    # Display the frame on the canvas
    canvas.create_image(0, 0, anchor=tk.NW, image=frame_image)

# Function to navigate frames
def navigate_frames(step):
    global frame_idx
    frame_idx = max(0, min(frame_idx + step, total_frames - 1))  # Ensure frame_idx stays in range
    print(f"frame index: {frame_idx}")
    show_frame()

# Function to select a folder for saving frames
def select_save_folder():
    global save_folder
    save_folder = filedialog.askdirectory()
    if save_folder:
        print(f"Selected folder to save frames: {save_folder}")

# Function to save the corresponding frame
def save_frame():
    global save_folder
    if capture2 is None:
        return
    
    if not save_folder:
        print("Error: No folder selected to save frames. Please select a folder first.")
        return
    
    capture2.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = capture2.read()
    
    if ret:
        # Extract the video name without the directory path
        video_name = os.path.basename(video1_path).replace(".mp4", "")
        
        # Create the filename with the video name and frame index
        filename = f"{video_name}_frame_{frame_idx}.png"
        
        # Save the frame in the selected folder
        save_path = os.path.join(save_folder, filename)
        cv2.imwrite(save_path, frame)
        print(f"Saved: {save_path}")

# Key event handler
def key_handler(event):
    if event.keysym == "Right":
        navigate_frames(1)  # Move forward by 1 frame
    elif event.keysym == "Left":
        navigate_frames(-1)  # Move backward by 1 frame
    elif event.keysym == "Up":
        if event.state & 0x4:  # Check if Ctrl is pressed
            navigate_frames(100)  # Move forward by 100 frames
        else:
            navigate_frames(10)  # Move forward by 10 frames
    elif event.keysym == "Down":
        if event.state & 0x4:  # Check if Ctrl is pressed
            navigate_frames(-100)  # Move backward by 100 frames
        else:
            navigate_frames(-10)  # Move backward by 10 frames
    elif event.keysym.lower() == "s":
        save_frame()  # Save the current frame

# GUI setup
root = tk.Tk()
root.title("Video Frame Navigator v1.0")
root.geometry("800x600")

# Add a canvas to display the video
canvas = tk.Canvas(root)
canvas.pack()

# Add buttons and bind keys
btn_open = tk.Button(root, text="Open Video", command=open_video)
btn_open.pack(pady=10)

btn_select_folder = tk.Button(root, text="Select Save Folder", command=select_save_folder)
btn_select_folder.pack(pady=10)

root.bind("<KeyPress>", key_handler)
root.mainloop()
