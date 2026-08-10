"""Focused tests for Mission terminal feedback v0.1 (Issue #31).

Proves the canonical RunAssessment persistence contract with REAL failing
scenarios (no placeholders): executed-run assessments at
runs/<run-id>/run-assessment.yaml, pre-run rejections at
missions/<mission-id>/run-assessment.yaml, the assessment<->ledger time
semantics across POL-04 reopens, the zero-run-initialization invariant for
pre-run rejections, arbitrary-location rejection, the real Pilot 01
artifact, and legacy grandfathering. Synthetic material lives under
tests/fixtures/ and temporary directories — never in live canonical state.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import validate_artifacts  # noqa: E402

PILOT_RUN = "run-20260810-agent-city-moltbook-outbound-fallback-contract"
PILOT_ASSESSMENT = REPO_ROOT / "runs" / PILOT_RUN / "run-assessment.yaml"
PILOT_MANIFEST = REPO_ROOT / "runs" / PILOT_RUN / "run-manifest.yaml"
LEDGER = REPO_ROOT / "mission" / "ledger.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "mission_terminal_feedback" / "rejected_pre_run"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run_cli() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_artifacts.py")],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def _assessment_errors(assessment_doc: dict) -> list[str]:
    errors: list[str] = []
    schema = json.loads((REPO_ROOT / "contracts" / "mission" / "run-assessment.schema.json")
                        .read_text(encoding="utf-8"))
    validate_artifacts.validate_value(assessment_doc, schema, "assessment", errors)
    return errors


def _ledger_item(signal_id: str, mission_id: str, disposition: str,
                 related: list[str] | None = None) -> dict:
    return {"signal_id": signal_id, "source_kind": "test_node",
            "source_native_ref": f"tests/{signal_id}.py",
            "disposition": disposition, "mission_id": mission_id,
            "related_run_ids": related or [], "updated_at": "2026-08-10T08:00:00Z"}


def _ledger(items: list[dict]) -> dict:
    return {"kind": "federation_hq_mission_ledger", "schema_version": "0.1.0",
            "items": items, "updated_at": "2026-08-10T08:00:00Z"}


def _executed_assessment(mission_id: str, run_id: str) -> dict:
    return {"kind": "federation_hq_run_assessment", "assessment_id": f"a-{run_id}",
            "mission_id": mission_id, "run_id": run_id,
            "target_repository": "kimeisele/agent-city",
            "terminal_outcome": "approved", "repair_class": "test",
            "review_verdict": "approved", "gate_verified": True,
            "target_merged": True, "run_record_merged": True,
            "human_role_handoffs": 0, "ledger_disposition": "completed",
            "created_at": "2026-08-10T08:00:00Z"}


def _rejection_assessment(mission_id: str) -> dict:
    return {"kind": "federation_hq_run_assessment", "assessment_id": f"r-{mission_id}",
            "mission_id": mission_id, "run_id": None,
            "target_repository": "kimeisele/agent-city",
            "terminal_outcome": "mission_rejected",
            "rejection_reason_code": "invalid_framing",
            "rejection_reason": "fixture", "ledger_disposition": "rejected",
            "created_at": "2026-08-10T08:00:00Z"}


def _write_mission(tmp_repo: Path, mission_id: str, signal_id: str) -> None:
    mission_dir = tmp_repo / "missions" / mission_id
    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "mission-candidate.yaml").write_text(yaml.safe_dump({
        "kind": "federation_hq_mission_candidate", "candidate_id": f"cand-{mission_id}",
        "signal_refs": [{"signal_id": signal_id, "source_kind": "test_node",
                         "source_native_ref": f"tests/{signal_id}.py"}],
        "target_repository": "kimeisele/agent-city", "problem_statement": "fixture",
        "disposition": "selected", "mission_id": mission_id,
        "created_at": "2026-08-10T08:00:00Z"}, sort_keys=False))
    (mission_dir / "mission-contract.yaml").write_text(yaml.safe_dump({
        "kind": "federation_hq_mission_contract", "mission_version": "0.1.0",
        "mission_id": mission_id, "source_candidate_id": f"cand-{mission_id}",
        "signal_refs": [{"signal_id": signal_id, "source_kind": "test_node",
                         "source_native_ref": f"tests/{signal_id}.py"}],
        "target_repository": "kimeisele/agent-city", "objective": "fixture objective",
        "decision_question": "fixture question", "bounded_scope": "fixture scope",
        "scope_enforcement": "declared", "prescribes_repair": False,
        "hard_constraints": [], "stop_conditions": [],
        "expected_allowed_outcomes": ["approved", "blocked"],
        "policy_reference": "docs/HQ_MISSION_POLICY.md", "policy_version": "0.1.0",
        "policy_sha256": "7d3c4eb4b6528e4aef515b429aafcdb6d9dc228d6feea098582b10a2a4f2241d",
        "status": "proposed", "creation_provenance": {"author_role": "other",
        "source_reference": "fixture"}, "created_at": "2026-08-10T08:00:00Z"}, sort_keys=False))


def _write_executed(tmp_repo: Path, run_id: str, mission_id: str) -> None:
    run_dir = tmp_repo / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-assessment.yaml").write_text(yaml.safe_dump(
        _executed_assessment(mission_id, run_id), sort_keys=False))


def _write_rejection(tmp_repo: Path, mission_id: str) -> None:
    mission_dir = tmp_repo / "missions" / mission_id
    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "run-assessment.yaml").write_text(yaml.safe_dump(
        _rejection_assessment(mission_id), sort_keys=False))


def _write_run_manifest(tmp_repo: Path, run_id: str, mission_id: str) -> None:
    run_dir = tmp_repo / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-manifest.yaml").write_text(yaml.safe_dump({
        "kind": "federation_hq_run_manifest", "run_id": run_id,
        "target_repository": "kimeisele/agent-city", "baseline_sha": "f" * 40,
        "coordination": {"protocol_version": "0.1.0", "issue_number": 1,
                         "issue_url": "https://github.com/kimeisele/federation-hq/issues/1"},
        "pipeline_state": "requested", "prompt_pins": {},
        "mission_input": {"mission_id": mission_id, "candidate": {}, "contract": {},
                          "admission_ledger": {}},
        "created_at": "2026-08-10T08:00:00Z"}, sort_keys=False))


def _feedback_errors(tmp_repo: Path, ledger: dict) -> list[str]:
    errors: list[str] = []
    validate_artifacts.check_terminal_feedback(
        tmp_repo / "runs", tmp_repo / "missions", ledger,
        REPO_ROOT / "contracts", tmp_repo, errors)
    return errors


# ── Real Pilot 01 (accepted fixture, unchanged) ───────────────────────────


def test_pilot01_canonical_executed_assessment_path():
    assert PILOT_ASSESSMENT.exists()
    doc = _load(PILOT_ASSESSMENT)
    assert doc["kind"] == "federation_hq_run_assessment"
    assert not _assessment_errors(doc)


def test_pilot01_assessment_matches_real_terminal_facts():
    doc = _load(PILOT_ASSESSMENT)
    assert doc["mission_id"] == "mission-20260810-agent-city-moltbook-outbound-fallback-contract"
    assert doc["run_id"] == PILOT_RUN
    assert doc["target_repository"] == "kimeisele/agent-city"
    assert doc["baseline_sha"] == "e01d011bd563e964afb55901a4ed9474191aec59"
    assert doc["terminal_outcome"] == "approved"
    assert doc["repair_class"] == "test"
    assert doc["review_verdict"] == "approved"
    assert doc["gate_verified"] is True
    assert doc["target_merge_sha"] == "a975b7da810b22587efd826411d08fb7d4b7d36b"
    assert doc["human_role_handoffs"] == 0
    assert doc["ledger_disposition"] == "completed"


def test_pilot01_identity_chain_and_ledger_agreement():
    assessment = _load(PILOT_ASSESSMENT)
    manifest = _load(PILOT_MANIFEST)
    assert assessment["run_id"] == PILOT_RUN == manifest["run_id"]
    assert assessment["target_repository"] == manifest["target_repository"]
    assert assessment["baseline_sha"] == manifest["baseline_sha"]
    assert assessment["mission_id"] == manifest["mission_input"]["mission_id"]
    ledger = _load(LEDGER)
    item = next(i for i in ledger["items"]
                if i["signal_id"] == "sig-20260810-agent-city-moltbook-outbound-fallback-test")
    assert item["disposition"] == "completed" == assessment["ledger_disposition"]
    assert item["mission_id"] == assessment["mission_id"]
    assert PILOT_RUN in item["related_run_ids"]
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr


# ── Arbitrary assessment location (real failing test) ─────────────────────


def test_arbitrary_assessment_location_fails():
    errors: list[str] = []
    validate_artifacts.check_assessment_locations(
        ["foo/run-assessment.yaml", "docs/run-assessment.yaml"], errors)
    assert any("outside the canonical locations" in e for e in errors)
    assert len(errors) == 2


def test_canonical_and_fixture_locations_allowed():
    errors: list[str] = []
    validate_artifacts.check_assessment_locations(
        ["runs/run-a/run-assessment.yaml", "missions/mission-a/run-assessment.yaml",
         "examples/mission/retrospective/run-assessment.retro-17.yaml",
         "tests/fixtures/mission_terminal_feedback/rejected_pre_run/run-assessment.yaml"],
        errors)
    assert errors == []


# ── Duplicate terminal assessment (real failing test) ─────────────────────


def test_duplicate_terminal_assessment_fails(tmp_path):
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_executed(tmp_repo, "run-A", "mission-A")
    _write_rejection(tmp_repo, "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "completed", ["run-A"])])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("duplicate canonical terminal assessment" in e for e in errors), errors


def test_two_executed_assessments_for_same_mission_fail(tmp_path):
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_executed(tmp_repo, "run-A", "mission-A")
    _write_executed(tmp_repo, "run-B", "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "completed", ["run-A", "run-B"])])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("duplicate canonical terminal assessment" in e for e in errors), errors


# ── Historical assessment across POL-04 reopen (time semantics) ───────────


def test_executed_assessment_valid_after_reopen(tmp_path):
    """T0: mission A completed; T1: same signal reopened into mission B —
    the historical assessment A remains valid (run linkage preserved)."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_executed(tmp_repo, "run-A", "mission-A")
    t0 = _ledger([_ledger_item("sig-X", "mission-A", "completed", ["run-A"])])
    assert _feedback_errors(tmp_repo, t0) == []
    t1 = _ledger([_ledger_item("sig-X", "mission-B", "active", ["run-A"])])
    assert _feedback_errors(tmp_repo, t1) == []


