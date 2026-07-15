"""Persistent extraction refs + per-turn graph refs (W2-D2/D3; §2/§3)."""

from __future__ import annotations

import pytest

from app.orchestrator.refs import CompositeRefResolver, TurnRefRegistry


class _PersistentRefs:
    def __init__(self) -> None:
        self.values = {"document:synthetic:artifact": {"grounded": True}}

    def resolve(self, ref: str) -> object | None:
        return self.values.get(ref)


def test_composite_refs_write_to_turn_and_resolve_persistent_fallback() -> None:
    turn = TurnRefRegistry("corr-synthetic")
    persistent = _PersistentRefs()
    refs = CompositeRefResolver(turn, persistent)

    turn_ref = refs.put({"query": "type 2 diabetes"}, kind="evidence-request")

    assert refs.resolve(turn_ref) == {"query": "type 2 diabetes"}
    assert refs.resolve("document:synthetic:artifact") == {"grounded": True}


def test_composite_refs_fail_closed_when_no_authority_resolves() -> None:
    refs = CompositeRefResolver(TurnRefRegistry("corr-synthetic"), _PersistentRefs())

    with pytest.raises(KeyError, match="unresolvable graph reference"):
        refs.resolve("document:missing:artifact")
