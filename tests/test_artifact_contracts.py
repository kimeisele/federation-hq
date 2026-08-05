"""Focused tests for the artifact contracts and scripts/validate_artifacts.py.

Covers: registry integrity (unique ids/versions, file existence, changelog
rationale), schema conformance of the examples, rejection of missing required
SHA / repository / prompt-version / verdict fields, path-escape rejection, and
the end-to-end CLI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CONTRACTS = REPO_ROOT / "contracts"
EXAMPLES = REPO_ROOT / "examples"

sys.path.insert(0, str(SCRIPTS))
import validate_artifacts  # noqa: E402

SCHEMA_FILES = {
    "run-manifest": CONTRACTS / "run-manifest.schema.json",
    "repair-candidate": CONTRACTS / "repair-candidate.schema.json",
    "repair-result": CONTRACTS / "repair-result.schema.json",
    "review-result": CONTRACTS / "review-result.schema.json",
}

EXAMPLE_FILES = {
    "run-manifest": EXAMPLES / "run-manifest.example.yaml",
    "repair-candidate": EXAMPLES / "repair-candidate.example.yaml",
    "repair-result": EXAMPLES / "repair-result.example.yaml",
    "review-result": EXAMPLES / "review-result.example.yaml",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _schema(kind: str) -> dict:
    return json.loads(SCHEMA_FILES[kind].read_text(encoding="utf-8"))


def _errors_for(kind: str, doc: dict) -> list[str]:
    errors: list[str] = []
    validate_artifacts.validate_value(doc, _schema(kind), kind, errors)
    return errors


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_artifacts.py"), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


# ── Registry integrity ────────────────────────────────────────────────────


def test_registry_and_examples_validate_end_to_end() -> None:
    result = _run_cli()
    assert result.returncode == 0, result.stderr


def test_registry_unique_ids_and_versions() -> None:
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    ids = [p["id"] for p in registry["prompts"]]
    assert len(ids) == len(set(ids)), f"duplicate prompt ids: {ids}"
    for entry in registry["prompts"]:
        versions = [v["version"] for v in entry["versions"]]
        assert len(versions) == len(set(versions)), f"duplicate versions for {entry['id']}"


def test_registry_referenced_prompt_files_exist() -> None:
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    for entry in registry["prompts"]:
        for version in entry["versions"]:
            path = REPO_ROOT / "prompts" / version["file"]
            assert path.exists(), f"registry references missing file: {version['file']}"
            assert path.is_file()


def test_registry_rejects_duplicate_prompt_id(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "schema_version: 1\n"
        "prompts:\n"
        "  - id: scout\n"
        "    versions:\n"
        "      - version: 0.1.0\n"
        "        file: scout/v0.1.0.md\n"
        "        released: '2026-08-05'\n"
        "        changelog: first\n"
        "  - id: scout\n"
        "    versions:\n"
        "      - version: 0.2.0\n"
        "        file: scout/v0.2.0.md\n"
        "        released: '2026-08-05'\n"
        "        changelog: second\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("duplicate prompt id" in e for e in errors)


def test_registry_rejects_duplicate_version(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "schema_version: 1\n"
        "prompts:\n"
        "  - id: scout\n"
        "    versions:\n"
        "      - version: 0.1.0\n"
        "        file: scout/v0.1.0.md\n"
        "        released: '2026-08-05'\n"
        "        changelog: first\n"
        "      - version: 0.1.0\n"
        "        file: scout/v0.1.0.md\n"
        "        released: '2026-08-05'\n"
        "        changelog: second\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("duplicate version" in e for e in errors)


def test_registry_rejects_missing_prompt_file(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "schema_version: 1\n"
        "prompts:\n"
        "  - id: scout\n"
        "    versions:\n"
        "      - version: 0.1.0\n"
        "        file: scout/v0.1.0.md\n"
        "        released: '2026-08-05'\n"
        "        changelog: first\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("does not exist" in e for e in errors)


def test_registry_rejects_missing_changelog(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "schema_version: 1\n"
        "prompts:\n"
        "  - id: scout\n"
        "    versions:\n"
        "      - version: 0.1.0\n"
        "        file: scout/v0.1.0.md\n"
        "        released: '2026-08-05'\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("missing required field 'changelog'" in e for e in errors)


# ── Schema conformance: examples are valid ────────────────────────────────


@pytest.mark.parametrize("kind", ["run-manifest", "repair-candidate", "repair-result", "review-result"])
def test_example_validates_against_schema(kind: str) -> None:
    doc = _load(EXAMPLE_FILES[kind])
    assert _errors_for(kind, doc) == []


@pytest.mark.parametrize("kind", ["run-manifest", "repair-candidate", "repair-result", "review-result"])
def test_example_kind_matches_schema(kind: str) -> None:
    doc = _load(EXAMPLE_FILES[kind])
    expected = {
        "run-manifest": "federation_hq_run_manifest",
        "repair-candidate": "federation_hq_repair_candidate",
        "repair-result": "federation_hq_repair_result",
        "review-result": "federation_hq_review_result",
    }[kind]
    assert doc["kind"] == expected


# ── Negative: missing required fields ─────────────────────────────────────


def test_rejects_missing_baseline_sha() -> None:
    doc = _load(EXAMPLE_FILES["repair-candidate"])
    del doc["baseline_sha"]
    errors = _errors_for("repair-candidate", doc)
    assert any("missing required field 'baseline_sha'" in e for e in errors)


def test_rejects_missing_target_repository() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["target_repository"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'target_repository'" in e for e in errors)


def test_rejects_missing_prompt_version() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["prompt_pins"]["repair"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'repair'" in e for e in errors)


def test_rejects_missing_verdict() -> None:
    doc = _load(EXAMPLE_FILES["review-result"])
    del doc["verdict"]
    errors = _errors_for("review-result", doc)
    assert any("missing required field 'verdict'" in e for e in errors)


def test_rejects_missing_repair_head_sha() -> None:
    doc = _load(EXAMPLE_FILES["repair-result"])
    del doc["repair_head_sha"]
    errors = _errors_for("repair-result", doc)
    assert any("missing required field 'repair_head_sha'" in e for e in errors)


def test_rejects_wrong_kind() -> None:
    doc = _load(EXAMPLE_FILES["review-result"])
    doc["kind"] = "federation_hq_repair_result"
    errors = _errors_for("review-result", doc)
    assert any("expected const" in e for e in errors)


def test_rejects_unknown_prompt_version_pin(tmp_path: Path) -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["prompt_pins"]["repair"]["version"] = "9.9.9"
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, {"prompts": []}, "run-manifest", errors)
    assert any("no released prompt" in e for e in errors)


# ── Negative: path escape ─────────────────────────────────────────────────


def test_rejects_traversal_evidence_location() -> None:
    doc = _load(EXAMPLE_FILES["repair-candidate"])
    doc["evidence_locations"] = ["../outside.txt"]
    errors: list[str] = []
    validate_artifacts.check_paths(doc, REPO_ROOT, errors)
    assert any("may escape the repository" in e for e in errors)


def test_rejects_absolute_evidence_location() -> None:
    doc = _load(EXAMPLE_FILES["review-result"])
    doc["evidence_checked"] = ["/etc/passwd"]
    errors: list[str] = []
    validate_artifacts.check_paths(doc, REPO_ROOT, errors)
    assert any("may escape the repository" in e for e in errors)


def test_rejects_windows_absolute_evidence_location() -> None:
    doc = _load(EXAMPLE_FILES["repair-result"])
    doc["evidence_locations"] = ["C:\\Users\\evil\\outside.txt"]
    errors: list[str] = []
    validate_artifacts.check_paths(doc, REPO_ROOT, errors)
    assert any("may escape the repository" in e for e in errors)


def test_repository_checkout_external_path_is_allowed() -> None:
    # repository_checkout is the local path of the target checkout and is
    # explicitly external to Federation HQ; it must not trip escape checks.
    doc = _load(EXAMPLE_FILES["repair-candidate"])
    doc["repository_checkout"] = "/absolutely/outside/the/repo"
    errors: list[str] = []
    validate_artifacts.check_paths(doc, REPO_ROOT, errors)
    assert errors == []


def test_escape_risk_utility() -> None:
    assert validate_artifacts.is_escape_risk("../escape")
    assert validate_artifacts.is_escape_risk("/etc/passwd")
    assert validate_artifacts.is_escape_risk("C:\\windows")
    assert not validate_artifacts.is_escape_risk("runs/run-1/manifest.yaml")
    assert not validate_artifacts.is_escape_risk("evidence/notes.txt")


# ── CLI end to end ────────────────────────────────────────────────────────


def test_cli_validates_single_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "repair-candidate.yaml"
    artifact.write_text((EXAMPLES / "repair-candidate.example.yaml").read_text(encoding="utf-8"))
    result = _run_cli("--artifact", str(artifact))
    assert result.returncode == 0, result.stderr


def test_cli_rejects_bad_single_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "repair-result.yaml"
    artifact.write_text(
        "kind: federation_hq_repair_result\n"
        "result_id: r1\n"
        "target_repository: acme/widget-service\n"
        "baseline_sha: '9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e'\n"
        "created_at: '2026-08-05T14:40:00Z'\n"
    )
    result = _run_cli("--artifact", str(artifact))
    assert result.returncode == 1
    assert "missing required field" in result.stderr


def test_cli_rejects_unknown_artifact_filename(tmp_path: Path) -> None:
    artifact = tmp_path / "mystery-file.yaml"
    artifact.write_text("kind: anything\n")
    result = _run_cli("--artifact", str(artifact))
    assert result.returncode == 1
    assert "no schema matches this filename" in result.stderr


def test_cli_rejects_escaping_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "repair-candidate.yaml"
    artifact.write_text(
        "kind: federation_hq_repair_candidate\n"
        "candidate_id: c1\n"
        "run_id: run-1\n"
        "target_repository: acme/widget-service\n"
        "baseline_sha: '9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e'\n"
        "defect_description: test\n"
        "selected_by: scout\n"
        "created_at: '2026-08-05T10:02:00Z'\n"
        "evidence_locations:\n"
        "  - ../../../etc/shadow\n"
    )
    result = _run_cli("--artifact", str(artifact))
    assert result.returncode == 1
    assert "may escape the repository" in result.stderr
