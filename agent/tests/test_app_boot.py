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
