"""
Probe an MKV pair and test whether OpenCV frame seeking is accurate.

For each file: print codec, size, FPS and frame count. Then decode the first
N frames sequentially (ground truth) and compare them with frames fetched by
seeking (CAP_PROP_POS_FRAMES) at random indices, forward and backward.

Run:  .venv/Scripts/python.exe tools/probe_video.py test_files/20261102_1347.mkv
"""

import os
import random
import sys
import time

import cv2
import numpy as np

SEQ_FRAMES = 600     # frames decoded sequentially as ground truth
SEEK_SAMPLES = 40    # random seeks to verify


def fourcc_str(cap):
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((code >> 8 * i) & 0xFF) for i in range(4))


def describe(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print("  cannot open", path)
        return None
    info = {
        "codec": fourcc_str(cap),
        "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    print("  %-28s codec=%s  %dx%d  fps=%.3f  frames=%d  (%.1f s)" % (
        os.path.basename(path), info["codec"], info["w"], info["h"],
        info["fps"], info["frames"], info["frames"] / info["fps"] if info["fps"] else 0))
    return info


def frame_hash(frame):
    small = cv2.resize(frame, (64, 64), interpolation=cv2.INTER_AREA)
    return small.astype(np.int16)


def match_index(target, truth):
    """Index in truth whose thumbnail is closest to target, and its error."""
    errs = [np.abs(target - t).mean() for t in truth]
    best = int(np.argmin(errs))
    return best, errs[best]


def seek_test(path):
    cap = cv2.VideoCapture(path)
    truth = []
    t0 = time.perf_counter()
    for _ in range(SEQ_FRAMES):
        ok, frame = cap.read()
        if not ok:
            break
        truth.append(frame_hash(frame))
    seq_ms = (time.perf_counter() - t0) * 1000 / max(1, len(truth))
    print("  sequential decode: %d frames, %.1f ms/frame" % (len(truth), seq_ms))

    random.seed(1)
    picks = [random.randrange(len(truth)) for _ in range(SEEK_SAMPLES)]
    bad, seek_times = [], []
    for idx in picks:  # random order = forward and backward jumps
        t0 = time.perf_counter()
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        seek_times.append((time.perf_counter() - t0) * 1000)
        if not ok:
            bad.append((idx, "read failed"))
            continue
        got, err = match_index(frame_hash(frame), truth)
        if got != idx:
            bad.append((idx, "got frame %d (err %.2f)" % (got, err)))
    cap.release()

    print("  seek: %d/%d exact, avg %.0f ms, max %.0f ms" % (
        SEEK_SAMPLES - len(bad), SEEK_SAMPLES,
        sum(seek_times) / len(seek_times), max(seek_times)))
    for idx, why in bad[:10]:
        print("    frame %d -> %s" % (idx, why))
    return not bad


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    root, ext = os.path.splitext(path)
    raw = path if root.endswith("_raw") else root + "_raw" + ext
    det = root[:-4] + ext if root.endswith("_raw") else path
    print("OpenCV", cv2.__version__)
    results = {}
    for p in (det, raw):
        print(p)
        if not os.path.isfile(p):
            print("  missing")
            continue
        if describe(p):
            results[p] = seek_test(p)
    print("\nSEEK ACCURATE" if results and all(results.values()) else "\nSEEK INACCURATE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
