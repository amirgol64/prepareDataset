"""
Cut a short, small sample pair out of a detected / raw movie pair.

Both files are re-encoded (H.264 via PyAV's libx264) from their first frame,
keeping every frame and its original timestamp, so frame i of the sample is
frame i of the source and the burned-in frame numbers still match the app.

Run:  .venv/Scripts/python.exe tools/make_sample.py SOURCE.mkv OUT_DIR [seconds] [crf]
      e.g. tools/make_sample.py test_files/20261102_1347.mkv samples 10 28
Writes OUT_DIR/sample.mkv and OUT_DIR/sample_raw.mkv.
"""

import os
import sys
from fractions import Fraction

import av

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from framePlayer import find_pair  # noqa: E402

KEYFRAME_EVERY = 25     # short GOPs keep seeking in the sample fast


def cut(src, dst, frames, crf):
    """Re-encode the first `frames` frames of src into dst."""
    with av.open(src) as inp, av.open(dst, "w") as out:
        source = inp.streams.video[0]
        source.thread_type = "AUTO"
        stream = out.add_stream("libx264")
        stream.width = source.codec_context.width
        stream.height = source.codec_context.height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {"crf": str(crf), "preset": "slow",
                          "g": str(KEYFRAME_EVERY)}
        first = None
        written = 0
        for frame in inp.decode(source):
            if written >= frames:
                break
            if first is None:
                first = frame.pts
            ms = round((frame.pts - first) * source.time_base * 1000)
            frame = frame.reformat(format="yuv420p")
            frame.pts = ms
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                out.mux(packet)
            written += 1
        for packet in stream.encode():
            out.mux(packet)
    return written


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    det, raw = find_pair(os.path.abspath(sys.argv[1]))
    if not det or not raw:
        print("need both name.mkv and name_raw.mkv")
        return 1
    out_dir = sys.argv[2]
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    crf = int(sys.argv[4]) if len(sys.argv) > 4 else 28
    os.makedirs(out_dir, exist_ok=True)

    with av.open(det) as probe:
        stream = probe.streams.video[0]
        pts = sorted(p.pts for p in probe.demux(stream) if p.pts is not None)
    limit = pts[0] + seconds / float(stream.time_base)
    frames = sum(1 for t in pts if t < limit)

    for src, name in ((det, "sample.mkv"), (raw, "sample_raw.mkv")):
        dst = os.path.join(out_dir, name)
        written = cut(src, dst, frames, crf)
        print("%s: %d frames, %.1f MB" % (dst, written, os.path.getsize(dst) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
