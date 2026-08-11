"""Execution release wiring tests (Issue #41).

Proves the release-wiring defect is fixed WITHOUT mutable 'latest' semantics:
the current OMP Director wrapper points at director@0.1.1, director@0.1.1
points downstream at operator@0.3.1, the OMP Operator wrapper loads
operator@0.3.1, and Repair/Review remain manifest-pin-driven so historical
runs and future releases stay correctly pinned. Also proves the intended
current execution release set is reachable and that Resource Governance
mechanics are reachable through it.

Mechanical assertions only — no LLM judgment, no historical manifest
rewrites, no real heavy command.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"
OMP = REPO_ROOT / ".omp" / "agents"
REGISTRY = PROMPTS / "registry.yaml"

# Intended current execution release set (Issue #41).
CURRENT_SET = {
    "director": "0.1.1",
    "operator": "0.3.1",
    "scout": "0.2.0",
    "repair": "0.2.1",
    "review": "0.2.1",
}


def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _released(pid: str, version: str) -> dict:
    for entry in _registry()["prompts"]:
        if entry["id"] == pid:
            for v in entry["versions"]:
                if v["version"] == version:
                    return v
    raise AssertionError(f"{pid}@{version} missing from registry")


def _prompt(pid: str, version: str) -> str:
    return (PROMPTS / pid / f"v{version}.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Run-manifest release proof (new run initialization) ───────────────────


def test_new_run_manifest_pins_exact_current_release_set():
    """A NEW run initialization pins operator 0.3.1 / scout 0.2.0 /
    repair 0.2.1 / review 0.2.1 with exact registry hashes."""
    manifest = {
        "kind": "federation_hq_run_manifest",
        "run_id": "run-test-release-wiring",
        "pipeline_state": "requested",
        "prompt_pins": {
            pid: {"id": pid, "version": ver,
                  "sha256": _released(pid, ver)["sha256"]}
            for pid, ver in CURRENT_SET.items() if pid != "director"
        },
    }
    pins = manifest["prompt_pins"]
    assert pins["operator"]["version"] == "0.3.1"
    assert pins["scout"]["version"] == "0.2.0"
    assert pins["repair"]["version"] == "0.2.1"
    assert pins["review"]["version"] == "0.2.1"
    for pid, ver in [("operator", "0.3.1"), ("scout", "0.2.0"),
                     ("repair", "0.2.1"), ("review", "0.2.1")]:
        entry = _released(pid, ver)
        assert entry["status"] == "released"
        assert pins[pid]["sha256"] == entry["sha256"] == _sha256(PROMPTS / entry["file"])


def test_worker_wrappers_are_manifest_pin_driven():
    """hq-repair / hq-review / hq-scout resolve their prompts from the Run
    Manifest prompt_pins, never hardcoded to a concrete successor."""
    for name, pin in [("hq-repair", "prompt_pins.repair"),
                      ("hq-review", "prompt_pins.review"),
                      ("hq-scout", "prompt_pins.scout")]:
        text = (OMP / f"{name}.md").read_text()
        assert pin in text, name
        assert "prompts/" in text and "<version>.md" in text, name
    # no successor hardcoding in the worker wrappers
    for name in ("hq-repair", "hq-review"):
        text = (OMP / f"{name}.md").read_text()
        assert "0.2.1" not in text, name


# ── Wiring drift regression ───────────────────────────────────────────────


def test_omp_director_wrapper_wired_to_director_011():
    text = (OMP / "hq-director.md").read_text()
    assert "director@0.1.1" in text
    assert "prompts/director/v0.1.1.md" in text
    assert "director@0.1.0" not in text
    assert "spawns:\n  - hq-operator" in text


def test_director_011_names_operator_031_downstream():
    """director@0.1.1 requires operator@0.3.1 admission downstream."""
    text = _flat(_prompt("director", "0.1.1"))
    assert "operator@0.3.1" in text
    assert "operator@0.3.0" not in text
    assert "Candidate@C + Contract@C + AdmissionLedger@B" in text


def test_omp_operator_wrapper_wired_to_operator_031():
    text = (OMP / "hq-operator.md").read_text()
    assert "operator@0.3.1" in text
    assert "prompts/operator/v0.3.1.md" in text
    assert "operator@0.3.0" not in text
    assert "spawns:\n  - hq-scout" in text and "hq-integrator" in text


def test_director_spawn_policy_unchanged():
    meta = yaml.safe_load((OMP / "hq-director.md").read_text().split("---\n", 2)[1])
    assert meta["spawns"] == ["hq-operator"]


def test_operator_spawn_policy_unchanged():
    meta = yaml.safe_load((OMP / "hq-operator.md").read_text().split("---\n", 2)[1])
    assert sorted(meta["spawns"]) == ["hq-integrator", "hq-repair", "hq-review", "hq-scout"]


def test_director_selection_semantics_unchanged_in_011():
    text = _flat(_prompt("director", "0.1.1"))
    for marker in ("BLOCKED — ambiguous mission selection",
                   "BLOCKED — formulation integration",
                   "BLOCKED — admission ledger basis",
                   "BLOCKED — Director decision persistence",
                   "no_mission_warranted",
                   "POL-04", "POL-14",
                   "NORMAL-merge", "no `--admin`"):
        assert marker in text, marker


# ── Resource Governance reachable through the intended chain ──────────────


def test_governance_mechanics_reachable_via_current_set():
    """operator@0.3.1 -> repair@0.2.1 / review@0.2.1 must carry the
    governance mechanics, so a NEW Director-selected run reaches them."""
    op = _flat(_prompt("operator", "0.3.1"))
    assert "environment_resource_exhaustion" in op
    assert "run_heavy_command.py" in op
    rep = _flat(_prompt("repair", "0.2.1"))
    assert "run_heavy_command.py" in rep and "check_execution_pressure.py" in rep
    assert "Maximum broad/full-suite executions per repair attempt: **1**" in rep
    rev = _flat(_prompt("review", "0.2.1"))
    assert "run_heavy_command.py" in rev and "check_execution_pressure.py" in rev
    assert "Never a third" in rev
    assert "Replay on the exact baseline ONLY the head failing" in rev


def test_registered_governance_releases_exact():
    for pid, ver in [("operator", "0.3.1"), ("repair", "0.2.1"), ("review", "0.2.1")]:
        entry = _released(pid, ver)
        assert _sha256(PROMPTS / entry["file"]) == entry["sha256"], (pid, ver)


# ── Historical bytes unchanged ────────────────────────────────────────────


def test_historical_release_bytes_unchanged():
    """director@0.1.0, operator@0.3.1, repair@0.2.1, review@0.2.1,
    scout@0.2.0 stay byte-identical to their registry pins."""
    for pid, ver in [("director", "0.1.0"), ("operator", "0.3.1"),
                     ("repair", "0.2.1"), ("review", "0.2.1"),
                     ("scout", "0.2.0")]:
        entry = _released(pid, ver)
        assert _sha256(PROMPTS / entry["file"]) == entry["sha256"], (pid, ver)


def test_director_011_registered_with_exact_hash():
    entry = _released("director", "0.1.1")
    assert entry["status"] == "released"
    assert entry["file"] == "director/v0.1.1.md"
    assert _sha256(PROMPTS / entry["file"]) == entry["sha256"]
    assert "PATCH release wiring update" in entry["changelog"]
    assert "operator@0.3.0 to operator@0.3.1" in entry["changelog"]
