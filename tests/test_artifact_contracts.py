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
    """A run manifest must carry exactly one of maintenance_request or
    mission_input (Issue #25: MissionContract-native mode is additive)."""
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["maintenance_request"]
    errors = _errors_for("run-manifest", doc)
    # Schema-level: maintenance_request is no longer unconditionally required
    # (mission_input mode is additive); the mode rule is enforced semantically.
    assert not errors, errors
    semantic: list[str] = []
    validate_artifacts.check_manifest_mission_mode(doc, "test", semantic)
    assert any("exactly one of maintenance_request or mission_input" in e for e in semantic)


def test_mission_input_manifest_mode_valid() -> None:
    """A manifest with mission_input (and no maintenance_request) is a valid
    MissionContract-native mode (Issue #25)."""
    doc = _load(EXAMPLE_FILES["run-manifest"])
    del doc["maintenance_request"]
    doc["mission_input"] = {
        "mission_id": "mission-fixture-bounded-recon",
        "candidate": {"path": "missions/mission-fixture-bounded-recon/mission-candidate.yaml",
                       "hq_commit_sha": "0" * 40, "sha256": "0" * 64},
        "contract": {"path": "missions/mission-fixture-bounded-recon/mission-contract.yaml",
                      "hq_commit_sha": "0" * 40, "sha256": "0" * 64},
        "admission_ledger": {"path": "mission/ledger.yaml",
                              "hq_commit_sha": "0" * 40, "sha256": "0" * 64},
    }
    doc["prompt_pins"]["operator"]["version"] = "0.3.0"
    doc["prompt_pins"]["scout"]["version"] = "0.2.0"
    doc["prompt_pins"]["repair"]["version"] = "0.2.0"
    doc["prompt_pins"]["review"]["version"] = "0.2.0"
    errors = _errors_for("run-manifest", doc)
    assert not errors, errors
    semantic: list[str] = []
    validate_artifacts.check_manifest_mission_mode(doc, "test", semantic)
    assert not semantic


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


# ── Coordination protocol contract coherence ───────────────────────────────

_CANDIDATE_SHA = "a40132d2663520e3ce85347f6f9fe0ba2e49f22b13b85337fb96bf8fcaaf7128"
_HQ_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_RUN = "run-20260805-widget-service-sorting"


def _coordination_doc(kind: str = "assignment", **overrides) -> dict:
    """Build a coordination-message document from the example for a message kind."""
    doc = _load(EXAMPLE_FILES["coordination-message"])
    if kind == "submission":
        doc.update(
            sender_role="scout",
            recipient_role="operator",
            message_type="artifact_submission",
            in_reply_to="msg-20260805-0001",
            supersedes=None,
            state_before="scouting",
            state_after="scouting",
            prompt_used="scout@0.1.0",
            artifact_ref={
                "kind": "repair_candidate",
                "path": f"run-output/{_RUN}/repair-candidate.yaml",
                "hq_commit_sha": None,
                "sha256": _CANDIDATE_SHA,
            },
        )
    elif kind == "acceptance":
        doc.update(
            sender_role="operator",
            recipient_role="scout",
            message_type="artifact_acceptance",
            in_reply_to="msg-20260805-0002",
            supersedes=None,
            state_before="scouting",
            state_after="candidate_selected",
            artifact_ref={
                "kind": "repair_candidate",
                "path": f"runs/{_RUN}/repair-candidate.yaml",
                "hq_commit_sha": _HQ_COMMIT,
                "sha256": _CANDIDATE_SHA,
            },
        )
    doc.update(overrides)
    return doc


def _coordination_errors(doc: dict) -> list[str]:
    errors: list[str] = []
    validate_artifacts.validate_value(doc, _schema("coordination-message"), "msg", errors)
    validate_artifacts.check_coordination_message(doc, "msg", errors)
    return errors


def test_submission_accepts_null_hq_commit_sha() -> None:
    assert _coordination_errors(_coordination_doc("submission")) == []


