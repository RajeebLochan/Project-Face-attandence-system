"""Face detection + embedding engines.

``OpenCVFaceEngine`` follows OpenCV's documented SFace pipeline: YuNet detects a face
and its five landmarks, ``FaceRecognizerSF.alignCrop`` produces the aligned 112x112
crop SFace expects, and ``feature`` produces the embedding. Each bounding box is kept
together with the embedding computed from it.
"""
import logging
import threading
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .model_files import EMBEDDING_DIM, MODEL_FILES, MODEL_VERSION, problems

logger = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    """The face models are not installed or failed verification."""


@dataclass(frozen=True)
class DetectedFace:
    box: tuple  # (x, y, width, height) in the analysed image's pixel coordinates
    score: float  # detector score, NOT a recognition confidence
    embedding: np.ndarray  # L2-normalised float32 vector


def normalise(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or not np.isfinite(norm) or norm == 0.0:
        raise ValueError("Embedding is not a finite, non-zero vector.")
    return vector / norm


def downscale(image, max_side):
    """Bound analysis cost: shrink large frames so the longest side is ``max_side``."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    import cv2

    scale = max_side / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


class FaceEngine:
    """Interface: ``analyze`` returns every detected face with its own embedding."""

    model_version = "abstract"
    embedding_dim = EMBEDDING_DIM

    def analyze(self, image_bgr) -> list[DetectedFace]:  # pragma: no cover - interface
        raise NotImplementedError


class OpenCVFaceEngine(FaceEngine):
    model_version = MODEL_VERSION

    def __init__(self):
        issues = problems()
        if issues:
            raise ModelUnavailable(
                "Face models are not ready (" + "; ".join(issues) + "). "
                "Run `python manage.py setup_face_models`."
            )
        import cv2

        detector_file, recognizer_file = (str(model.path) for model in MODEL_FILES)
        self._detector = cv2.FaceDetectorYN.create(
            detector_file, "", (320, 320), settings.FACE_DETECTION_SCORE, 0.3, 50
        )
        self._recognizer = cv2.FaceRecognizerSF.create(recognizer_file, "")
        # OpenCV DNN networks are not safe for concurrent use from several threads.
        self._lock = threading.Lock()
        logger.info("Loaded OpenCV face engine %s", self.model_version)

    def analyze(self, image_bgr):
        image = downscale(np.ascontiguousarray(image_bgr), settings.FACE_ANALYSIS_MAX_SIDE)
        height, width = image.shape[:2]
        results = []
        with self._lock:
            self._detector.setInputSize((width, height))
            _, faces = self._detector.detect(image)
            if faces is None:
                return []
            for row in faces:
                aligned = self._recognizer.alignCrop(image, row)
                embedding = normalise(self._recognizer.feature(aligned))
                x, y, w, h = (int(round(v)) for v in row[:4])
                x0, y0 = max(0, x), max(0, y)
                x1, y1 = min(width, x + w), min(height, y + h)
                box = (x0, y0, max(0, x1 - x0), max(0, y1 - y0))
                results.append(DetectedFace(box=box, score=float(row[-1]), embedding=embedding))
        return results


_engine = None
_engine_lock = threading.Lock()


def get_engine() -> FaceEngine:
    """Create the configured engine on first use. Failures are not cached."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                engine_class = import_string(settings.FACE_ENGINE)
                _engine = engine_class()
    return _engine


def reset_engine():
    global _engine
    with _engine_lock:
        _engine = None


@receiver(setting_changed)
def _reset_on_setting_change(setting, **kwargs):
    if setting in {"FACE_ENGINE", "FACE_MODEL_DIR", "FACE_DETECTION_SCORE"}:
        reset_engine()
