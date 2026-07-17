"""ORM-independent persistence contracts for authoritative aggregates."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Protocol, TypeVar

from orchestrator.domain.authoritative import AuthoritativeRecord
from orchestrator.domain.primitives import IdempotencyKey, RecordVersion

RecordT = TypeVar("RecordT", bound=AuthoritativeRecord)


class DuplicateRecordError(Exception):
    """The aggregate identifier is already owned by an authoritative record."""


class DuplicateIdempotencyKeyError(Exception):
    """An idempotency key is already bound to a different aggregate identifier."""


class RepositoryConstraintError(Exception):
    """A database constraint rejected an otherwise valid repository operation."""


class ConcurrentWriteError(Exception):
    """A compare-and-swap write used a stale record version."""


class AuthoritativeRepository(Protocol[RecordT]):
    """Storage operations with no embedded domain transition policy."""

    def add(self, record: RecordT) -> None:
        """Store a new authoritative record, rejecting a duplicate identifier."""

    def get(self, record_id: str) -> RecordT | None:
        """Return the current record for an aggregate identifier, if present."""

    def get_by_idempotency_key(self, idempotency_key: IdempotencyKey) -> RecordT | None:
        """Return the record durably bound to an idempotency key, if any."""

    def compare_and_swap(
        self, record: RecordT, *, expected_record_version: RecordVersion
    ) -> None:
        """Replace a record only at exactly one version after the expectation."""


class RepositoryUnitOfWork(Protocol):
    """Transaction boundary for coordinated authoritative writes."""

    def transaction(self) -> AbstractContextManager[RepositoryUnitOfWork]:
        """Open one atomic repository transaction."""

    def iter_repositories(
        self,
    ) -> Iterator[AuthoritativeRepository[AuthoritativeRecord]]:
        """Expose repositories for infrastructure diagnostics and test fixtures."""
