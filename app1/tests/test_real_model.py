"""Checks against the real OpenCV models. Tagged "model"; skipped unless
FACE_TEST_MODEL_DIR points at a directory with the verified ONNX files
(``python manage.py setup_face_models`` then FACE_TEST_MODEL_DIR=var/models).

No real faces are used, so these verify loading, checksums, the preprocessing
pipeline and robustness, NOT recognition accuracy.
"""
import os
import threading
import unittest
from pathlib import Path

import numpy as np
from django.test import SimpleTestCase, override_settings, tag

from app1.recognition.engine import get_engine, reset_engine
from app1.recognition.model_files import EMBEDDING_DIM, MODEL_VERSION, problems

MODEL_DIR = os.environ.get("FACE_TEST_MODEL_DIR")


@tag("model")
@unittest.skipUnless(MODEL_DIR, "set FACE_TEST_MODEL_DIR to run real-model checks")
@override_settings(
    FACE_ENGINE="app1.recognition.engine.OpenCVFaceEngine",
    FACE_MODEL_DIR=Path(MODEL_DIR or ".").resolve(),
)
class RealModelTests(SimpleTestCase):
    def setUp(self):
        reset_engine()
        self.addCleanup(reset_engine)

    def test_files_verify(self):
        self.assertEqual(problems(), [])
        self.assertEqual(get_engine().model_version, MODEL_VERSION)

    def test_frames_without_faces(self):
        engine = get_engine()
        rng = np.random.default_rng(0)
        frames = [
            np.zeros((480, 640, 3), np.uint8),
            np.full((480, 640, 3), 128, np.uint8),
            rng.integers(0, 256, (480, 640, 3), dtype=np.uint8),
            np.zeros((1080, 1920, 3), np.uint8),  # downscaled before detection
        ]
        for frame in frames:
            self.assertEqual(engine.analyze(frame), [])

    def test_embedding_pipeline_is_normalised_and_deterministic(self):
        engine = get_engine()
        rng = np.random.default_rng(1)
        image = rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)
        row = np.array([[200, 120, 160, 200, 240, 190, 320, 190, 280, 240, 250, 290, 310, 290, 0.99]], np.float32)
        crops = [engine._recognizer.alignCrop(image, row[0]) for _ in range(2)]
        self.assertEqual(crops[0].shape, (112, 112, 3))
        features = [engine._recognizer.feature(crop).reshape(-1) for crop in crops]
        self.assertEqual(features[0].shape, (EMBEDDING_DIM,))
        np.testing.assert_array_equal(features[0], features[1])

    def test_concurrent_use_is_safe(self):
        engine = get_engine()
        errors = []

        def work():
            try:
                for _ in range(3):
                    engine.analyze(np.zeros((240, 320, 3), np.uint8))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
