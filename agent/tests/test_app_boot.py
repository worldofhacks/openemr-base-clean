"""E1.1 — the app boots with a valid environment (ARCHITECTURE.md §2)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_boots_and_exposes_root(complete_env):
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "clinical-copilot-agent"
        # No secret should ever appear in a public response.
        assert "test-client-secret" not in resp.text
        assert "sk-ant-test" not in resp.text


# ---------------------------------------------------------------------------
# W2-M1 — Day-1 container spike: W2 native-dependency import smoke (AC-1) and
# the no-torch / consistent-environment guard (AC-2). Appended per the
# extend-only rule; everything above this marker is frozen W1 and untouched.
#
# All third-party imports happen INSIDE test bodies via importlib so a missing
# package surfaces as a clean, per-test FAILURE with an actionable message —
# never a collection error that takes down the whole file. No model downloads
# and no network access anywhere below (imports and local-metadata scans only).
# ---------------------------------------------------------------------------

import importlib
import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys

import pytest


def _import_w2_dep_or_fail(module_name: str, why: str):
    """Import a W2-M1 dependency inside a test body, failing helpfully if absent.

    Keeps a missing package a per-test assertion failure (with remediation
    guidance) instead of an ImportError at collection time.
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(
            f"W2-M1 dependency '{module_name}' is not importable ({why}). "
            f"Stage it via agent/pyproject.toml and reinstall the environment "
            f"(this ticket solely owns pyproject/Dockerfile edits this wave). "
            f"Import error: {exc!r}"
        )


# spec(W2-M1:AC-1)
# guards: MVP deploy discovering a broken/missing pdfium native wheel Tuesday
# night — the entire W2-R6 reader stack renders through pypdfium2.
def test_pypdfium2_imports_and_reports_version():
    """pypdfium2 imports (loading its bundled pdfium native library) and the
    installed distribution reports a non-empty version. Import + local
    metadata only — no file rendering, no network."""
    _import_w2_dep_or_fail("pypdfium2", "pdfium renderer, W2-R6 reader stack")
    try:
        version = importlib.metadata.version("pypdfium2")
    except importlib.metadata.PackageNotFoundError:
        pytest.fail(
            "pypdfium2 module is importable but no 'pypdfium2' distribution "
            "metadata is installed — vendored/broken install; it must be a "
            "real pinned dependency in agent/pyproject.toml"
        )
    assert isinstance(version, str) and version.strip(), (
        "pypdfium2 is installed but reports an empty distribution version — "
        "broken install metadata"
    )


# spec(W2-M1:AC-1)
# guards: W2-M4 (words+boxes reader) landing on an image where pdfplumber and
# its pillow dependency were never staged — W2-M4 may NOT touch pyproject, so
# this ticket is the only place those deps can land.
def test_pdfplumber_imports_with_its_pillow_dependency():
    """pdfplumber imports, and PIL.Image (pillow — the transitive imaging dep
    this ticket stages for W2-M4) imports alongside it. Import-only."""
    _import_w2_dep_or_fail("pdfplumber", "words+boxes extraction, staged for W2-M4")
    _import_w2_dep_or_fail(
        "PIL.Image", "pillow, pdfplumber's imaging dependency required by W2-M4"
    )


# spec(W2-M1:AC-1)
# guards: OCR grounds (W2-D3) silently absent in production — the pytesseract
# wheel installs fine while the tesseract BINARY (and eng traineddata) is
# missing from the image, which no pure-import smoke would catch.
def test_pytesseract_imports_and_resolves_tesseract_binary():
    """pytesseract imports AND a working tesseract binary resolves on PATH:
    pytesseract.get_tesseract_version() must succeed and return a truthy
    version. Local binary invocation only — no OCR run, no network."""
    mod = _import_w2_dep_or_fail("pytesseract", "local OCR grounds, W2-D3")
    try:
        version = mod.get_tesseract_version()
    except Exception as exc:  # TesseractNotFoundError etc. — any failure means no usable binary
        pytest.fail(
            "pytesseract imported but no working 'tesseract' binary resolves "
            f"on PATH (get_tesseract_version() raised {exc!r}). On Debian slim "
            "install 'tesseract-ocr' + 'tesseract-ocr-eng'; locally, install "
            "the tesseract package for this platform."
        )
    assert version, (
        "pytesseract.get_tesseract_version() returned a falsy value — the "
        "tesseract binary is not reporting a usable version"
    )


