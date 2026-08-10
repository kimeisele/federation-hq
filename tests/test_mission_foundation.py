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
            assert doc["source_candidate_id"]
            assert doc["policy_version"] == "0.1.0"
            assert len(doc["policy_sha256"]) == 64


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


# ── Rule 3b: historical contracts pin the governing policy ────────────────


def test_contracts_pin_exact_policy_bytes():
    import hashlib

    policy = REPO_ROOT / "docs" / "HQ_MISSION_POLICY.md"
    policy_sha = hashlib.sha256(policy.read_bytes()).hexdigest()
    for issue in RUNS:
        for f in _retro_files("mission-contract", issue):
            doc = _load(f)
            assert doc["policy_version"] == "0.1.0"
            assert doc["policy_sha256"] == policy_sha, \
                f"{f.name}: policy pin must equal the exact current policy bytes"
    neg_c = _load(NEG / "mission-contract.neg-c-wrong-framing.yaml")
    assert neg_c["policy_sha256"] == policy_sha


# ── Rule 3c: policy pin is validated against the real policy bytes ────────

PROJECTION_TIMESTAMP = "2026-08-10T03:00:31Z"  # Issue #23 creation time


def _validate_contract_file(path: Path) -> list[str]:
    """Run a mission-contract file through the NORMAL validator path (the
    same function the CLI uses), returning errors."""
    errors: list[str] = []
    validate_artifacts.validate_artifact(
        path, SCHEMAS.parent, REPO_ROOT, errors, None)
    return errors


def test_policy_pin_correct_version_and_hash_valid():
    errors = _validate_contract_file(_retro_files("mission-contract", "17")[0])
    assert not errors


def test_policy_pin_wrong_hash_invalid(tmp_path):
    base = _load(_retro_files("mission-contract", "17")[0])
    bad = dict(base)
    bad["policy_sha256"] = "f" * 64
    p = tmp_path / "mission-contract.bad-hash.yaml"
    p.write_text(yaml.safe_dump(bad, sort_keys=False))
    errors = _validate_contract_file(p)
    assert any("policy_sha256" in e and "does not match" in e for e in errors)


def test_policy_pin_wrong_version_invalid(tmp_path):
    base = _load(_retro_files("mission-contract", "17")[0])
    bad = dict(base)
    bad["policy_version"] = "9.9.9"
    p = tmp_path / "mission-contract.bad-version.yaml"
    p.write_text(yaml.safe_dump(bad, sort_keys=False))
    errors = _validate_contract_file(p)
    assert any("policy_version" in e and "does not match" in e for e in errors)


def test_policy_pin_missing_reference_invalid(tmp_path):
    base = _load(_retro_files("mission-contract", "17")[0])
    for ref, expect in [("docs/does-not-exist.md", "does not resolve to the canonical"),
                        ("../../etc/passwd", "escapes")]:
        bad = dict(base)
        bad["policy_reference"] = ref
        p = tmp_path / "mission-contract.bad-ref.yaml"
        p.write_text(yaml.safe_dump(bad, sort_keys=False))
        errors = _validate_contract_file(p)
        assert any(expect in e for e in errors), (ref, errors)


def test_policy_pin_non_canonical_reference_invalid(tmp_path):
    base = _load(_retro_files("mission-contract", "17")[0])
    bad = dict(base)
    bad["policy_reference"] = "README.md"
    p = tmp_path / "mission-contract.noncanonical.yaml"
    p.write_text(yaml.safe_dump(bad, sort_keys=False))
    errors = _validate_contract_file(p)
    assert any("does not resolve to the canonical" in e for e in errors)


def test_policy_pin_retro_fixtures_still_validate_via_cli():
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Federation HQ artifact validation OK" in result.stdout


# ── Retrospective provenance honesty ─────────────────────────────────────


def test_retro_projection_timestamps_not_presented_as_run_times():
    """Retro created_at must equal the documented projection reference
    (Issue #23 creation time), never the original run-opening times."""
    run_created = {
        "17": "2026-08-09T14:42:28Z",
        "19": "2026-08-09T16:03:54Z",
        "21": "2026-08-09T17:06:47Z",
    }
    for issue, run_ts in run_created.items():
        for prefix in ("mission-candidate", "mission-contract", "run-assessment"):
            for f in _retro_files(prefix, issue):
                doc = _load(f)
                assert doc["created_at"] == PROJECTION_TIMESTAMP, f.name
                assert doc["created_at"] != run_ts, f.name


def test_retro_contract_provenance_identifies_projection():
    for issue in RUNS:
        for f in _retro_files("mission-contract", issue):
            doc = _load(f)
            prov = doc["creation_provenance"]
            assert prov["author_role"] == "other"
            assert "NON-CANONICAL retrospective projection" in prov["source_reference"]
            assert "Mission Foundation" in prov["source_reference"]


