"""High-level recognition operations used by enrolment and the kiosk."""
from dataclasses import dataclass

from django.conf import settings

from .engine import DetectedFace, get_engine
from .gallery import MatchResult, gallery


@dataclass(frozen=True)
class FaceCheck:
    status: str  # "ok", "no_face", "multiple_faces" or "face_too_small"
    face: DetectedFace | None = None


MESSAGES = {
    "no_face": "No face was found. Face the camera in good light.",
    "multiple_faces": "More than one face is visible. Only one person at a time.",
    "face_too_small": "The face is too small. Move closer to the camera.",
}


def check_single_face(image_bgr) -> FaceCheck:
    """Analyse an image and require exactly one usable face."""
    faces = get_engine().analyze(image_bgr)
    if not faces:
        return FaceCheck("no_face")
    if len(faces) > 1:
        return FaceCheck("multiple_faces")
    face = faces[0]
    if min(face.box[2], face.box[3]) < settings.FACE_MIN_SIZE_PX:
        return FaceCheck("face_too_small", face)
    return FaceCheck("ok", face)


@dataclass(frozen=True)
class IdentifyResult:
    status: str  # FaceCheck statuses other than "ok", or a MatchResult status
    match: MatchResult | None = None


def identify(image_bgr) -> IdentifyResult:
    check = check_single_face(image_bgr)
    if check.status != "ok":
        return IdentifyResult(check.status)
    engine = get_engine()
    result = gallery.match(
        check.face.embedding,
        model_version=engine.model_version,
        threshold=settings.FACE_MATCH_THRESHOLD,
        margin=settings.FACE_MATCH_MARGIN,
    )
    return IdentifyResult(result.status, result)
