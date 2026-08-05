"""Focused tests for the artifact contracts and scripts/validate_artifacts.py.

Covers: registry integrity (unique ids/versions, file existence, changelog
rationale), schema conformance of the examples, rejection of missing required
SHA / repository / prompt-version / verdict fields, path-escape rejection, and
the end-to-end CLI.
"""
from __future__ import annotations

import hashlib
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
    "coordination-message": CONTRACTS / "coordination-message.schema.json",
}

EXAMPLE_FILES = {
    "run-manifest": EXAMPLES / "run-manifest.example.yaml",
    "repair-candidate": EXAMPLES / "repair-candidate.example.yaml",
    "repair-result": EXAMPLES / "repair-result.example.yaml",
    "review-result": EXAMPLES / "review-result.example.yaml",
    "coordination-message": EXAMPLES / "coordination-message.example.yaml",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# 64 lowercase hex chars: a structurally valid but content-neutral sha256.
_ZERO64 = "0" * 64


REGISTRY_VERSION_HEADER = (
    "schema_version: 1\n"
    "prompts:\n"
)


def _version_block(pid: str, version: str, changelog: str) -> str:
    return (
        f"  - id: {pid}\n"
        "    versions:\n"
        f"      - version: {version}\n"
        f"        file: {pid}/v{version}.md\n"
        f"        sha256: \"{_ZERO64}\"\n"
        "        status: released\n"
        "        released: '2026-08-05'\n"
        f"        changelog: {changelog}\n"
    )


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
        REGISTRY_VERSION_HEADER
        + _version_block("scout", "0.1.0", "first")
        + _version_block("scout", "0.2.0", "second")
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
        f"      - version: 0.1.0\n        file: scout/v0.1.0.md\n        sha256: \"{_ZERO64}\"\n        status: released\n        released: '2026-08-05'\n        changelog: first\n"
        f"      - version: 0.1.0\n        file: scout/v0.1.0.md\n        sha256: \"{_ZERO64}\"\n        status: released\n        released: '2026-08-05'\n        changelog: second\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("duplicate version" in e for e in errors)


def test_registry_rejects_missing_prompt_file(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(REGISTRY_VERSION_HEADER + _version_block("scout", "0.1.0", "first"))
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
        f"        sha256: \"{_ZERO64}\"\n"
        "        status: released\n"
        "        released: '2026-08-05'\n"
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("missing required field 'changelog'" in e for e in errors)


def test_registry_sha256_matches_prompt_file_bytes() -> None:
    """Registry hashes must equal the exact UTF-8 bytes of the prompt files."""
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    import hashlib

    for entry in registry["prompts"]:
        for version in entry["versions"]:
            path = REPO_ROOT / "prompts" / version["file"]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert version["sha256"] == actual, (
                f"registry sha256 for {entry['id']}@{version['version']} is stale"
            )


def test_registry_rejects_sha256_not_matching_file(tmp_path: Path) -> None:
    """A registry hash that does not match the referenced file fails closed."""
    prompts_dir = tmp_path / "prompts" / "scout"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "v0.1.0.md").write_text("actual prompt content\n")
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        REGISTRY_VERSION_HEADER
        + _version_block("scout", "0.1.0", "first").replace(_ZERO64, "a" * 64)
    )
    errors: list[str] = []
    validate_artifacts.validate_registry(registry, tmp_path, errors)
    assert any("does not match prompt file bytes" in e for e in errors)


# ── Schema conformance: examples are valid ────────────────────────────────


@pytest.mark.parametrize("kind", ["run-manifest", "repair-candidate", "repair-result", "review-result", "coordination-message"])
def test_example_validates_against_schema(kind: str) -> None:
    doc = _load(EXAMPLE_FILES[kind])
    assert _errors_for(kind, doc) == []


