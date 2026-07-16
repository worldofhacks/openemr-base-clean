from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pre_push_hook_runs_the_full_recorded_gate() -> None:
    hook = (ROOT / "githooks" / "pre-push").read_text(encoding="utf-8")
    makefile = (ROOT / "agent" / "Makefile").read_text(encoding="utf-8")

    assert "make PYTHON=\"$python_bin\" eval-tier1" in hook
    assert "python -m evals.w2_runner run --tier recorded" not in hook
    assert "eval-tier1:" in makefile
    assert "-m evals.w2_runner run --tier recorded" in makefile
    assert "-m evals.artifact_scan evals/results-tier1.json evals/recordings" in makefile
    assert "W2_EVAL_NETWORK=disabled" in makefile
    assert "COHERE_API_KEY=" in makefile


def test_hook_install_uses_repository_local_hooks_path() -> None:
    makefile = (ROOT / "agent" / "Makefile").read_text(encoding="utf-8")

    assert 'config core.hooksPath githooks' in makefile
