"""W2-REQ-60 / AF-P1-10: bounded retry for the managed Cohere reranker.

PDF p.6 Engineering Requirements: "All outbound LLM and retrieval calls must
have timeouts and retry logic."  The Cohere path already had a 4 s timeout, a
2-failure/30 s breaker, a PHI screen, and a local fallback; these tests pin the
missing bounded-retry policy:

- retryable failures (timeout, connect error, HTTP 429, eligible 5xx) are
  retried within a bounded attempt budget;
- permanent failures (other 4xx, validation/contract errors) are never retried;
- backoff is jittered within deterministic bounds and never a real sleep here
  (the clock and sleep seams are injected);
- an overall deadline bounds total managed latency;
- the breaker is updated per logical attempt and stops retries mid-loop;
- fallback to the local reranker preserves the (scores, reason) contract;
- attempt/retry/fallback telemetry carries no query or document content.

Everything is offline: Cohere is always a stub and no test sleeps for real.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from corpus.retrieval import (
    CohereReranker,
    CohereRetryPolicy,
    RerankerSeam,
)


DOCUMENTS = ["hypertension management guidance", "lipid panel interpretation"]
QUERY = "hypertension"


class _FakeClock:
    """Deterministic monotonic clock; ``sleep`` only advances the clock."""

    def __init__(self, start: float = 1000.0):
        self.now_value = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_value += seconds

    def advance(self, seconds: float) -> None:
        self.now_value += seconds


class _ScriptedReranker:
    """Raises the scripted exception per call, then returns keyword scores."""

    model_name = "cohere-test-stub"

    def __init__(self, failures: list[Exception], *, clock: _FakeClock | None = None, call_cost_seconds: float = 0.0):
        self.failures = list(failures)
        self.calls = 0
        self.clock = clock
        self.call_cost_seconds = call_cost_seconds

    def scores(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        if self.clock is not None and self.call_cost_seconds:
            self.clock.advance(self.call_cost_seconds)
        if self.failures:
            raise self.failures.pop(0)
        return [0.99 if "hypertension" in document else 0.01 for document in documents]


class _LocalStub:
    model_name = "local-test-stub"

    def __init__(self) -> None:
        self.calls = 0

    def scores(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        return [0.99 if "hypertension" in document else 0.01 for document in documents]


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.cohere.com/v2/rerank")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("synthetic status", request=request, response=response)


def _seam(
    cohere: object,
    *,
    clock: _FakeClock,
    local: object | None = None,
    policy: CohereRetryPolicy | None = None,
    rng: object = None,
) -> RerankerSeam:
    return RerankerSeam(
        mode="cohere",
        cohere=cohere,
        local=local if local is not None else _LocalStub(),
        retry_policy=policy,
        clock=clock.now,
        sleep=clock.sleep,
        rng=rng if rng is not None else (lambda: 0.0),
    )


def test_default_policy_is_bounded_and_valid() -> None:
    policy = CohereRetryPolicy()
    assert policy.max_attempts == 2
    assert policy.backoff_seconds == 0.25
    assert policy.overall_deadline_seconds == 8.0


@pytest.mark.parametrize(
    ("max_attempts", "backoff_seconds", "overall_deadline_seconds"),
    [(0, 0.25, 8.0), (2, -0.1, 8.0), (2, 0.25, 0.0)],
)
def test_policy_rejects_unbounded_or_negative_configuration(
    max_attempts: int, backoff_seconds: float, overall_deadline_seconds: float
) -> None:
    with pytest.raises(ValueError):
        CohereRetryPolicy(
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            overall_deadline_seconds=overall_deadline_seconds,
        )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("synthetic timeout"),
        httpx.ConnectTimeout("synthetic connect timeout"),
        httpx.ConnectError("synthetic connect failure"),
        _status_error(429),
        _status_error(500),
        _status_error(502),
        _status_error(503),
        _status_error(504),
    ],
)
def test_retryable_failure_is_retried_once_then_succeeds(failure: Exception) -> None:
    clock = _FakeClock()
    cohere = _ScriptedReranker([failure])
    seam = _seam(cohere, clock=clock)

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert scores == [0.99, 0.01]
    assert reason is None
    assert cohere.calls == 2
    assert len(clock.sleeps) == 1


@pytest.mark.parametrize(
    "failure",
    [
        _status_error(400),
        _status_error(401),
        _status_error(403),
        _status_error(404),
        _status_error(422),
        RuntimeError("reranker returned an invalid result shape"),
        ValueError("reranker score is outside the response contract"),
    ],
)
def test_permanent_failure_is_never_retried(failure: Exception) -> None:
    clock = _FakeClock()
    local = _LocalStub()
    cohere = _ScriptedReranker([failure])
    seam = _seam(cohere, clock=clock, local=local)

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert scores == [0.99, 0.01]
    assert reason == "cohere_unavailable"
    assert cohere.calls == 1  # no retry on a permanent failure
    assert clock.sleeps == []  # and no backoff sleep either
    assert local.calls == 1


def test_retry_budget_is_exhausted_after_max_attempts_then_falls_back() -> None:
    clock = _FakeClock()
    local = _LocalStub()
    cohere = _ScriptedReranker(
        [httpx.ReadTimeout("synthetic timeout"), httpx.ReadTimeout("synthetic timeout")]
    )
    seam = _seam(cohere, clock=clock, local=local)

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert scores == [0.99, 0.01]
    assert reason == "cohere_unavailable"
    assert cohere.calls == 2  # default budget: one initial attempt + one retry
    assert len(clock.sleeps) == 1
    assert local.calls == 1


def test_backoff_is_jittered_within_deterministic_bounds() -> None:
    base = CohereRetryPolicy().backoff_seconds

    low_clock = _FakeClock()
    low = _ScriptedReranker([httpx.ReadTimeout("synthetic timeout")])
    _seam(low, clock=low_clock, rng=lambda: 0.0).rerank(QUERY, DOCUMENTS)

    high_clock = _FakeClock()
    high = _ScriptedReranker([httpx.ReadTimeout("synthetic timeout")])
    _seam(high, clock=high_clock, rng=lambda: 0.999999).rerank(QUERY, DOCUMENTS)

    assert low_clock.sleeps == [pytest.approx(base * 0.5)]
    assert len(high_clock.sleeps) == 1
    assert base * 0.5 <= high_clock.sleeps[0] < base


def test_overall_deadline_stops_retries_and_bounds_total_latency() -> None:
    policy = CohereRetryPolicy()
    clock = _FakeClock()
    start = clock.now()
    # Each managed attempt burns almost the whole deadline (a slow 4 s timeout
    # scaled up): the first failure must not schedule a retry.
    cohere = _ScriptedReranker(
        [httpx.ReadTimeout("synthetic timeout")], clock=clock, call_cost_seconds=7.9
    )
    seam = _seam(cohere, clock=clock)

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert reason == "cohere_unavailable"
    assert scores == [0.99, 0.01]
    assert cohere.calls == 1  # no retry may begin past the deadline
    assert clock.sleeps == []  # the deadline exit never sleeps first
    assert clock.now() - start <= policy.overall_deadline_seconds


def test_total_latency_stays_bounded_when_retry_is_allowed() -> None:
    policy = CohereRetryPolicy()
    attempt_timeout_seconds = 4.0  # CohereReranker's frozen per-attempt timeout
    clock = _FakeClock()
    start = clock.now()
    cohere = _ScriptedReranker(
        [
            httpx.ReadTimeout("synthetic timeout"),
            httpx.ReadTimeout("synthetic timeout"),
        ],
        clock=clock,
        call_cost_seconds=attempt_timeout_seconds,
    )
    seam = _seam(cohere, clock=clock)

    _, reason = seam.rerank(QUERY, DOCUMENTS)

    assert reason == "cohere_unavailable"
    assert cohere.calls == 2
    # A retry may only START inside the deadline, so the worst-case wall time
    # is the deadline plus one per-attempt timeout.
    assert clock.now() - start <= policy.overall_deadline_seconds + attempt_timeout_seconds


def test_breaker_counts_each_attempt_and_blocks_the_next_request() -> None:
    clock = _FakeClock()
    local = _LocalStub()
    cohere = _ScriptedReranker(
        [httpx.ConnectError("synthetic"), httpx.ConnectError("synthetic")]
    )
    seam = _seam(cohere, clock=clock, local=local)

    _, first_reason = seam.rerank(QUERY, DOCUMENTS)
    assert first_reason == "cohere_unavailable"
    assert cohere.calls == 2  # both attempts counted by the 2-failure breaker

    _, second_reason = seam.rerank(QUERY, DOCUMENTS)
    assert second_reason == "cohere_unavailable"
    assert cohere.calls == 2  # circuit open: zero further managed calls
    assert local.calls == 2


def test_open_circuit_stops_the_retry_loop_without_a_futile_sleep() -> None:
    clock = _FakeClock()
    policy = CohereRetryPolicy(max_attempts=3)
    cohere = _ScriptedReranker(
        [
            _status_error(429),
            _status_error(429),
            _status_error(429),
        ]
    )
    seam = _seam(cohere, clock=clock, policy=policy)

    _, reason = seam.rerank(QUERY, DOCUMENTS)

    assert reason == "cohere_unavailable"
    assert cohere.calls == 2  # the breaker opened after the second attempt
    assert len(clock.sleeps) == 1  # no sleep is spent once the circuit is open


def test_failed_half_open_probe_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    # The breaker itself (frozen behavior, outside the retry seam) reads
    # ``corpus.retrieval.time.monotonic``; pin it to the same fake clock.
    monkeypatch.setattr("corpus.retrieval.time.monotonic", clock.now)
    cohere = _ScriptedReranker(
        [
            httpx.ConnectError("synthetic"),
            httpx.ConnectError("synthetic"),
            httpx.ConnectError("synthetic"),
        ]
    )
    seam = _seam(cohere, clock=clock)

    seam.rerank(QUERY, DOCUMENTS)  # two attempts; the breaker opens
    assert cohere.calls == 2

    clock.advance(31.0)  # past the 30 s recovery window
    sleeps_before = len(clock.sleeps)
    _, reason = seam.rerank(QUERY, DOCUMENTS)

    assert reason == "cohere_unavailable"
    assert cohere.calls == 3  # exactly one half-open probe, never hammered
    assert len(clock.sleeps) == sleeps_before


def test_fallback_without_local_reranker_keeps_the_contract_shape() -> None:
    clock = _FakeClock()
    cohere = _ScriptedReranker(
        [httpx.ReadTimeout("synthetic timeout"), httpx.ReadTimeout("synthetic timeout")]
    )
    seam = RerankerSeam(
        mode="cohere",
        cohere=cohere,
        local=None,
        clock=clock.now,
        sleep=clock.sleep,
        rng=lambda: 0.0,
    )

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert scores is None
    assert reason == "cohere_unavailable"


def test_success_after_retry_keeps_the_breaker_closed() -> None:
    clock = _FakeClock()
    cohere = _ScriptedReranker([httpx.ReadTimeout("synthetic timeout")])
    seam = _seam(cohere, clock=clock)

    _, first_reason = seam.rerank(QUERY, DOCUMENTS)
    assert first_reason is None
    assert cohere.calls == 2

    _, second_reason = seam.rerank(QUERY, DOCUMENTS)
    assert second_reason is None
    assert cohere.calls == 3  # the recovered breaker allows the next request


def test_retry_through_the_stubbed_cohere_http_client_classifies_transport_errors() -> None:
    class _TimeoutOnceClient:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, url: str, **kwargs: object) -> object:
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadTimeout("synthetic timeout")

            class _Response:
                @staticmethod
                def raise_for_status() -> None:
                    return None

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "results": [
                            {"index": 0, "relevance_score": 0.9},
                            {"index": 1, "relevance_score": 0.1},
                        ]
                    }

            return _Response()

    client = _TimeoutOnceClient()
    clock = _FakeClock()
    seam = _seam(CohereReranker("unit-test-placeholder", client=client), clock=clock)

    scores, reason = seam.rerank(QUERY, DOCUMENTS)

    assert scores == [0.9, 0.1]
    assert reason is None
    assert client.calls == 2
    assert len(clock.sleeps) == 1


def test_retry_telemetry_names_fields_and_carries_no_request_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agent.evidence_retrieval")
    clock = _FakeClock()
    cohere = _ScriptedReranker(
        [httpx.ReadTimeout("synthetic timeout"), _status_error(429)]
    )
    seam = _seam(cohere, clock=clock)

    seam.rerank(QUERY, DOCUMENTS)

    by_message: dict[str, list[logging.LogRecord]] = {}
    for record in caplog.records:
        if record.name == "agent.evidence_retrieval":
            by_message.setdefault(record.message, []).append(record)

    attempts = by_message["rerank.cohere.attempt"]
    assert [record.attempt for record in attempts] == [1, 2]
    assert attempts[0].failure_class == "timeout"
    assert attempts[0].retryable is True
    assert attempts[1].failure_class == "http_429"
    assert attempts[1].retryable is True

    (retry,) = by_message["rerank.cohere.retry"]
    assert retry.attempt == 2
    assert retry.backoff_ms > 0

    (fallback,) = by_message["rerank.cohere.fallback"]
    assert fallback.reason == "attempts_exhausted"
    assert fallback.attempts == 2

    assert QUERY not in caplog.text
    for document in DOCUMENTS:
        assert document not in caplog.text


def test_permanent_failure_telemetry_reports_the_status_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agent.evidence_retrieval")
    clock = _FakeClock()
    seam = _seam(_ScriptedReranker([_status_error(404)]), clock=clock)

    seam.rerank(QUERY, DOCUMENTS)

    by_message: dict[str, list[logging.LogRecord]] = {}
    for record in caplog.records:
        if record.name == "agent.evidence_retrieval":
            by_message.setdefault(record.message, []).append(record)

    (attempt,) = by_message["rerank.cohere.attempt"]
    assert attempt.failure_class == "http_404"
    assert attempt.retryable is False
    (fallback,) = by_message["rerank.cohere.fallback"]
    assert fallback.reason == "permanent_failure"
    assert "rerank.cohere.retry" not in by_message
    assert QUERY not in caplog.text
