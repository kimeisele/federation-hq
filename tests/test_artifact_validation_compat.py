"""Artifact validation compatibility regressions (post-benchmark Issue).

Defect A: mission_input manifests must be validated against MissionContract-
native COMPATIBILITY FLOORS (released versions at or above the floor), never
against one frozen exact current release tuple. Defect B: the Artifact
Contract Validation workflow must provide full history (fetch-depth: 0) so
historical immutable pins resolve.

Separation preserved: current runtime release selection lives in
director@0.1.1 -> execution_prompt_pins, never in the validator. The
validator asks only: are the exact recorded releases actually released,
hash-correct, and compatible with mission_input semantics?
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
PROMPTS = REPO_ROOT / "prompts"

sys.path.insert(0, str(SCRIPTS))
import validate_artifacts  # noqa: E402


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _released(pid: str, version: str) -> dict:
    reg = _load(PROMPTS / "registry.yaml")
    for entry in reg["prompts"]:
        if entry["id"] == pid:
            for v in entry["versions"]:
                if v["version"] == version:
                    return v
    raise AssertionError(f"{pid}@{version} missing from registry")


def _mission_manifest(pins: dict[str, str]) -> dict:
    """A mission_input manifest with the given {role: version} pins, using
    exact released hashes for the named versions."""
    prompt_pins = {}
    for pid, version in pins.items():
        entry = _released(pid, version)
        prompt_pins[pid] = {"id": pid, "version": version, "sha256": entry["sha256"]}
    return {
        "kind": "federation_hq_run_manifest",
        "run_id": "run-test-compat",
        "target_repository": "kimeisele/agent-city",
        "baseline_sha": "f" * 40,
        "coordination": {"protocol_version": "0.1.0", "issue_number": 1,
                         "issue_url": "https://github.com/kimeisele/federation-hq/issues/1"},
        "pipeline_state": "requested",
        "prompt_pins": prompt_pins,
        "mission_input": {"mission_id": "mission-fixture-bounded-recon"},
        "created_at": "2026-08-11T09:00:00Z",
    }


def _mode_errors(doc: dict) -> list[str]:
    errors: list[str] = []
    validate_artifacts.check_manifest_mission_mode(doc, "test", errors)
    return errors


# ── Semver parsing (exactly MAJOR.MINOR.PATCH numeric) ────────────────────


def test_parse_release_version_normal():
    assert validate_artifacts.parse_release_version("0.3.1") == (0, 3, 1)
    assert validate_artifacts.parse_release_version("1.2.3") == (1, 2, 3)
    assert validate_artifacts.parse_release_version("0.2.0") == (0, 2, 0)


def test_parse_release_version_rejects_non_normal_shapes():
    for bad in ("0.3", "0.3.1.2", "v0.3.1", "0.3.1-beta", "0.x.1", "a.b.c", None, 3, ""):
        assert validate_artifacts.parse_release_version(bad) is None, repr(bad)


# ── V1: historical MissionContract-native set passes ──────────────────────


def test_v1_historical_mission_native_set_valid():
    errors = _mode_errors(_mission_manifest(
        {"operator": "0.3.0", "scout": "0.2.0", "repair": "0.2.0", "review": "0.2.0"}))
    assert not errors, errors


# ── V2: current patch releases pass ───────────────────────────────────────


def test_v2_current_release_set_valid():
    errors = _mode_errors(_mission_manifest(
        {"operator": "0.3.1", "scout": "0.2.0", "repair": "0.2.1", "review": "0.2.1"}))
    assert not errors, errors


# ── V3: below-minimum releases rejected ───────────────────────────────────


def test_v3_below_minimum_releases_rejected():
    cases = [
        {"operator": "0.2.1", "scout": "0.2.0", "repair": "0.2.0", "review": "0.2.0"},
        {"operator": "0.3.0", "scout": "0.1.0", "repair": "0.2.0", "review": "0.2.0"},
        {"operator": "0.3.0", "scout": "0.2.0", "repair": "0.1.0", "review": "0.2.0"},
        {"operator": "0.3.0", "scout": "0.2.0", "repair": "0.2.0", "review": "0.1.0"},
    ]
    for pins in cases:
        errors = _mode_errors(_mission_manifest(pins))
        assert errors, pins
        joined = " ".join(errors)
        assert "MissionContract-native" in joined, pins
        assert "minimum" in joined, pins
        assert "exactly current" not in joined and "require operator@0.3.0" not in joined


def test_v3_unparseable_version_rejected():
    doc = _mission_manifest(
        {"operator": "0.3.1", "scout": "0.2.0", "repair": "0.2.1", "review": "0.2.1"})
    doc["prompt_pins"]["operator"]["version"] = "latest"
    errors = _mode_errors(doc)
    assert any("unparseable version" in e for e in errors), errors


# ── V4: registry/hash validation still enforced ───────────────────────────


def test_v4_compatible_version_bad_hash_rejected():
    """A floor-compatible version with a mismatched SHA is rejected by
    prompt-pin validation (registry release hash required)."""
    doc = _mission_manifest(
        {"operator": "0.3.1", "scout": "0.2.0", "repair": "0.2.1", "review": "0.2.1"})
    doc["prompt_pins"]["operator"]["sha256"] = "0" * 64
    errors: list[str] = []
    registry = _load(PROMPTS / "registry.yaml")
    validate_artifacts.check_prompt_pins(doc, registry, "test", errors)
    assert any("does not match registry release" in e for e in errors), errors


def test_v4_unreleased_version_rejected():
    """A floor-compatible version that is not a released registry version is
    rejected by prompt-pin validation."""
    doc = _mission_manifest(
        {"operator": "0.3.1", "scout": "0.2.0", "repair": "0.2.1", "review": "0.2.1"})
    doc["prompt_pins"]["repair"]["version"] = "9.9.9"
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, _load(PROMPTS / "registry.yaml"), "test", errors)
    assert any("no released prompt" in e for e in errors), errors


# ── V5/V6: real canonical manifests (Benchmark 01 + Pilot #37) ────────────


def _full_manifest_errors(rel: str) -> list[str]:
    path = REPO_ROOT / rel
    doc = _load(path)
    errors: list[str] = []
    registry = _load(PROMPTS / "registry.yaml")
    validate_artifacts.check_prompt_pins(doc, registry, path.name, errors)
    validate_artifacts.check_manifest_mission_mode(doc, path.name, errors)
    validate_artifacts.check_mission_pin(doc, REPO_ROOT, REPO_ROOT / "contracts",
                                         path.name, errors)
    return errors


def test_v5_benchmark01_manifest_valid():
    rel = "runs/run-20260811-agent-city-state-reliability-hot-reload-recon/run-manifest.yaml"
    doc = _load(REPO_ROOT / rel)
    pins = doc["prompt_pins"]
    assert (pins["operator"]["version"], pins["scout"]["version"],
            pins["repair"]["version"], pins["review"]["version"]) == \
        ("0.3.1", "0.2.0", "0.2.1", "0.2.1")
    assert _full_manifest_errors(rel) == []


def test_v6_pilot37_manifest_valid_unchanged():
    rel = "runs/run-20260810-agent-city-brainvoice-fact-checking-recon/run-manifest.yaml"
    doc = _load(REPO_ROOT / rel)
    pins = doc["prompt_pins"]
    assert (pins["operator"]["version"], pins["scout"]["version"],
            pins["repair"]["version"], pins["review"]["version"]) == \
        ("0.3.0", "0.2.0", "0.2.0", "0.2.0")
    assert _full_manifest_errors(rel) == []


# ── V7: workflow history contract ─────────────────────────────────────────


def test_v7_artifact_validation_workflow_fetch_depth_zero():
    wf = (REPO_ROOT / ".github" / "workflows" / "artifact-validation.yml").read_text()
    assert "actions/checkout@v4" in wf
    assert "fetch-depth: 0" in wf


def test_full_validator_green_on_full_history():
    result = subprocess.run([sys.executable, str(SCRIPTS / "validate_artifacts.py")],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Federation HQ artifact validation OK" in result.stdout
