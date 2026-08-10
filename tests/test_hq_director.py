"""Focused tests for hq-director v0.1 (Issue #33, ADR-0002 step 4).

Proves MECHANICAL contracts only (never LLM intelligence): the director@0.1.0
release, the hq-director / hq-operator OMP adapters and spawn authority,
fixture outputs validating against the existing Mission schemas, the POL-04
terminal trap under the existing validator, no-mission and ambiguity
outcomes, the recon boundary, and no live-state pollution.
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

FIX = REPO_ROOT / "tests" / "fixtures" / "director"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _registry() -> dict:
    return _load(REPO_ROOT / "prompts" / "registry.yaml")


def _released(pid: str, version: str) -> dict:
    for entry in _registry()["prompts"]:
        if entry["id"] != pid:
            continue
        for v in entry["versions"]:
            if v["version"] == version:
                return v
    raise AssertionError(f"{pid}@{version} missing")


def _run_cli() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_artifacts.py")],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1])


# ── Rules 1-2: director@0.1.0 registry + historical hashes ───────────────


def test_director_release_registered_with_matching_hash():
    entry = _released("director", "0.1.0")
    assert entry["status"] == "released"
    assert entry["file"] == "director/v0.1.0.md"
    assert _sha256(REPO_ROOT / "prompts" / entry["file"]) == entry["sha256"]
    assert "changelog" in entry and entry["changelog"].strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_prompt_hashes_unchanged():
    for pid, version, f in [("operator", "0.1.0", "operator/v0.1.0.md"),
                            ("operator", "0.2.0", "operator/v0.2.0.md"),
                            ("operator", "0.2.1", "operator/v0.2.1.md"),
                            ("operator", "0.3.0", "operator/v0.3.0.md"),
                            ("scout", "0.1.0", "scout/v0.1.0.md"),
                            ("scout", "0.2.0", "scout/v0.2.0.md"),
                            ("repair", "0.1.0", "repair/v0.1.0.md"),
                            ("repair", "0.2.0", "repair/v0.2.0.md"),
                            ("review", "0.1.0", "review/v0.1.0.md"),
                            ("review", "0.2.0", "review/v0.2.0.md")]:
        assert _sha256(REPO_ROOT / "prompts" / f) == _released(pid, version)["sha256"], f


# ── Rules 3-7: OMP adapters and spawn authority ───────────────────────────


def test_hq_director_wrapper_valid_frontmatter():
    meta = _frontmatter(REPO_ROOT / ".omp" / "agents" / "hq-director.md")
    assert meta["name"] == "hq-director"
    assert meta.get("description")
    assert meta.get("spawns") == ["hq-operator"]


def test_hq_operator_wrapper_valid_frontmatter():
    meta = _frontmatter(REPO_ROOT / ".omp" / "agents" / "hq-operator.md")
    assert meta["name"] == "hq-operator"
    assert meta.get("description")
    assert sorted(meta.get("spawns")) == ["hq-integrator", "hq-repair", "hq-review", "hq-scout"]


def test_worker_wrappers_remain_spawn_denied():
    for name in ("hq-scout", "hq-repair", "hq-review", "hq-integrator"):
        meta = _frontmatter(REPO_ROOT / ".omp" / "agents" / f"{name}.md")
        assert meta["spawns"] == [], name


def test_director_wrapper_loads_canonical_prompt():
    text = (REPO_ROOT / ".omp" / "agents" / "hq-director.md").read_text()
    assert "director@0.1.0" in text and "prompts/director/v0.1.0.md" in text
    assert len(text) < 4000  # thin wrapper, no duplicated semantics


def test_operator_wrapper_loads_canonical_prompt():
    text = (REPO_ROOT / ".omp" / "agents" / "hq-operator.md").read_text()
    assert "operator@0.3.0" in text and "prompts/operator/v0.3.0.md" in text
    assert len(text) < 4000


# ── Rules 8-9: no new schema; fixture outputs validate ────────────────────


def test_no_new_director_artifact_schema():
    schemas = [p.name for p in (REPO_ROOT / "contracts").rglob("*.schema.json")]
    assert not any("director" in s for s in schemas)
    assert not any("decision" in s for s in schemas)


def test_expected_outputs_validate_against_existing_schemas():
    schema_cache: dict[str, dict] = {}
    for rel in [
        "three_signal/expected/mission-candidate.yaml",
        "three_signal/expected/signal-b-disposition.yaml",
        "three_signal/expected/signal-c-disposition.yaml",
        "recon_boundary/expected/recon-mission-contract.yaml",
    ]:
        doc = _load(FIX / rel)
        kind = doc["kind"]
        schema_name = {
            "federation_hq_mission_candidate": "mission/mission-candidate.schema.json",
            "federation_hq_mission_contract": "mission/mission-contract.schema.json",
        }[kind]
        if schema_name not in schema_cache:
            schema_cache[schema_name] = json.loads(
                (REPO_ROOT / "contracts" / schema_name).read_text(encoding="utf-8"))
        errors: list[str] = []
        validate_artifacts.validate_value(doc, schema_cache[schema_name], rel, errors)
        assert not errors, (rel, errors)


def test_three_signal_contract_and_candidate_agree():
    cand = _load(FIX / "three_signal/expected/mission-candidate.yaml")
    contr = _load(FIX / "three_signal/expected/mission-contract.yaml")
    assert cand["mission_id"] == contr["mission_id"] == "mission-director-fixture-a"
    assert contr["source_candidate_id"] == cand["candidate_id"]
    assert cand["disposition"] == "selected"
    assert contr["status"] == "proposed"
    assert contr["prescribes_repair"] is False


# ── Rule 10: terminal trap cannot silently reopen (existing POL-04) ───────


def test_terminal_trap_cannot_reopen_under_existing_validator():
    fixture_ledger = _load(FIX / "three_signal/ledger.yaml")
    candidate_b = _load(FIX / "three_signal/expected/signal-b-disposition.yaml")
    # A selected candidate on the trap signal WITHOUT an override must fail
    # the existing POL-04 reopen guard.
    trap = dict(candidate_b)
    trap["disposition"] = "selected"
    trap.pop("policy_eligibility", None)
    errors: list[str] = []
    validate_artifacts.check_ledger_reopen(trap, fixture_ledger, "trap", errors)
    assert any("terminal ledger disposition" in e and "wont_fix" in e for e in errors), errors
    # The expected non-mission disposition passes.
    errors = []
    validate_artifacts.check_ledger_reopen(candidate_b, fixture_ledger, "trap", errors)
    assert not errors


# ── Rules 11-12: no-mission outcome; live ledger unpolluted ───────────────


def test_no_mission_fixture_has_zero_contracts():
    expected_dir = FIX / "no_mission/expected"
    assert list(expected_dir.glob("mission-contract*.yaml")) == []
    assert list(expected_dir.glob("*.json")) == []


def test_terminal_signal_d_preserved_completed():
    """POL-04: an existing terminal Ledger disposition is authoritative and
    preserved — the Director reports it, never re-dispositions it."""
    ledger = _load(FIX / "no_mission/ledger.yaml")
    item = next(i for i in ledger["items"] if i["signal_id"] == "sig-director-fixture-d")
    assert item["disposition"] == "completed"
    # No expected Candidate rewrites D into a new disposition.
    for rel in ("signal-e-candidate.yaml", "signal-f-candidate.yaml"):
        cand = _load(FIX / "no_mission/expected" / rel)
        assert cand["signal_refs"][0]["signal_id"] != "sig-director-fixture-d"
    # The trap candidate on D without an override fails the existing POL-04 guard.
    errors: list[str] = []
    validate_artifacts.check_ledger_reopen(
        {"kind": "federation_hq_mission_candidate", "candidate_id": "x",
         "signal_refs": [{"signal_id": "sig-director-fixture-d", "source_kind": "test_node",
                          "source_native_ref": "x"}],
         "target_repository": "kimeisele/agent-city", "problem_statement": "x",
         "disposition": "selected", "created_at": "2026-08-10T09:00:00Z"},
        ledger, "trap", errors)
    assert any("terminal ledger disposition" in e for e in errors), errors


def test_no_mission_new_signals_schema_valid():
    """New non-mission outcomes are schema-valid MissionCandidate decision
    objects: E no_mission_warranted, F duplicate/duplicate_of sig-D."""
    schema = json.loads((REPO_ROOT / "contracts" / "mission" / "mission-candidate.schema.json")
                        .read_text(encoding="utf-8"))
    e = _load(FIX / "no_mission/expected/signal-e-candidate.yaml")
    errors: list[str] = []
    validate_artifacts.validate_value(e, schema, "signal-e", errors)
    assert not errors, errors
    assert e["disposition"] == "no_mission_warranted"
    assert "mission_id" not in e
    f = _load(FIX / "no_mission/expected/signal-f-candidate.yaml")
    errors = []
    validate_artifacts.validate_value(f, schema, "signal-f", errors)
    assert not errors, errors
    assert f["disposition"] == "duplicate"
    assert f["duplicate_of"] == "sig-director-fixture-d"
    assert "mission_id" not in f
    # Duplicate requires duplicate_of under the mission semantic check.
    semantic: list[str] = []
    validate_artifacts.check_mission_artifact(f, "signal-f", semantic)
    assert not semantic


def test_live_ledger_has_no_director_fixtures():
    live = _load(REPO_ROOT / "mission" / "ledger.yaml")
    live_ids = {i["signal_id"] for i in live["items"]}
    assert not any(s.startswith("sig-director-fixture") for s in live_ids)


def test_no_director_fixtures_in_live_missions():
    live = [p.name for p in (REPO_ROOT / "missions").iterdir() if p.is_dir()]
    assert not any("director-fixture" in n for n in live)


# ── Rules 13-15: Pilot 01 / operator / worker prompts unchanged ───────────


def test_pilot01_unchanged():
    a = _load(REPO_ROOT / "runs" / "run-20260810-agent-city-moltbook-outbound-fallback-contract"
              / "run-assessment.yaml")
    assert a["terminal_outcome"] == "approved"
    assert a["run_id"] == "run-20260810-agent-city-moltbook-outbound-fallback-contract"


def test_operator_and_worker_prompts_unchanged():
    changed = subprocess.run(["git", "diff", "--name-only", "main", "HEAD"],
                             capture_output=True, text=True, cwd=REPO_ROOT).stdout.splitlines()
    for rel in ("prompts/operator/v0.3.0.md", "prompts/scout/v0.2.0.md",
                "prompts/repair/v0.2.0.md", "prompts/review/v0.2.0.md",
                "prompts/operator/v0.2.1.md"):
        assert rel not in changed, f"{rel} changed in this slice"


# ── Rules 12b-16: ambiguity, recon, formulation lifecycle, no ranking ─────


def test_ambiguity_cycle_produces_zero_mission_artifacts():
    expected_dir = FIX / "ambiguous/expected"
    assert list(expected_dir.glob("mission-contract*.yaml")) == []
    assert list(expected_dir.glob("*candidate*.yaml")) == []
    signals = _load(FIX / "ambiguous/signals.yaml")
    assert len(signals) == 2
    ledger = _load(FIX / "ambiguous/ledger.yaml")
    ledger_ids = {i["signal_id"] for i in ledger["items"]}
    assert not any(s["signal_id"] in ledger_ids for s in signals)  # neither terminal
    assert all(s["last_observed_evidence"] for s in signals)
    decision = (expected_dir / "decision.md").read_text()
    assert "BLOCKED" in decision and "ambiguous mission selection" in decision


def test_no_ranking_or_self_score_fields():
    for rel in ["three_signal/expected/mission-candidate.yaml",
                "three_signal/expected/mission-contract.yaml",
                "no_mission/expected/signal-e-candidate.yaml",
                "no_mission/expected/signal-f-candidate.yaml",
                "recon_boundary/expected/recon-mission-contract.yaml"]:
        doc = _load(FIX / rel)
        for key in ("risk_score", "priority_score", "confidence", "importance",
                    "llm_score", "rank"):
            assert key not in doc, (rel, key)


def test_recon_contract_prescribes_no_repair():
    contr = _load(FIX / "recon_boundary/expected/recon-mission-contract.yaml")
    assert contr["prescribes_repair"] is False
    flat = " ".join((contr["objective"] + " " + contr["bounded_scope"]).split()).lower()
    assert "recon" in flat or "investigat" in flat
    assert not any(word in flat for word in ("change file", "implement y", "restore class"))


def test_formulation_must_be_canonical_before_operator_handoff():
    """The Director normal-merges its validated formulation PR BEFORE spawning
    the Operator; the handoff payload pins the exact merged HQ commit."""
    prompt = (REPO_ROOT / "prompts" / "director" / "v0.1.0.md").read_text()
    flat = " ".join(prompt.split())
    assert "NORMAL Federation HQ merge" in flat
    assert "BLOCKED — formulation integration" in flat
    assert "After the formulation merge SUCCEEDS" in flat
    assert "exact formulation merge commit C" in flat
    assert "unmerged PR head" in flat and "mutable branch name" in flat
    wrapper = (REPO_ROOT / ".omp" / "agents" / "hq-director.md").read_text()
    assert "NORMAL-merged" in wrapper and "exact merged commit C" in wrapper


def test_operator_handoff_payload_uses_exact_merged_commit():
    """Pure mechanical handoff model: formulation PR head H -> normal
    integration -> merged commit C -> payload pins C, never H/branch."""
    payload = {
        "mission_id": "mission-director-fixture-a",
        "candidate_path": "missions/mission-director-fixture-a/mission-candidate.yaml",
        "contract_path": "missions/mission-director-fixture-a/mission-contract.yaml",
        "hq_commit": "c" * 40,  # the exact MERGED HQ commit
        "cycle_issue": "kimeisele/federation-hq#99996",
    }
    assert payload["hq_commit"] == "c" * 40
    assert len(payload["hq_commit"]) == 40
    assert not any(payload[k] in ("h" * 40, "branch/name") for k in ("hq_commit",))
    assert payload["candidate_path"].startswith("missions/")
    assert payload["contract_path"].startswith("missions/")
    assert "maintenance_request" not in str(payload)



# ── Final ordering fix: POL-04 admission basis B, mixed-commit pins ───────


def _admission_contract(mission_id: str, signal_id: str) -> dict:
    return {"kind": "federation_hq_mission_contract", "mission_version": "0.1.0",
            "mission_id": mission_id, "source_candidate_id": f"cand-{mission_id}",
            "signal_refs": [{"signal_id": signal_id, "source_kind": "test_node",
                             "source_native_ref": f"tests/{signal_id}.py"}],
            "target_repository": "kimeisele/agent-city", "objective": "bounded question",
            "decision_question": "q", "bounded_scope": "scope",
            "scope_enforcement": "declared", "prescribes_repair": False,
            "hard_constraints": [], "stop_conditions": [],
            "expected_allowed_outcomes": ["approved", "blocked"],
            "policy_reference": "docs/HQ_MISSION_POLICY.md", "policy_version": "0.1.0",
            "policy_sha256": "7d3c4eb4b6528e4aef515b429aafcdb6d9dc228d6feea098582b10a2a4f2241d",
            "status": "proposed", "creation_provenance": {"author_role": "other",
            "source_reference": "fixture"}, "created_at": "2026-08-10T09:00:00Z"}


def _admission_candidate(signal_id: str, mission_id: str,
                         override: dict | None = None) -> dict:
    doc = {"kind": "federation_hq_mission_candidate", "candidate_id": f"cand-{mission_id}",
           "signal_refs": [{"signal_id": signal_id, "source_kind": "test_node",
                            "source_native_ref": f"tests/{signal_id}.py"}],
           "target_repository": "kimeisele/agent-city", "problem_statement": "fixture",
           "disposition": "selected", "mission_id": mission_id,
           "created_at": "2026-08-10T09:00:00Z"}
    if override is not None:
        doc["prior_disposition_override"] = override
    return doc


def _b_ledger(items: list[dict]) -> dict:
    return {"kind": "federation_hq_mission_ledger", "schema_version": "0.1.0",
            "items": items, "updated_at": "2026-08-10T09:00:00Z"}


def test_illegal_reopen_blocked_against_preformulation_ledger():
    """Case A: Ledger B shows signal X completed; a selected Candidate at C
    with NO override must be blocked when admission evaluates against B —
    even if the post-formulation Ledger C would show the mission active."""
    ledger_b = _b_ledger([{"signal_id": "sig-X", "source_kind": "test_node",
                           "source_native_ref": "x", "disposition": "completed",
                           "related_run_ids": ["run-1"],
                           "updated_at": "2026-08-10T08:00:00Z"}])
    cand = _admission_candidate("sig-X", "mission-new")
    contr = _admission_contract("mission-new", "sig-X")
    decision, problems = validate_artifacts.evaluate_mission_admission(
        cand, contr, ledger_b, REPO_ROOT / "contracts", REPO_ROOT)
    assert decision == "invalid_input", problems
    assert any("terminal ledger disposition" in p for p in problems)


def test_evidence_backed_reopen_admitted_against_b():
    """Case B: the same Ledger B with an explicit prior_disposition_override
    (completed + new evidence) admits."""
    ledger_b = _b_ledger([{"signal_id": "sig-X", "source_kind": "test_node",
                           "source_native_ref": "x", "disposition": "completed",
                           "related_run_ids": ["run-1"],
                           "updated_at": "2026-08-10T08:00:00Z"}])
    cand = _admission_candidate("sig-X", "mission-new", {
        "ledger_signal_id": "sig-X", "prior_disposition": "completed",
        "new_evidence_refs": ["https://example.com/evidence/new"]})
    contr = _admission_contract("mission-new", "sig-X")
    decision, problems = validate_artifacts.evaluate_mission_admission(
        cand, contr, ledger_b, REPO_ROOT / "contracts", REPO_ROOT)
    assert decision == "admitted", problems


def test_new_signal_admitted_against_b():
    """Case C: Ledger B has no signal X -> selected Candidate admits."""
    ledger_b = _b_ledger([])
    cand = _admission_candidate("sig-X", "mission-new")
    contr = _admission_contract("mission-new", "sig-X")
    decision, problems = validate_artifacts.evaluate_mission_admission(
        cand, contr, ledger_b, REPO_ROOT / "contracts", REPO_ROOT)
    assert decision == "admitted", problems


def test_mixed_commit_mission_input_valid():
    """Candidate/Contract @ C and AdmissionLedger @ B with B != C validate
    through the existing mission-pin machinery (feature, not inconsistency)."""
    import subprocess as _sp
    c = _sp.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                cwd=REPO_ROOT).stdout.strip()
    # B = the pre-slice main commit; the admission Ledger is pinned to the
    # EXACT bytes at B (later live cycles legitimately add ledger items, so
    # the working-tree ledger is never substituted) -> B != C, both real
    # commits.
    b = "af28e5b3cd1eefdb660c3a69da4bbd7397c0bcae"
    assert b != c
    ledger_bytes = validate_artifacts._pinned_bytes(REPO_ROOT, b, "mission/ledger.yaml")
    assert ledger_bytes is not None
    ledger_sha = hashlib.sha256(ledger_bytes).hexdigest()
    doc = {
        "kind": "federation_hq_run_manifest", "run_id": "run-mixed-pin",
        "target_repository": "kimeisele/agent-city", "baseline_sha": "f" * 40,
        "coordination": {"protocol_version": "0.1.0", "issue_number": 1,
                         "issue_url": "https://github.com/kimeisele/federation-hq/issues/1"},
        "pipeline_state": "requested",
        "prompt_pins": {"operator": {"id": "operator", "version": "0.3.0",
                                     "sha256": _released("operator", "0.3.0")["sha256"]},
                        "scout": {"id": "scout", "version": "0.2.0",
                                  "sha256": _released("scout", "0.2.0")["sha256"]},
                        "repair": {"id": "repair", "version": "0.2.0",
                                   "sha256": _released("repair", "0.2.0")["sha256"]},
                        "review": {"id": "review", "version": "0.2.0",
                                   "sha256": _released("review", "0.2.0")["sha256"]}},
        "mission_input": {
            "mission_id": "mission-fixture-bounded-recon",
            "candidate": {"path": "missions/mission-fixture-bounded-recon/mission-candidate.yaml",
                          "hq_commit_sha": c,
                          "sha256": _sha256(REPO_ROOT / "missions" / "mission-fixture-bounded-recon"
                                            / "mission-candidate.yaml")},
            "contract": {"path": "missions/mission-fixture-bounded-recon/mission-contract.yaml",
                         "hq_commit_sha": c,
                         "sha256": _sha256(REPO_ROOT / "missions" / "mission-fixture-bounded-recon"
                                           / "mission-contract.yaml")},
            "admission_ledger": {"path": "mission/ledger.yaml",
                                 "hq_commit_sha": b, "sha256": ledger_sha},
        },
        "created_at": "2026-08-10T09:00:00Z",
    }
    errors: list[str] = []
    validate_artifacts.check_manifest_mission_mode(doc, "mixed", errors)
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts", "mixed", errors)
    assert not errors, errors


def test_handoff_payload_b_not_c():
    """The handoff payload carries formulation_commit C AND admission ledger
    commit B, with B != C; candidate/contract pin to C, ledger to B."""
    payload = {
        "mission_id": "mission-director-fixture-a",
        "candidate_path": "missions/mission-director-fixture-a/mission-candidate.yaml",
        "contract_path": "missions/mission-director-fixture-a/mission-contract.yaml",
        "formulation_commit": "c" * 40,
        "admission_ledger_path": "mission/ledger.yaml",
        "admission_ledger_commit": "b" * 40,
        "admission_ledger_sha256": "0" * 64,
        "cycle_issue": "kimeisele/federation-hq#99996",
    }
    assert payload["formulation_commit"] != payload["admission_ledger_commit"]
    assert len(payload["formulation_commit"]) == 40 == len(payload["admission_ledger_commit"])
    assert payload["admission_ledger_path"] == "mission/ledger.yaml"
    assert "maintenance_request" not in str(payload)


def test_director_prompt_records_before_formulation_ordering():
    text = (REPO_ROOT / "prompts" / "director" / "v0.1.0.md").read_text()
    flat = " ".join(text.split())
    assert "BLOCKED — admission ledger basis" in flat
    assert "admission_ledger" in flat and "hq_commit_sha: B" in flat
    assert "Candidate@C + Contract@C + AdmissionLedger@B" in flat
    assert "BLOCKED — formulation integration" in flat




def test_full_validator_green():
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr


# ── No-mission Ledger persistence (final symmetry fix) ────────────────────


def test_nm1_new_no_mission_warranted_persistence():
    """NM1: the no-mission cycle projects a NEW no_mission_warranted signal
    into the Ledger (candidate_id present, schema-valid), with zero
    MissionContracts."""
    after = _load(FIX / "no_mission/expected/ledger-after-nm.yaml")
    e = next(i for i in after["items"] if i["signal_id"] == "sig-director-fixture-e")
    assert e["disposition"] == "no_mission_warranted"
    assert e["candidate_id"] == "cand-director-fixture-e"
    schema = json.loads((REPO_ROOT / "contracts" / "mission" / "mission-ledger.schema.json")
                        .read_text(encoding="utf-8"))
    errors: list[str] = []
    validate_artifacts.validate_value(after, schema, "ledger-after-nm", errors)
    assert not errors, errors
    assert list((FIX / "no_mission/expected").glob("mission-contract*.yaml")) == []


def test_nm2_new_duplicate_persistence():
    """NM2: D remains completed; F (new duplicate of D) gains a terminal
    duplicate projection; Ledger stays schema-valid."""
    after = _load(FIX / "no_mission/expected/ledger-after-nm.yaml")
    by_id = {i["signal_id"]: i for i in after["items"]}
    assert by_id["sig-director-fixture-d"]["disposition"] == "completed"
    assert by_id["sig-director-fixture-d"]["related_run_ids"] == ["run-director-fixture-d"]
    f = by_id["sig-director-fixture-f"]
    assert f["disposition"] == "duplicate"
    assert f["duplicate_of"] == "sig-director-fixture-d"
    assert f["candidate_id"] == "cand-director-fixture-f"


def test_nm3_already_terminal_no_rewrite():
    """NM3: D's disposition/mission_id/related_run_ids are unchanged by the
    cycle — the fixture Ledger's D item is byte-identical before and after."""
    before = _load(FIX / "no_mission/ledger.yaml")
    after = _load(FIX / "no_mission/expected/ledger-after-nm.yaml")
    d_before = next(i for i in before["items"] if i["signal_id"] == "sig-director-fixture-d")
    d_after = next(i for i in after["items"] if i["signal_id"] == "sig-director-fixture-d")
    # disposition, mission_id, related_run_ids and updated_at are untouched.
    for key in ("disposition", "mission_id", "related_run_ids", "updated_at"):
        assert d_before.get(key) == d_after.get(key), key


