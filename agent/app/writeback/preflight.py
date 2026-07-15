"""Canonical document-category path/ID/ACL preflight (W2-D9)."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.documents import FailureReason


class CategoryMismatch(ValueError):
    reason = FailureReason.CATEGORY_MISMATCH


@dataclass(frozen=True)
class CategoryExpectation:
    path: str
    category_id: str


@dataclass(frozen=True)
class CategoryResolution:
    path: str
    category_id: str
    writable: bool


def verify_category_path(
    expected: CategoryExpectation, resolved: CategoryResolution
) -> str:
    """Return only the canonical path accepted by the write API, never its ID."""

    if (
        resolved.path != expected.path
        or resolved.category_id != expected.category_id
        or not resolved.writable
    ):
        raise CategoryMismatch("category path, expected id, or write ACL did not match")
    if not expected.path.startswith("/") or ".." in expected.path.split("/"):
        raise CategoryMismatch("category path is not canonical")
    return expected.path
