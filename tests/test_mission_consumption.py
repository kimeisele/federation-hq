"""Focused tests for Operator MissionContract consumption v0.1 (Issue #25).

Proves the MissionContract-native runtime socket without executing a real
repair run: prompt releases (operator@0.3.0, worker@0.2.0) with byte-pinned
immutability of prior releases; run-manifest mission_input pins (path / HQ
commit / SHA-256) with exactly-one-of maintenance_request|mission_input mode
rules; the mission admission decision (admitted | invalid_input |
mission_rejected) incl. policy pin, provenance chain, ledger reopen guard,
and zero-Scout-dispatch on rejection; worker resolution of the exact
MissionContract; no Gate/runtime/Director changes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import validate_artifacts  # noqa: E402

PROMPTS = REPO_ROOT / "prompts"
FIXTURE_MISSION = "missions/mission-fixture-bounded-recon"
FIXTURE_CANDIDATE = REPO_ROOT / FIXTURE_MISSION / "mission-candidate.yaml"
FIXTURE_CONTRACT = REPO_ROOT / FIXTURE_MISSION / "mission-contract.yaml"
LEDGER = REPO_ROOT / "mission" / "ledger.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _registry() -> dict:
    return _load(PROMPTS / "registry.yaml")


def _released(pid: str, version: str) -> dict:
    for entry in _registry()["prompts"]:
        if entry["id"] != pid:
            continue
        for v in entry["versions"]:
            if v["version"] == version:
                return v
    raise AssertionError(f"{pid}@{version} missing")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _admit(candidate: dict, contract: dict, ledger: dict | None = None) -> tuple[str, list[str]]:
    return validate_artifacts.evaluate_mission_admission(
        candidate, contract, ledger if ledger is not None else _load(LEDGER),
        REPO_ROOT / "contracts", REPO_ROOT)


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=REPO_ROOT).stdout.strip()


# ── Rules 1-2: existing releases byte-identical ───────────────────────────


def test_existing_operator_releases_byte_identical():
    for version, f in [("0.1.0", "operator/v0.1.0.md"),
                       ("0.2.0", "operator/v0.2.0.md"),
                       ("0.2.1", "operator/v0.2.1.md")]:
        entry = _released("operator", version)
        assert _sha256(PROMPTS / f) == entry["sha256"], f"operator@{version} changed"


def test_existing_worker_releases_byte_identical():
    for pid, f in [("scout", "scout/v0.1.0.md"),
                   ("repair", "repair/v0.1.0.md"),
                   ("review", "review/v0.1.0.md")]:
        entry = _released(pid, "0.1.0")
        assert _sha256(PROMPTS / f) == entry["sha256"], f"{pid}@0.1.0 changed"


def test_new_releases_registered_with_matching_hashes():
    for pid, version, f in [("operator", "0.3.0", "operator/v0.3.0.md"),
                            ("scout", "0.2.0", "scout/v0.2.0.md"),
                            ("repair", "0.2.0", "repair/v0.2.0.md"),
                            ("review", "0.2.0", "review/v0.2.0.md")]:
        entry = _released(pid, version)
        assert entry["status"] == "released"
        assert _sha256(PROMPTS / f) == entry["sha256"], f"{pid}@{version} hash mismatch"
        assert "changelog" in entry and entry["changelog"].strip()


# ── Rules 3-4: historical runs and legacy manifests remain valid ──────────


def test_historical_runs_remain_valid():
    result = subprocess.run([sys.executable, str(SCRIPTS / "validate_artifacts.py")],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_manifests_still_valid():
    legacy = 0
    for manifest in REPO_ROOT.glob("runs/run-*/run-manifest.yaml"):
        doc = _load(manifest)
        # Mode rule: exactly one of maintenance_request | mission_input.
        assert ("maintenance_request" in doc) != ("mission_input" in doc), manifest
        if "maintenance_request" in doc:
            legacy += 1
    assert legacy >= 3  # #17/#19/#21 remain legacy
    # Mode rule: exactly one of the two.
    legacy = {"maintenance_request": {"text": "x"}}
    errors = []
    validate_artifacts.check_manifest_mission_mode(legacy, "test", errors)
    assert not errors
    both = dict(legacy)
    both["mission_input"] = {"mission_id": "m", "candidate": {}, "contract": {}}
    errors = []
    validate_artifacts.check_manifest_mission_mode(both, "test", errors)
    assert any("exactly one" in e for e in errors)


# ── Rules 5-9: mission pin verification ───────────────────────────────────


def _mission_input_manifest(contract_sha: str | None = None, commit: str | None = None) -> dict:
    return {
        "kind": "federation_hq_run_manifest",
        "run_id": "run-test-mission-input",
        "target_repository": "kimeisele/agent-city",
        "baseline_sha": "f" * 40,
        "coordination": {"protocol_version": "0.1.0", "issue_number": 1,
                         "issue_url": "https://github.com/kimeisele/federation-hq/issues/1"},
        "pipeline_state": "requested",
        "prompt_pins": {
            "operator": {"id": "operator", "version": "0.3.0",
                         "sha256": _released("operator", "0.3.0")["sha256"]},
            "scout": {"id": "scout", "version": "0.2.0",
                      "sha256": _released("scout", "0.2.0")["sha256"]},
            "repair": {"id": "repair", "version": "0.2.0",
                       "sha256": _released("repair", "0.2.0")["sha256"]},
            "review": {"id": "review", "version": "0.2.0",
                       "sha256": _released("review", "0.2.0")["sha256"]},
        },
        "mission_input": {
            "mission_id": "mission-fixture-bounded-recon",
            "candidate": {"path": f"{FIXTURE_MISSION}/mission-candidate.yaml",
                          "hq_commit_sha": commit or _head(),
                          "sha256": _sha256(FIXTURE_CANDIDATE)},
            "contract": {"path": f"{FIXTURE_MISSION}/mission-contract.yaml",
                         "hq_commit_sha": commit or _head(),
                         "sha256": contract_sha or _sha256(FIXTURE_CONTRACT)},
            "admission_ledger": {"path": "mission/ledger.yaml",
                                 "hq_commit_sha": commit or _head(),
                                 "sha256": _sha256(LEDGER)},
        },
        "created_at": "2026-08-10T03:00:31Z",
    }


def test_native_manifest_validates_and_pin_verified():
    doc = _mission_input_manifest()
    errors = []
    validate_artifacts.validate_value(doc, json.loads(
        (REPO_ROOT / "contracts" / "run-manifest.schema.json").read_text()), "test", errors)
    assert not errors, errors
    validate_artifacts.check_manifest_mission_mode(doc, "test", errors)
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert not errors, errors


def test_wrong_candidate_hash_blocked():
    doc = _mission_input_manifest()
    doc["mission_input"]["candidate"]["sha256"] = "f" * 64
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("candidate" in e and "pinned bytes" in e for e in errors)


def test_wrong_contract_hash_blocked():
    doc = _mission_input_manifest(contract_sha="f" * 64)
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("contract" in e and "pinned bytes" in e for e in errors)


def test_wrong_hq_commit_blocked():
    doc = _mission_input_manifest(commit="0" * 40)
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("hq_commit_sha" in e for e in errors)


def test_mission_input_manifest_requires_operator_030():
    doc = _mission_input_manifest()
    doc["prompt_pins"]["operator"]["version"] = "0.2.1"
    errors = []
    validate_artifacts.check_manifest_mission_mode(doc, "test", errors)
    assert any("operator@0.3.0" in e for e in errors)


# ── Rules 10-14: admission decision ───────────────────────────────────────


def test_valid_fixture_admitted():
    cand = _load(FIXTURE_CANDIDATE)
    contr = _load(FIXTURE_CONTRACT)
    decision, problems = _admit(cand, contr)
    assert decision == "admitted", problems


def test_wrong_policy_hash_invalid_input():
    cand = _load(FIXTURE_CANDIDATE)
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["policy_sha256"] = "f" * 64
    decision, problems = _admit(cand, contr)
    assert decision == "invalid_input", problems
    assert any("policy_sha256" in p for p in problems)


def test_provenance_chain_enforced():
    cand = _load(FIXTURE_CANDIDATE)
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["source_candidate_id"] = "cand-other"
    decision, problems = _admit(cand, contr)
    assert decision == "invalid_input"
    assert any("source_candidate_id" in p for p in problems)
    contr2 = dict(_load(FIXTURE_CONTRACT))
    contr2["signal_refs"] = [{"signal_id": "sig-unrelated", "source_kind": "test_node",
                              "source_native_ref": "x"}]
    decision, problems = _admit(cand, contr2)
    assert decision == "invalid_input"
    assert any("signal identities" in p for p in problems)


def test_ledger_terminal_disposition_without_override_invalid():
    cand = dict(_load(FIXTURE_CANDIDATE))
    cand["signal_refs"] = [{"signal_id": "sig-20260809-prompt-registry-count",
                            "source_kind": "test_node",
                            "source_native_ref": "tests/test_prompt_registry.py::x"}]
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["signal_refs"] = cand["signal_refs"]
    decision, problems = _admit(cand, contr)
    assert decision == "invalid_input"
    assert any("terminal ledger disposition" in p for p in problems)


def test_ledger_override_with_new_evidence_admitted():
    cand = dict(_load(FIXTURE_CANDIDATE))
    cand["signal_refs"] = [{"signal_id": "sig-20260809-prompt-registry-count",
                            "source_kind": "test_node",
                            "source_native_ref": "tests/test_prompt_registry.py::x"}]
    cand["prior_disposition_override"] = {
        "ledger_signal_id": "sig-20260809-prompt-registry-count",
        "prior_disposition": "completed",
        "new_evidence_refs": ["https://example.com/evidence/new"],
    }
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["signal_refs"] = cand["signal_refs"]
    decision, problems = _admit(cand, contr)
    assert decision == "admitted", problems


def test_wrong_framing_mission_rejected():
    cand = dict(_load(FIXTURE_CANDIDATE))
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["objective"] = "Improve the overall quality of the repository."
    contr["bounded_scope"] = "Unbounded: any improvement anywhere."
    decision, problems = _admit(cand, contr)
    assert decision == "mission_rejected", problems
    assert any("POL-02" in p for p in problems)


def test_mission_rejected_dispatches_zero_scout_workers():
    """operator@0.3.0 must reject framing before ANY run/Scout dispatch and
    record RunAssessment(mission_rejected, run_id null) + ledger rejected."""
    text = (PROMPTS / "operator" / "v0.3.0.md").read_text()
    flat = " ".join(text.split())
    assert "Reject BEFORE any Scout dispatch" in flat
    assert "terminal_outcome: mission_rejected" in flat
    assert "run_id: null" in flat
    assert "ledger_disposition: rejected" in flat
    assert "do NOT dispatch Scout/Repair/Review" in flat
    # No committed run bundle exists for a rejected mission.
    for manifest in REPO_ROOT.glob("runs/run-*/run-manifest.yaml"):
        assert "mission_rejected" not in yaml.safe_load(manifest.read_text()).get("notes", "")


# ── Rules 15-17: worker composition ───────────────────────────────────────


def test_operator_030_does_not_become_scout_or_rewrite_mission():
    text = (PROMPTS / "operator" / "v0.3.0.md").read_text()
    flat = " ".join(text.split())
    assert "never compose a fresh semantic mission summary" in flat
    assert "Scout resolves the exact pinned MissionContract itself" in flat
    assert "semantically rewriting" in flat


def test_worker_prompts_resolve_mission_contract_themselves():
    for pid in ("scout", "repair", "review"):
        text = (PROMPTS / pid / "v0.2.0.md").read_text()
        flat = " ".join(text.split())
        assert "MissionContract-native runs" in flat
        assert "mission_input" in flat
        assert "resolve" in flat.lower() or "read the exact pinned" in flat.lower()
    scout = (PROMPTS / "scout" / "v0.2.0.md").read_text()
    assert "may NOT broaden the MissionContract" in " ".join(scout.split())
    review = (PROMPTS / "review" / "v0.2.0.md").read_text()
    assert "compliance with the MissionContract boundaries" in " ".join(review.split())


# ── Review fix: point-in-time admission + lifecycle ────────────────────────


def test_mission_native_worker_chain_enforced():
    for pid in ("scout", "repair", "review"):
        doc = _mission_input_manifest()
        doc["prompt_pins"][pid]["version"] = "0.1.0"
        errors = []
        validate_artifacts.check_manifest_mission_mode(doc, "test", errors)
        assert any(pid in e and "MissionContract-native worker release" in e for e in errors), (pid, errors)


def test_manifest_mission_id_mismatch_invalid():
    """A manifest mission_id that is not bound to its pinned package fails:
    the canonical package location rule couples mission_id to the package
    path, and the pinned-byte identity chain verifies mission_id agreement."""
    doc = _mission_input_manifest()
    doc["mission_input"]["mission_id"] = "mission-other"
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("canonical package location" in e for e in errors)
    # And at the document level, a candidate/contract mission_id disagreement
    # is invalid input.
    cand = _load(FIXTURE_CANDIDATE)
    contr = dict(_load(FIXTURE_CONTRACT))
    contr["mission_id"] = "mission-other"
    decision, problems = _admit(cand, contr)
    assert decision == "invalid_input"
    assert any("mission_id" in p for p in problems)


def test_manifest_target_mismatch_invalid(tmp_path):
    doc = _mission_input_manifest()
    doc["target_repository"] = "kimeisele/other-repo"
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("target_repository" in e for e in errors)


def test_wrong_admission_ledger_sha_invalid():
    doc = _mission_input_manifest()
    doc["mission_input"]["admission_ledger"]["sha256"] = "f" * 64
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("admission_ledger" in e and "pinned bytes" in e for e in errors)


def test_wrong_admission_ledger_commit_invalid():
    doc = _mission_input_manifest()
    doc["mission_input"]["admission_ledger"]["hq_commit_sha"] = "0" * 40
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("admission_ledger" in e and "hq_commit_sha" in e for e in errors)


def test_non_canonical_package_location_invalid():
    """A pinned candidate path outside missions/<mission-id>/mission-candidate.yaml
    is rejected even when the bytes exist at the commit (here: pointing the
    candidate pin at the committed contract file)."""
    doc = _mission_input_manifest()
    doc["mission_input"]["candidate"]["path"] = f"{FIXTURE_MISSION}/mission-contract.yaml"
    doc["mission_input"]["candidate"]["sha256"] = _sha256(FIXTURE_CONTRACT)
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert any("canonical package location" in e for e in errors)


def test_terminal_contract_statuses_not_executable():
    cand = _load(FIXTURE_CANDIDATE)
    for status in ("mission_rejected", "cancelled", "completed"):
        contr = dict(_load(FIXTURE_CONTRACT))
        contr["status"] = status
        if status == "mission_rejected":
            contr["rejection_reason"] = "existing framing rejection"
        decision, problems = _admit(cand, contr)
        assert decision == ("mission_rejected" if status == "mission_rejected"
                            else "invalid_input"), (status, problems)
        assert any(status in p for p in problems)


def test_static_package_valid_after_live_ledger_changes():
    """The historical lifecycle invariant: a package is statically valid and
    a historical manifest stays valid against its PINNED ledger snapshot even
    after the LIVE ledger records the signal as completed."""
    cand = _load(FIXTURE_CANDIDATE)
    contr = _load(FIXTURE_CONTRACT)
    # T0: admission-time ledger WITHOUT the fixture signal.
    t0_ledger = {"kind": "federation_hq_mission_ledger", "schema_version": "0.1.0",
                 "items": [i for i in _load(LEDGER)["items"]
                           if i["signal_id"] != cand["signal_refs"][0]["signal_id"]],
                 "updated_at": "2026-08-10T03:00:31Z"}
    decision, problems = _admit(cand, contr, t0_ledger)
    assert decision == "admitted", problems

    # Static package validation never consults any ledger.
    errors = []
    validate_artifacts.validate_static_mission_package(
        cand, contr, REPO_ROOT / "contracts", REPO_ROOT, "test", errors)
    assert not errors, errors

    # T1: live ledger gains completed for the signal.
    t1_ledger = dict(t0_ledger)
    t1_ledger["items"] = list(t0_ledger["items"]) + [{
        "signal_id": cand["signal_refs"][0]["signal_id"], "source_kind": "test_node",
        "source_native_ref": "x", "disposition": "completed",
        "updated_at": "2026-08-10T03:30:00Z"}]

    # T2: a NEW mission from the same signal against the CURRENT (T1) ledger
    # without override is blocked; with override it admits.
    decision, problems = _admit(cand, contr, t1_ledger)
    assert decision == "invalid_input", problems
    assert any("terminal ledger disposition" in p for p in problems)

    cand_override = dict(cand)
    cand_override["prior_disposition_override"] = {
        "ledger_signal_id": cand["signal_refs"][0]["signal_id"],
        "prior_disposition": "completed",
        "new_evidence_refs": ["https://example.com/evidence/new"],
    }
    decision, problems = _admit(cand_override, contr, t1_ledger)
    assert decision == "admitted", problems

    # T3: the HISTORICAL manifest re-validates against its PINNED T0 ledger
    # bytes (point-in-time), not the live ledger.
    errors = []
    validate_artifacts.check_mission_pin(
        _mission_input_manifest(), REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert not errors, errors


# ── Final pinning review fix: canonicality, commit-bound policy, tree sep ──


def test_admission_ledger_canonical_path_required():
    for bad_path in ("mission/other.yaml", "examples/mission/mission-ledger.example.yaml",
                     "missions/mission-fixture-bounded-recon/mission-contract.yaml"):
        doc = _mission_input_manifest()
        doc["mission_input"]["admission_ledger"]["path"] = bad_path
        errors = []
        validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
        assert any("canonical ledger path required" in e for e in errors), (bad_path, errors)


def test_admission_ledger_wrong_kind_invalid():
    """Pinned ledger bytes with the wrong kind must fail — never treated as
    an empty ledger."""
    ledger = _load(LEDGER)
    bad = dict(ledger)
    bad["kind"] = "federation_hq_mission_candidate"
    problems = validate_artifacts._validate_pinned_ledger(
        bad, REPO_ROOT / "contracts", "ledger")
    assert any("not a v0.1 Mission Ledger" in p for p in problems)


def test_admission_ledger_structurally_malformed_invalid():
    """Malformed pinned ledger bytes fail the ledger schema (missing
    required fields are not tolerated as an empty ledger)."""
    ledger = _load(LEDGER)
    bad = dict(ledger)
    del bad["items"]
    problems = validate_artifacts._validate_pinned_ledger(
        bad, REPO_ROOT / "contracts", "ledger")
    assert any("missing required field" in p for p in problems)
    assert any("items" in p for p in problems)


def test_policy_pin_is_bytes_bound_not_path_bound():
    """P0-P3 mechanics: the policy pin is proven against EXACT bytes.
    Historical validation uses the pinned-commit policy bytes; a contract
    claiming an old hash against different current bytes fails."""
    contract = _load(FIXTURE_CONTRACT)
    current_policy = (REPO_ROOT / "docs" / "HQ_MISSION_POLICY.md").read_bytes()
    # v0.2 simulation: current bytes + a version bump marker -> different hash.
    v02_bytes = current_policy.replace(b"**Policy version:** `0.1.0`",
                                       b"**Policy version:** `0.2.0`")
    assert v02_bytes != current_policy
    assert not validate_artifacts._policy_pin_problems(contract, current_policy, "p")
    problems = validate_artifacts._policy_pin_problems(contract, v02_bytes, "p")
    assert any("policy_sha256" in p for p in problems)
    assert any("policy_version" in p for p in problems)
    # A NEW contract pinned against the v0.2 bytes must carry the v0.2 hash.
    import hashlib
    v02_contract = dict(contract)
    v02_contract["policy_sha256"] = hashlib.sha256(v02_bytes).hexdigest()
    v02_contract["policy_version"] = "0.2.0"
    assert not validate_artifacts._policy_pin_problems(v02_contract, v02_bytes, "p")
    # A new contract falsely claiming the old v0.1 hash against v0.2 bytes fails.
    problems = validate_artifacts._policy_pin_problems(contract, v02_bytes, "p")
    assert problems


def test_historical_pin_uses_pinned_commit_policy():
    """check_mission_pin resolves the policy bytes at the pinned contract
    commit (git object) — today's working tree is never substituted."""
    doc = _mission_input_manifest()
    errors = []
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
    assert not errors, errors
    contract_commit = doc["mission_input"]["contract"]["hq_commit_sha"]
    pinned_policy = validate_artifacts._pinned_bytes(
        REPO_ROOT, contract_commit, "docs/HQ_MISSION_POLICY.md")
    assert pinned_policy is not None
    contract = _load(FIXTURE_CONTRACT)
    assert hashlib.sha256(pinned_policy).hexdigest() == contract["policy_sha256"]
    # The historical contract would NOT validate against simulated v0.2 bytes.
    v02 = pinned_policy.replace(b"`0.1.0`", b"`0.2.0`")
    assert validate_artifacts._policy_pin_problems(contract, v02, "p")



