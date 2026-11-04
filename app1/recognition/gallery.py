"""In-memory cache of the embeddings that are currently allowed to be matched.

Eligible = student authorised AND consent given AND photo present AND an embedding for
the engine's model version. The cache is rebuilt only when a cheap signature query
over the eligible rows changes (so updates made by other worker processes are picked
up), or when a signal invalidates it in this process. Matching never reads or
re-encodes enrolment photos.
"""
import logging
import threading
from dataclasses import dataclass

import numpy as np
from django.db.models import Count, Max, Sum

from ..models import FaceEmbedding, Student

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchResult:
    status: str  # "match", "ambiguous", "unknown" or "empty"
    student_id: int | None = None
    distance: float | None = None
    runner_up_distance: float | None = None


def eligible_embeddings(model_version):
    return FaceEmbedding.objects.filter(
        model_version=model_version,
        student__authorized=True,
        student__consent_status=Student.Consent.GIVEN,
    ).exclude(student__image="")


class Gallery:
    def __init__(self):
        self._lock = threading.Lock()
        self._key = None
        self._student_ids = np.empty(0, dtype=np.int64)
        self._matrix = np.empty((0, 0), dtype=np.float32)
        self.loads = 0  # number of rebuilds; useful for tests and diagnostics

    def invalidate(self):
        with self._lock:
            self._key = None

    @staticmethod
    def _signature(model_version):
        aggregate = eligible_embeddings(model_version).aggregate(
            count=Count("id"),
            id_sum=Sum("id"),
            newest_embedding=Max("created_at"),
            newest_student_change=Max("student__updated_at"),
        )
        return tuple(aggregate.values())

    def snapshot(self, model_version, dim):
        signature = self._signature(model_version)
        key = (model_version, dim, signature)
        with self._lock:
            if key != self._key:
                self._student_ids, self._matrix = self._load(model_version, dim)
                self._key = key
                self.loads += 1
            return self._student_ids, self._matrix

    @staticmethod
    def _load(model_version, dim):
        ids, vectors = [], []
        rows = eligible_embeddings(model_version).values_list("id", "student_id", "vector")
        for embedding_id, student_id, raw in rows.iterator():
            try:
                vector = np.frombuffer(bytes(raw), dtype="<f4")
                if vector.shape != (dim,) or not np.isfinite(vector).all():
                    raise ValueError("wrong shape or non-finite values")
                norm = float(np.linalg.norm(vector))
                if norm == 0.0:
                    raise ValueError("zero vector")
            except ValueError as exc:
                # Skip just this entry; recognition keeps working for everyone else.
                logger.warning("Skipping invalid face embedding #%s: %s", embedding_id, exc)
                continue
            ids.append(student_id)
            vectors.append(vector / norm)
        if not vectors:
            return np.empty(0, dtype=np.int64), np.empty((0, dim), dtype=np.float32)
        return np.asarray(ids, dtype=np.int64), np.vstack(vectors).astype(np.float32)

    def match(self, embedding, *, model_version, threshold, margin):
        embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
        student_ids, matrix = self.snapshot(model_version, embedding.shape[0])
        if len(student_ids) == 0:
            return MatchResult("empty")
        distances = np.linalg.norm(matrix - embedding, axis=1)
        order = np.argsort(distances)
        best = int(order[0])
        best_distance = float(distances[best])
        best_student = int(student_ids[best])
        runner_up = None
        for index in order[1:]:
            if int(student_ids[index]) != best_student:
                runner_up = float(distances[index])
                break
        if best_distance >= threshold:
            return MatchResult("unknown", None, best_distance, runner_up)
        if runner_up is not None and runner_up - best_distance < margin:
            return MatchResult("ambiguous", None, best_distance, runner_up)
        return MatchResult("match", best_student, best_distance, runner_up)


gallery = Gallery()
