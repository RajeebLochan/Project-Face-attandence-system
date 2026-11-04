"""Gallery mapping, eligibility, invalidation, invalid entries and matching rules."""
from unittest import mock

import numpy as np
from django.test import override_settings

from app1.models import FaceEmbedding, Student
from app1.recognition import engine as engine_module
from app1.recognition.engine import ModelUnavailable, get_engine, normalise
from app1.recognition.gallery import gallery
from app1.recognition.service import identify
from app1.services import students as student_service
from app1.storage import PrivateMediaStorage

from .fakes import FakeFaceEngine, colour_embedding, one_face
from .helpers import BLUE, GREEN, GREY, RED, AppTestCase, make_student, solid

MODEL = FakeFaceEngine.model_version


def match(colour, threshold=1.128, margin=0.1):
    return gallery.match(colour_embedding(solid(colour)), model_version=MODEL, threshold=threshold, margin=margin)


class GalleryMappingTests(AppTestCase):
    def test_matches_return_student_ids_even_when_names_are_shared(self):
        first = make_student(name="Same Name", colour=RED)
        second = make_student(name="Same Name", colour=BLUE)
        self.assertEqual(match(RED).student_id, first.pk)
        self.assertEqual(match(BLUE).student_id, second.pk)

    def test_unknown_face(self):
        make_student(colour=RED)
        result = match(GREEN)
        self.assertEqual(result.status, "unknown")
        self.assertIsNone(result.student_id)

    def test_empty_gallery(self):
        self.assertEqual(match(RED).status, "empty")

    def test_ambiguous_when_two_students_are_too_close(self):
        make_student(colour=RED)
        make_student(colour=RED)
        result = match(RED)
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.student_id)

    def test_threshold_is_a_strict_distance_limit(self):
        student = make_student(colour=RED)
        self.assertEqual(match(RED, threshold=0.001).student_id, student.pk)  # distance 0 < limit
        self.assertEqual(match(BLUE, threshold=2.0).status, "match")  # distance sqrt(2) < 2
        self.assertEqual(match(BLUE, threshold=1.414).status, "unknown")  # sqrt(2) >= 1.414

    def test_only_eligible_students_are_matched(self):
        cases = {
            "not authorized": make_student(colour=RED, authorized=False),
            "no consent": make_student(colour=BLUE, consent=False),
            "other model version": make_student(colour=GREEN, model_version="old"),
        }
        for label, student in cases.items():
            with self.subTest(case=label):
                self.assertNotEqual(match(RED).student_id, student.pk)
        self.assertEqual(match(RED).status, "empty")
        no_photo = make_student(colour=GREY)
        Student.objects.filter(pk=no_photo.pk).update(image="")
        self.assertEqual(match(GREY).status, "empty")

    def test_invalid_entries_are_skipped_without_disabling_others(self):
        good = make_student(colour=RED)
        broken = [make_student(colour=c, embedding=False) for c in (BLUE, GREEN, GREY)]
        FaceEmbedding.objects.create(student=broken[0], model_version=MODEL, vector=b"short")
        FaceEmbedding.objects.create(student=broken[1], model_version=MODEL,
                                     vector=np.full(128, np.nan, dtype="<f4").tobytes())
        FaceEmbedding.objects.create(student=broken[2], model_version=MODEL,
                                     vector=np.zeros(128, dtype="<f4").tobytes())
        self.assertEqual(match(RED).student_id, good.pk)
        ids, matrix = gallery.snapshot(MODEL, 128)
        self.assertEqual(list(ids), [good.pk])

    def test_missing_photo_file_does_not_break_matching(self):
        student = make_student(colour=RED)
        PrivateMediaStorage().delete(student.image.name)
        self.assertEqual(match(RED).student_id, student.pk)