def test_submission_rejects_non_null_hq_commit_sha() -> None:
    errors = _coordination_errors(
        _coordination_doc("submission", artifact_ref={
            "kind": "repair_candidate",
            "path": f"run-output/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": _HQ_COMMIT,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("hq_commit_sha must be null" in e for e in errors)


def test_acceptance_requires_non_null_hq_commit_sha() -> None:
    errors = _coordination_errors(
        _coordination_doc("acceptance", artifact_ref={
            "kind": "repair_candidate",
            "path": f"runs/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": None,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("non-null 40-hex hq_commit_sha" in e for e in errors)
    errors = _coordination_errors(
        _coordination_doc("acceptance", artifact_ref={
            "kind": "repair_candidate",
            "path": f"runs/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": "xyz",
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("hq_commit_sha" in e for e in errors)


def test_submission_and_acceptance_preserve_same_sha256() -> None:
    submission = _coordination_doc("submission")
    acceptance = _coordination_doc("acceptance")
    assert submission["artifact_ref"]["sha256"] == acceptance["artifact_ref"]["sha256"]
    assert _coordination_errors(submission) == []
    assert _coordination_errors(acceptance) == []


def test_worker_submission_does_not_advance_state() -> None:
    errors = _coordination_errors(
        _coordination_doc("submission", state_after="candidate_selected")
    )
    assert any("must not advance pipeline state" in e for e in errors)


def test_acceptance_and_next_assignment_are_separate_transitions() -> None:
    # The acceptance itself must not perform the next role's dispatch transition.
    errors = _coordination_errors(
        _coordination_doc("acceptance", recipient_role="repair",
                          state_before="candidate_selected", state_after="repair_in_progress")
    )
    assert any("invalid acceptance transition" in e for e in errors)
    # A separate Repair assignment performs that transition, referencing the
    # accepted repair_candidate.
    repair_assignment = _coordination_doc(
        "assignment", recipient_role="repair",
        state_before="candidate_selected", state_after="repair_in_progress",
        artifact_ref={
            "kind": "repair_candidate",
            "path": f"runs/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": _HQ_COMMIT,
            "sha256": _CANDIDATE_SHA,
        },
    )
    assert _coordination_errors(repair_assignment) == []


def test_only_operator_emits_control_message_types() -> None:
    errors = _coordination_errors(_coordination_doc("assignment", sender_role="scout"))
    assert any("may only be emitted by operator" in e for e in errors)


def test_operator_cannot_emit_artifact_submission() -> None:
    errors = _coordination_errors(
        _coordination_doc("submission", sender_role="operator", recipient_role="scout")
    )
    assert any("may only be emitted by scout, repair or review" in e for e in errors)


def test_worker_cannot_emit_run_closed() -> None:
    errors = _coordination_errors(
        _coordination_doc("acceptance", message_type="run_closed", sender_role="review",
                          recipient_role="operator", state_before="approved",
                          state_after="approved")
    )
    assert any("may only be emitted by operator" in e for e in errors)


def test_recipient_all_fails() -> None:
    errors = _coordination_errors(_coordination_doc("assignment", recipient_role="all"))
    assert any("not in enum" in e for e in errors)


def test_unsupported_protocol_version_fails() -> None:
    errors = _coordination_errors(_coordination_doc("submission", protocol_version="0.2.0"))
    assert any("expected const" in e for e in errors)


def test_prompt_used_required_for_submission() -> None:
    errors = _coordination_errors(_coordination_doc("submission", prompt_used=None))
    assert any("requires prompt_used" in e for e in errors)


def test_prompt_used_must_match_sender_role() -> None:
    errors = _coordination_errors(_coordination_doc("submission", prompt_used="repair@0.1.0"))
    assert any("must match <sender_role>@<version>" in e for e in errors)


def test_worker_message_must_be_addressed_to_operator() -> None:
    errors = _coordination_errors(_coordination_doc("submission", recipient_role="repair"))
    assert any("must be addressed to operator" in e for e in errors)


def test_assignment_must_target_one_worker() -> None:
    errors = _coordination_errors(_coordination_doc("assignment", recipient_role="operator"))
    assert any("addressed to one worker role" in e for e in errors)


def test_acceptance_requires_canonical_path() -> None:
    errors = _coordination_errors(
        _coordination_doc("acceptance", artifact_ref={
            "kind": "repair_candidate",
            "path": f"run-output/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": _HQ_COMMIT,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("canonical path must be runs/<run-id>/" in e for e in errors)


def test_run_closed_requires_terminal_state() -> None:
    errors = _coordination_errors(
        _coordination_doc("acceptance", message_type="run_closed",
                          state_before="repair_submitted", state_after="repair_submitted")
    )
    assert any("already-terminal state" in e for e in errors)


def test_rework_request_does_not_advance_state() -> None:
    errors = _coordination_errors(
        _coordination_doc("acceptance", message_type="rework_request",
                          recipient_role="repair", state_before="repair_in_progress",
                          state_after="repair_submitted", artifact_ref=None)
    )
    assert any("must not advance state" in e for e in errors)


def test_run_manifest_issue_number_url_mismatch_fails() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["coordination"]["issue_number"] = 11
    errors: list[str] = []
    validate_artifacts.check_coordination_reference(doc, "run-manifest", errors)
    assert any("does not match issue_number" in e for e in errors)


def test_run_manifest_issue_url_outside_federation_hq_fails() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["coordination"]["issue_url"] = "https://github.com/other-org/other-repo/issues/10"
    errors = _errors_for("run-manifest", doc)
    assert any("does not match pattern" in e for e in errors)


def test_run_manifest_unsupported_coordination_protocol_fails() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    doc["coordination"]["protocol_version"] = "0.2.0"
    errors = _errors_for("run-manifest", doc)
    assert any("expected const" in e for e in errors)


def test_run_manifest_coherent_coordination_reference_accepted() -> None:
    doc = _load(EXAMPLE_FILES["run-manifest"])
    errors: list[str] = []
    validate_artifacts.check_coordination_reference(doc, "run-manifest", errors)
    assert errors == []


# ── Terminalization and reference enforcement ──────────────────────────────

_REVIEW_RESULT_SHA = "1560a69d11e377cc60c4e235488d67b505d3177b67594b1903e5e2f2be5ff886"


def _blocked_doc(state_before: str, state_after: str, sender: str = "repair") -> dict:
    """Build a worker blocked report (state must stay unchanged)."""
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc.update(
        sender_role=sender,
        recipient_role="operator",
        message_type="blocked",
        in_reply_to="msg-20260805-0004",
        supersedes=None,
        state_before=state_before,
        state_after=state_after,
        artifact_ref=None,
        body="Cannot proceed: evidence unreachable at the baseline SHA.",
    )
    return doc


def _closure_doc(state_before: str, state_after: str, *, recipient: str = "scout",
                 in_reply_to: str | None = "msg-20260805-0100",
                 ref=None, body: str = "Blocker verified as unrecoverable: evidence unreachable.",
                 **overrides) -> dict:
    """Build an Operator run_closed message."""
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc.update(
        sender_role="operator",
        recipient_role=recipient,
        message_type="run_closed",
        in_reply_to=in_reply_to,
        supersedes=None,
        state_before=state_before,
        state_after=state_after,
        artifact_ref=ref,
        body=body,
    )
    doc.update(overrides)
    return doc


def _review_result_ref() -> dict:
    return {
        "kind": "review_result",
        "path": f"runs/{_RUN}/review-result.yaml",
        "hq_commit_sha": _HQ_COMMIT,
        "sha256": _REVIEW_RESULT_SHA,
    }


def test_worker_blocked_keeps_state_unchanged() -> None:
    assert _coordination_errors(_blocked_doc("repair_in_progress", "repair_in_progress")) == []


def test_worker_cannot_directly_transition_run_to_blocked() -> None:
    errors = _coordination_errors(_blocked_doc("repair_in_progress", "blocked"))
    assert any("must not advance state" in e for e in errors)


def test_operator_run_closed_accepts_scouting_to_blocked() -> None:
    assert _coordination_errors(_closure_doc("scouting", "blocked")) == []


def test_operator_run_closed_accepts_repair_in_progress_to_blocked() -> None:
    assert _coordination_errors(
        _closure_doc("repair_in_progress", "blocked", recipient="repair")
    ) == []


def test_operator_run_closed_accepts_changes_requested_to_blocked() -> None:
    assert _coordination_errors(
        _closure_doc("changes_requested", "blocked", recipient="repair")
    ) == []


def test_non_terminal_to_blocked_closure_requires_in_reply_to() -> None:
    errors = _coordination_errors(_closure_doc("scouting", "blocked", in_reply_to=None))
    assert any("requires in_reply_to" in e for e in errors)


def test_non_terminal_to_blocked_closure_permits_null_artifact_ref() -> None:
    errors = _coordination_errors(_closure_doc("scouting", "blocked", ref=None))
    assert errors == []


def test_non_terminal_to_blocked_closure_requires_worker_recipient() -> None:
    errors = _coordination_errors(_closure_doc("scouting", "blocked", recipient="operator"))
    assert any("one concrete worker role" in e for e in errors)


def test_approved_closure_requires_canonical_artifact_ref() -> None:
    errors = _coordination_errors(
        _closure_doc("approved", "approved", recipient="review", ref=None)
    )
    assert any("requires a canonical artifact_ref" in e for e in errors)
    ok = _closure_doc("approved", "approved", recipient="review", ref=_review_result_ref())
    assert _coordination_errors(ok) == []


def test_blocked_closure_accepts_review_result_reference() -> None:
    errors = _coordination_errors(
        _closure_doc("blocked", "blocked", recipient="review", ref=_review_result_ref())
    )
    assert errors == []


def test_run_opened_without_artifact_ref_fails() -> None:
    doc = _load(EXAMPLE_FILES["coordination-message"])
    doc.update(
        sender_role="operator", recipient_role="scout", message_type="run_opened",
        in_reply_to=None, supersedes=None,
        state_before="requested", state_after="requested", artifact_ref=None,
        body="Run opened.",
    )
    errors = _coordination_errors(doc)
    assert any("requires a canonical run_manifest artifact_ref" in e for e in errors)


def test_assignment_without_artifact_ref_fails() -> None:
    errors = _coordination_errors(_coordination_doc("assignment", artifact_ref=None))
    assert any("assignment requires a canonical artifact_ref" in e for e in errors)


def test_assignment_with_non_canonical_reference_fails() -> None:
    errors = _coordination_errors(
        _coordination_doc("assignment", artifact_ref={
            "kind": "run_manifest",
            "path": f"run-output/{_RUN}/run-manifest.yaml",
            "hq_commit_sha": None,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("canonical path must be runs/<run-id>/" in e for e in errors)


def test_assignment_artifact_kind_must_match_recipient() -> None:
    errors = _coordination_errors(
        _coordination_doc("assignment", recipient_role="review",
                          state_before="repair_submitted", state_after="independent_review",
                          artifact_ref={
                              "kind": "run_manifest",
                              "path": f"runs/{_RUN}/run-manifest.yaml",
                              "hq_commit_sha": _HQ_COMMIT,
                              "sha256": _CANDIDATE_SHA,
                          })
    )
    assert any("must reference one of" in e for e in errors)


def test_repair_reassignment_requires_review_result() -> None:
    errors = _coordination_errors(
        _coordination_doc("assignment", recipient_role="repair",
                          state_before="changes_requested", state_after="repair_in_progress",
                          artifact_ref={
                              "kind": "repair_candidate",
                              "path": f"runs/{_RUN}/repair-candidate.yaml",
                              "hq_commit_sha": _HQ_COMMIT,
                              "sha256": _CANDIDATE_SHA,
                          })
    )
    assert any("must reference review_result" in e for e in errors)
    ok = _coordination_doc(
        "assignment", recipient_role="repair",
        state_before="changes_requested", state_after="repair_in_progress",
        artifact_ref=_review_result_ref(),
    )
    assert _coordination_errors(ok) == []


def test_submission_empty_delivery_path_fails() -> None:
    errors = _coordination_errors(
        _coordination_doc("submission", artifact_ref={
            "kind": "repair_candidate",
            "path": "",
            "hq_commit_sha": None,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("non-empty delivery path" in e for e in errors)


def test_submission_canonical_looking_path_fails() -> None:
    errors = _coordination_errors(
        _coordination_doc("submission", artifact_ref={
            "kind": "repair_candidate",
            "path": f"runs/{_RUN}/repair-candidate.yaml",
            "hq_commit_sha": None,
            "sha256": _CANDIDATE_SHA,
        })
    )
    assert any("must not claim canonical placement" in e for e in errors)


def test_submission_external_run_output_path_succeeds() -> None:
    errors = _coordination_errors(_coordination_doc("submission"))
    assert errors == []
    assert _coordination_doc("submission")["artifact_ref"]["path"].startswith("run-output/")


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
