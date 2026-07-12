#!/usr/bin/env bash

set -euo pipefail

K6_IMAGE="${K6_IMAGE:-grafana/k6:2.1.0}"
LOAD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

expect_abort() {
    local expected="$1"
    shift

    local output
    local status
    set +e
    output="$(docker run --rm \
        -e K6_NO_USAGE_REPORT=true \
        "$@" \
        -v "${LOAD_DIR}:/scripts:ro" \
        "${K6_IMAGE}" run --quiet /scripts/chat_capped.js 2>&1)"
    status=$?
    set -e

    if [[ ${status} -eq 0 ]]; then
        echo "expected chat_capped.js to abort, but it exited 0" >&2
        exit 1
    fi
    if ! rg --fixed-strings --quiet "${expected}" <<<"${output}"; then
        echo "expected abort message not found: ${expected}" >&2
        exit 1
    fi
}

expect_public_abort() {
    local expected="$1"
    shift

    local output
    local status
    set +e
    output="$(docker run --rm \
        -e K6_NO_USAGE_REPORT=true \
        "$@" \
        -v "${LOAD_DIR}:/scripts:ro" \
        "${K6_IMAGE}" run --quiet /scripts/public_baseline.js 2>&1)"
    status=$?
    set -e

    if [[ ${status} -eq 0 ]]; then
        echo "expected public_baseline.js to abort, but it exited 0" >&2
        exit 1
    fi
    if ! rg --fixed-strings --quiet "${expected}" <<<"${output}"; then
        echo "expected abort message not found: ${expected}" >&2
        exit 1
    fi
}

profile="sonnet-4.6-200k-8192-single-call-retries2"

expect_abort "CHAT_BASE_URL is required"
expect_abort "CHAT_SESSION_ID is required" \
    -e CHAT_BASE_URL=https://example.invalid
expect_abort "CHAT_SPEND_CAP_USD is required" \
    -e CHAT_BASE_URL=https://example.invalid \
    -e CHAT_SESSION_ID=synthetic-session
expect_abort "below the conservative" \
    -e CHAT_BASE_URL=https://example.invalid \
    -e CHAT_SESSION_ID=synthetic-session \
    -e CHAT_SPEND_CAP_USD=0.50
expect_abort "exceeds the hard" \
    -e CHAT_BASE_URL=https://example.invalid \
    -e CHAT_SESSION_ID=synthetic-session \
    -e CHAT_SPEND_CAP_USD=3.01
expect_abort "CHAT_PROFILE_ACK must equal" \
    -e CHAT_BASE_URL=https://example.invalid \
    -e CHAT_SESSION_ID=synthetic-session \
    -e CHAT_SPEND_CAP_USD=2.62 \
    -e CHAT_PROFILE_ACK=wrong-profile
expect_abort "CHAT_VALIDATE_ONLY" \
    -e CHAT_BASE_URL=https://example.invalid \
    -e CHAT_SESSION_ID=synthetic-session \
    -e CHAT_SPEND_CAP_USD=2.62 \
    -e CHAT_PROFILE_ACK="${profile}" \
    -e CHAT_VALIDATE_ONLY=true

expect_public_abort "BASELINE_VUS is required"
expect_public_abort "BASELINE_VUS is required" -e BASELINE_VUS=11

echo "k6 guardrails: 9 fail-closed cases passed"
