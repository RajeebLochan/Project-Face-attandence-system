"""Strict decoding of untrusted images (enrolment photos and kiosk frames)."""
from io import BytesIO

import numpy as np
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG"}


class ImageValidationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def decode_image(uploaded, *, max_bytes):
    """Return an RGB-validated image as a BGR ``uint8`` array, or raise ImageValidationError.

    Checks, in order: byte size, real format (from content, not file name or MIME type),
    decoded dimensions (before decoding pixel data), and full decodability.
    """
    if uploaded is None:
        raise ImageValidationError("missing", "No image was provided.")
    size = getattr(uploaded, "size", None)
    if size is not None and size > max_bytes:
        raise ImageValidationError("too_large", f"The image is larger than {max_bytes // 1024} KB.")
    data = uploaded.read(max_bytes + 1)
    if not data:
        raise ImageValidationError("empty", "The image file is empty.")
    if len(data) > max_bytes:
        raise ImageValidationError("too_large", f"The image is larger than {max_bytes // 1024} KB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ALLOWED_FORMATS:
                raise ImageValidationError("unsupported_format", "Only JPEG and PNG images are accepted.")
            width, height = image.size
            if min(width, height) < settings.IMAGE_MIN_SIDE_PX:
                raise ImageValidationError(
                    "too_small", f"The image must be at least {settings.IMAGE_MIN_SIDE_PX} pixels on each side."
                )
            if max(width, height) > settings.IMAGE_MAX_SIDE_PX:
                raise ImageValidationError(
                    "dimensions_too_large",
                    f"The image must be at most {settings.IMAGE_MAX_SIDE_PX} pixels on each side.",
                )
            image.load()
            rgb = ImageOps.exif_transpose(image).convert("RGB")
    except ImageValidationError:
        raise
    except Image.DecompressionBombError:
        raise ImageValidationError("dimensions_too_large", "The image dimensions are too large.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
        raise ImageValidationError("corrupt", "The image could not be read (corrupt or not an image).") from None
    return np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[:, :, ::-1])


def encode_jpeg(image_bgr, quality=90) -> bytes:
    """Re-encode as a clean JPEG (drops EXIF/GPS metadata and any trailing payload)."""
    buffer = BytesIO()
    Image.fromarray(np.ascontiguousarray(image_bgr[:, :, ::-1])).save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()