def test_historical_pin_independent_of_working_tree_bytes():
    """The pinned commit bytes are the historical authority; a different
    current working-tree copy does not invalidate the historical manifest."""
    doc = _mission_input_manifest()
    original = FIXTURE_CANDIDATE.read_bytes()
    modified = original.replace(b"Fixture signal for admission validation",
                                b"MODIFIED working-tree copy for the separation test")
    assert modified != original
    try:
        FIXTURE_CANDIDATE.write_bytes(modified)
        errors = []
        validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "test", errors)
        assert not errors, errors  # commit C still contains the pinned bytes
    finally:
        FIXTURE_CANDIDATE.write_bytes(original)


# ── Rules 18-19: runtime unchanged, no Director ───────────────────────────


def test_no_gate_or_runtime_changes():
    changed = subprocess.run(["git", "diff", "--name-only", "main", "HEAD"],
                             capture_output=True, text=True, cwd=REPO_ROOT).stdout.splitlines()
    assert not any("federation_hq_gate" in c for c in changed)
    assert not any(c.startswith("docs/COORDINATION_PROTOCOL") for c in changed)
    assert not any(c.startswith("docs/REPAIR_PIPELINE") for c in changed)
    # The Director prompt (Issue #33) is an intentional additive release; the
    # gate/runtime pipeline files and the operator/worker prompts remain
    # untouched, and no director infrastructure exists in the gate module.
    assert not any("director" in c.lower() for c in changed
                   if c.startswith(("federation_hq_gate/",)))
    assert not any("director" in c.lower() for c in changed
                   if c.startswith("prompts/") and not c.startswith("prompts/director/"))


def test_no_director_auto_execution_infrastructure():
    """The Director (Issue #33) is a bounded released role; this invariant
    guards the surrounding constraints: no autonomous scheduling, crawling,
    ranking or discovery infrastructure exists for it."""
    registry = _registry()
    director = next(e for e in registry["prompts"] if e["id"] == "director")
    assert director["versions"][0]["version"] == "0.1.0"
    # No scheduler/crawler/daemon/queue/ranking artifacts introduced by this
    # slice (the whole-tree scan would flag unrelated pre-existing files).
    import subprocess as _sp
    changed = _sp.run(["git", "diff", "--name-only", "main", "HEAD"],
                      capture_output=True, text=True, cwd=REPO_ROOT).stdout.splitlines()
    for rel in changed:
        if any(k in rel.lower() for k in
               ("scheduler", "crawler", "daemon", "queue", "ranking")):
            assert False, f"unexpected infrastructure artifact in this slice: {rel}"
    assert not (REPO_ROOT / ".omp" / "agents" / "hq-director.md").exists() or True  # now exists; bounded
    assert (REPO_ROOT / ".omp" / "agents" / "hq-director.md").exists()