# spec(W2-M1:AC-1)
# guards: the embedding/reranker stack (bge-small + mxbai-rerank-base-v1,
# W2-D4 ONNX rev fallback) being unimportable — or pinned to a fastembed
# version too old to expose the cross-encoder reranker API at all.
def test_fastembed_embedding_and_reranker_modules_import():
    """fastembed imports and exposes TextEmbedding, and its cross-encoder
    reranker module imports and exposes TextCrossEncoder. Imports only —
    no model instantiation, therefore no model download and no network."""
    fastembed = _import_w2_dep_or_fail("fastembed", "ONNX embeddings, W2-D4")
    assert hasattr(fastembed, "TextEmbedding"), (
        "fastembed imported but does not expose TextEmbedding — wrong or "
        "broken fastembed distribution; bge-small-en-v1.5 loads through it"
    )
    cross_encoder_mod = _import_w2_dep_or_fail(
        "fastembed.rerank.cross_encoder",
        "local reranker module for mxbai-rerank-base-v1, W2-D4 rev fallback",
    )
    assert hasattr(cross_encoder_mod, "TextCrossEncoder"), (
        "fastembed.rerank.cross_encoder imported but does not expose "
        "TextCrossEncoder — fastembed pin too old for the local reranker path"
    )


# spec(W2-M1:AC-1)
# guards: an onnxruntime wheel that installs but whose native library is
# broken (or provider-less) on the deploy architecture — imports fine in
# metadata terms yet cannot execute a single model.
def test_onnxruntime_imports_and_offers_cpu_execution_provider():
    """onnxruntime imports and lists CPUExecutionProvider among available
    providers — proving the native runtime loaded, without loading any model
    and without network."""
    ort = _import_w2_dep_or_fail("onnxruntime", "ONNX runtime backing fastembed, W2-D4")
    providers = ort.get_available_providers()
    assert "CPUExecutionProvider" in providers, (
        f"onnxruntime imported but CPUExecutionProvider is unavailable "
        f"(providers={providers!r}) — the native runtime cannot execute "
        f"models on this architecture"
    )


def _canonical_dist_name(name: str) -> str:
    """PEP 503 canonicalization (stdlib-only, no packaging import needed here)."""
    return re.sub(r"[-_.]+", "-", name).lower()


# spec(W2-M1:AC-2)
# guards: a transitive dep of the embedding/rerank stack quietly dragging the
# multi-hundred-MB torch wheel into the image and blowing the Railway RSS and
# image-size budget (§6 W2-O1) — the ONNX path exists precisely to avoid it.
def test_no_torch_distribution_anywhere_in_dependency_tree():
    """Pinned invariant — EXPECTED GREEN at RED phase by construction (ticket
    AC-2 marks it a guard): torch is absent today and must STAY absent as the
    W2 deps land. Checks three layers: no importable 'torch' module, no
    installed torch/pytorch distribution, and no installed distribution that
    unconditionally requires torch. Local metadata scan only — no network."""
    assert importlib.util.find_spec("torch") is None, (
        "a 'torch' module is importable in this environment — torch is banned "
        "anywhere in the W2 dependency tree (ticket W2-M1, W2-D4 ONNX path)"
    )

    installed: dict[str, importlib.metadata.Distribution] = {}
    for dist in importlib.metadata.distributions():
        try:
            raw_name = dist.metadata["Name"]
        except Exception:
            raw_name = None
        if not raw_name:
            continue
        installed[_canonical_dist_name(raw_name)] = dist

    banned_names = {"torch", "pytorch"}
    present_banned = sorted(banned_names & installed.keys())
    assert not present_banned, (
        f"banned distribution(s) installed: {present_banned} — no torch "
        f"distribution may be present anywhere in the dependency tree (AC-2)"
    )

    # Dependency-tree scan: no installed distribution may declare an ACTIVE
    # (non-extra-gated, marker-true) requirement on torch. Extra-gated torch
    # requirements (e.g. `torch; extra == "gpu"`) are inert unless installed,
    # so they are skipped exactly as `pip check` would skip them.
    from packaging.requirements import InvalidRequirement, Requirement

    offenders: list[str] = []
    for cname, dist in installed.items():
        for req_str in dist.requires or []:
            try:
                req = Requirement(req_str)
            except InvalidRequirement:
                continue
            if _canonical_dist_name(req.name) not in banned_names:
                continue
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            offenders.append(f"{cname} -> {req_str}")
    assert not offenders, (
        f"installed distribution(s) unconditionally require torch: {offenders} "
        f"— the W2 dep set must resolve without torch (AC-2, W2-D4)"
    )


