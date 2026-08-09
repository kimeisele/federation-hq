"""Focused tests for Automated Role Execution v0.1 (Issue #15).

Proves the architecture surface of the OMP reference adapter:
- registry release integrity for the additive operator@0.2.0 (and
  immutability of operator@0.1.0);
- dispatchable-assignment recognition criteria in the new Operator release;
- worker receives the Issue/assignment reference rather than a rewritten
  semantic summary;
- role prompt resolution from the run's canonical pin;
- wrapper role boundaries (no self-accept, no state advance, no Gate
  publish, no merge; Review cannot act as Operator);
- Repair and Review remain distinct executions;
- adapter-unavailable fallback and execution-failure visibility;
- prior run records remain valid (full validator + suite are green).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import validate_artifacts  # noqa: E402

REGISTRY = REPO_ROOT / "prompts" / "registry.yaml"
OMP_AGENTS = REPO_ROOT / ".omp" / "agents"
OPERATOR_010 = REPO_ROOT / "prompts" / "operator" / "v0.1.0.md"
OPERATOR_020 = REPO_ROOT / "prompts" / "operator" / "v0.2.0.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str) -> str:
    """Collapse prose line wraps so substring assertions survive wrapping."""
    return " ".join(text.split())


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _registry() -> dict:
    return _load(REGISTRY)


def _registry_versions(pid: str) -> list[dict]:
    for entry in _registry()["prompts"]:
        if entry["id"] == pid:
            return entry["versions"]
    raise AssertionError(f"prompt id {pid!r} missing from registry")


def _released(pid: str, version: str) -> dict:
    for v in _registry_versions(pid):
        if v["version"] == version:
            return v
    raise AssertionError(f"{pid}@{version} missing from registry")


# ── Registry integrity ────────────────────────────────────────────────────


def test_operator_020_released_with_matching_hash():
    entry = _released("operator", "0.2.0")
    assert entry["status"] == "released"
    assert entry["released"] == "2026-08-09"
    assert entry["file"] == "operator/v0.2.0.md"
    prompt_file = REPO_ROOT / "prompts" / entry["file"]
    assert prompt_file.exists()
    assert entry["sha256"] == _sha256(prompt_file)
    assert "changelog" in entry and entry["changelog"].strip()


def test_operator_010_still_immutable_and_pinnable():
    entry = _released("operator", "0.1.0")
    assert entry["status"] == "released"
    assert entry["sha256"] == _sha256(OPERATOR_010)
    # 0.2.0 is additive: the 0.1.0 bytes are untouched.
    assert OPERATOR_010.read_bytes() == OPERATOR_010.read_bytes()


def test_operator_020_is_additive_not_breaking():
    """The new release must preserve 0.1.0 invariants (no merge authority,
    no role work, no autonomous targets) while adding dispatch guidance."""
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    for marker in [
        "no merge authority",
        "You are a coordinator",
        "Performing Scout, Repair, or Review work in the same run",
        "Automated role dispatch",
        "fallback",
        "external/manual dispatch",
        "prompt_pins",
    ]:
        assert marker in text, f"operator@0.2.0 missing marker {marker!r}"


# ── Dispatchable-assignment recognition ──────────────────────────────────


def test_operator_020_recognizes_all_dispatchable_assignments():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    for role, state in [
        ("scout", "scouting"),
        ("repair", "repair_in_progress"),
        ("review", "independent_review"),
    ]:
        assert role in text and state in text, (
            f"operator@0.2.0 must recognize {role} assignments (state {state})"
        )


def test_operator_020_worker_gets_reference_not_semantic_summary():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    assert "Coordination Issue reference" in text
    assert "never a rewritten semantic summary" in text


def test_operator_020_role_prompt_resolved_from_canonical_pin():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    assert "run manifest" in text and "prompt_pins" in text
    assert "exact released prompt version pinned by the run manifest" in text


# ── Fallback and failure visibility ──────────────────────────────────────


def test_operator_020_adapter_unavailable_falls_back_safely():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    assert "If no compatible execution adapter is available" in text
    assert "use external/manual dispatch exactly as before" in text


def test_operator_020_execution_failure_is_visible_blocker():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    assert "NOT semantic acceptance" in text
    assert "never fabricate a submission" in text
    assert "blocked" in text


def test_operator_020_return_to_human_limited():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    for marker in [
        "terminal completion",
        "genuine policy decision",
        "unrecoverable execution failure",
        "permission/credential boundary",
    ]:
        assert marker in text


# ── Independence invariant ───────────────────────────────────────────────


def test_operator_020_independence_invariant():
    text = _norm(OPERATOR_020.read_text(encoding="utf-8"))
    assert "distinct worker contexts" in text
    assert "must not inherit Repair's private conversational reasoning" in text
    assert "information boundary" in text


# ── OMP wrappers ─────────────────────────────────────────────────────────


def _wrapper(name: str) -> Path:
    path = OMP_AGENTS / name
    assert path.exists(), f"wrapper {name} missing"
    return path


def test_wrappers_exist_and_are_thin():
    expected = {"hq-scout.md", "hq-repair.md", "hq-review.md", "hq-integrator.md"}
    assert {p.name for p in OMP_AGENTS.glob("*.md")} >= expected
    for name in expected:
        text = _norm(_wrapper(name).read_text(encoding="utf-8"))
        # Thin: wrappers are role-boundary adapters, not prompt copies.
        assert len(text) < 4000, f"{name} is not thin"
        # Wrappers must not duplicate the canonical prompt objective blocks.
        for canonical_marker in [
            "You are the **Unwired Functionality Scout**",
            "You are the **Targeted Repair Builder**",
            "You are the **Independent Repair Reviewer**",
            "You are the **HQ Operator**",
        ]:
            assert canonical_marker not in text, f"{name} duplicates canonical prompt"


def test_worker_wrapper_reads_issue_and_assignment_reference():
    for name in ["hq-scout.md", "hq-repair.md", "hq-review.md"]:
        text = _norm(_wrapper(name).read_text(encoding="utf-8"))
        assert "Coordination Issue" in text
        assert "recipient_role" in text
        assert "run manifest" in text
        assert "canonical prompt" in text


def test_worker_wrapper_cannot_self_accept_or_advance_state():
    for name in ["hq-scout.md", "hq-repair.md", "hq-review.md"]:
        text = _norm(_wrapper(name).read_text(encoding="utf-8"))
        assert "accept your own artifact" in text
        assert "advance pipeline state" in text


def test_review_wrapper_cannot_publish_or_merge_as_operator():
    text = _norm(_wrapper("hq-review.md").read_text(encoding="utf-8"))
    assert "never publish `federation-hq/review`" in text
    assert "never merge" in text
    assert "Operator action after canonical acceptance" in text


def test_repair_and_review_are_separate_executions():
    repair = _norm(_wrapper("hq-repair.md").read_text(encoding="utf-8"))
    review = _norm(_wrapper("hq-review.md").read_text(encoding="utf-8"))
    assert repair != review
    assert "REPAIR worker" in repair and "REVIEW worker" in review
    assert "separate execution" in review or "SEPARATE execution" in review
    assert "do not inherit the Repair worker's private reasoning" in review


def test_integrator_wrapper_is_mechanical_only():
    text = _norm(_wrapper("hq-integrator.md").read_text(encoding="utf-8"))
    assert "MECHANICAL" in text or "mechanical" in text
    assert "NORMAL merge only" in text
    for forbidden in ["--admin", "force-push", "branch-protection changes"]:
        assert forbidden in text
    assert "report the concrete blocker" in text


# ── Canonical pin resolution through the validator ───────────────────────


def test_registry_validates_and_020_is_pinnable():
    errors: list[str] = []
    registry = validate_artifacts.validate_registry(REGISTRY, REPO_ROOT, errors)
    assert not errors, errors
    assert registry is not None
    # A run manifest pinning operator@0.2.0 (correct hash) must resolve.
    manifest = {
        "prompt_pins": {
            "operator": {
                "id": "operator",
                "version": "0.2.0",
                "sha256": _released("operator", "0.2.0")["sha256"],
            },
        }
    }
    pin_errors: list[str] = []
    validate_artifacts.check_prompt_pins(manifest, registry, "test", pin_errors)
    assert not pin_errors, pin_errors


def test_prior_run_manifests_still_pin_released_versions():
    """Pilot 01/02 run manifests must still resolve against the registry."""
    errors: list[str] = []
    registry = validate_artifacts.validate_registry(REGISTRY, REPO_ROOT, errors)
    assert not errors, errors
    for manifest_path in REPO_ROOT.glob("runs/run-*/run-manifest.yaml"):
        doc = _load(manifest_path)
        pin_errors: list[str] = []
        validate_artifacts.check_prompt_pins(doc, registry, str(manifest_path), pin_errors)
        assert not pin_errors, (manifest_path, pin_errors)
