"""Management commands: model setup (checksums), template rebuild, legacy photo relocation."""
import hashlib
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.test import override_settings

from app1.models import FaceEmbedding, Student
from app1.recognition.model_files import ModelFile
from app1.storage import PrivateMediaStorage

from .fakes import FakeFaceEngine
from .helpers import BLUE, RED, AppTestCase, image_bytes, make_student


def fake_models(directory, payloads):
    return tuple(
        ModelFile(key=name, filename=f"{name}.onnx", url="https://example.invalid/x",
                  sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        for name, data in payloads.items()
    )


class SetupFaceModelsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.media_root / "source"
        self.target = self.media_root / "models"
        self.source.mkdir()
        self.payloads = {"detector": b"detector-bytes", "recognizer": b"recognizer-bytes" * 10}
        for name, data in self.payloads.items():
            (self.source / f"{name}.onnx").write_bytes(data)

    def run_setup(self, models, *args):
        out = StringIO()
        with override_settings(FACE_MODEL_DIR=self.target), \
                mock.patch("app1.management.commands.setup_face_models.MODEL_FILES", models):
            call_command("setup_face_models", "--from-dir", str(self.source), *args, stdout=out)
        return out.getvalue()

    def test_installs_and_verifies(self):
        models = fake_models(self.target, self.payloads)
        output = self.run_setup(models)
        self.assertIn("installed and verified", output)
        self.assertEqual((self.target / "detector.onnx").read_bytes(), self.payloads["detector"])
        self.assertIn("already installed", self.run_setup(models))

    def test_checksum_mismatch_is_rejected_and_discarded(self):
        models = fake_models(self.target, self.payloads)
        (self.source / "detector.onnx").write_bytes(b"tampered")
        with self.assertRaisesMessage(CommandError, "SHA-256 mismatch"):
            self.run_setup(models)
        self.assertFalse((self.target / "detector.onnx").exists())
        self.assertEqual([p.name for p in self.target.iterdir()], [])

    def test_missing_source_file(self):
        models = fake_models(self.target, self.payloads)
        (self.source / "recognizer.onnx").unlink()
        with self.assertRaisesMessage(CommandError, "does not exist"):
            self.run_setup(models)

    def test_pinned_sources_are_https_with_checksums(self):
        from app1.recognition.model_files import MODEL_FILES, SOURCE_COMMIT

        self.assertRegex(SOURCE_COMMIT, r"^[0-9a-f]{40}$")
        for model in MODEL_FILES:
            self.assertTrue(model.url.startswith("https://media.githubusercontent.com/media/opencv/opencv_zoo/"))
            self.assertIn(SOURCE_COMMIT, model.url)
            self.assertRegex(model.sha256, r"^[0-9a-f]{64}$")


class RebuildEmbeddingsTests(AppTestCase):
    def test_rebuild_regenerates_current_version_and_removes_stale_or_withdrawn(self):
        current = make_student(colour=RED, embedding=False)
        stale = make_student(colour=BLUE, model_version="old-version")
        withdrawn = make_student(colour=RED, consent=False)
        missing = make_student(colour=RED, embedding=False)
        PrivateMediaStorage().delete(missing.image.name)
        out = StringIO()
        call_command("rebuild_face_embeddings", stdout=out)
        version = FakeFaceEngine.model_version
        self.assertTrue(FaceEmbedding.objects.filter(student=current, model_version=version).exists())
        self.assertEqual(list(stale.embeddings.values_list("model_version", flat=True)), [version])
        self.assertFalse(withdrawn.embeddings.exists())
        self.assertFalse(missing.embeddings.exists())
        self.assertIn(f"student #{missing.pk}: photo missing or unreadable", out.getvalue())

    @override_settings(FACE_ENGINE="app1.tests.fakes.UnavailableFaceEngine")
    def test_rebuild_requires_models(self):
        with self.assertRaisesMessage(CommandError, "not ready"):
            call_command("rebuild_face_embeddings", stdout=StringIO())


class RelocateLegacyPhotosTests(AppTestCase):
    def test_moves_into_private_storage_with_random_name(self):
        legacy_dir = self.media_root / "legacy_media"
        (legacy_dir / "students").mkdir(parents=True)
        (legacy_dir / "students" / "Old_Name.jpg").write_bytes(image_bytes(RED))
        student = make_student(photo=False, embedding=False)
        Student.objects.filter(pk=student.pk).update(image="students/Old_Name.jpg")
        traversal = make_student(photo=False, embedding=False)
        Student.objects.filter(pk=traversal.pk).update(image="../outside.jpg")
        (self.media_root / "outside.jpg").write_bytes(image_bytes(RED))

        out = StringIO()
        call_command("relocate_legacy_photos", "--source", str(legacy_dir), "--delete-source", stdout=out)
        student.refresh_from_db()
        self.assertRegex(student.image.name, r"^students/[0-9a-f]{32}\.jpg$")
        self.assertTrue(PrivateMediaStorage().exists(student.image.name))
        self.assertFalse((legacy_dir / "students" / "Old_Name.jpg").exists())
        self.assertIn(f"student #{traversal.pk}: photo not found", out.getvalue())
        traversal.refresh_from_db()
        self.assertEqual(traversal.image.name, "../outside.jpg")


class RolesCommandTests(AppTestCase):
    def test_sync_roles_resets_permissions(self):
        from django.contrib.auth.models import Group, Permission

        group = Group.objects.get(name="Instructor")
        group.permissions.add(Permission.objects.get(codename="delete_student"))
        call_command("sync_roles", stdout=StringIO())
        self.assertFalse(group.permissions.filter(codename="delete_student").exists())
        self.assertTrue(group.permissions.filter(codename="use_kiosk").exists())


class StorageWriteTests(AppTestCase):
    def test_files_are_private_on_disk(self):
        name = PrivateMediaStorage().save("students/x.jpg", ContentFile(b"data"))
        mode = Path(PrivateMediaStorage().path(name)).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