def test_executed_assessment_invalid_when_run_linkage_lost(tmp_path):
    """T2: removing run-A from related_run_ids loses the historical
    execution linkage and must fail."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_executed(tmp_repo, "run-A", "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-B", "active", [])])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("related_run_ids does not contain the executed run id" in e for e in errors)


def test_rejection_valid_after_reopen(tmp_path):
    """R0: mission A rejected, ledger rejected; R1: same signal reopened
    into mission B — the historical rejection stays valid."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    r0 = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    assert _feedback_errors(tmp_repo, r0) == []
    r1 = _ledger([_ledger_item("sig-X", "mission-B", "active", ["run-B"])])
    assert _feedback_errors(tmp_repo, r1) == []


def test_rejection_still_requires_rejected_when_current(tmp_path):
    """While the Ledger still points at the rejected mission, its
    disposition must be rejected."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "active")])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("disposition must be rejected" in e for e in errors), errors


# ── Pre-run rejection: no run initialized ─────────────────────────────────


def test_rejection_invalid_when_run_manifest_exists(tmp_path):
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    _write_run_manifest(tmp_repo, "run-X", "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("rejected BEFORE run initialization" in e for e in errors), errors


def test_rejection_valid_without_run_manifest(tmp_path):
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    assert _feedback_errors(tmp_repo, ledger) == []


# ── Pre-run assessment schema validation (final review fix) ──────────────


def test_pre_run_assessment_schema_violation_invalid(tmp_path):
    """A canonical-shaped pre-run rejection that violates the RunAssessment
    schema (unexpected field under additionalProperties: false) is INVALID —
    the test fails if schema validation of pre-run assessments is removed."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    assessment = _rejection_assessment("mission-A")
    assessment["invented_confidence"] = 0.99
    (tmp_repo / "missions" / "mission-A" / "run-assessment.yaml").write_text(
        yaml.safe_dump(assessment, sort_keys=False))
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("invented_confidence" in e for e in errors), errors