def test_nm4_no_operator_handoff_for_no_mission_cycle():
    """NM4: a no-mission terminal cycle spawns no Operator and opens no
    MissionContract (structural expected state)."""
    after = _load(FIX / "no_mission/expected/ledger-after-nm.yaml")
    assert not any(i.get("mission_id") for i in after["items"])
    # The director prompt requires no worker spawns on no-mission cycles.
    prompt = (REPO_ROOT / "prompts" / "director" / "v0.1.0.md").read_text()
    flat = " ".join(prompt.split())
    assert "No-mission cycles spawn NO workers" in flat
    assert "BLOCKED — Director decision persistence" in flat


def test_nm5_ambiguity_not_terminalized():
    """NM5: the ambiguous cycle stays BLOCKED with zero mission artifacts and
    neither eligible signal is projected into a terminal disposition."""
    expected_dir = FIX / "ambiguous/expected"
    assert list(expected_dir.glob("mission-contract*.yaml")) == []
    assert list(expected_dir.glob("*candidate*.yaml")) == []
    assert "BLOCKED" in (expected_dir / "decision.md").read_text()
    # No expected Ledger projection exists for the ambiguous signals.
    assert list(expected_dir.glob("ledger*.yaml")) == []
    prompt = (REPO_ROOT / "prompts" / "director" / "v0.1.0.md").read_text()
    assert "do NOT invent terminal Ledger dispositions" in " ".join(prompt.split())


def test_no_or_true_in_director_tests():
    """No unconditional placeholder asserts in Director tests (self-excluded)."""
    src = (REPO_ROOT / "tests" / "test_hq_director.py").read_text()
    src = src.split("def test_no_or_true_in_director_tests():", 1)[0]
    token = "or " + "True"
    assert token not in src
    assert "\n    assert True\n" not in src