@pytest.mark.parametrize("kind", ["run-manifest", "repair-candidate", "repair-result", "review-result", "coordination-message"])
def test_example_kind_matches_schema(kind: str) -> None:
    doc = _load(EXAMPLE_FILES[kind])
    expected = {
        "run-manifest": "federation_hq_run_manifest",
        "repair-candidate": "federation_hq_repair_candidate",
        "repair-result": "federation_hq_repair_result",
        "review-result": "federation_hq_review_result",
        "coordination-message": "federation_hq_coordination_message",
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


def test_rejects_missing_maintenance_request() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["maintenance_request"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'maintenance_request'" in e for e in errors)


def test_rejects_missing_pipeline_state() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["pipeline_state"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'pipeline_state'" in e for e in errors)


def test_rejects_non_iso8601_created_at() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["created_at"] = "08/05/2026 09:15"
    errors = _errors_for("run-manifest", doc)
    assert any("does not match pattern" in e for e in errors)


def test_rejects_maintenance_request_without_text() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["maintenance_request"]["text"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'text'" in e for e in errors)


def test_rejects_missing_prompt_pin_hash() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["prompt_pins"]["review"]["sha256"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'sha256'" in e for e in errors)


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


def test_rejects_mismatched_prompt_pin_hash() -> None:
    """A pin hash that does not match the registry release fails closed."""
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["prompt_pins"]["repair"]["sha256"] = "b" * 64
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, registry, "run-manifest", errors)
    assert any("does not match registry release" in e for e in errors)


def test_released_prompt_pin_accepted() -> None:
    """Pins to released registry versions with matching hashes are accepted."""
    doc = _load(EXAMPLE_FILES["run-manifest"])
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, registry, "run-manifest", errors)
    assert errors == []


def test_unreleased_bootstrap_prompt_rejected() -> None:
    """Pins to unreleased_bootstrap registry versions fail validation."""
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "id": "scout",
                "versions": [
                    {
                        "version": "0.1.0",
                        "file": "scout/v0.1.0.md",
                        "sha256": "c" * 64,
                        "status": "unreleased_bootstrap",
                        "released": "2026-08-05",
                        "changelog": "draft",
                    }
                ],
            }
        ],
    }
    doc = _load(EXAMPLE_FILES["run-manifest"])
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, registry, "run-manifest", errors)
    assert any("no released prompt" in e for e in errors)


def test_release_resolution_excludes_unreleased_status() -> None:
    """registry_release_hashes must include only status exactly 'released'."""
    registry = {
        "prompts": [
            {"id": "scout", "versions": [
                {"version": "0.1.0", "sha256": "a" * 64, "status": "released"},
                {"version": "0.2.0", "sha256": "b" * 64, "status": "unreleased_bootstrap"},
                {"version": "0.3.0", "sha256": "c" * 64, "status": "draft"},
            ]},
        ]
    }
    hashes = validate_artifacts.registry_release_hashes(registry)
    assert ("scout", "0.1.0") in hashes
    assert ("scout", "0.2.0") not in hashes
    assert ("scout", "0.3.0") not in hashes


# ── HQ Operator and coordination protocol ───────────────────────────────────


def test_operator_registry_entry_released_with_matching_hash() -> None:
    """operator@0.1.0 exists, is released, and its hash matches the file bytes."""
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    operator = next(e for e in registry["prompts"] if e["id"] == "operator")
    version = operator["versions"][0]
    assert version["version"] == "0.1.0"
    assert version["status"] == "released"
    path = REPO_ROOT / "prompts" / version["file"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert version["sha256"] == actual


def test_original_three_prompt_hashes_unchanged() -> None:
    """The three original released prompt files are byte-identical to their pins."""
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    expected = {
        "scout": "6940ea39da9799fa6595809cfce6a948427bdc07edba2e48774418642fe6eb8a",
        "repair": "0606d8ed7bec4501c10613d8e13b399325d6245b72f5d9212ea65e459f933f84",
        "review": "82b9cc37176304d418a934b261fa5efa2374d4e886c5afdec3789eaa264479e9",
    }
    for entry in registry["prompts"]:
        if entry["id"] in expected:
            version = entry["versions"][0]
            assert version["sha256"] == expected[entry["id"]], (
                f"{entry['id']} prompt bytes changed after release"
            )


def test_run_manifest_requires_operator_pin() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["prompt_pins"]["operator"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'operator'" in e for e in errors)


def test_run_manifest_requires_coordination_reference() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["coordination"]
    errors = _errors_for("run-manifest", doc)
    assert any("missing required field 'coordination'" in e for e in errors)


def test_run_manifest_rejects_invalid_coordination_issue_url() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["coordination"]["issue_url"] = "https://example.com/not-an-issue"
    errors = _errors_for("run-manifest", doc)
    assert any("does not match pattern" in e for e in errors)


def test_operator_pin_rejected_when_unreleased() -> None:
    """Only released Operator versions can be pinned."""
    registry = {
        "prompts": [
            {
                "id": "operator",
                "versions": [
                    {
                        "version": "0.1.0",
                        "file": "operator/v0.1.0.md",
                        "sha256": "d" * 64,
                        "status": "unreleased_bootstrap",
                        "released": "2026-08-05",
                        "changelog": "draft",
                    }
                ],
            }
        ],
    }
    doc = _load(EXAMPLE_FILES["run-manifest"])
    errors: list[str] = []
    validate_artifacts.check_prompt_pins(doc, registry, "run-manifest", errors)
    assert any("no released prompt" in e for e in errors)


def test_coordination_invalid_sender_role() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["sender_role"] = "scoutmaster"
    errors = _errors_for("coordination-message", doc)
    assert any("not in enum" in e for e in errors)


def test_coordination_invalid_recipient_role() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["recipient_role"] = "builder"
    errors = _errors_for("coordination-message", doc)
    assert any("not in enum" in e for e in errors)


def test_coordination_unknown_message_type() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["message_type"] = "ping"
    errors = _errors_for("coordination-message", doc)
    assert any("not in enum" in e for e in errors)


def test_coordination_malformed_repository() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["target_repository"] = "not-a-repository"
    errors = _errors_for("coordination-message", doc)
    assert any("does not match pattern" in e for e in errors)


def test_coordination_malformed_hq_commit_sha() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["artifact_ref"]["hq_commit_sha"] = "xyz"
    errors = _errors_for("coordination-message", doc)
    assert any("does not match pattern" in e for e in errors)


def test_coordination_malformed_artifact_hash() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["artifact_ref"]["sha256"] = "zz"
    errors = _errors_for("coordination-message", doc)
    assert any("does not match pattern" in e for e in errors)


def test_coordination_malformed_timestamp() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["created_at"] = "08/05/2026 19:50"
    errors = _errors_for("coordination-message", doc)
    assert any("does not match pattern" in e for e in errors)


def test_coordination_baseline_sha_nullable() -> None:
    """blocked messages may carry a null baseline_sha and null artifact_ref."""
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc["baseline_sha"] = None
    doc["artifact_ref"] = None
    assert _errors_for("coordination-message", doc) == []


# ── Issue templates ─────────────────────────────────────────────────────────

TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

EXPECTED_TEMPLATES = {"hq-run.md", "hq-change.md", "hq-defect.md"}


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) == 3, f"{path.name}: front matter not delimited by ---"
    assert parts[0].strip() == "", f"{path.name}: content before front matter"
    return yaml.safe_load(parts[1])


@pytest.mark.parametrize("name", sorted(EXPECTED_TEMPLATES))
def test_issue_template_front_matter_valid(name: str) -> None:
    path = TEMPLATE_DIR / name
    assert path.exists()
    fm = _front_matter(path)
    assert fm["name"].startswith("HQ ")
    assert fm["about"]
    assert fm["title"].startswith("[HQ ")
    assert fm["labels"] == []


def test_no_role_specific_issue_templates() -> None:
    """No separate Operator/Scout/Repair/Review templates exist."""
    present = {p.name for p in TEMPLATE_DIR.iterdir() if p.is_file()}
    for name in EXPECTED_TEMPLATES:
        assert name in present, f"missing template: {name}"
    for role in ("operator", "scout", "repair", "review"):
        assert not any(role in name for name in present), (
            f"role-specific Issue template present: {role}"
        )


def test_hq_run_template_has_required_sections() -> None:
    text = (TEMPLATE_DIR / "hq-run.md").read_text()
    for marker in (
        "Run ID",
        "Target repository",
        "Baseline SHA",
        "## Bounded maintenance request",
        "Source reference",
        "Pinned prompt releases",
        "HQ run path",
        "Current pipeline state",
        "Active role assignment",
        "Known baseline failures",
        "Constraints and stop conditions",
        "runs/<run-id>/",
        "operational coordination thread",
        "are canonical",
    ):
        assert marker in text, f"hq-run.md missing: {marker}"


def test_hq_change_template_has_invariants() -> None:
    text = (TEMPLATE_DIR / "hq-change.md").read_text()
    for marker in (
        "Affected canonical files",
        "Current behavior",
        "Proposed behavior",
        "Rationale and evidence",
        "Prompt-version impact",
        "Schema or contract impact",
        "Migration impact on existing runs",
        "Explicit non-goals",
        "Acceptance criteria",
        "not edited in place",
        "not inserted opportunistically",
        "Amend existing canonical documents",
        "not automatically authorized",
    ):
        assert marker in text, f"hq-change.md missing: {marker}"


def test_hq_defect_template_has_required_fields() -> None:
    text = (TEMPLATE_DIR / "hq-defect.md").read_text()
    for marker in (
        "Affected file, workflow, contract or renderer",
        "Exact HQ commit SHA",
        "Expected behavior",
        "Observed behavior",
        "Reproduction",
        "Command",
        "Exit code",
        "Evidence location",
        "Reproduces on `main`",
        "Impact on released prompts",
        "Impact on existing runs",
        "Minimal repair boundary",
        "No unrelated cleanup",
        "No mutation of released prompt files",
        "claims until reproduced",
    ):
        assert marker in text, f"hq-defect.md missing: {marker}"


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


# ── Committed run bundle coherence ──────────────────────────────────────────

BUNDLE_FILES = {
    "run-manifest": "run-manifest.yaml",
    "repair-candidate": "repair-candidate.yaml",
    "repair-result": "repair-result.yaml",
    "review-result": "review-result.yaml",
}


def _write_bundle(root: Path, kind: str = "coherent",
                  files: tuple[str, ...] = BUNDLE_FILES.keys()) -> Path:
    """Write a run bundle under *root*/runs/run-1; return the runs dir.

    *files* selects which artifact kinds to write (subset bundles model
    incomplete in-progress runs); *kind* mutates a document to model a
    specific coherence violation.
    """
    run_dir = root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    docs: dict[str, dict] = {}
    for kind_name in files:
        docs[kind_name] = _load(EXAMPLE_FILES[kind_name])
    if kind == "bad_run_id":
        docs["repair-result"]["run_id"] = "run-999"
    elif kind == "bad_candidate_chain":
        docs["repair-result"]["candidate_id"] = "other-candidate"
    elif kind == "bad_result_chain":
        docs["review-result"]["result_id"] = "other-result"
    elif kind == "bad_head":
        docs["review-result"]["reviewer_head_sha"] = "0" * 40
    elif kind == "bad_repo":
        docs["repair-result"]["target_repository"] = "other-org/other-repo"
    elif kind == "bad_baseline":
        docs["repair-candidate"]["baseline_sha"] = "1" * 40
    for kind_name in files:
        (run_dir / BUNDLE_FILES[kind_name]).write_text(
            yaml.safe_dump(docs[kind_name], sort_keys=False)
        )
    return root / "runs"


def _bundle_errors(runs_dir: Path, root: Path) -> list[str]:
    errors: list[str] = []
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    validate_artifacts.validate_run_bundles(
        runs_dir, CONTRACTS, root, errors, registry
    )
    return errors


def test_coherent_run_bundle_validates(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "coherent")
    assert _bundle_errors(runs_dir, tmp_path) == []


def test_bundle_requires_manifest(tmp_path: Path) -> None:
    runs_dir = _write_bundle(
        tmp_path, "coherent", files=("repair-candidate", "repair-result")
    )
    assert any("missing required run-manifest" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_duplicate_manifest(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "coherent")
    (runs_dir / "run-1" / "run-manifest.json").write_text(
        yaml.safe_dump(_load(EXAMPLE_FILES["run-manifest"]), sort_keys=False)
    )
    assert any("duplicate run-manifest" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_duplicate_candidate(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "coherent")
    (runs_dir / "run-1" / "repair-candidate.json").write_text(
        yaml.safe_dump(_load(EXAMPLE_FILES["repair-candidate"]), sort_keys=False)
    )
    assert any("duplicate repair-candidate" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_manifest_only_requested_run_accepted(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "coherent", files=("run-manifest",))
    assert _bundle_errors(runs_dir, tmp_path) == []


def test_bundle_manifest_plus_candidate_accepted(tmp_path: Path) -> None:
    runs_dir = _write_bundle(
        tmp_path, "coherent", files=("run-manifest", "repair-candidate")
    )
    assert _bundle_errors(runs_dir, tmp_path) == []


def test_bundle_rejects_run_id_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_run_id")
    assert any("run_id mismatch" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_candidate_id_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_candidate_chain")
    assert any("candidate_id does not match" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_result_id_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_result_chain")
    assert any("result_id does not match" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_reviewer_head_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_head")
    assert any("reviewer_head_sha does not match" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_target_repository_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_repo")
    assert any("target_repository does not match" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_bundle_rejects_baseline_sha_mismatch(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_baseline")
    assert any("baseline_sha does not match" in e for e in _bundle_errors(runs_dir, tmp_path))


def test_cli_validates_run_bundles(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "coherent")
    result = _run_cli("--runs-dir", str(runs_dir))
    assert result.returncode == 0, result.stderr


def test_cli_rejects_incoherent_run_bundles(tmp_path: Path) -> None:
    runs_dir = _write_bundle(tmp_path, "bad_head")
    result = _run_cli("--runs-dir", str(runs_dir))
    assert result.returncode == 1
    assert "reviewer_head_sha does not match" in result.stderr


def test_run_bundle_binds_maintenance_request(tmp_path: Path) -> None:
    """Every run binds its original maintenance request via the manifest."""
    runs_dir = _write_bundle(tmp_path, "coherent")
    manifest = _load(runs_dir / "run-1" / "run-manifest.yaml")
    mr = manifest["maintenance_request"]
    assert mr["text"].strip()
    assert mr["source"] in ("human_operator", "issue", "other")
    assert mr["created_at"]


def test_run_bundle_prompt_pins_resolve_to_exact_hashes(tmp_path: Path) -> None:
    """Bundle pin hashes must equal the released registry hashes."""
    runs_dir = _write_bundle(tmp_path, "coherent")
    manifest = _load(runs_dir / "run-1" / "run-manifest.yaml")
    registry = _load(REPO_ROOT / "prompts" / "registry.yaml")
    hashes = validate_artifacts.registry_release_hashes(registry)
    for role, pin in manifest["prompt_pins"].items():
        assert (pin["id"], pin["version"]) in hashes
        assert pin["sha256"] == hashes[(pin["id"], pin["version"])]
