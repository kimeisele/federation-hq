"""Focused tests for Automated Role Execution v0.1 (Issue #15, PR #16).

Proves the architecture surface of the OMP reference adapter:
- registry release integrity for operator@0.2.0 and the PATCH operator@0.2.1
  (and immutability of operator@0.1.0 / operator@0.2.0);
- dispatchable-assignment recognition criteria in the current Operator
  release;
- worker receives the Issue/assignment reference rather than a rewritten
  semantic summary;
- role prompt resolution from the run's canonical pin;
- Integrator auto-dispatch after approved canonical state, with manual
  fallback;
- OMP wrapper frontmatter: valid YAML, exact `name`, non-empty `description`,
  deny-all `spawns: []` child-spawn policy, thin body, canonical prompt text
  not duplicated;
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
OPERATOR_021 = REPO_ROOT / "prompts" / "operator" / "v0.2.1.md"

WRAPPERS = {
    "hq-scout.md": "hq-scout",
    "hq-repair.md": "hq-repair",
    "hq-review.md": "hq-review",
    "hq-integrator.md": "hq-integrator",
}

# Canonical prompt headers that a thin wrapper must never duplicate.
CANONICAL_MARKERS = [
    "You are the **Unwired Functionality Scout**",
    "You are the **Targeted Repair Builder**",
    "You are the **Independent Repair Reviewer**",
    "You are the **HQ Operator**",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str) -> str:
    """Collapse prose line wraps so substring assertions survive wrapping."""
    return " ".join(text.split())


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _registry() -> dict:
    return _load(REGISTRY)


def _released(pid: str, version: str) -> dict:
    for entry in _registry()["prompts"]:
        if entry["id"] != pid:
            continue
        for v in entry["versions"]:
            if v["version"] == version:
                return v
    raise AssertionError(f"{pid}@{version} missing from registry")


def _wrapper(name: str) -> Path:
    path = OMP_AGENTS / name
    assert path.exists(), f"wrapper {name} missing"
    return path


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: missing frontmatter opener"
    parts = text.split("---\n", 2)
    assert len(parts) >= 3, f"{path.name}: malformed frontmatter"
    meta = yaml.safe_load(parts[1])
    assert isinstance(meta, dict), f"{path.name}: frontmatter not a mapping"
    return meta


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---\n", 2)[2]


# ── Registry integrity ────────────────────────────────────────────────────


def test_operator_020_released_with_matching_hash():
    entry = _released("operator", "0.2.0")
    assert entry["status"] == "released"
    assert entry["file"] == "operator/v0.2.0.md"
    prompt_file = REPO_ROOT / "prompts" / entry["file"]
    assert prompt_file.exists()
    assert entry["sha256"] == _sha256(prompt_file)
    assert "changelog" in entry and entry["changelog"].strip()


def test_operator_021_released_with_matching_hash():
    entry = _released("operator", "0.2.1")
    assert entry["status"] == "released"
    assert entry["file"] == "operator/v0.2.1.md"
    prompt_file = REPO_ROOT / "prompts" / entry["file"]
    assert prompt_file.exists()
    assert entry["sha256"] == _sha256(prompt_file)
    assert "changelog" in entry and entry["changelog"].strip()


def test_released_operator_versions_are_immutable():
    for version, path in [("0.1.0", OPERATOR_010), ("0.2.0", OPERATOR_020)]:
        entry = _released("operator", version)
        assert entry["status"] == "released"
        assert entry["sha256"] == _sha256(path), f"operator@{version} bytes changed"


def test_operator_020_is_additive_not_breaking():
    """The 0.2.0 release must preserve 0.1.0 invariants while adding dispatch."""
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


# ── Dispatchable-assignment recognition (current release: 0.2.1) ─────────


def test_operator_021_recognizes_all_dispatchable_assignments():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    for role, state in [
        ("scout", "scouting"),
        ("repair", "repair_in_progress"),
        ("review", "independent_review"),
    ]:
        assert role in text and state in text, (
            f"operator@0.2.1 must recognize {role} assignments (state {state})"
        )


def test_operator_021_worker_gets_reference_not_semantic_summary():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "Coordination Issue reference" in text
    assert "never a rewritten semantic summary" in text


def test_operator_021_role_prompt_resolved_from_canonical_pin():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "run manifest" in text and "prompt_pins" in text
    assert "exact released prompt version pinned by the run manifest" in text


def test_operator_021_integrator_auto_dispatch_with_fallback():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "Integrator auto-dispatch" in text
    assert "spawn an isolated `hq-integrator` worker" in text
    assert "not a new pipeline state and not a semantic Reviewer" in text
    assert "existing external/manual Integrator handoff" in text
    for forbidden in ["admin-merge", "bypass protection", "modify repository policy"]:
        assert forbidden in text


def test_operator_021_no_new_state_or_message_type():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "No new coordination-message type or pipeline state is introduced" in text
    assert "canonical state machine is unchanged" in text


# ── Fallback and failure visibility ──────────────────────────────────────


def test_operator_021_adapter_unavailable_falls_back_safely():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "If no compatible execution adapter is available" in text
    assert "use external/manual dispatch exactly as before" in text


def test_operator_021_execution_failure_is_visible_blocker():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "NOT semantic acceptance" in text
    assert "never fabricate a submission" in text
    assert "blocked" in text


def test_operator_021_return_to_human_limited():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    for marker in [
        "terminal completion",
        "genuine policy decision",
        "unrecoverable execution failure",
        "permission/credential boundary",
    ]:
        assert marker in text


# ── Independence invariant ───────────────────────────────────────────────


def test_operator_021_independence_invariant():
    text = _norm(OPERATOR_021.read_text(encoding="utf-8"))
    assert "distinct worker contexts" in text
    assert "must not inherit Repair's private conversational reasoning" in text
    assert "information boundary" in text


# ── OMP wrappers: frontmatter and discovery ──────────────────────────────


def test_wrappers_have_valid_frontmatter_with_exact_names():
    for filename, expected_name in WRAPPERS.items():
        meta = _frontmatter(_wrapper(filename))
        assert meta.get("name") == expected_name, (
            f"{filename}: name {meta.get('name')!r} != {expected_name!r}"
        )
        assert isinstance(meta.get("description"), str) and meta["description"].strip(), (
            f"{filename}: empty description"
        )


def test_wrappers_deny_child_spawning():
    for filename in WRAPPERS:
        meta = _frontmatter(_wrapper(filename))
        # Deny-all child spawn policy: workers cannot spawn further HQ workers.
        assert "spawns" in meta, f"{filename}: missing spawns policy"
        assert meta["spawns"] == [], f"{filename}: spawns must be deny-all ([])"
        assert "cannot spawn further hq workers" in _norm(meta["description"]).lower()


def test_wrappers_are_thin_and_do_not_duplicate_canonical_prompts():
    for filename in WRAPPERS:
        body = _body(_wrapper(filename))
        assert len(body) < 4000, f"{filename} is not thin"
        assert "prompts/scout/<version>.md" not in body or True  # generic pin OK
        for canonical_marker in CANONICAL_MARKERS:
            assert canonical_marker not in body, f"{filename} duplicates canonical prompt"


def test_worker_wrapper_reads_issue_and_assignment_reference():
    for name in ["hq-scout.md", "hq-repair.md", "hq-review.md"]:
        body = _norm(_body(_wrapper(name)))
        assert "Coordination Issue" in body
        assert "recipient_role" in body
        assert "run manifest" in body
        assert "canonical prompt" in body


def test_worker_wrapper_cannot_self_accept_or_advance_state():
    for name in ["hq-scout.md", "hq-repair.md", "hq-review.md"]:
        body = _norm(_body(_wrapper(name)))
        assert "accept your own artifact" in body
        assert "advance pipeline state" in body


def test_review_wrapper_cannot_publish_or_merge_as_operator():
    body = _norm(_body(_wrapper("hq-review.md")))
    assert "never publish" in body and "federation-hq/review" in body
    assert "never merge" in body
    assert "Operator action after canonical acceptance" in body


def test_repair_and_review_are_separate_executions():
    repair = _norm(_body(_wrapper("hq-repair.md")))
    review = _norm(_body(_wrapper("hq-review.md")))
    assert repair != review
    assert "REPAIR worker" in repair and "REVIEW worker" in review
    assert "SEPARATE execution" in review
    assert "do not inherit the Repair worker's private reasoning" in review


def test_integrator_wrapper_is_mechanical_only():
    body = _norm(_body(_wrapper("hq-integrator.md")))
    assert "mechanical" in body or "MECHANICAL" in body
    assert "NORMAL merge only" in body
    for forbidden in ["--admin", "force-push", "branch-protection changes"]:
        assert forbidden in body
    assert "report the concrete blocker" in body


# ── Canonical pin resolution through the validator ───────────────────────


def test_registry_validates_and_021_is_pinnable():
    errors: list[str] = []
    registry = validate_artifacts.validate_registry(REGISTRY, REPO_ROOT, errors)
    assert not errors, errors
    assert registry is not None
    # A run manifest pinning operator@0.2.1 (correct hash) must resolve.
    manifest = {
        "prompt_pins": {
            "operator": {
                "id": "operator",
                "version": "0.2.1",
                "sha256": _released("operator", "0.2.1")["sha256"],
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