def test_pre_run_assessment_missing_reason_code_invalid(tmp_path):
    """Schema + mission semantic checks require rejection_reason_code on a
    mission_rejected assessment."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    assessment = _rejection_assessment("mission-A")
    del assessment["rejection_reason_code"]
    (tmp_repo / "missions" / "mission-A" / "run-assessment.yaml").write_text(
        yaml.safe_dump(assessment, sort_keys=False))
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    errors = _feedback_errors(tmp_repo, ledger)
    assert any("rejection_reason_code" in e for e in errors), errors


def test_valid_rejection_fixture_still_valid(tmp_path):
    """The valid rejection path (schema-valid, no run initialized, Ledger
    rejected) remains VALID."""
    tmp_repo = tmp_path / "repo"
    _write_mission(tmp_repo, "mission-A", "sig-X")
    _write_rejection(tmp_repo, "mission-A")
    ledger = _ledger([_ledger_item("sig-X", "mission-A", "rejected")])
    assert _feedback_errors(tmp_repo, ledger) == []


# ── Legacy grandfathering ─────────────────────────────────────────────────


def test_legacy_runs_without_assessment_remain_valid():
    for run_id in ("run-20260809-agent-city-prompt-registry-contract-drift",
                   "run-20260809-agent-city-issue-open-helper-isolation",
                   "run-20260809-agent-city-contract-proposal-service-sync"):
        assert not (REPO_ROOT / "runs" / run_id / "run-assessment.yaml").exists()
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr
