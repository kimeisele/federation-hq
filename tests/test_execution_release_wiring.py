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


def _director_handoff() -> dict:
    """Model the director@0.1.1 execution_prompt_pins: resolved from the
    canonical registry and verified against exact prompt bytes — the real
    runtime source of the Operator's required inputs."""
    pins = {}
    for pid, ver in [("operator", "0.3.1"), ("scout", "0.2.0"),
                     ("repair", "0.2.1"), ("review", "0.2.1")]:
        entry = _released(pid, ver)
        assert entry["status"] == "released", pid
        actual = _sha256(PROMPTS / entry["file"])
        assert actual == entry["sha256"], pid
        pins[pid] = {"id": pid, "version": ver, "sha256": actual}
    return {"execution_prompt_pins": pins}


def _manifest_from_handoff(handoff: dict) -> dict:
    """The Run Manifest's prompt_pins COPY the supplied handoff pins exactly
    (handoff.operator -> manifest.prompt_pins.operator, etc.). Any
    substitution to another version would break W3/W4 identity."""
    return {
        "kind": "federation_hq_run_manifest",
        "run_id": "run-test-release-wiring",
        "pipeline_state": "requested",
        "prompt_pins": {pid: dict(pin)
                        for pid, pin in handoff["execution_prompt_pins"].items()},
    }


def test_new_run_manifest_copies_director_handoff_pins():
    """A NEW run's manifest pins are the EXACT Director handoff pins
    (operator 0.3.1 / scout 0.2.0 / repair 0.2.1 / review 0.2.1), copied —
    not independently selected."""
    handoff = _director_handoff()
    manifest = _manifest_from_handoff(handoff)
    pins = manifest["prompt_pins"]
    assert pins["operator"]["version"] == "0.3.1"
    assert pins["scout"]["version"] == "0.2.0"
    assert pins["repair"]["version"] == "0.2.1"
    assert pins["review"]["version"] == "0.2.1"
    # no substitution to historical releases
    assert pins["operator"]["version"] != "0.3.0"
    assert pins["repair"]["version"] != "0.2.0"
    assert pins["review"]["version"] != "0.2.0"


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


# ── Pin handoff continuity (final review fix, Issue #41) ────────────────────


def test_w1_director_handoff_requires_all_four_pins():
    """director@0.1.1 must require handoff of the exact four execution pins
    with exact SHA verification, and BLOCK on any pin failure."""
    text = _flat(_prompt("director", "0.1.1"))
    assert "execution_prompt_pins" in text
    assert "operator: 0.3.1" in text
    assert "scout: 0.2.0" in text
    assert "repair: 0.2.1" in text
    assert "review: 0.2.1" in text
    assert "BLOCKED — execution release pins" in text
    assert "status: released" in text
    assert "registry SHA-256 must equal the exact bytes" in text
    assert '"latest"' in text and "do NOT substitute another release" in text


def test_w2_operator_wrapper_consumes_not_chooses():
    text = _flat((OMP / "hq-operator.md").read_text())
    low = text.lower()
    assert "execution_prompt_pins" in text
    assert "BLOCKED — execution release pins" in text
    assert "must not choose versions yourself" in low
    assert "resolve \"latest\"" in low
    assert "copy the supplied pins" in low
    assert "current latest" in low


def test_w3_handoff_to_manifest_pin_identity():
    """Manifest prompt_pins are BYTE/VALUE-IDENTICAL to the Director handoff
    pins for all four roles; substitution (e.g. 0.2.0/0.3.0) breaks this."""
    handoff = _director_handoff()
    manifest = _manifest_from_handoff(handoff)
    for pid in ("operator", "scout", "repair", "review"):
        supplied = handoff["execution_prompt_pins"][pid]
        recorded = manifest["prompt_pins"][pid]
        assert recorded == supplied, pid
        assert recorded["id"] == pid and recorded["version"] == supplied["version"]
        assert recorded["sha256"] == supplied["sha256"]


def test_w4_prompt_hash_chain_exact():
    """registry SHA == actual prompt byte SHA == handoff SHA == manifest SHA
    for every supplied pin."""
    handoff = _director_handoff()
    manifest = _manifest_from_handoff(handoff)
    for pid in ("operator", "scout", "repair", "review"):
        entry = _released(pid, manifest["prompt_pins"][pid]["version"])
        chain = [
            entry["sha256"],
            _sha256(PROMPTS / entry["file"]),
            handoff["execution_prompt_pins"][pid]["sha256"],
            manifest["prompt_pins"][pid]["sha256"],
        ]
        assert len(set(chain)) == 1, (pid, chain)


def test_w5_historical_run_untouched():
    """Pilot #37's manifest remains pinned to its historical releases."""
    manifest = yaml.safe_load((REPO_ROOT / "runs"
        / "run-20260810-agent-city-brainvoice-fact-checking-recon"
        / "run-manifest.yaml").read_text(encoding="utf-8"))
    pins = manifest["prompt_pins"]
    assert pins["operator"]["version"] == "0.3.0"
    assert pins["scout"]["version"] == "0.2.0"
    assert pins["repair"]["version"] == "0.2.0"
    assert pins["review"]["version"] == "0.2.0"
    for pid in ("operator", "scout", "repair", "review"):
        entry = _released(pid, pins[pid]["version"])
        assert pins[pid]["sha256"] == entry["sha256"] == _sha256(PROMPTS / entry["file"]), pid


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
