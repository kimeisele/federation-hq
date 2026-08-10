"""Focused tests for the Mission Foundation v0.1 slice (Issue #23).

Proves the mission-layer contracts and their retroactive/negative fixtures:
- MissionCandidate / MissionContract / RunAssessment accept the three
  retrospective projections (#17 Pilot 03, #19 Stabilization 01, #21
  Stabilization 02);
- signal IDs are immutable internal identities; source references can have
  evidence-backed aliases;
- duplicate links to an existing ledger item; no_mission_warranted requires
  no MissionContract; wont_fix does not silently reopen;
  mission_rejected is representable;
- RunAssessment forbids free-form confidence/self-scoring fields;
- canonical historical run artifacts and the existing execution contracts
  remain unchanged; the full artifact validator stays green.
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

SCHEMAS = REPO_ROOT / "contracts" / "mission"
RETRO = REPO_ROOT / "examples" / "mission" / "retrospective"
NEG = REPO_ROOT / "examples" / "mission" / "negative"
LEDGER = REPO_ROOT / "mission" / "ledger.yaml"

RUNS = {
    "17": "run-20260809-agent-city-prompt-registry-contract-drift",
    "19": "run-20260809-agent-city-issue-open-helper-isolation",
    "21": "run-20260809-agent-city-contract-proposal-service-sync",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _errors_for(kind: str, doc: dict) -> list[str]:
    errors: list[str] = []
    validate_artifacts.validate_value(doc, _schema(kind), kind, errors)
    return errors


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_artifacts.py"), *args],
        capture_output=True,
        text=True,
    )


def _retro_files(prefix: str, issue: str) -> list[Path]:
    return sorted(RETRO.glob(f"{prefix}.retro-{issue}-*.yaml"))


# ── Rules 1-3: the three retrospectives validate ──────────────────────────


def test_mission_candidate_accepts_retrospectives():
    for issue, run_id in RUNS.items():
        files = _retro_files("mission-candidate", issue)
        assert files, f"missing retro candidate for #{issue}"
        for f in files:
            doc = _load(f)
            errors = _errors_for("mission-candidate.schema.json", doc)
            assert not errors, (f, errors)
            assert doc["mission_id"] == run_id
            assert doc["disposition"] == "selected"


def test_mission_contract_accepts_retrospectives():
    for issue, run_id in RUNS.items():
        files = _retro_files("mission-contract", issue)
        assert files, f"missing retro contract for #{issue}"
        for f in files:
            doc = _load(f)
            errors = _errors_for("mission-contract.schema.json", doc)
            assert not errors, (f, errors)
            assert doc["mission_id"] == run_id
            assert doc["status"] == "completed"
            assert doc["prescribes_repair"] is False


def test_run_assessment_accepts_real_terminal_facts():
    expected = {
        "17": ("89bf10eea690617f38627e63554a89457a90ed71", "93266453275"),
        "19": ("1cc173eef57e408a17494b3d13d696dbf4e54edb", "93273255627"),
        "21": ("e01d011bd563e964afb55901a4ed9474191aec59", "93285178946"),
    }
    for issue, run_id in RUNS.items():
        files = _retro_files("run-assessment", issue)
        assert files, f"missing retro assessment for #{issue}"
        for f in files:
            doc = _load(f)
            errors = _errors_for("run-assessment.schema.json", doc)
            assert not errors, (f, errors)
            assert doc["run_id"] == run_id
            assert doc["terminal_outcome"] == "approved"
            assert doc["review_verdict"] == "approved"
            assert doc["gate_verified"] is True
            assert doc["target_merged"] is True
            assert doc["run_record_merged"] is True
            assert doc["human_role_handoffs"] == 0
            target_merge, gate_run = expected[issue]
            assert doc["target_merge_sha"] == target_merge
            assert doc["gate_check_run_id"] == gate_run


# ── Rule 4: signal IDs are immutable internal identities ──────────────────


def test_signal_ids_are_internal_immutable_identities():
    """signal_id is HQ-assigned and distinct from the source-native ref."""
    ledger = _load(LEDGER)
    seen: set[str] = set()
    for item in ledger["items"]:
        assert item["signal_id"] not in seen, "signal_id must be unique"
        seen.add(item["signal_id"])
        assert item["signal_id"] != item["source_native_ref"]
        # Identity is stable even though the source ref may change.
        assert item["disposition"] in {
            "completed", "wont_fix", "no_mission_warranted",
            "duplicate", "rejected", "superseded", "active",
        }


# ── Rule 5: source references can have aliases ────────────────────────────


def test_source_references_can_have_aliases():
    # Retro-17: the test was renamed during the run; the renamed node is an
    # evidence-backed alias of the same immutable signal_id.
    ledger = _load(LEDGER)
    item = next(i for i in ledger["items"]
                if i["signal_id"] == "sig-20260809-prompt-registry-count")
    assert "tests/test_prompt_registry.py::TestGetPromptRegistry::test_singleton_has_all_7_builders" in item["aliases"]
    # Candidate + contract fixtures carry the same alias.
    cand = _load(_retro_files("mission-candidate", "17")[0])
    assert cand["signal_refs"][0]["aliases"]


# ── Rule 6: duplicate links to an existing ledger item ────────────────────


def test_duplicate_links_to_existing_ledger_item():
    doc = _load(NEG / "mission-candidate.neg-d-duplicate.yaml")
    errors = _errors_for("mission-candidate.schema.json", doc)
    assert not errors
    assert doc["disposition"] == "duplicate"
    ledger_ids = {i["signal_id"] for i in _load(LEDGER)["items"]}
    assert doc["duplicate_of"] in ledger_ids


# ── Rule 7: no_mission_warranted requires no MissionContract ──────────────


def test_no_mission_warranted_requires_no_mission_contract():
    doc = _load(NEG / "mission-candidate.neg-a-no-mission-warranted.yaml")
    errors = _errors_for("mission-candidate.schema.json", doc)
    assert not errors
    assert doc["disposition"] == "no_mission_warranted"
    assert "mission_id" not in doc
    # Validator semantic check rejects a mission_id on no_mission_warranted.
    bad = dict(doc)
    bad["mission_id"] = "mission-should-not-exist"
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("no_mission_warranted must not carry a mission_id" in e for e in semantic)


# ── Rule 8: wont_fix does not silently reopen ─────────────────────────────


def test_wont_fix_does_not_silently_reopen():
    doc = _load(NEG / "mission-candidate.neg-b-wont-fix-reopen.yaml")
    errors = _errors_for("mission-candidate.schema.json", doc)
    assert not errors
    assert doc["disposition"] == "wont_fix"
    assert "mission_id" not in doc


# ── Rule 9: mission_rejected is representable ─────────────────────────────


def test_mission_rejected_is_representable():
    doc = _load(NEG / "mission-contract.neg-c-wrong-framing.yaml")
    errors = _errors_for("mission-contract.schema.json", doc)
    assert not errors
    assert doc["status"] == "mission_rejected"
    assert doc["rejection_reason"]
    # Validator requires a rejection_reason on mission_rejected.
    bad = dict(doc)
    bad.pop("rejection_reason", None)
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("mission_rejected requires rejection_reason" in e for e in semantic)


# ── Rule 10: RunAssessment forbids fake free-form confidence scoring ──────


def test_run_assessment_forbids_free_form_confidence():
    base = _load(_retro_files("run-assessment", "17")[0])
    for field in ("system_confidence", "confidence", "importance", "risk_score"):
        bad = dict(base)
        bad[field] = 0.95
        errors = _errors_for("run-assessment.schema.json", bad)
        assert any(field in e for e in errors), f"{field} must be rejected"


# ── Rule 11: canonical historical run artifacts remain unchanged ──────────


def test_canonical_run_artifacts_unchanged():
    """The retro projections live in examples/, never in runs/; the actual
    run records must still validate as-is (full validator covers this)."""
    assert not list((REPO_ROOT / "runs").glob("*/mission-*.yaml"))
    assert not list((REPO_ROOT / "runs").glob("*/run-assessment*.yaml"))
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr


# ── Rule 12: existing artifact validation remains green ───────────────────


def test_existing_artifact_validation_green():
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Federation HQ artifact validation OK" in result.stdout


# ── Rule 13: existing execution contracts/behavior unchanged ──────────────


def test_existing_execution_contracts_unchanged():
    """The five founding execution schemas are byte-identical to main; no
    gate/operator/prompt files changed in this slice."""
    import subprocess as sp

    expected = {
        "contracts/run-manifest.schema.json": None,
        "contracts/repair-candidate.schema.json": None,
        "contracts/repair-result.schema.json": None,
        "contracts/review-result.schema.json": None,
        "contracts/coordination-message.schema.json": None,
    }
    # Files changed in this slice, compared against main@HEAD.
    changed = sp.run(
        ["git", "diff", "--name-only", "main", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.splitlines()
    for rel in expected:
        assert rel not in changed, f"{rel} must not change in this slice"
    for rel in (
        "federation_hq_gate/policy.py",
        "prompts/registry.yaml",
        "prompts/operator/v0.2.1.md",
    ):
        assert rel not in changed, f"{rel} must not change in this slice"
