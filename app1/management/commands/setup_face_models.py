"""Download (or copy) the face models and verify their SHA-256 checksums."""
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app1.recognition.model_files import MODEL_FILES, SOURCE_COMMIT, sha256_of


class Command(BaseCommand):
    help = (
        "Install the pinned YuNet and SFace ONNX models into FACE_MODEL_DIR from the OpenCV "
        "Model Zoo (fixed commit) or from a local directory, verifying SHA-256 checksums."
    )

    def add_arguments(self, parser):
        parser.add_argument("--from-dir", type=Path,
                            help="Copy the model files from this local directory instead of downloading.")
        parser.add_argument("--force", action="store_true", help="Replace files that already verify.")
        parser.add_argument("--timeout", type=int, default=120, help="Network timeout in seconds.")

    def handle(self, *args, from_dir=None, force=False, timeout=120, **options):
        target_dir = Path(settings.FACE_MODEL_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        self.stdout.write(f"Model directory: {target_dir}")
        source = f"{from_dir}" if from_dir else f"opencv/opencv_zoo @ {SOURCE_COMMIT[:12]}"
        self.stdout.write(f"Source: {source}")
        for model in MODEL_FILES:
            if model.path.is_file() and not force and sha256_of(model.path) == model.sha256:
                self.stdout.write(f"  {model.filename}: already installed and verified")
                continue
            handle, temp_name = tempfile.mkstemp(prefix=".download-", dir=target_dir)
            os.close(handle)
            try:
                digest = self._fetch(model, Path(temp_name), from_dir, timeout)
                if digest != model.sha256:
                    raise CommandError(
                        f"{model.filename}: SHA-256 mismatch (expected {model.sha256}, got {digest}). "
                        "The file was discarded."
                    )
                os.chmod(temp_name, 0o644)
                os.replace(temp_name, model.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            self.stdout.write(self.style.SUCCESS(f"  {model.filename}: installed and verified"))
        self.stdout.write(self.style.SUCCESS("Face models are ready."))

    def _fetch(self, model, destination, from_dir, timeout):
        digest = hashlib.sha256()
        limit = model.size + 1024
        if from_dir:
            source = from_dir / model.filename
            if not source.is_file():
                raise CommandError(f"{source} does not exist.")
            reader = open(source, "rb")
        else:
            if not model.url.startswith("https://"):
                raise CommandError("Refusing to download a model over a non-HTTPS URL.")
            try:
                reader = urllib.request.urlopen(model.url, timeout=timeout)  # noqa: S310 (pinned https URL)
            except OSError as exc:
                raise CommandError(
                    f"Could not download {model.filename}: {exc}. If this host has no internet access, "
                    "download the files elsewhere, verify them, and use --from-dir."
                ) from None
        written = 0
        with reader, open(destination, "wb") as out:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                written += len(chunk)
                if written > limit:
                    raise CommandError(f"{model.filename}: download is larger than expected; aborted.")
                digest.update(chunk)
                out.write(chunk)
        return digest.hexdigest()