# spec(W2-M1:AC-2)
# guards: new deps landing with silently broken/conflicting requirement
# metadata that only explodes at import time inside the deployed container.
def test_installed_environment_passes_pip_check():
    """Pinned invariant — EXPECTED GREEN at RED phase by construction (ticket
    AC-2): the installed environment's dependency metadata is self-consistent
    per `pip check`, and must remain so as the W2 deps land. `pip check` is a
    purely local installed-metadata scan; the version self-check is disabled
    so no network is touched."""
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "pip check reports a broken dependency tree for this environment:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# W2-M1 independent-review freeze additions (AC-5). These exercise only the
# operator probe's deterministic seams: synthetic data, fake cgroup files, and
# fake model loaders. They never download a model, open a socket, or run OCR.
# ---------------------------------------------------------------------------

import json


_MIB = 1024**2
_IMMUTABLE_HF_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_EXPECTED_HF_MODEL_REVISIONS = {
    "qdrant/bge-small-en-v1.5-onnx-q": "52398278842ec682c6f32300af41344b1c0b0bb2",
    "mixedbread-ai/mxbai-rerank-base-v1": "800f24c113213a187e65bde9db00c15a2bb12738",
}


def _fake_cgroup_v2_files(
    monkeypatch,
    spike,
    tmp_path,
    *,
    limit_bytes: int | None,
    current_bytes: int | None,
    peak_bytes: int | None,
) -> None:
    """Point every cgroup-v2 probe path at a deterministic local fixture."""

    cgroup = tmp_path / "fake-cgroup-v2"
    cgroup.mkdir()

    values = {
        "memory.max": "max" if limit_bytes is None else str(limit_bytes),
        "memory.current": None if current_bytes is None else str(current_bytes),
        "memory.peak": None if peak_bytes is None else str(peak_bytes),
    }
    for filename, value in values.items():
        if value is not None:
            (cgroup / filename).write_text(value, encoding="ascii")

    monkeypatch.setattr(spike, "CGROUP_V2_LIMIT", str(cgroup / "memory.max"))
    monkeypatch.setattr(
        spike, "CGROUP_V1_LIMIT", str(cgroup / "missing-v1-limit")
    )
    # These two path constants are the required test seam for container-wide
    # usage. ``raising=False`` keeps this a clean RED assertion on today's
    # process-only implementation while freezing the names for the repair.
    monkeypatch.setattr(
        spike, "CGROUP_V2_CURRENT", str(cgroup / "memory.current"), raising=False
    )
    monkeypatch.setattr(
        spike, "CGROUP_V2_PEAK", str(cgroup / "memory.peak"), raising=False
    )


def _stub_probe_work(monkeypatch, spike) -> None:
    """Replace quick-probe OCR/image work; numpy/BM25 remain real and local."""

    monkeypatch.setattr(spike, "synthetic_chunks", lambda count: ["synthetic"] * count)
    monkeypatch.setattr(spike, "make_synthetic_page_200dpi", lambda: object())
    monkeypatch.setattr(spike, "run_ocr", lambda _image: "SYNTHETIC")
    monkeypatch.setattr(spike.time, "sleep", lambda _seconds: None)


def _probe_report(stdout: str) -> dict[str, object]:
    """Decode the JSON object that precedes the final human-readable log line."""

    start = stdout.index('{\n  "mode"')
    report, _end = json.JSONDecoder().raw_decode(stdout[start:])
    return report


def _run_quick_probe(monkeypatch, capsys, spike) -> tuple[int, dict[str, object]]:
    _stub_probe_work(monkeypatch, spike)
    exit_code = spike.main(["--quick", "--chunks", "1"])
    return exit_code, _probe_report(capsys.readouterr().out)


# spec(W2-M1:AC-5)
# guards: certifying only the short-lived probe process while another process
# (the serving app or runtime) owns most of the deployed container's memory.
def test_capacity_peak_includes_cgroup_container_memory(
    monkeypatch, tmp_path, capsys
):
    """The reported peak is container-wide, not merely /proc/self VmHWM."""

    spike = importlib.import_module("ops.spike_rss")
    _fake_cgroup_v2_files(
        monkeypatch,
        spike,
        tmp_path,
        limit_bytes=512 * _MIB,
        current_bytes=220 * _MIB,
        peak_bytes=300 * _MIB,
    )
    monkeypatch.setattr(spike, "best_rss_mb", lambda: 64.0)
    monkeypatch.setattr(spike, "peak_rss_mb", lambda: 64.0)

    exit_code, report = _run_quick_probe(monkeypatch, capsys, spike)

    assert report["peak_rss_mb"] == 300, (
        "AC-5 must compare the ceiling with container-wide cgroup memory; "
        "the fake cgroup's post-workload current usage is 220 MiB and its "
        "actual peak is 300 MiB while the probe process peaked at 64 MiB, "
        f"but the report recorded {report['peak_rss_mb']!r} MiB"
    )
    assert report["verdict"] == "PASS" and exit_code == 0, (
        "a fully measured 300 MiB container peak under the 409 MiB ceiling "
        "must produce PASS and exit zero (an always-NO-VERDICT repair is not "
        f"valid); got verdict={report['verdict']!r}, exit_code={exit_code}"
    )


# spec(W2-M1:AC-5)
# guards: an UNKNOWN limit or an unavailable peak falling through ``ok =
# not errors`` and being emitted as a successful operator command.
@pytest.mark.parametrize("missing_measurement", ["limit", "peak"])
def test_capacity_probe_no_verdict_exits_nonzero_when_measurement_unavailable(
    monkeypatch, tmp_path, capsys, missing_measurement
):
    """Missing either required AC-5 measurement can never certify capacity."""

    spike = importlib.import_module("ops.spike_rss")
    _fake_cgroup_v2_files(
        monkeypatch,
        spike,
        tmp_path,
        limit_bytes=None if missing_measurement == "limit" else 512 * _MIB,
        current_bytes=None if missing_measurement == "peak" else 96 * _MIB,
        peak_bytes=None if missing_measurement == "peak" else 128 * _MIB,
    )
    if missing_measurement == "peak":
        monkeypatch.setattr(spike, "best_rss_mb", lambda: None)
        monkeypatch.setattr(spike, "peak_rss_mb", lambda: None)
    else:
        monkeypatch.setattr(spike, "best_rss_mb", lambda: 48.0)
        monkeypatch.setattr(spike, "peak_rss_mb", lambda: 64.0)

    exit_code, report = _run_quick_probe(monkeypatch, capsys, spike)

    assert "NO-VERDICT" in str(report["verdict"]) and exit_code != 0, (
        f"missing {missing_measurement} must emit NO-VERDICT and return nonzero; "
        f"got verdict={report['verdict']!r}, exit_code={exit_code}"
    )


