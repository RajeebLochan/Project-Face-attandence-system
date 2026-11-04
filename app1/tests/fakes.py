"""Deterministic stand-ins for the face engine. Used only by tests.

``FakeFaceEngine`` derives a one-hot "embedding" from an image's average colour, so
synthetic solid-colour images behave like distinct (or identical) faces without any
real face data or model download. This exercises the real pipeline wiring (decoding,
gallery, matching, attendance) but says NOTHING about real-world recognition accuracy.
"""
import numpy as np

from app1.recognition.engine import DetectedFace, FaceEngine, ModelUnavailable
from app1.recognition.model_files import EMBEDDING_DIM


def colour_embedding(image_bgr):
    b, g, r = (int(v) // 64 for v in image_bgr.reshape(-1, 3).mean(axis=0))
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vector[b * 16 + g * 4 + r] = 1.0
    return vector


def one_face(image_bgr, size=150):
    return [DetectedFace(box=(10, 10, size, size), score=0.99, embedding=colour_embedding(image_bgr))]


class FakeFaceEngine(FaceEngine):
    model_version = "fake-colour-engine-v1"
    calls = 0
    override = None  # optional callable(image) -> list[DetectedFace]

    def analyze(self, image_bgr):
        type(self).calls += 1
        if type(self).override is not None:
            return type(self).override(image_bgr)
        return one_face(image_bgr)

    @classmethod
    def reset(cls):
        cls.calls = 0
        cls.override = None


class UnavailableFaceEngine(FaceEngine):
    def __init__(self):
        raise ModelUnavailable("Face models are not ready (test).")
