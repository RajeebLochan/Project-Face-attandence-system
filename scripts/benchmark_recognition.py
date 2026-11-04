"""Measure recognition costs on this machine with synthetic inputs (no real faces).

Usage:
    FACE_MODEL_DIR=var/models python scripts/benchmark_recognition.py

Reports median timings. Frames contain no faces, so detection timings are a lower
bound for frames with a face; the embedding timing is measured separately on an
aligned crop. Matching uses random unit vectors as a stand-in gallery.
"""
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Project101.settings.dev")

import django  # noqa: E402

django.setup()

import numpy as np  # noqa: E402

from app1.recognition.engine import get_engine  # noqa: E402
from app1.recognition.gallery import Gallery  # noqa: E402


def median_ms(func, repeat=20):
    func()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main():
    started = time.perf_counter()
    engine = get_engine()
    print(f"engine load (incl. checksum): {(time.perf_counter() - started) * 1000:.0f} ms")
    rng = np.random.default_rng(0)
    for size in ((480, 640), (720, 1280), (1080, 1920)):
        frame = rng.integers(0, 256, (*size, 3), dtype=np.uint8)
        timing = median_ms(lambda frame=frame: engine.analyze(frame))
        print(f"analyze {size[1]}x{size[0]} frame (detection, no face): {timing:.1f} ms")
    image = rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)
    row = np.array([200, 120, 160, 200, 240, 190, 320, 190, 280, 240, 250, 290, 310, 290, 0.99], np.float32)
    crop = engine._recognizer.alignCrop(image, row)
    print(f"alignCrop + SFace embedding (1 face): "
          f"{median_ms(lambda: engine._recognizer.feature(engine._recognizer.alignCrop(image, row))):.1f} ms")
    del crop
    for count in (50, 500, 5000):
        vectors = rng.standard_normal((count, 128)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        gallery = Gallery()
        gallery.snapshot = lambda version, dim, v=vectors, n=count: (np.arange(n, dtype=np.int64), v)
        probe = vectors[0]
        timing = median_ms(
            lambda g=gallery, p=probe: g.match(p, model_version="x", threshold=1.128, margin=0.1), repeat=200
        )
        print(f"gallery match vs {count} students: {timing:.3f} ms")


if __name__ == "__main__":
    main()