# spec(W2-M1:AC-5)
# guards: the exact ONNX weights used for the measured bge/mxbai stack drifting
# at an unpinned Hugging Face branch after this capacity evidence is approved.
def test_capacity_models_use_explicit_immutable_huggingface_revisions(
    monkeypatch, tmp_path, capsys
):
    """Both measured model loads carry a lowercase immutable 40-hex revision."""

    spike = importlib.import_module("ops.spike_rss")
    fastembed = importlib.import_module("fastembed")
    cross_encoder = importlib.import_module("fastembed.rerank.cross_encoder")
    huggingface_hub = importlib.import_module("huggingface_hub")

    embed_source_repo = next(
        model["sources"]["hf"]
        for model in fastembed.TextEmbedding.list_supported_models()
        if model["model"] == spike.EMBED_MODEL
    )
    constructor_calls: dict[str, tuple[str, dict[str, object]]] = {}
    registration_calls: list[dict[str, object]] = []
    download_calls: list[tuple[object, object, str]] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            constructor_calls["embedding"] = (model_name, kwargs)

        def embed(self, texts):
            return ([0.0] * spike.EMBED_DIM for _text in texts)

    class FakeTextCrossEncoder:
        @classmethod
        def add_custom_model(cls, **kwargs):
            registration_calls.append(kwargs)

        def __init__(self, model_name, **kwargs):
            constructor_calls["reranker"] = (model_name, kwargs)

        def rerank(self, _query, documents):
            return [1.0 for _document in documents]

    def fake_snapshot_download(*args, **kwargs):
        destination = str(tmp_path / f"snapshot-{len(download_calls)}")
        repo_id = kwargs.get("repo_id", args[0] if args else None)
        download_calls.append((repo_id, kwargs.get("revision"), destination))
        return destination

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    monkeypatch.setattr(cross_encoder, "TextCrossEncoder", FakeTextCrossEncoder)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    # Also cover an implementation that imports snapshot_download at module scope.
    monkeypatch.setattr(spike, "snapshot_download", fake_snapshot_download, raising=False)
    monkeypatch.setattr(spike, "make_synthetic_page_200dpi", lambda: object())
    monkeypatch.setattr(spike, "run_ocr", lambda _image: "SYNTHETIC")
    monkeypatch.setattr(spike, "http_get", lambda _url: 200)
    monkeypatch.setattr(spike, "best_rss_mb", lambda: 32.0)
    monkeypatch.setattr(spike, "peak_rss_mb", lambda: 48.0)
    monkeypatch.setattr(spike.time, "sleep", lambda _seconds: None)
    _fake_cgroup_v2_files(
        monkeypatch,
        spike,
        tmp_path,
        limit_bytes=512 * _MIB,
        current_bytes=40 * _MIB,
        peak_bytes=48 * _MIB,
    )

    spike.main(
        ["--chunks", "1", "--app-url", "http://probe.invalid/health", "--cache-dir", str(tmp_path)]
    )
    capsys.readouterr()

    reranker_registration = registration_calls[-1] if registration_calls else {}
    reranker_sources = reranker_registration.get("sources")
    reranker_source_repo = getattr(reranker_sources, "hf", None)

    def pinned_source_for(
        model_kind: str, constructor_source_repo: object
    ) -> tuple[object, object]:
        _model_name, constructor_kwargs = constructor_calls[model_kind]
        if "revision" in constructor_kwargs:
            return constructor_source_repo, constructor_kwargs["revision"]
        model_path = constructor_kwargs.get("specific_model_path")
        for repo_id, revision, destination in download_calls:
            if model_path == destination:
                return repo_id, revision
        return constructor_source_repo, None

    observed_sources = dict(
        (
            pinned_source_for("embedding", embed_source_repo),
            pinned_source_for("reranker", reranker_source_repo),
        )
    )
    canonical_model_wiring = (
        constructor_calls["embedding"][0] == spike.EMBED_MODEL
        and constructor_calls["reranker"][0] == spike.RERANK_MODEL
        and reranker_registration.get("model") == spike.RERANK_MODEL
        and reranker_source_repo == spike.RERANK_MODEL
    )
    assert (
        canonical_model_wiring
        and observed_sources == _EXPECTED_HF_MODEL_REVISIONS
        and all(
            isinstance(revision, str)
            and _IMMUTABLE_HF_REVISION_RE.fullmatch(revision) is not None
            for revision in observed_sources.values()
        )
    ), (
        "AC-5 must measure the canonical FastEmbed models at the approved "
        "immutable revisions (bogus 40-hex values are not acceptable), and "
        "mxbai must traverse register_reranker(); expected "
        f"{_EXPECTED_HF_MODEL_REVISIONS!r}, got sources={observed_sources!r}, "
        f"registration={reranker_registration!r}"
    )