def test_retro_candidate_source_identifies_projection():
    for issue in RUNS:
        for f in _retro_files("mission-candidate", issue):
            doc = _load(f)
            assert "retrospective" in doc["source"].lower()
            assert "Issue #23" in doc["source"]


def test_ledger_retro_entries_dated_as_projection_state():
    """Retrospective (Issue #23) ledger entries are dated at the projection
    timestamp and identified as retroactive; LIVE entries (first ingestion,
    e.g. MissionContract Pilot 01) keep their own timestamps and are not
    mislabeled as retroactive. The ledger top-level updated_at equals the
    latest entry update time."""
    ledger = _load(LEDGER)
    retro = [i for i in ledger["items"] if "Retroactive (Issue #23)" in i["last_observed_evidence"]]
    live = [i for i in ledger["items"] if "Retroactive (Issue #23)" not in i["last_observed_evidence"]]
    assert retro, "expected retrospective ledger entries"
    for item in retro:
        assert item["updated_at"] == PROJECTION_TIMESTAMP
        assert "runs/run-" in item["last_observed_evidence"]
    for item in live:
        assert "Retroactive (Issue #23)" not in item["last_observed_evidence"]
        assert item["updated_at"] != PROJECTION_TIMESTAMP
    assert ledger["updated_at"] == max(i["updated_at"] for i in ledger["items"])
    header = LEDGER.read_text(encoding="utf-8").splitlines()
    assert any("retroactively assigned" in line for line in header)


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


# ── Rule 8: signal provenance is enforced (POL-01) ─────────────────────────


def test_empty_candidate_signal_refs_is_invalid():
    base = _load(_retro_files("mission-candidate", "17")[0])
    bad = dict(base)
    bad["signal_refs"] = []
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("signal_refs must contain at least one" in e for e in semantic)


def test_contract_without_source_candidate_is_invalid():
    base = _load(_retro_files("mission-contract", "17")[0])
    bad = dict(base)
    bad.pop("source_candidate_id")
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("source_candidate_id is required" in e for e in semantic)


def test_contract_without_signal_refs_is_invalid():
    base = _load(_retro_files("mission-contract", "17")[0])
    bad = dict(base)
    bad["signal_refs"] = []
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("signal_refs must contain at least one" in e for e in semantic)


# ── Rule 8b: wont_fix does not silently reopen; real ledger reopen guard ──


def test_wont_fix_does_not_silently_reopen():
    doc = _load(NEG / "mission-candidate.neg-b-wont-fix-reopen.yaml")
    errors = _errors_for("mission-candidate.schema.json", doc)
    assert not errors
    assert doc["disposition"] == "wont_fix"
    assert "mission_id" not in doc


def _ledger_with(*items) -> dict:
    return {"kind": "federation_hq_mission_ledger", "schema_version": "0.1.0",
            "items": list(items), "updated_at": "2026-08-09T19:00:00Z"}


def _ledger_item(signal_id: str, disposition: str) -> dict:
    return {"signal_id": signal_id, "source_kind": "test_node",
            "source_native_ref": f"tests/example_{signal_id}.py",
            "disposition": disposition, "updated_at": "2026-08-09T19:00:00Z"}


def _candidate_selected(signal_id: str, override: dict | None = None) -> dict:
    doc = {
        "kind": "federation_hq_mission_candidate",
        "candidate_id": f"cand-{signal_id}",
        "signal_refs": [{"signal_id": signal_id, "source_kind": "test_node",
                          "source_native_ref": f"tests/example_{signal_id}.py"}],
        "target_repository": "kimeisele/agent-city",
        "problem_statement": "bounded problem",
        "disposition": "selected",
        "created_at": "2026-08-09T19:30:00Z",
    }
    if override is not None:
        doc["prior_disposition_override"] = override
    return doc


_VALID_OVERRIDE = {
    "ledger_signal_id": "sig-X", "prior_disposition": "wont_fix",
    "new_evidence_refs": ["https://example.com/evidence/1"],
}


def test_reopen_guard_wont_fix_blocks_without_override():
    ledger = _ledger_with(_ledger_item("sig-X", "wont_fix"))
    errors = []
    validate_artifacts.check_ledger_reopen(_candidate_selected("sig-X"), ledger, "test", errors)
    assert any("terminal ledger disposition" in e and "sig-X" in e for e in errors)


def test_reopen_guard_valid_override_supersedes():
    ledger = _ledger_with(_ledger_item("sig-X", "wont_fix"))
    errors = []
    cand = _candidate_selected("sig-X", dict(_VALID_OVERRIDE))
    validate_artifacts.check_ledger_reopen(cand, ledger, "test", errors)
    assert not errors


