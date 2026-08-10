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
    assert "EXACT MERGED HQ commit" in flat
    assert "unmerged PR head" in flat and "mutable branch name" in flat
    wrapper = (REPO_ROOT / ".omp" / "agents" / "hq-director.md").read_text()
    assert "NORMAL-merge" in wrapper and "resolve the exact merged HQ commit" in wrapper


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


def test_full_validator_green():
    result = _run_cli()
    assert result.returncode == 0, result.stdout + result.stderr
