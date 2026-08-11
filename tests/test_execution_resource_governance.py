"""Mechanical tests for Execution Resource Governance v0.1 (Issue #39).

Covers ONLY the mechanical policy/helper boundaries — prompt markers, the
pressure preflight helper, registry pins, immutability of released bytes.
Deliberately does NOT unit-test subjective LLM judgment.

Primary acceptance case: the Pilot #37 model (19 deterministic baseline
failures + 22 Timeout failures; baseline replay of exact head failing IDs;
approved with environmental qualification; no second whole baseline suite;
blocked environment_resource_exhaustion when saturation prevents
classification).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
PROMPTS = REPO_ROOT / "prompts"
REGISTRY = PROMPTS / "registry.yaml"

sys.path.insert(0, str(SCRIPTS))
import check_execution_pressure as pressure  # noqa: E402


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _released(pid: str, version: str) -> dict:
    reg = _load(REGISTRY)
    for entry in reg["prompts"]:
        if entry["id"] == pid:
            for v in entry["versions"]:
                if v["version"] == version:
                    return v
    raise AssertionError(f"{pid}@{version} missing from registry")


def _prompt(pid: str, version: str) -> str:
    return (PROMPTS / pid / f"v{version}.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _flat_low(text: str) -> str:
    return " ".join(text.split()).lower()


# ── Pressure preflight helper (mechanical) ────────────────────────────────


def test_pressure_helper_output_shape():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_execution_pressure.py")],
        capture_output=True, text=True)
    lines = result.stdout.splitlines()
    assert lines[-1] in ("OK", "PRESSURED", "UNKNOWN")
    fields = dict(line.split("=", 1) for line in lines[:-1])
    assert "cpu_count" in fields and "threshold" in fields
    assert result.returncode in (0, 1, 2)


def test_pressure_helper_threshold_parsing(monkeypatch):
    monkeypatch.delenv("FHQ_HEAVY_LOAD_THRESHOLD", raising=False)
    assert pressure._threshold() == 4.0
    monkeypatch.setenv("FHQ_HEAVY_LOAD_THRESHOLD", "0.7")
    assert pressure._threshold() == 0.7
    monkeypatch.setenv("FHQ_HEAVY_LOAD_THRESHOLD", "0.001")  # below min -> default
    assert pressure._threshold() == 4.0
    monkeypatch.setenv("FHQ_HEAVY_LOAD_THRESHOLD", "banana")
    assert pressure._threshold() == 4.0


def test_pressure_helper_main_states(monkeypatch):
    monkeypatch.setattr(pressure, "_load_1m", lambda: None)
    assert pressure.main() == 1  # UNKNOWN
    monkeypatch.setattr(pressure, "_load_1m", lambda: 1.0)
    monkeypatch.setattr(pressure, "_cpu_count", lambda: 1)
    monkeypatch.setenv("FHQ_HEAVY_LOAD_THRESHOLD", "0.5")
    assert pressure.main() == 2  # normalized 1.0 >= 0.5 -> PRESSURED
    monkeypatch.setattr(pressure, "_load_1m", lambda: 1.0)
    monkeypatch.setattr(pressure, "_cpu_count", lambda: 4)
    monkeypatch.setenv("FHQ_HEAVY_LOAD_THRESHOLD", "4.0")
    assert pressure.main() == 0  # normalized 0.25 < 4.0 -> OK


def test_pressure_helper_unknown_does_not_block_focused_work():
    text = _flat((SCRIPTS / "check_execution_pressure.py").read_text())
    assert "UNKNOWN never blocks focused work" in text
    assert "OK" in text and "PRESSURED" in text and "UNKNOWN" in text


# ── Heavy command definition (documentation semantics) ────────────────────


def test_heavy_command_defined_in_prompts():
    for pid, ver in [("repair", "0.2.1"), ("review", "0.2.1")]:
        low = _flat_low(_prompt(pid, ver))
        assert "a command is heavy when it" in low, pid
        assert "repository-wide test suite" in low, pid
        assert "roughly 60 seconds" in low, pid
    low = _flat_low(_prompt("repair", "0.2.1"))
    assert "focused affected-component tests are normally not heavy" in low


# ── Repair verification budget ────────────────────────────────────────────


def test_repair_focused_first_and_broad_cap():
    low = _flat_low(_prompt("repair", "0.2.1"))
    assert "do not automatically run the full repository suite merely because it exists" in low
    assert "maximum broad/full-suite executions per repair attempt: **1**" in low
    assert "focused-first verification" in low


def test_repair_broad_suite_conditions():
    low = _flat_low(_prompt("repair", "0.2.1"))
    assert "repository governance explicitly requires it" in low
    assert "broad cross-cutting effects" in low
    assert "bounded checks cannot establish regression safety" in low


# ── Reviewer differential verification ────────────────────────────────────


def test_reviewer_not_required_to_rerun_every_command():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "not required to rerun every command" in low
    assert "independently verify every material semantic claim" in low
    assert "cheapest sufficient evidence path" in low


def test_reviewer_differential_baseline_replay_only_failing_ids():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "at most one broad/full suite at head" in low
    assert "replay on the exact baseline only the head failing test ids" in low
    assert "do not automatically execute the entire full suite at baseline" in low
    # Pilot #37 model: baseline replay of exact suspicious IDs -> pre-existing
    assert "pre-existing" in low and "candidate newly-introduced failures" in low


def test_reviewer_broad_suite_cap():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "default maximum: **1** broad/full-suite run per review attempt" in low
    assert "absolute maximum: **2**" in low
    assert "never a third" in low
    assert "state the concrete unresolved question before launching it" in low


def test_reviewer_independence_intact():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "must independently verify every material semantic claim" in low
    assert "ci is evidence, not semantic reviewer authority" in low
    # Independence is not weakened into trusting Repair claims.
    assert "must" in low and "repair_result" in _flat_low(_prompt("review", "0.2.1"))


# ── Retry circuit breaker ─────────────────────────────────────────────────


def test_heavy_retry_circuit_breaker_zero_identical_retries():
    for pid, ver in [("repair", "0.2.1"), ("review", "0.2.1")]:
        low = _flat_low(_prompt(pid, ver))
        assert "0 identical retries" in low, pid
        assert "timeout, sigterm" in low, pid
        assert '"no output yet" is not a reason' in low, pid
        assert "environment_resource_exhaustion" in low, pid


def test_retry_requires_concrete_environment_recovery():
    low = _flat_low(_prompt("repair", "0.2.1"))
    assert "concrete evidence the execution environment materially recovered or changed" in low
    assert "record why" in low


# ── Host pressure preflight wiring ────────────────────────────────────────


def test_pressure_preflight_referenced_before_heavy_commands():
    for pid, ver in [("repair", "0.2.1"), ("review", "0.2.1")]:
        low = _flat_low(_prompt(pid, ver))
        assert "check_execution_pressure.py" in low, pid
        assert "pressured" in low and "unknown does not block focused work" in low, pid


# ── Concurrent heavy work bound (Operator) ────────────────────────────────


def test_operator_concurrent_heavy_bound():
    low = _flat_low(_prompt("operator", "0.3.1"))
    assert "max concurrent heavy verification commands per run: 1" in low
    assert "endless re-dispatch" in low


def test_operator_resource_exhaustion_terminalizes_blocked():
    low = _flat_low(_prompt("operator", "0.3.1"))
    assert "environment_resource_exhaustion" in low
    assert "record `blocked` with the concrete environmental cause" in low
    # Operator still holds no semantic review authority (coordination only).
    low03 = _flat_low(_prompt("operator", "0.3.0"))
    assert "coordinator" in low03 and "superior engineering authority" in low03


# ── Environmental failure classification ──────────────────────────────────


def test_environmental_classification_evidence_gated():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "deterministic regression from environment-induced" in low
    assert "head and baseline under comparable pressure" in low
    assert "terminated externally" in low
    assert "blocked" in low and "environment_resource_exhaustion" in low


def test_no_infinite_retry_to_avoid_blocked():
    low = _flat_low(_prompt("review", "0.2.1"))
    assert "retry indefinitely to avoid a blocked verdict" in low
    low_rep = _flat_low(_prompt("repair", "0.2.1"))
    assert "do not repeatedly rerun it hoping for prettier output" in low_rep


# ── No universal full-suite rule (ADR-0005) ───────────────────────────────


def test_adr_documents_no_universal_full_suite_rule():
    adr = _flat_low((REPO_ROOT / "docs" / "decisions"
                     / "ADR-0005-execution-resource-governance-v0.1.md").read_text())
    assert "no universal full-suite rule" in adr
    assert "verification depth is proportional to repair scope" in adr
    assert "environment_resource_exhaustion" in adr
    assert "retry circuit breaker" in adr and "host pressure preflight" in adr
    assert "differential" in adr


# ── Registry integrity for the successors ─────────────────────────────────


def test_successors_released_with_exact_hashes():
    for pid, ver in [("repair", "0.2.1"), ("review", "0.2.1"), ("operator", "0.3.1")]:
        entry = _released(pid, ver)
        actual = hashlib.sha256((PROMPTS / entry["file"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"], (pid, ver)
        assert entry["status"] == "released"
        assert "Issue #39" in entry["changelog"], (pid, ver)


def test_released_original_bytes_immutable():
    """The 0.2.0/0.3.0 bytes remain pinned; successors do not mutate them."""
    for pid, ver in [("repair", "0.2.0"), ("review", "0.2.0"), ("operator", "0.3.0")]:
        entry = _released(pid, ver)
        actual = hashlib.sha256((PROMPTS / entry["file"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"], (pid, ver)


def test_successors_are_minimal_additive():
    """Successors preserve the originals' content (one documented exception:
    the review 0.2.1 baseline allowed-action line, reworded to the
    differential strategy)."""
    exceptions = {
        ("review", "0.2.1"): {"- Re-running baseline commands at the baseline SHA to compare."},
    }
    for pid, old, new in [("repair", "0.2.0", "0.2.1"),
                          ("review", "0.2.0", "0.2.1"),
                          ("operator", "0.3.0", "0.3.1")]:
        orig = _prompt(pid, old)
        succ = _prompt(pid, new)
        succ_flat = "\n".join(ln for ln in succ.splitlines() if ln.strip())
        skip = exceptions.get((pid, new), set())
        missing = [ln for ln in orig.splitlines()
                   if ln.strip() and ln.strip() not in skip and ln.strip() not in succ_flat]
        assert not missing, (pid, missing[:5])
