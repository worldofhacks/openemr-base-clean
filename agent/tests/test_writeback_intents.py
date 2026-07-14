"""Exactly-once intent protocol tests (W2-D10; §3)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.schemas.writeback import WriteLeg, WriteState


@dataclass
class FakeTransport:
    discoveries: list[list[object]] = field(default_factory=list)
    post_result: object | None = "remote-1"
    post_error: Exception | None = None
    posts: int = 0

    async def discover(self, _intent):
        return self.discoveries.pop(0) if self.discoveries else []

    async def post(self, _intent, _payload):
        self.posts += 1
        if self.post_error is not None:
            raise self.post_error
        return self.post_result

    async def verify(self, _intent, match, _payload_hash):
        return bool(getattr(match, "verified", True))


def _spec(patient_id: str = "patient-synthetic-a"):
    from app.writeback.intents import IntentSpec

    return IntentSpec(
        patient_id=patient_id,
        document_id_or_content_hash="sha256:synthetic",
        leg=WriteLeg.SOURCE_DOCUMENT,
        version=1,
        field_id="source",
        correlation_marker="corr-marker-1",
        payload_hash="payload-sha256",
    )


@pytest.mark.asyncio
async def test_patient_is_part_of_the_permanent_intent_key():
    from app.writeback.intents import InMemoryIntentRepository

    repo = InMemoryIntentRepository()
    first = await repo.get_or_create(_spec("patient-synthetic-a"))
    duplicate = await repo.get_or_create(_spec("patient-synthetic-a"))
    other_patient = await repo.get_or_create(_spec("patient-synthetic-b"))

    assert first.intent_id == duplicate.intent_id
    assert first.intent_id != other_patient.intent_id


@pytest.mark.asyncio
async def test_reconcile_unique_remote_match_completes_without_post():
    from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository, RemoteMatch

    repo = InMemoryIntentRepository()
    transport = FakeTransport(
        discoveries=[
            [RemoteMatch(remote_id="remote-existing", payload_hash="payload-sha256")]
        ]
    )

    result = await ExactlyOnceWriter(repo, transport).execute(_spec(), payload={"x": 1})

    assert result.state is WriteState.COMPLETE
    assert result.remote_id == "remote-existing"
    assert result.verified is True
    assert transport.posts == 0


@pytest.mark.asyncio
async def test_commit_then_timeout_moves_unknown_and_never_blind_retries():
    from app.writeback.intents import (
        AmbiguousCommitError,
        ExactlyOnceWriter,
        InMemoryIntentRepository,
        ReconciliationRequired,
    )

    repo = InMemoryIntentRepository()
    transport = FakeTransport(post_error=AmbiguousCommitError("timeout after send"))
    writer = ExactlyOnceWriter(repo, transport)

    first = await writer.execute(_spec(), payload={"x": 1})
    assert first.state is WriteState.UNKNOWN
    assert transport.posts == 1

    with pytest.raises(ReconciliationRequired):
        await writer.execute(_spec(), payload={"x": 1})
    assert transport.posts == 1


@pytest.mark.asyncio
async def test_conflicting_remote_matches_stop_without_post():
    from app.writeback.intents import (
        ExactlyOnceWriter,
        InMemoryIntentRepository,
        ReconciliationConflict,
        RemoteMatch,
    )

    transport = FakeTransport(
        discoveries=[
            [
                RemoteMatch(remote_id="remote-1", payload_hash="payload-sha256"),
                RemoteMatch(remote_id="remote-2", payload_hash="payload-sha256"),
            ]
        ]
    )

    with pytest.raises(ReconciliationConflict):
        await ExactlyOnceWriter(InMemoryIntentRepository(), transport).execute(
            _spec(), payload={"x": 1}
        )
    assert transport.posts == 0