class GalleryCacheTests(AppTestCase):
    def test_cache_reused_between_frames(self):
        make_student(colour=RED)
        match(RED)
        loads = gallery.loads
        for _ in range(5):
            match(RED)
        self.assertEqual(gallery.loads, loads)

    def test_no_enrolment_photo_is_read_or_re_encoded_per_frame(self):
        for colour in (RED, BLUE, GREEN):
            make_student(colour=colour)
        FakeFaceEngine.calls = 0
        with mock.patch.object(PrivateMediaStorage, "open", side_effect=AssertionError("photo read")):
            for _ in range(10):
                result = identify(solid(RED))
                self.assertEqual(result.status, "match")
        # Exactly one analysis per frame (the frame itself), never per enrolled student.
        self.assertEqual(FakeFaceEngine.calls, 10)

    def test_external_updates_are_seen_via_signature(self):
        student = make_student(colour=RED)
        self.assertEqual(match(RED).status, "match")
        # queryset.update() bypasses signals, as a change made by another process would.
        Student.objects.filter(pk=student.pk).update(authorized=False)
        self.assertEqual(match(RED).status, "empty")
        Student.objects.filter(pk=student.pk).update(authorized=True)
        self.assertEqual(match(RED).status, "match")

    def test_signal_invalidation_on_authorization_change(self):
        student = make_student(colour=RED)
        self.assertEqual(match(RED).student_id, student.pk)
        admin = self.admin_user()
        with self.captureOnCommitCallbacks(execute=True):
            student_service.set_authorization(student, False, actor=admin)
        self.assertEqual(match(RED).status, "empty")

    def test_invalidated_by_consent_withdrawal_and_deletion(self):
        admin = self.admin_user()
        withdrawn = make_student(colour=RED)
        deleted = make_student(colour=BLUE)
        self.assertEqual(match(RED).student_id, withdrawn.pk)
        self.assertEqual(match(BLUE).student_id, deleted.pk)
        with self.captureOnCommitCallbacks(execute=True):
            student_service.remove_biometrics(withdrawn, actor=admin, reason="withdrawn")
            student_service.delete_student(deleted, actor=admin)
        self.assertEqual(match(RED).status, "empty")
        self.assertEqual(match(BLUE).status, "empty")

    def test_photo_replacement_updates_template(self):
        admin = self.admin_user()
        student = make_student(colour=RED)
        with self.captureOnCommitCallbacks(execute=True):
            updated = student_service.replace_photo(student, image_bgr=solid(BLUE),
                                                    face=one_face(solid(BLUE))[0], actor=admin)
            student_service.set_authorization(updated, True, actor=admin)
        self.assertEqual(match(BLUE).student_id, student.pk)
        self.assertNotEqual(match(RED).status, "match")


class EngineLoadingTests(AppTestCase):
    def test_engine_created_lazily_once(self):
        engine_module.reset_engine()
        self.assertIsNone(engine_module._engine)
        first = get_engine()
        self.assertIs(get_engine(), first)

    @override_settings(FACE_ENGINE="app1.recognition.engine.OpenCVFaceEngine")
    def test_real_engine_reports_missing_models_clearly(self):
        with self.assertRaises(ModelUnavailable) as ctx:
            get_engine()
        self.assertIn("setup_face_models", str(ctx.exception))
        # Failure is not cached: a later call tries again.
        self.assertIsNone(engine_module._engine)

    def test_normalise_rejects_bad_vectors(self):
        for bad in (np.zeros(4), np.array([np.nan, 1.0]), np.array([np.inf, 0.0])):
            with self.assertRaises(ValueError):
                normalise(bad)
        self.assertAlmostEqual(float(np.linalg.norm(normalise([3.0, 4.0]))), 1.0, places=6)

    def test_downscale_bounds_analysis_size(self):
        from app1.recognition.engine import downscale

        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(downscale(image, 640).shape[:2], (360, 640))
        small = np.zeros((240, 320, 3), dtype=np.uint8)
        self.assertIs(downscale(small, 640), small)