def test_reopen_guard_completed_signal_cannot_silently_reopen():
    ledger = _ledger_with(_ledger_item("sig-Y", "completed"))
    errors = []
    validate_artifacts.check_ledger_reopen(_candidate_selected("sig-Y"), ledger, "test", errors)
    assert any("completed" in e for e in errors)


def test_reopen_guard_unrelated_signal_remains_selectable():
    ledger = _ledger_with(_ledger_item("sig-X", "wont_fix"))
    errors = []
    validate_artifacts.check_ledger_reopen(_candidate_selected("sig-NEW"), ledger, "test", errors)
    assert not errors


def test_reopen_guard_override_requires_new_evidence():
    bad_override = {"ledger_signal_id": "sig-X", "prior_disposition": "wont_fix",
                    "new_evidence_refs": []}
    semantic = []
    validate_artifacts.check_mission_artifact(
        _candidate_selected("sig-X", bad_override), "test", semantic)
    assert any("new_evidence_ref" in e for e in semantic)


# ── Rule 9: mission_rejected is representable and closes the loop ─────────


def test_mission_rejected_is_representable():
    doc = _load(NEG / "mission-contract.neg-c-wrong-framing.yaml")
    errors = _errors_for("mission-contract.schema.json", doc)
    assert not errors
    assert doc["status"] == "mission_rejected"
    assert doc["rejection_reason"]
    assert doc["source_candidate_id"]
    # Validator requires a rejection_reason on mission_rejected.
    bad = dict(doc)
    bad.pop("rejection_reason", None)
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("mission_rejected requires rejection_reason" in e for e in semantic)


def test_mission_rejected_run_assessment_closes_loop():
    """Contract mission_rejected -> RunAssessment terminal_outcome
    mission_rejected -> ledger_disposition rejected, without fabricated
    executed-run facts."""
    doc = _load(NEG / "run-assessment.neg-c-wrong-framing.yaml")
    errors = _errors_for("run-assessment.schema.json", doc)
    assert not errors
    assert doc["terminal_outcome"] == "mission_rejected"
    assert doc["rejection_reason_code"] == "invalid_framing"
    assert doc["ledger_disposition"] == "rejected"
    assert doc["run_id"] is None
    for forbidden in ("review_verdict", "gate_verified", "target_merged",
                      "run_record_merged", "human_role_handoffs", "repair_class"):
        assert forbidden not in doc


def test_mission_rejected_assessment_requires_reason_code():
    base = _load(NEG / "run-assessment.neg-c-wrong-framing.yaml")
    bad = dict(base)
    bad.pop("rejection_reason_code")
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("rejection_reason_code" in e for e in semantic)


def test_mission_rejected_assessment_forbids_fabricated_run_facts():
    base = _load(NEG / "run-assessment.neg-c-wrong-framing.yaml")
    bad = dict(base)
    bad["review_verdict"] = "blocked"
    semantic = []
    validate_artifacts.check_mission_artifact(bad, "test", semantic)
    assert any("must not carry executed-run facts" in e for e in semantic)


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
    # run-assessment.yaml in runs/<run-id>/ is the canonical executed-run
    # terminal feedback (Issue #31) since MissionContract Pilot 01; the
    # retrospective projection artifacts never live in runs/.
    for assessment in (REPO_ROOT / "runs").glob("*/run-assessment*.yaml"):
        assert assessment.parent.name == "run-20260810-agent-city-moltbook-outbound-fallback-contract"
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
        "contracts/repair-candidate.schema.json": None,
        "contracts/repair-result.schema.json": None,
        "contracts/review-result.schema.json": None,
        "contracts/coordination-message.schema.json": None,
    }
    # contracts/run-manifest.schema.json is excluded: Issue #25 (Operator
    # MissionContract consumption) legitimately extends it ADDITIVELY with
    # the optional mission_input pin; legacy maintenance_request semantics
    # are preserved and historical manifests still validate (covered by the
    # consumption tests). The other execution schemas are untouched.
    # Files changed in this slice, compared against main@HEAD.
    changed = sp.run(
        ["git", "diff", "--name-only", "main", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.splitlines()
    for rel in expected:
        assert rel not in changed, f"{rel} must not change in this slice"
    for rel in (
        "federation_hq_gate/policy.py",
        "prompts/operator/v0.2.1.md",
    ):
        assert rel not in changed, f"{rel} must not change in this slice"
    # prompts/registry.yaml IS legitimately extended by Issue #25 (additive
    # releases operator@0.3.0 / scout|repair|review@0.2.0); prior release
    # immutability is asserted by tests/test_mission_consumption.py.
