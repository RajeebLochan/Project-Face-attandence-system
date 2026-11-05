"""Pinned, checksum-verified model files used by the OpenCV face engine.

Models come from the OpenCV Model Zoo (https://github.com/opencv/opencv_zoo) at a
fixed commit. Download them with ``python manage.py setup_face_models``.

* YuNet face detector (``face_detection_yunet_2023mar.onnx``)
* SFace face recognizer (``face_recognition_sface_2021dec.onnx``)

Licences are those published upstream for each model directory in the zoo; review
them before deployment.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

SOURCE_COMMIT = "47534e27c9851bb1128ccc0102f1145e27f23f98"
_BASE_URL = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{SOURCE_COMMIT}/models"

# Bump when the model files *or* the preprocessing pipeline change. Embeddings are stored
# with this tag, and ``rebuild_face_embeddings`` regenerates them for the new version.
MODEL_VERSION = "yunet-2023mar+sface-2021dec/aligncrop-v1"
EMBEDDING_DIM = 128


@dataclass(frozen=True)
class ModelFile:
    key: str
    filename: str
    url: str
    sha256: str
    size: int

    @property
    def path(self) -> Path:
        return Path(settings.FACE_MODEL_DIR) / self.filename


MODEL_FILES = (
    ModelFile(
        key="detector",
        filename="face_detection_yunet_2023mar.onnx",
        url=f"{_BASE_URL}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        size=232_589,
    ),
    ModelFile(
        key="recognizer",
        filename="face_recognition_sface_2021dec.onnx",
        url=f"{_BASE_URL}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        size=38_696_353,
    ),
)


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def problems() -> list[str]:
    """Return human-readable problems with the installed model files (empty if all good)."""
    found = []
    for model in MODEL_FILES:
        if not model.path.is_file():
            found.append(f"{model.filename} is missing from {model.path.parent}")
        elif sha256_of(model.path) != model.sha256:
            found.append(f"{model.filename} failed its SHA-256 check")
    return found
