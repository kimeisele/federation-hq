#!/usr/bin/env python3
"""Validate Federation HQ artifact contracts.

Checks, using the Python standard library plus PyYAML (a declared dev
dependency of this project — see pyproject.toml):

- prompt registry: schema version, unique prompt ids, unique versions per id,
  every referenced prompt file exists inside the repository, every release has
  a changelog rationale, a release status, and a SHA-256 that matches the
  referenced prompt file's bytes;
- every example artifact validates against its JSON Schema (a small structural
  subset: type, const, enum, pattern, properties, required,
  additionalProperties, items);
- run-manifest prompt pins resolve to exact released prompt hashes;
- committed run bundles below `runs/` are discovered, validated, and
  cross-checked (run_id, repository/SHA agreement, candidate/result chains,
  review head, exact prompt hashes);
- artifact path fields cannot escape the repository (no absolute paths, no
  ``..`` traversal).

Structural validation only — schema conformance never proves semantic truth.

Usage:
    python scripts/validate_artifacts.py
    python scripts/validate_artifacts.py --artifact runs/<run>/repair-result.yaml
    python scripts/validate_artifacts.py --runs-dir runs

Exit codes: 0 = valid, 1 = validation failures, 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - only on broken environments
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]

# Filename prefix -> schema file (relative to contracts/).
SCHEMA_MATCHERS: list[tuple[str, str]] = [
    ("run-manifest", "run-manifest.schema.json"),
    ("repair-candidate", "repair-candidate.schema.json"),
    ("repair-result", "repair-result.schema.json"),
    ("review-result", "review-result.schema.json"),
    ("coordination-message", "coordination-message.schema.json"),
    ("mission-candidate", "mission/mission-candidate.schema.json"),
    ("mission-contract", "mission/mission-contract.schema.json"),
    ("run-assessment", "mission/run-assessment.schema.json"),
    ("mission-ledger", "mission/mission-ledger.schema.json"),
]

# Keys whose string values are repo-relative artifact paths and must not
# escape the repository. ``repository_checkout`` is intentionally absent: it
# is the local path of the *target* repository checkout, external by design.
REPO_RELATIVE_PATH_KEYS = frozenset({"location", "evidence_locations", "evidence_checked"})

_WINDOWS_ABS = re.compile(r"^([A-Za-z]:[\\/]|\\\\|//)")


# ── Document loading ──────────────────────────────────────────────────────


def load_document(path: Path) -> dict:
    """Load a JSON or YAML document as a dict."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:  # pragma: no cover - pyyaml is a declared dev dependency
        raise RuntimeError(
            f"PyYAML is required to read {path.name} (dev dependency, see pyproject.toml)."
        )
    return yaml.safe_load(text)


# ── Minimal JSON Schema subset validator ──────────────────────────────────


def _resolve_ref(schema: dict, ref: str) -> dict:
    if not ref.startswith("#/$defs/"):
        raise ValueError(f"unsupported $ref {ref!r}: only local #/$defs/ refs are supported")
    defs = schema.get("$defs", {})
    name = ref[len("#/$defs/"):]
    if name not in defs:
        raise ValueError(f"unknown $defs target {name!r}")
    return defs[name]


def validate_value(value, schema: dict, where: str, errors: list[str], root: dict | None = None) -> None:
    """Validate *value* against a structural JSON Schema subset, appending errors."""
    root = root or schema
    if "$ref" in schema:
        validate_value(value, _resolve_ref(root, schema["$ref"]), where, errors, root)
        return
    if "const" in schema:
        if value != schema["const"]:
            errors.append(f"{where}: expected const {schema['const']!r}, got {value!r}")
        return
    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(f"{where}: value {value!r} not in enum {schema['enum']}")
        return

    typ = schema.get("type")
    if isinstance(typ, str):
        type_list = [typ]
    elif isinstance(typ, list):
        type_list = typ
    else:
        type_list = []

    def _type_matches(value, t: str) -> bool:
        if t == "string":
            return isinstance(value, str)
        if t == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if t == "object":
            return isinstance(value, dict)
        if t == "array":
            return isinstance(value, list)
        if t == "null":
            return value is None
        return True  # unknown type keyword: no constraint

    if type_list and not any(_type_matches(value, t) for t in type_list):
        errors.append(f"{where}: expected type {type_list}, got {type(value).__name__}")
        return
    branch = next((t for t in type_list if _type_matches(value, t)), None)

    if branch == "string":
        pattern = schema.get("pattern")
        if pattern:
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{where}: string {value!r} does not match pattern {pattern!r}")
            except re.error:
                pass  # malformed pattern in schema: not a document problem
        return
    if branch == "integer":
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{where}: value {value} is below minimum {minimum}")
        return
    if branch == "object":
        props = schema.get("properties", {})
        for name, subschema in props.items():
            if name in value:
                validate_value(value[name], subschema, f"{where}.{name}", errors, root)
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{where}: missing required field {required!r}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            if extra:
                errors.append(f"{where}: unexpected field(s) {extra}")
        return
    if branch == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_value(item, items, f"{where}[{index}]", errors, root)
        return
    # branch in (None, "null"): nothing further to check.


# ── Path-escape checks ────────────────────────────────────────────────────


def _iter_path_values(data) -> None:
    """Yield ``(where, value)`` for every repo-relative path field."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in REPO_RELATIVE_PATH_KEYS:
                if isinstance(value, str):
                    yield key, value
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, str):
                            yield f"{key}[{index}]", item
            yield from _iter_path_values(value)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from _iter_path_values(item)


def is_escape_risk(value: str) -> bool:
    """True when *value* cannot be a safe repo-relative artifact path."""
    if not value:
        return True
    if value.startswith(("/", "\\")) or _WINDOWS_ABS.match(value):
        return True
    return ".." in Path(value).parts


def check_paths(data, repo_root: Path, errors: list[str]) -> None:
    """Reject artifact path fields that are absolute or escape the repository."""
    root = repo_root.resolve()
    for where, value in _iter_path_values(data):
        if is_escape_risk(value):
            errors.append(f"{where}: artifact path {value!r} may escape the repository")
            continue
        try:
            resolved = (root / value).resolve()
        except (OSError, RuntimeError):  # pragma: no cover - defensive
            errors.append(f"{where}: cannot resolve artifact path {value!r}")
            continue
        if resolved != root and root not in resolved.parents:
            errors.append(f"{where}: artifact path {value!r} resolves outside the repository")


# ── Registry checks ───────────────────────────────────────────────────────


def registry_releases(registry: dict) -> set[tuple[str, str]]:
    """Return the set of released ``(id, version)`` pairs in a registry."""
    return set(registry_release_hashes(registry))


def registry_release_hashes(registry: dict) -> dict[tuple[str, str], str]:
    """Return {(id, version): sha256} for every **released** version.

    Only entries whose status is exactly ``released`` are pinnable by run
    manifests; ``unreleased_bootstrap`` and any other status are excluded.
    """
    hashes: dict[tuple[str, str], str] = {}
    prompts = registry.get("prompts", []) if isinstance(registry, dict) else []
    for entry in prompts:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        for ver in entry.get("versions", []):
            if not isinstance(ver, dict) or not isinstance(pid, str):
                continue
            if ver.get("status") != "released":
                continue
            version = ver.get("version")
            sha = ver.get("sha256")
            if isinstance(version, str) and isinstance(sha, str):
                hashes[(pid, version)] = sha.lower()
    return hashes


def validate_registry(registry_path: Path, repo_root: Path, errors: list[str]) -> dict | None:
    """Validate the prompt registry; return its data (or None on failure)."""
    try:
        registry = load_document(registry_path)
    except Exception as exc:
        errors.append(f"registry: could not load {registry_path}: {exc}")
        return None
    if not isinstance(registry, dict):
        errors.append(f"registry: {registry_path} must contain an object")
        return None
    if registry.get("schema_version") != 1:
        errors.append(f"registry: unsupported schema_version {registry.get('schema_version')!r}")

    prompts = registry.get("prompts", [])
    if not isinstance(prompts, list):
        errors.append("registry: 'prompts' must be a list")
        return registry

    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    prompts_dir = (repo_root / "prompts").resolve()

    for index, entry in enumerate(prompts):
        where = f"registry.prompts[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: expected an object")
            continue
        pid = entry.get("id")
        if not isinstance(pid, str) or not pid.strip():
            errors.append(f"{where}: missing prompt id")
            continue
        if pid in seen_ids:
            errors.append(f"{where}: duplicate prompt id {pid!r}")
        seen_ids.add(pid)

        versions = entry.get("versions", [])
        if not isinstance(versions, list) or not versions:
            errors.append(f"{where}: prompt {pid!r} must declare at least one version")
            continue
        seen_versions: set[str] = set()
        for vindex, ver in enumerate(versions):
            vwhere = f"{where}.versions[{vindex}]"
            if not isinstance(ver, dict):
                errors.append(f"{vwhere}: expected an object")
                continue
            for required in ("version", "file", "released", "changelog", "status", "sha256"):
                value = ver.get(required)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{vwhere}: missing required field {required!r}")
            status = ver.get("status")
            if status not in ("released", "unreleased_bootstrap"):
                errors.append(
                    f"{vwhere}: status must be 'released' or 'unreleased_bootstrap', got {status!r}"
                )
            sha = ver.get("sha256")
            if isinstance(sha, str):
                if not re.fullmatch(r"[0-9a-f]{64}", sha):
                    errors.append(f"{vwhere}: sha256 must be 64 lowercase hex chars")
                else:
                    fname = ver.get("file")
                    if isinstance(fname, str) and fname:
                        fpath = (prompts_dir / fname).resolve()
                        if fpath.exists() and fpath.is_file():
                            actual = hashlib.sha256(fpath.read_bytes()).hexdigest()
                            if actual != sha.lower():
                                errors.append(
                                    f"{vwhere}: sha256 {sha!r} does not match prompt file bytes "
                                    f"({actual})"
                                )
            version = ver.get("version")
            if isinstance(version, str):
                if version in seen_versions:
                    errors.append(f"{vwhere}: duplicate version {version!r} for prompt {pid!r}")
                seen_versions.add(version)
                pair = (pid, version)
                if pair in seen_pairs:
                    errors.append(f"{vwhere}: duplicate (id, version) pair {pair!r}")
                seen_pairs.add(pair)
            fname = ver.get("file")
            if isinstance(fname, str) and fname:
                fpath = (prompts_dir / fname).resolve()
                if not fpath.exists():
                    errors.append(f"{vwhere}: prompt file {fname!r} does not exist")
                elif prompts_dir not in fpath.parents:
                    errors.append(f"{vwhere}: prompt file {fname!r} escapes the prompts/ directory")
    return registry


def check_prompt_pins(doc: dict, registry: dict | None, where: str, errors: list[str]) -> None:
    """Verify run-manifest prompt pins resolve to exact released prompt hashes."""
    if registry is None:
        return
    pins = doc.get("prompt_pins") if isinstance(doc, dict) else None
    if not isinstance(pins, dict):
        return
    release_hashes = registry_release_hashes(registry)
    for role, pin in pins.items():
        if not isinstance(pin, dict):
            continue
        pid, version = pin.get("id"), pin.get("version")
        pin_sha = pin.get("sha256")
        if (pid, version) not in release_hashes:
            errors.append(
                f"{where}.prompt_pins.{role}: no released prompt {pid!r}@{version!r} in registry"
            )
            continue
        expected = release_hashes[(pid, version)]
        if not isinstance(pin_sha, str) or pin_sha.lower() != expected:
            errors.append(
                f"{where}.prompt_pins.{role}: sha256 {pin_sha!r} does not match registry release "
                f"{pid!r}@{version!r} ({expected})"
            )


# ── Coordination protocol contract checks ──────────────────────────────────

_CONTROL_MESSAGE_TYPES = frozenset(
    {"run_opened", "assignment", "artifact_acceptance", "rework_request", "run_closed"}
)
_WORKER_MESSAGE_TYPES = frozenset({"artifact_submission", "blocked"})
_WORKER_ROLES = frozenset({"scout", "repair", "review"})
_TERMINAL_STATES = frozenset({"approved", "blocked", "invalid_candidate"})
_NON_TERMINAL_STATES = frozenset(
    {
        "requested",
        "scouting",
        "candidate_selected",
        "repair_in_progress",
        "repair_submitted",
        "independent_review",
        "changes_requested",
    }
)

# Assignment artifact-kind requirements per recipient role (the Repair
# re-assignment after changes_requested references the accepted review_result).
_ASSIGNMENT_REF_KINDS: dict[str, frozenset[str]] = {
    "scout": frozenset({"run_manifest"}),
    "repair": frozenset({"repair_candidate", "review_result"}),
    "review": frozenset({"repair_result"}),
}

# Assignment transitions permitted per recipient role (docs/COORDINATION_PROTOCOL.md).
_ASSIGNMENT_TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "scout": frozenset({("requested", "scouting")}),
    "repair": frozenset(
        {("candidate_selected", "repair_in_progress"), ("changes_requested", "repair_in_progress")}
    ),
    "review": frozenset({("repair_submitted", "independent_review")}),
}

# Artifact-acceptance transitions permitted per recipient role.
_ACCEPTANCE_TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "scout": frozenset({("scouting", "candidate_selected")}),
    "repair": frozenset({("repair_in_progress", "repair_submitted")}),
    "review": frozenset(
        {
            ("independent_review", "approved"),
            ("independent_review", "changes_requested"),
            ("independent_review", "blocked"),
            ("independent_review", "invalid_candidate"),
        }
    ),
}


def _check_canonical_ref(ref: dict, run_id: object, where: str, errors: list[str]) -> None:
    """Require a canonical artifact reference: runs/<run-id>/ path, exact HQ commit SHA, SHA-256."""
    path = ref.get("path")
    if not isinstance(path, str) or not path.startswith(f"runs/{run_id}/"):
        errors.append(f"{where}.artifact_ref: canonical path must be runs/<run-id>/..., got {path!r}")
    hq = ref.get("hq_commit_sha")
    if not isinstance(hq, str) or not re.fullmatch(r"[0-9a-f]{40}", hq):
        errors.append(f"{where}.artifact_ref: canonical reference requires a non-null 40-hex hq_commit_sha")
    sha = ref.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors.append(f"{where}.artifact_ref: canonical reference requires a 64-hex sha256")


def check_coordination_reference(doc: dict, where: str, errors: list[str]) -> None:
    """Verify the run manifest's coordination Issue reference is internally coherent.

    Cross-field check only: the URL must be a kimeisele/federation-hq Issue and
    its number must equal ``issue_number``. No GitHub API is called.
    """
    coord = doc.get("coordination") if isinstance(doc, dict) else None
    if not isinstance(coord, dict):
        return  # presence and format are enforced by the schema
    issue_number = coord.get("issue_number")
    issue_url = coord.get("issue_url")
    match = (
        re.fullmatch(r"https://github\.com/kimeisele/federation-hq/issues/([0-9]+)", issue_url)
        if isinstance(issue_url, str)
        else None
    )
    if match is None:
        return  # schema pattern enforces the URL shape
    url_number = int(match.group(1))
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
        errors.append(f"{where}.coordination: issue_number must be a positive integer")
    elif url_number != issue_number:
        errors.append(
            f"{where}.coordination: issue_url number {url_number} does not match "
            f"issue_number {issue_number}"
        )


def check_coordination_message(doc: dict, where: str, errors: list[str]) -> None:
    """Validate one supplied coordination-message document against the protocol contract.

    Enforces sender/message authority, submission versus acceptance artifact
    phases, and message/state semantics. Structural contract only; it does not
    semantically validate live GitHub comments.
    """
    if not isinstance(doc, dict):
        return
    mtype = doc.get("message_type")
    sender = doc.get("sender_role")
    recipient = doc.get("recipient_role")
    before, after = doc.get("state_before"), doc.get("state_after")
    run_id = doc.get("run_id")

    if mtype in _CONTROL_MESSAGE_TYPES:
        if sender != "operator":
            errors.append(
                f"{where}: message_type {mtype!r} may only be emitted by operator, got {sender!r}"
            )
    elif mtype in _WORKER_MESSAGE_TYPES:
        if sender not in _WORKER_ROLES:
            errors.append(
                f"{where}: message_type {mtype!r} may only be emitted by scout, repair or review, "
                f"got {sender!r}"
            )
        if recipient != "operator":
            errors.append(
                f"{where}: worker message {mtype!r} must be addressed to operator, got {recipient!r}"
            )
    else:
        return  # unknown types are already rejected by the schema enum

    if mtype == "artifact_submission":
        prompt_used = doc.get("prompt_used")
        if not isinstance(prompt_used, str) or not prompt_used:
            errors.append(f"{where}: artifact_submission requires prompt_used")
        elif sender in _WORKER_ROLES and not re.fullmatch(
            rf"{re.escape(sender)}@[0-9]+\.[0-9]+\.[0-9]+", prompt_used
        ):
            errors.append(
                f"{where}: prompt_used {prompt_used!r} must match <sender_role>@<version> "
                f"for sender {sender!r}"
            )
        ref = doc.get("artifact_ref")
        if not isinstance(ref, dict):
            errors.append(f"{where}: artifact_submission requires an artifact_ref")
        else:
            if ref.get("hq_commit_sha") is not None:
                errors.append(
                    f"{where}.artifact_ref: artifact_submission hq_commit_sha must be null "
                    "(submitted bytes are not yet recorded canonically)"
                )
            if not isinstance(ref.get("sha256"), str) or not ref["sha256"]:
                errors.append(f"{where}.artifact_ref: artifact_submission requires sha256")
            path = ref.get("path")
            if not isinstance(path, str) or not path.strip():
                errors.append(
                    f"{where}.artifact_ref: artifact_submission requires a non-empty delivery path"
                )
            elif path.startswith(f"runs/{run_id}/"):
                errors.append(
                    f"{where}.artifact_ref: artifact_submission path must not claim canonical "
                    f"placement under runs/<run-id>/, got {path!r}"
                )
        if before != after:
            errors.append(
                f"{where}: artifact_submission must not advance pipeline state "
                f"(state_before == state_after), got ({before} -> {after})"
            )
        return

    if mtype == "artifact_acceptance":
        ref = doc.get("artifact_ref")
        if not isinstance(ref, dict):
            errors.append(f"{where}: artifact_acceptance requires a canonical artifact_ref")
        else:
            _check_canonical_ref(ref, run_id, where, errors)
        if recipient in _ACCEPTANCE_TRANSITIONS:
            if (before, after) not in _ACCEPTANCE_TRANSITIONS[recipient]:
                errors.append(
                    f"{where}: invalid acceptance transition ({before} -> {after}) for "
                    f"recipient {recipient!r}; allowed: "
                    f"{sorted(_ACCEPTANCE_TRANSITIONS[recipient])}"
                )
        else:
            errors.append(
                f"{where}: artifact_acceptance recipient must be scout, repair or review, "
                f"got {recipient!r}"
            )
        return

    if mtype == "assignment":
        if recipient not in _ASSIGNMENT_TRANSITIONS:
            errors.append(
                f"{where}: assignment must be addressed to one worker role, got {recipient!r}"
            )
            return
        if (before, after) not in _ASSIGNMENT_TRANSITIONS[recipient]:
            errors.append(
                f"{where}: invalid assignment transition ({before} -> {after}) for "
                f"recipient {recipient!r}; allowed: {sorted(_ASSIGNMENT_TRANSITIONS[recipient])}"
            )
        ref = doc.get("artifact_ref")
        if not isinstance(ref, dict):
            errors.append(f"{where}: assignment requires a canonical artifact_ref")
            return
        _check_canonical_ref(ref, run_id, where, errors)
        kind = ref.get("kind")
        allowed_kinds = _ASSIGNMENT_REF_KINDS.get(recipient, frozenset())
        if recipient == "repair":
            expected = "review_result" if before == "changes_requested" else "repair_candidate"
            if kind != expected:
                errors.append(
                    f"{where}.artifact_ref: repair assignment must reference "
                    f"{expected}, got {kind!r}"
                )
        elif kind not in allowed_kinds:
            errors.append(
                f"{where}.artifact_ref: assignment to {recipient!r} must reference one of "
                f"{sorted(allowed_kinds)}, got {kind!r}"
            )
        return

    if mtype == "run_opened":
        if (before, after) != ("requested", "requested"):
            errors.append(
                f"{where}: run_opened must keep state requested -> requested, "
                f"got ({before} -> {after})"
            )
        ref = doc.get("artifact_ref")
        if not isinstance(ref, dict):
            errors.append(f"{where}: run_opened requires a canonical run_manifest artifact_ref")
            return
        _check_canonical_ref(ref, run_id, where, errors)
        if ref.get("kind") != "run_manifest":
            errors.append(
                f"{where}.artifact_ref: run_opened must reference a run_manifest, "
                f"got {ref.get('kind')!r}"
            )
        return

    if mtype == "rework_request":
        if before != after:
            errors.append(
                f"{where}: rework_request must not advance state "
                f"(state_before == state_after), got ({before} -> {after})"
            )
        return

    if mtype == "blocked":
        if before != after:
            errors.append(
                f"{where}: blocked report must not advance state "
                f"(state_before == state_after), got ({before} -> {after})"
            )
        return

    if mtype == "run_closed":
        if after == "blocked" and before in _NON_TERMINAL_STATES:
            # Operator terminalization after an unrecoverable worker blocker:
            # no canonical terminal artifact exists yet.
            if not isinstance(doc.get("in_reply_to"), str) or not doc["in_reply_to"]:
                errors.append(
                    f"{where}: non-terminal-to-blocked run_closed requires in_reply_to "
                    "(the worker blocked report)"
                )
            if recipient not in _WORKER_ROLES:
                errors.append(
                    f"{where}: non-terminal-to-blocked run_closed must address one concrete "
                    f"worker role, got {recipient!r}"
                )
            body = doc.get("body")
            if not isinstance(body, str) or not body.strip():
                errors.append(f"{where}: run_closed body must identify the blocker")
            ref = doc.get("artifact_ref")
            if ref is not None and not isinstance(ref, dict):
                errors.append(f"{where}: artifact_ref must be an object or null")
            return
        if before != after or before not in _TERMINAL_STATES:
            errors.append(
                f"{where}: run_closed requires an already-terminal state "
                f"(before == after in {{approved, blocked, invalid_candidate}}), "
                f"got ({before} -> {after})"
            )
        ref = doc.get("artifact_ref")
        if not isinstance(ref, dict):
            errors.append(f"{where}: run_closed requires a canonical artifact_ref")
            return
        _check_canonical_ref(ref, run_id, where, errors)
        return


# ── Schema / example checks ───────────────────────────────────────────────


def schema_for_artifact(filename: str) -> str | None:
    """Map an artifact filename to its schema file name (or None)."""
    for prefix, schema_name in SCHEMA_MATCHERS:
        if filename.startswith(prefix):
            return schema_name
    return None


def validate_schemas(schemas_dir: Path, errors: list[str]) -> None:
    """Ensure every declared schema file exists and parses as JSON."""
    for _, schema_name in SCHEMA_MATCHERS:
        path = schemas_dir / schema_name
        if not path.exists():
            errors.append(f"schema: {schema_name} not found in {schemas_dir}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"schema: {schema_name} is not valid JSON: {exc}")


def validate_artifact(
    path: Path,
    schemas_dir: Path,
    repo_root: Path,
    errors: list[str],
    registry: dict | None = None,
) -> None:
    """Validate one artifact document against its schema plus path-escape checks."""
    schema_name = schema_for_artifact(path.name)
    if schema_name is None:
        errors.append(f"{path.name}: no schema matches this filename")
        return
    try:
        doc = load_document(path)
    except Exception as exc:
        errors.append(f"{path.name}: could not load: {exc}")
        return
    if not isinstance(doc, dict):
        errors.append(f"{path.name}: artifact must be an object")
        return
    try:
        schema = json.loads((schemas_dir / schema_name).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"{path.name}: cannot read schema {schema_name}: {exc}")
        return
    validate_value(doc, schema, path.name, errors)
    check_paths(doc, repo_root, errors)
    if "run-manifest" in path.name:
        check_prompt_pins(doc, registry, path.name, errors)
        check_coordination_reference(doc, path.name, errors)
        check_manifest_mission_mode(doc, path.name, errors)
        check_mission_pin(doc, repo_root, schemas_dir, path.name, errors)
    if "coordination-message" in path.name:
        check_coordination_message(doc, path.name, errors)
    if "mission-candidate" in path.name or "mission-contract" in path.name \
            or "run-assessment" in path.name:
        check_mission_artifact(doc, path.name, errors)
    if "mission-contract" in path.name:
        check_mission_policy_pin(doc, repo_root, path.name, errors)
    # The live POL-04 reopen guard applies to current formulations. The
    # documented NON-CANONICAL retrospective projection directory
    # (examples/mission/retrospective/) is exempt: those files snapshot the
    # pre-run decision moment, predating the ledger entries their signals now
    # hold. The guard applies everywhere else, including all real candidates.
    if "mission-candidate" in path.name and "retrospective" not in path.parts:
        check_ledger_reopen(doc, _load_ledger(repo_root), path.name, errors)


def validate_examples(
    examples_dir: Path,
    schemas_dir: Path,
    repo_root: Path,
    errors: list[str],
    registry: dict | None = None,
) -> None:
    """Validate every example artifact (recursively) against its schema."""
    for path in sorted(examples_dir.rglob("*")):
        if not path.is_file():
            continue
        validate_artifact(path, schemas_dir, repo_root, errors, registry)


def check_mission_artifact(doc: dict, where: str, errors: list[str]) -> None:
    """Mission-layer semantic checks beyond structural schema validation.

    Policy semantics (docs/HQ_MISSION_POLICY.md):
    - POL-01: signal_refs must have at least one entry (the minimal schema
      subset validator does not enforce minItems);
    - provenance chain signal -> candidate -> contract is the only path
      (source_candidate_id required on contracts);
    - POL-05: no_mission_warranted must not carry a mission_id (no
      MissionContract is opened);
    - duplicate must reference the existing ledger item it duplicates;
    - superseded must reference what supersedes it;
    - POL-10: mission_rejected (contract) requires rejection_reason;
    - RunAssessment: the mission_rejected branch requires a
      rejection_reason_code and must not fabricate normal execution facts;
      the executed-run branch keeps requiring its run facts.
    """
    kind = doc.get("kind")
    if kind == "federation_hq_mission_candidate":
        if not isinstance(doc.get("signal_refs"), list) or not doc["signal_refs"]:
            errors.append(
                f"{where}: signal_refs must contain at least one structured signal (POL-01)"
            )
        disposition = doc.get("disposition")
        if disposition == "no_mission_warranted" and doc.get("mission_id") is not None:
            errors.append(
                f"{where}: no_mission_warranted must not carry a mission_id "
                f"(no MissionContract is opened)"
            )
        if disposition == "duplicate" and not doc.get("duplicate_of"):
            errors.append(f"{where}: duplicate disposition requires duplicate_of")
        if disposition == "superseded" and not doc.get("superseded_by"):
            errors.append(f"{where}: superseded disposition requires superseded_by")
        override = doc.get("prior_disposition_override")
        if override is not None:
            if not isinstance(override, dict):
                errors.append(f"{where}: prior_disposition_override must be an object")
            elif not isinstance(override.get("new_evidence_refs"), list) \
                    or not override["new_evidence_refs"]:
                errors.append(
                    f"{where}: prior_disposition_override requires at least one "
                    f"concrete new_evidence_ref (no vague reconsideration override)"
                )
    if kind == "federation_hq_mission_contract":
        if not isinstance(doc.get("source_candidate_id"), str) or not doc["source_candidate_id"]:
            errors.append(
                f"{where}: source_candidate_id is required (signal -> candidate -> contract "
                f"provenance; human requests are candidates with source_kind: human_request)"
            )
        if not isinstance(doc.get("signal_refs"), list) or not doc["signal_refs"]:
            errors.append(
                f"{where}: signal_refs must contain at least one structured signal (POL-01)"
            )
        if doc.get("status") == "mission_rejected" and not doc.get("rejection_reason"):
            errors.append(
                f"{where}: mission_rejected requires rejection_reason "
                f"(framing invalid/unsafe/duplicate/unsupported/evidence-inadequate)"
            )
    if kind == "federation_hq_run_assessment":
        _check_run_assessment(doc, where, errors)


_EXECUTED_RUN_FACTS = frozenset({
    "run_id", "repair_class", "review_verdict", "gate_verified",
    "target_merged", "run_record_merged", "human_role_handoffs",
})


def _check_run_assessment(doc: dict, where: str, errors: list[str]) -> None:
    """RunAssessment branch semantics: pre-execution mission_rejected vs an
    executed run. The rejection branch must not pretend review/gate/merge
    facts constitute a normal execution run."""
    outcome = doc.get("terminal_outcome")
    if outcome == "mission_rejected":
        if not isinstance(doc.get("rejection_reason_code"), str) \
                or not doc["rejection_reason_code"]:
            errors.append(
                f"{where}: terminal_outcome mission_rejected requires a machine-readable "
                f"rejection_reason_code"
            )
        # run_id may be present as null (no run was initialized) but must not
        # name a canonical run.
        if doc.get("run_id") is not None:
            errors.append(
                f"{where}: mission_rejected assessment run_id must be null/absent "
                f"(no run was initialized)"
            )
        fabricated = sorted(set(_EXECUTED_RUN_FACTS - {"run_id"}) & set(doc))
        if fabricated:
            errors.append(
                f"{where}: mission_rejected assessment must not carry executed-run facts "
                f"(no review_verdict/gate/target/run-record/handoffs), got {fabricated}"
            )
        return
    missing = sorted(f for f in _EXECUTED_RUN_FACTS if f not in doc)
    if missing:
        errors.append(
            f"{where}: executed-run assessment requires run facts {missing}"
        )
    elif not isinstance(doc.get("run_id"), str) or not doc["run_id"]:
        errors.append(f"{where}: executed-run assessment requires a canonical run_id")


def _load_ledger(repo_root: Path) -> dict | None:
    """Load the persistent Mission Ledger, or None when absent/unreadable."""
    path = repo_root / "mission" / "ledger.yaml"
    if not path.exists():
        return None
    try:
        doc = load_document(path)
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


_TERMINAL_LEDGER_DISPOSITIONS = frozenset({
    "completed", "wont_fix", "no_mission_warranted",
    "duplicate", "rejected", "superseded",
})

# Canonical policy artifact and its explicit machine-readable version marker.
_CANONICAL_POLICY_PATH = Path("docs") / "HQ_MISSION_POLICY.md"
_POLICY_VERSION_MARKER = re.compile(r"Policy version:\*\*\s*`([0-9]+\.[0-9]+\.[0-9]+)`")


def _resolve_policy_version(text: str) -> str | None:
    """Extract the policy's explicit version marker, or None."""
    match = _POLICY_VERSION_MARKER.search(text)
    return match.group(1) if match else None


def _policy_pin_problems(contract_doc: dict, policy_bytes: bytes, where: str) -> list[str]:
    """Prove a MissionContract's policy pin against EXACT policy bytes.

    Used with the CURRENT canonical policy bytes for new formulations and
    with the historical policy bytes resolved at the pinned contract HQ
    commit for existing Run Manifests. Never duplicated into the contract;
    docs/HQ_MISSION_POLICY.md stays the single policy source.
    """
    problems: list[str] = []
    reference = contract_doc.get("policy_reference")
    if not isinstance(reference, str) or not reference:
        problems.append(f"{where}: policy_reference is required")
        return problems
    if is_escape_risk(reference):
        problems.append(f"{where}: policy_reference {reference!r} escapes the repository")
        return problems
    if Path(reference).as_posix() != _CANONICAL_POLICY_PATH.as_posix():
        problems.append(
            f"{where}: policy_reference {reference!r} does not resolve to the canonical "
            f"HQ Mission Policy ({_CANONICAL_POLICY_PATH.as_posix()})"
        )
        return problems
    actual_hash = hashlib.sha256(policy_bytes).hexdigest()
    supplied_hash = contract_doc.get("policy_sha256")
    if not isinstance(supplied_hash, str) or supplied_hash != actual_hash:
        problems.append(
            f"{where}: policy_sha256 {supplied_hash!r} does not match the policy "
            f"bytes ({actual_hash})"
        )
    try:
        policy_text = policy_bytes.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"{where}: policy file is not UTF-8 text")
        return problems
    actual_version = _resolve_policy_version(policy_text)
    supplied_version = contract_doc.get("policy_version")
    if actual_version is None:
        problems.append(f"{where}: cannot resolve the policy version marker")
    elif not isinstance(supplied_version, str) or supplied_version != actual_version:
        problems.append(
            f"{where}: policy_version {supplied_version!r} does not match the policy "
            f"version marker ({actual_version})"
        )
    return problems


def check_mission_policy_pin(doc: dict, repo_root: Path, where: str, errors: list[str]) -> None:
    """Prove a MissionContract's policy pin against the CURRENT canonical
    policy bytes (new-formulation context). Existing Run Manifests resolve
    policy bytes from the pinned contract commit instead (see
    check_mission_pin)."""
    if doc.get("kind") != "federation_hq_mission_contract":
        return
    policy_path = (repo_root / _CANONICAL_POLICY_PATH).resolve()
    if not policy_path.exists():
        errors.append(f"{where}: canonical policy file not found")
        return
    try:
        policy_bytes = policy_path.read_bytes()
    except OSError as exc:
        errors.append(f"{where}: cannot read canonical policy: {exc}")
        return
    errors.extend(_policy_pin_problems(doc, policy_bytes, where))


def check_ledger_reopen(doc: dict, ledger: dict | None, where: str, errors: list[str]) -> None:
    """POL-04 reopen guard: a signal with a terminal ledger disposition must
    not silently become a selectable mission.

    Validation happens against the live repository-native ledger. A candidate
    may reopen a terminal signal as selected ONLY via an explicit
    prior_disposition_override carrying at least one concrete new-evidence
    reference and the old ledger signal identity.
    """
    if doc.get("kind") != "federation_hq_mission_candidate":
        return
    if doc.get("disposition") != "selected":
        return
    if ledger is None:
        return  # no ledger to guard against; nothing to enforce
    ledger_by_signal = {item.get("signal_id"): item for item in ledger.get("items", [])}
    overrides = doc.get("prior_disposition_override")
    override_map = {}
    if isinstance(overrides, dict):
        override_map = {
            overrides.get("ledger_signal_id"): overrides.get("prior_disposition")
        }
    for ref in doc.get("signal_refs", []) or []:
        signal_id = ref.get("signal_id") if isinstance(ref, dict) else None
        item = ledger_by_signal.get(signal_id)
        if item is None:
            continue  # new signal: no prior disposition
        prior = item.get("disposition")
        if prior not in _TERMINAL_LEDGER_DISPOSITIONS:
            continue
        if override_map.get(signal_id) != prior:
            errors.append(
                f"{where}: signal {signal_id!r} has terminal ledger disposition "
                f"{prior!r}; reopening as selected requires an explicit "
                f"prior_disposition_override for ledger_signal_id {signal_id!r} "
                f"matching prior_disposition {prior!r} with new evidence (POL-04)"
            )


# ── MissionContract-native mode: manifest mode, mission pin, admission ─────

_MISSION_SCHEMA_FILES = {
    "federation_hq_mission_candidate": "mission/mission-candidate.schema.json",
    "federation_hq_mission_contract": "mission/mission-contract.schema.json",
}


def _validate_mission_doc(doc: dict, schemas_dir: Path, repo_root: Path,
                          where: str, errors: list[str]) -> None:
    """Structural + mission-semantic validation of a mission artifact."""
    kind = doc.get("kind")
    schema_name = _MISSION_SCHEMA_FILES.get(kind)
    if schema_name is None:
        errors.append(f"{where}: unknown mission artifact kind {kind!r}")
        return
    try:
        schema = json.loads((schemas_dir / schema_name).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"{where}: cannot read schema {schema_name}: {exc}")
        return
    validate_value(doc, schema, where, errors)
    check_paths(doc, repo_root, errors)
    check_mission_artifact(doc, where, errors)
    # NOTE: the policy pin is NOT validated here. Policy-byte proof happens
    # in the admission context only: current canonical bytes for a new
    # formulation, pinned-contract-commit bytes for an existing Run
    # Manifest (check_mission_pin). Static package validity must not depend
    # forever on today's policy bytes.


def check_manifest_mission_mode(doc: dict, where: str, errors: list[str]) -> None:
    """A run manifest uses exactly one of legacy maintenance_request or
    MissionContract-native mission_input; mission_input manifests require the
    MissionContract-native Operator release (operator@0.3.0)."""
    has_legacy = doc.get("maintenance_request") is not None
    has_mission = doc.get("mission_input") is not None
    if has_legacy == has_mission:
        errors.append(
            f"{where}: exactly one of maintenance_request or mission_input must be present"
        )
        return
    if has_mission:
        pins = doc.get("prompt_pins") or {}
        op = pins.get("operator") or {}
        version = op.get("version")
        if version != "0.3.0":
            errors.append(
                f"{where}: mission_input manifests require operator@0.3.0 "
                f"(MissionContract-native Operator), got operator@{version}"
            )
        # MissionContract-native worker chain: the same release set must be
        # used so every worker implements the MissionContract composition
        # contract (legacy workers treat maintenance_request as authority).
        for pid, expected in (("scout", "0.2.0"), ("repair", "0.2.0"), ("review", "0.2.0")):
            worker = pins.get(pid) or {}
            if worker.get("version") != expected:
                errors.append(
                    f"{where}: mission_input manifests require {pid}@{expected} "
                    f"(MissionContract-native worker release), got {pid}@{worker.get('version')}"
                )


def _pinned_bytes(repo_root: Path, commit: str, path: str) -> bytes | None:
    """Exact bytes of *path* at *commit* (git cat-file), or None."""
    result = subprocess.run(
        ["git", "cat-file", "-p", f"{commit}:{path}"],
        capture_output=True, cwd=repo_root,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _validate_pinned_ledger(ledger_doc: dict, schemas_dir: Path, where: str) -> list[str]:
    """Validate pinned admission-ledger bytes: schema plus canonical kind and
    schema_version. Malformed or non-Ledger bytes are never treated as an
    empty ledger."""
    problems: list[str] = []
    try:
        ledger_schema = json.loads(
            (schemas_dir / "mission" / "mission-ledger.schema.json").read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        problems.append(f"{where}: cannot read ledger schema: {exc}")
        return problems
    validate_value(ledger_doc, ledger_schema, where, problems)
    if ledger_doc.get("kind") != "federation_hq_mission_ledger" or \
            ledger_doc.get("schema_version") != "0.1.0":
        problems.append(f"{where}: pinned bytes are not a v0.1 Mission Ledger")
    return problems


def check_mission_pin(doc: dict, repo_root: Path, schemas_dir: Path,
                      where: str, errors: list[str]) -> None:
    """Verify a manifest's mission_input pins and the manifest identity chain.

    For each pin (candidate, contract, admission_ledger): repository-relative
    path, exact SHA-256 of the bytes, and an exact HQ commit SHA that
    contains those bytes. The PINNED COMMIT BYTES are the historical pin
    authority — the current working tree is never substituted. Then load
    Candidate/Contract/Ledger from the pinned commit bytes and verify the
    identity chain (mission_id, target_repository, source_candidate_id,
    signal provenance, canonical missions/<mission-id>/ package location)
    and the point-in-time admission decision against the PINNED admission
    ledger snapshot.
    """
    mission_input = doc.get("mission_input")
    if not isinstance(mission_input, dict):
        return
    pins: dict[str, dict] = {}
    for label in ("candidate", "contract", "admission_ledger"):
        pin = mission_input.get(label)
        if not isinstance(pin, dict):
            errors.append(f"{where}.mission_input.{label}: missing pin")
            continue
        path = pin.get("path")
        sha = pin.get("sha256")
        commit = pin.get("hq_commit_sha")
        if not isinstance(path, str) or is_escape_risk(path):
            errors.append(f"{where}.mission_input.{label}: path must be repository-relative")
            continue
        # The admission Ledger pin is canonical: only mission/ledger.yaml.
        if label == "admission_ledger" and Path(path).as_posix() != "mission/ledger.yaml":
            errors.append(
                f"{where}.mission_input.admission_ledger: canonical ledger path required, "
                f"got {path!r} expected mission/ledger.yaml"
            )
        if not isinstance(commit, str):
            errors.append(f"{where}.mission_input.{label}: hq_commit_sha is required")
            continue
        # PINNED COMMIT BYTES are the historical authority. The current
        # working tree is never substituted and never required to match: the
        # current package is governed separately by validate_static_mission_package.
        pinned = _pinned_bytes(repo_root, commit, path)
        if pinned is None or not isinstance(sha, str) or \
                hashlib.sha256(pinned).hexdigest() != sha:
            errors.append(
                f"{where}.mission_input.{label}: hq_commit_sha {commit} does not contain "
                f"{path} with the pinned bytes"
            )
            continue
        pins[label] = {"path": path, "sha256": sha, "commit": commit, "bytes": pinned}

    if not all(label in pins for label in ("candidate", "contract", "admission_ledger")):
        return  # pins already failed; skip the identity chain

    mission_id = mission_input.get("mission_id")
    cand_path = pins["candidate"]["path"]
    contr_path = pins["contract"]["path"]
    # Canonical package location.
    for label, path in (("candidate", cand_path), ("contract", contr_path)):
        expected = f"missions/{mission_id}/{('mission-candidate' if label == 'candidate' else 'mission-contract')}.yaml"
        if isinstance(mission_id, str) and path != expected:
            errors.append(
                f"{where}.mission_input.{label}: canonical package location required, "
                f"got {path!r} expected {expected!r}"
            )

    def _parse(label: str) -> dict | None:
        try:
            doc = yaml.safe_load(pins[label]["bytes"].decode("utf-8"))
        except Exception:
            errors.append(f"{where}.mission_input.{label}: pinned bytes are not valid YAML")
            return None
        return doc if isinstance(doc, dict) else None

    cand_doc = _parse("candidate")
    contr_doc = _parse("contract")
    ledger_doc = _parse("admission_ledger")
    if cand_doc is None or contr_doc is None or ledger_doc is None:
        return
    # The PINNED admission-ledger bytes must be a valid Mission Ledger —
    # malformed or non-Ledger bytes are never treated as an empty ledger.
    ledger_errors = _validate_pinned_ledger(ledger_doc, schemas_dir, f"{where}.mission_input.admission_ledger")
    if ledger_errors:
        errors.extend(ledger_errors)
        return

    # Manifest identity chain against the PINNED bytes.
    if cand_doc.get("mission_id") != mission_id:
        errors.append(
            f"{where}.mission_input: manifest mission_id {mission_id!r} does not match "
            f"the pinned candidate mission_id {cand_doc.get('mission_id')!r}"
        )
    if contr_doc.get("mission_id") != mission_id:
        errors.append(
            f"{where}.mission_input: manifest mission_id {mission_id!r} does not match "
            f"the pinned contract mission_id {contr_doc.get('mission_id')!r}"
        )
    target = doc.get("target_repository")
    if cand_doc.get("target_repository") != target:
        errors.append(
            f"{where}: manifest target_repository {target!r} does not match the pinned "
            f"candidate target_repository {cand_doc.get('target_repository')!r}"
        )
    if contr_doc.get("target_repository") != target:
        errors.append(
            f"{where}: manifest target_repository {target!r} does not match the pinned "
            f"contract target_repository {contr_doc.get('target_repository')!r}"
        )
    if contr_doc.get("source_candidate_id") != cand_doc.get("candidate_id"):
        errors.append(
            f"{where}.mission_input: contract source_candidate_id does not match the "
            f"pinned candidate_id"
        )
    cand_signals = {r.get("signal_id") for r in cand_doc.get("signal_refs", [])}
    contract_signals = {r.get("signal_id") for r in contr_doc.get("signal_refs", [])}
    if not cand_signals or not cand_signals <= contract_signals:
        errors.append(
            f"{where}.mission_input: pinned contract signal identities do not cover "
            f"the pinned candidate signals"
        )

    # Historical Mission Policy: resolve the governing policy bytes from the
    # SAME pinned contract commit (git object), never today's working tree.
    policy_ref = contr_doc.get("policy_reference")
    if not isinstance(policy_ref, str) or is_escape_risk(policy_ref):
        errors.append(f"{where}.mission_input: contract policy_reference is invalid")
        return
    contract_commit = pins["contract"]["commit"]
    policy_bytes = _pinned_bytes(repo_root, contract_commit, policy_ref)
    if policy_bytes is None:
        errors.append(
            f"{where}.mission_input: policy {policy_ref!r} not found at contract commit "
            f"{contract_commit}"
        )
        return
    policy_problems = _policy_pin_problems(contr_doc, policy_bytes, f"{where}.mission_input")
    if policy_problems:
        errors.extend(policy_problems)
        return

    # Point-in-time admission against the PINNED admission-time ledger bytes
    # and the historical policy bytes.
    decision, problems = evaluate_mission_admission(
        cand_doc, contr_doc, ledger_doc, schemas_dir, repo_root,
        policy_bytes=policy_bytes)
    if decision != "admitted":
        for problem in problems:
            errors.append(f"{where}.mission_input: {problem}")


def evaluate_mission_admission(candidate_doc: dict, contract_doc: dict, ledger: dict | None,
                               schemas_dir: Path, repo_root: Path,
                               policy_bytes: bytes | None = None) -> tuple[str, list[str]]:
    """POINT-IN-TIME mission admission for a MissionContract-native run.

    Returns (decision, problems): decision is one of
    ``admitted`` | ``invalid_input`` | ``mission_rejected``.
    - invalid_input: structural/integrity failures (schema, policy pin,
      provenance chain, non-executable terminal contract status, ledger
      reopen guard without override) — never infer a mission decision from
      corrupted input.
    - mission_rejected: structurally valid, correctly pinned, but the framing
      violates Mission Policy (POL-02 unbounded framing) or the contract is
      already terminally mission_rejected — terminal admission outcome
      BEFORE any Scout dispatch; the existing rejection is preserved, not
      re-derived.

    The ledger argument is the EXACT admission-time snapshot (a pinned
    admission_ledger for existing manifests; the current ledger bytes for a
    new run being formulated, pinned before the manifest is created). The
    live ledger is never consulted here.
    """
    problems: list[str] = []
    _validate_mission_doc(candidate_doc, schemas_dir, repo_root, "mission-candidate", problems)
    _validate_mission_doc(contract_doc, schemas_dir, repo_root, "mission-contract", problems)
    if problems:
        return "invalid_input", problems

    # Policy pin: new formulations validate against the CURRENT canonical
    # policy bytes; an existing Run Manifest passes the historical policy
    # bytes resolved at the pinned contract commit (check_mission_pin).
    if policy_bytes is None:
        try:
            policy_bytes = (repo_root / _CANONICAL_POLICY_PATH).read_bytes()
        except OSError as exc:
            problems.append(f"mission admission: cannot read canonical policy: {exc}")
            return "invalid_input", problems
    problems.extend(_policy_pin_problems(contract_doc, policy_bytes, "mission admission"))
    if problems:
        return "invalid_input", problems

    # Provenance chain.
    if contract_doc.get("source_candidate_id") != candidate_doc.get("candidate_id"):
        problems.append(
            "mission admission: contract source_candidate_id does not match the candidate_id"
        )
    cand_signals = {r.get("signal_id") for r in candidate_doc.get("signal_refs", [])}
    contract_signals = {r.get("signal_id") for r in contract_doc.get("signal_refs", [])}
    if not cand_signals or not cand_signals <= contract_signals:
        problems.append(
            "mission admission: contract signal identities do not cover the candidate signals"
        )
    if candidate_doc.get("disposition") not in ("selected",):
        problems.append(
            f"mission admission: candidate disposition "
            f"{candidate_doc.get('disposition')!r} does not permit execution"
        )
    if candidate_doc.get("mission_id") is not None and \
            candidate_doc["mission_id"] != contract_doc.get("mission_id"):
        problems.append("mission admission: candidate mission_id does not agree with the contract")
    if candidate_doc.get("target_repository") != contract_doc.get("target_repository"):
        problems.append("mission admission: target repository disagreement")
    if problems:
        return "invalid_input", problems

    # Terminal MissionContract statuses (executable vs non-executable).
    status = contract_doc.get("status")
    if status == "mission_rejected":
        problems.append(
            "mission admission: contract is terminally mission_rejected; the existing "
            f"rejection is preserved, not re-derived (reason: {contract_doc.get('rejection_reason') or 'none'})"
        )
        return "mission_rejected", problems
    if status in ("cancelled", "completed"):
        problems.append(
            f"mission admission: contract status {status!r} is not executable as a new "
            f"mission; revisiting the underlying signal requires a NEW "
            f"MissionCandidate/MissionContract path with Ledger override semantics (POL-04)"
        )
        return "invalid_input", problems
    if status != "proposed":
        problems.append(
            f"mission admission: contract status {status!r} is not permitted for execution"
        )
        return "invalid_input", problems

    # Semantic mission-framing rejection (structurally valid input).
    objective = str(contract_doc.get("objective") or "").lower()
    if re.search(r"improve (the )?(overall )?(quality|health) of (the )?(repository|repo)", objective):
        problems.append("POL-02: unbounded 'improve this repo' framing")
        return "mission_rejected", problems

    # Mission Ledger POL-04 reopen guard against the EXACT admission-time
    # ledger snapshot passed in.
    ledger_errors: list[str] = []
    check_ledger_reopen(candidate_doc, ledger, "mission admission", ledger_errors)
    if ledger_errors:
        return "invalid_input", ledger_errors
    return "admitted", []


def validate_static_mission_package(candidate_doc: dict, contract_doc: dict,
                                    schemas_dir: Path, repo_root: Path,
                                    where: str, errors: list[str]) -> None:
    """Static MissionPackage validation: the immutable Candidate/Contract
    relationships of a committed missions/<mission-id>/ package.

    Static validation NEVER consults the Mission Ledger — admission is a
    point-in-time decision made against the ledger snapshot pinned at run
    opening; a historical mission package does not become invalid merely
    because the live ledger later recorded its completion.
    """
    _validate_mission_doc(candidate_doc, schemas_dir, repo_root, f"{where}/candidate", errors)
    _validate_mission_doc(contract_doc, schemas_dir, repo_root, f"{where}/contract", errors)
    if contract_doc.get("source_candidate_id") != candidate_doc.get("candidate_id"):
        errors.append(
            f"{where}: contract source_candidate_id does not match the candidate_id"
        )
    cand_signals = {r.get("signal_id") for r in candidate_doc.get("signal_refs", [])}
    contract_signals = {r.get("signal_id") for r in contract_doc.get("signal_refs", [])}
    if not cand_signals or not cand_signals <= contract_signals:
        errors.append(f"{where}: contract signal identities do not cover the candidate signals")
    if candidate_doc.get("mission_id") is not None and \
            candidate_doc["mission_id"] != contract_doc.get("mission_id"):
        errors.append(f"{where}: candidate mission_id does not agree with the contract")
    if candidate_doc.get("target_repository") != contract_doc.get("target_repository"):
        errors.append(f"{where}: target repository disagreement")


# ── Committed run bundle checks ────────────────────────────────────────────

RUN_ARTIFACT_PREFIXES = (
    "run-manifest",
    "repair-candidate",
    "repair-result",
    "review-result",
    "run-assessment",
)


def _find_artifacts(run_dir: Path, prefix: str) -> list[Path]:
    """Return every artifact file in *run_dir* with the given prefix."""
    return [
        path
        for path in sorted(run_dir.iterdir())
        if (
            path.is_file()
            and path.name.startswith(prefix)
            and path.suffix.lower() in (".yaml", ".yml", ".json")
        )
    ]


def _resolve_single(
    run_dir: Path,
    prefix: str,
    where: str,
    errors: list[str],
) -> Path | None:
    """Resolve zero-or-one artifact of *prefix*; reject duplicates.

    Returns the single artifact path, or ``None`` when absent. More than one
    artifact of a recognized type is an error (the directory is ambiguous).
    """
    matches = _find_artifacts(run_dir, prefix)
    if len(matches) > 1:
        errors.append(
            f"{where}: duplicate {prefix} artifacts: "
            + ", ".join(p.name for p in matches)
        )
    return matches[0] if matches else None


def _load_if_valid(path: Path | None, schemas_dir: Path, repo_root: Path,
                   errors: list[str], registry: dict | None) -> dict | None:
    """Validate one bundle artifact; return its document when valid."""
    if path is None:
        return None
    before = len(errors)
    validate_artifact(path, schemas_dir, repo_root, errors, registry)
    if len(errors) > before:
        return None
    try:
        doc = load_document(path)
    except Exception as exc:  # pragma: no cover - validate_artifact already loaded it
        errors.append(f"{path.name}: could not load: {exc}")
        return None
    return doc if isinstance(doc, dict) else None


def validate_run_bundles(
    runs_dir: Path,
    schemas_dir: Path,
    repo_root: Path,
    errors: list[str],
    registry: dict | None = None,
) -> None:
    """Validate every committed run bundle below *runs_dir* and cross-check it.

    Per run directory: exactly one ``run-manifest`` artifact is required; zero
    or one of each of ``repair-candidate``, ``repair-result``, and
    ``review-result`` is allowed; duplicates of any recognized type are
    rejected; incomplete in-progress runs (e.g. manifest-only or
    manifest + candidate) remain permitted. For the artifacts that are
    present: all parse and satisfy their schemas; all carry the same
    ``run_id``; target repository and baseline SHA agree with the manifest;
    the repair result's ``candidate_id`` matches the selected candidate; the
    review result's ``candidate_id``/``result_id`` match the candidate and
    repair result; the review ``reviewer_head_sha`` equals the repair
    ``repair_head_sha``; prompt pins resolve to exact released prompt hashes.
    """
    if not runs_dir.is_dir():
        errors.append(f"runs: directory not found: {runs_dir}")
        return
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not run_dirs:
        return
    for run_dir in run_dirs:
        where = f"runs/{run_dir.name}"
        manifest_path = _resolve_single(run_dir, "run-manifest", where, errors)
        candidate_path = _resolve_single(run_dir, "repair-candidate", where, errors)
        result_path = _resolve_single(run_dir, "repair-result", where, errors)
        review_path = _resolve_single(run_dir, "review-result", where, errors)
        if manifest_path is None:
            errors.append(f"{where}: missing required run-manifest artifact")
            continue
        if not any((candidate_path, result_path, review_path)):
            # Manifest-only requested run: valid, nothing further to cross-check.
            continue

        manifest = _load_if_valid(manifest_path, schemas_dir, repo_root, errors, registry)
        candidate = _load_if_valid(candidate_path, schemas_dir, repo_root, errors, registry)
        result = _load_if_valid(result_path, schemas_dir, repo_root, errors, registry)
        review = _load_if_valid(review_path, schemas_dir, repo_root, errors, registry)

        # Same run_id across all present artifacts.
        run_ids: dict[str, str] = {}
        for label, doc in (("manifest", manifest), ("candidate", candidate),
                           ("result", result), ("review", review)):
            if doc is None:
                continue
            rid = doc.get("run_id")
            if isinstance(rid, str) and rid:
                run_ids[label] = rid
        if len(set(run_ids.values())) > 1:
            errors.append(f"{where}: run_id mismatch across artifacts: {run_ids}")

        # Target repository and baseline SHA agree with the manifest.
        if manifest is not None:
            for label, doc in (("candidate", candidate), ("result", result),
                               ("review", review)):
                if doc is None:
                    continue
                if doc.get("target_repository") != manifest.get("target_repository"):
                    errors.append(
                        f"{where}.{label}: target_repository does not match the run manifest"
                    )
                # baseline_sha is compared only where the artifact schema
                # declares the field (candidate and repair result).
                if "baseline_sha" in doc and doc.get("baseline_sha") != manifest.get("baseline_sha"):
                    errors.append(f"{where}.{label}: baseline_sha does not match the run manifest")

        # Repair result candidate_id matches the selected candidate.
        if candidate is not None and result is not None:
            if result.get("candidate_id") != candidate.get("candidate_id"):
                errors.append(f"{where}: repair-result candidate_id does not match the candidate")

        # Review result chains to the candidate and the repair result.
        if review is not None:
            if candidate is not None and review.get("candidate_id") != candidate.get("candidate_id"):
                errors.append(f"{where}: review-result candidate_id does not match the candidate")
            if result is not None and review.get("result_id") != result.get("result_id"):
                errors.append(f"{where}: review-result result_id does not match the repair-result")
            if result is not None and review.get("reviewer_head_sha") != result.get("repair_head_sha"):
                errors.append(
                    f"{where}: review-result reviewer_head_sha does not match "
                    "repair-result repair_head_sha"
                )

        # Canonical terminal RunAssessment (Mission terminal feedback v0.1).
        assessment_path = _resolve_single(run_dir, "run-assessment", where, errors)
        assessment = _load_if_valid(assessment_path, schemas_dir, repo_root, errors, registry)
        if assessment is not None:
            # Executed-run identity chain: assessment.run_id == directory
            # run-id == run-manifest.run_id; target and mission_id agree.
            if assessment.get("run_id") != run_dir.name:
                errors.append(
                    f"{where}.run-assessment: run_id {assessment.get('run_id')!r} does not "
                    f"match the directory run id {run_dir.name!r}"
                )
            if manifest is not None and assessment.get("run_id") != manifest.get("run_id"):
                errors.append(
                    f"{where}.run-assessment: run_id does not match the run manifest"
                )
            if manifest is not None and assessment.get("target_repository") != manifest.get("target_repository"):
                errors.append(
                    f"{where}.run-assessment: target_repository does not match the run manifest"
                )
            if manifest is not None and "baseline_sha" in assessment and \
                    assessment.get("baseline_sha") != manifest.get("baseline_sha"):
                errors.append(
                    f"{where}.run-assessment: baseline_sha does not match the run manifest"
                )
            if manifest is not None:
                mission_input = manifest.get("mission_input")
                if isinstance(mission_input, dict) and isinstance(mission_input.get("mission_id"), str) \
                        and assessment.get("mission_id") != mission_input.get("mission_id"):
                    errors.append(
                        f"{where}.run-assessment: mission_id does not match mission_input.mission_id"
                    )
        # MissionContract-native terminal runs REQUIRE a canonical assessment;
        # legacy maintenance_request runs are grandfathered.
        if manifest is not None and isinstance(manifest.get("mission_input"), dict) \
                and manifest.get("pipeline_state") in _TERMINAL_STATES \
                and assessment is None:
            errors.append(
                f"{where}: MissionContract-native terminal run requires a canonical "
                f"run-assessment.yaml"
            )


# ── Mission terminal feedback (v0.1): assessment <-> ledger agreement ─────


def _ledger_item_by_signal(ledger: dict | None, signal_id: str) -> dict | None:
    if ledger is None:
        return None
    for item in ledger.get("items", []):
        if item.get("signal_id") == signal_id:
            return item
    return None


def _mission_signal_id(missions_dir: Path, mission_id: str) -> str | None:
    """The first signal identity of a committed mission package, or None."""
    cand = missions_dir / mission_id / "mission-candidate.yaml"
    if not cand.exists():
        return None
    try:
        doc = load_document(cand)
    except Exception:
        return None
    refs = doc.get("signal_refs", []) if isinstance(doc, dict) else []
    for ref in refs:
        if isinstance(ref, dict) and isinstance(ref.get("signal_id"), str):
            return ref["signal_id"]
    return None


def check_assessment_locations(paths, errors: list[str]) -> None:
    """Reject committed run-assessment files outside the two canonical
    locations. *paths* is a list of repository-relative path strings."""
    for rel in paths:
        p = Path(rel)
        parts = p.parts
        # Canonical executed assessment: runs/<run-id>/run-assessment.yaml
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "run-assessment.yaml":
            continue
        # Canonical pre-run rejection: missions/<mission-id>/run-assessment.yaml
        if len(parts) == 3 and parts[0] == "missions" and parts[2] == "run-assessment.yaml":
            continue
        # examples/ and tests/ are the non-canonical fixtures trees.
        if parts and parts[0] in ("examples", "tests"):
            continue
        errors.append(
            f"{rel}: run-assessment outside the canonical locations "
            f"(runs/<run-id>/ or missions/<mission-id>/)"
        )


def check_terminal_feedback(runs_dir: Path, missions_dir: Path, ledger: dict | None,
                            schemas_dir: Path, repo_root: Path, errors: list[str]) -> None:
    """Canonical terminal RunAssessment <-> Mission Ledger agreement, and the
    canonical assessment locations (Mission terminal feedback v0.1).

    Executed runs: runs/<run-id>/run-assessment.yaml. Pre-run rejections:
    missions/<mission-id>/run-assessment.yaml (run_id null, no execution
    artifacts, and NO run manifest may exist with the same mission_id —
    rejection happened BEFORE run initialization). One canonical assessment
    per terminal mission attempt.

    Time semantics: a RunAssessment is historical terminal truth; the Ledger
    is CURRENT signal state. If the current Ledger item still points to the
    assessed mission, the disposition must agree; if the signal was
    legitimately reopened (POL-04 override) into a newer mission, the
    historical assessment stays valid — only the immutable signal identity
    and the historical run linkage (related_run_ids) are required.
    """
    ledger_by_signal = {i.get("signal_id"): i for i in (ledger or {}).get("items", [])}

    executed: dict[str, dict] = {}   # mission_id -> assessment doc (runs/)
    for run_dir in sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []:
        if not run_dir.is_dir():
            continue
        assessment_path = run_dir / "run-assessment.yaml"
        if not assessment_path.exists():
            continue
        try:
            doc = load_document(assessment_path)
        except Exception as exc:
            errors.append(f"runs/{run_dir.name}/run-assessment.yaml: could not load: {exc}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"runs/{run_dir.name}/run-assessment.yaml: must be an object")
            continue
        if doc.get("terminal_outcome") == "mission_rejected":
            errors.append(
                f"runs/{run_dir.name}/run-assessment.yaml: pre-run rejection must live at "
                f"missions/<mission-id>/run-assessment.yaml, not in a run directory"
            )
            continue
        mission_id = doc.get("mission_id")
        run_id = doc.get("run_id")
        if isinstance(mission_id, str):
            if mission_id in executed:
                errors.append(
                    f"duplicate canonical terminal assessment for mission {mission_id!r} "
                    f"(runs/{run_dir.name}/run-assessment.yaml and another run)"
                )
            executed[mission_id] = doc
        signal_id = _mission_signal_id(missions_dir, mission_id) if isinstance(mission_id, str) else None
        if signal_id is None:
            continue  # legacy assessment without a mission package: grandfathered
        item = ledger_by_signal.get(signal_id)
        if item is None:
            errors.append(
                f"runs/{run_dir.name}/run-assessment.yaml: signal {signal_id!r} has no "
                f"Mission Ledger item"
            )
            continue
        if item.get("mission_id") == mission_id:
            # The Ledger still represents this terminal mission as CURRENT
            # state: disposition must agree.
            if item.get("disposition") != doc.get("ledger_disposition"):
                errors.append(
                    f"runs/{run_dir.name}/run-assessment.yaml: Ledger disposition "
                    f"{item.get('disposition')!r} does not match assessment "
                    f"{doc.get('ledger_disposition')!r}"
                )
        # If the Ledger now points to a newer mission (POL-04 reopen), the
        # historical disposition is not compared; the run linkage remains.
        related = item.get("related_run_ids") or []
        if isinstance(run_id, str) and run_id not in related:
            errors.append(
                f"runs/{run_dir.name}/run-assessment.yaml: Ledger related_run_ids does not "
                f"contain the executed run id {run_id!r}"
            )

    if missions_dir.is_dir():
        for mission_dir in sorted(p for p in missions_dir.iterdir() if p.is_dir()):
            assessment_path = mission_dir / "run-assessment.yaml"
            if not assessment_path.exists():
                continue
            try:
                doc = load_document(assessment_path)
            except Exception as exc:
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: could not load: {exc}"
                )
                continue
            if not isinstance(doc, dict):
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: must be an object"
                )
                continue
            if doc.get("terminal_outcome") != "mission_rejected" or \
                    doc.get("run_id") is not None:
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: pre-run rejection must "
                    f"use terminal_outcome mission_rejected with run_id null"
                )
            mission_id = mission_dir.name
            if doc.get("mission_id") != mission_id:
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: mission_id does not "
                    f"match the package directory"
                )
            if mission_id in executed:
                errors.append(
                    f"duplicate canonical terminal assessment for mission {mission_id!r}: both "
                    f"a pre-run rejection (missions/) and an executed run assessment (runs/)"
                )
            # A pre-run rejection claims NO run was initialized: no run
            # manifest may reference this mission.
            if runs_dir.is_dir():
                for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
                    manifest_path = run_dir / "run-manifest.yaml"
                    if not manifest_path.exists():
                        continue
                    try:
                        manifest = load_document(manifest_path)
                    except Exception:
                        continue
                    if not isinstance(manifest, dict):
                        continue
                    mi = manifest.get("mission_input")
                    if isinstance(mi, dict) and mi.get("mission_id") == mission_id:
                        errors.append(
                            f"missions/{mission_dir.name}/run-assessment.yaml: rejected BEFORE "
                            f"run initialization but runs/{run_dir.name}/run-manifest.yaml "
                            f"references mission {mission_id!r}"
                        )
            signal_id = _mission_signal_id(missions_dir, mission_id)
            if signal_id is None:
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: mission package has no "
                    f"signal identity"
                )
                continue
            item = ledger_by_signal.get(signal_id)
            if item is None:
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: signal {signal_id!r} "
                    f"has no Mission Ledger item"
                )
                continue
            if item.get("mission_id") == mission_id and item.get("disposition") != "rejected":
                errors.append(
                    f"missions/{mission_dir.name}/run-assessment.yaml: Ledger disposition must "
                    f"be rejected for a pre-run rejection, got {item.get('disposition')!r}"
                )
            # If the signal was legitimately reopened into a newer mission,
            # the historical rejection remains valid (no forcing).

    # Arbitrary assessment locations fail: only the two canonical patterns.
    try:
        committed = subprocess.run(
            ["git", "ls-files", "*run-assessment*.yaml", "*run-assessment*.yml"],
            capture_output=True, text=True, cwd=repo_root,
        ).stdout.splitlines()
    except Exception:
        committed = []
    check_assessment_locations(committed, errors)


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Federation HQ artifact contracts.")
    parser.add_argument("--repo-root", default=None, help="Repository root (default: this checkout).")
    parser.add_argument("--registry", default=None, help="Prompt registry path (default: prompts/registry.yaml).")
    parser.add_argument("--schemas-dir", default=None, help="Contracts directory (default: contracts/).")
    parser.add_argument("--examples-dir", default=None, help="Examples directory (default: examples/).")
    parser.add_argument("--runs-dir", default=None, help="Runs directory (default: runs/).")
    parser.add_argument("--artifact", default=None, help="Validate a single artifact file (default: all examples).")
    parser.add_argument("--quiet", action="store_true", help="Only print failures.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else REPO_ROOT
    registry_path = Path(args.registry).resolve() if args.registry else repo_root / "prompts" / "registry.yaml"
    schemas_dir = Path(args.schemas_dir).resolve() if args.schemas_dir else repo_root / "contracts"
    errors: list[str] = []

    registry = validate_registry(registry_path, repo_root, errors)
    validate_schemas(schemas_dir, errors)
    if args.artifact:
        validate_artifact(Path(args.artifact).resolve(), schemas_dir, repo_root, errors, registry)
    else:
        examples_dir = Path(args.examples_dir).resolve() if args.examples_dir else repo_root / "examples"
        validate_examples(examples_dir, schemas_dir, repo_root, errors, registry)
        runs_dir = Path(args.runs_dir).resolve() if args.runs_dir else repo_root / "runs"
        validate_run_bundles(runs_dir, schemas_dir, repo_root, errors, registry)
        # The persistent Mission Ledger is repository-native structured state.
        # Its filename (ledger.yaml) does not match the mission-ledger prefix
        # matcher, so it is validated explicitly against its schema.
        ledger_path = repo_root / "mission" / "ledger.yaml"
        if ledger_path.exists():
            try:
                ledger_doc = load_document(ledger_path)
            except Exception as exc:
                errors.append(f"mission/ledger.yaml: could not load: {exc}")
            else:
                if not isinstance(ledger_doc, dict):
                    errors.append("mission/ledger.yaml: artifact must be an object")
                else:
                    try:
                        ledger_schema = json.loads(
                            (schemas_dir / "mission" / "mission-ledger.schema.json")
                            .read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, OSError) as exc:
                        errors.append(
                            f"mission/ledger.yaml: cannot read ledger schema: {exc}"
                        )
                    else:
                        validate_value(ledger_doc, ledger_schema, "mission/ledger.yaml", errors)
                        check_paths(ledger_doc, repo_root, errors)
        # Committed mission packages under missions/<mission-id>/: STATIC
        # validation only. Admission is a point-in-time decision against the
        # ledger snapshot pinned at run opening; the live ledger is never
        # consulted here, so a historical package whose signal later became
        # completed remains valid.
        missions_dir = repo_root / "missions"
        if missions_dir.is_dir():
            for mission_dir in sorted(p for p in missions_dir.iterdir() if p.is_dir()):
                cand = mission_dir / "mission-candidate.yaml"
                contr = mission_dir / "mission-contract.yaml"
                if not cand.exists() or not contr.exists():
                    errors.append(f"missions/{mission_dir.name}: missing candidate or contract")
                    continue
                try:
                    cand_doc = load_document(cand)
                    contr_doc = load_document(contr)
                except Exception as exc:
                    errors.append(f"missions/{mission_dir.name}: could not load: {exc}")
                    continue
                validate_static_mission_package(
                    cand_doc, contr_doc, schemas_dir, repo_root,
                    f"missions/{mission_dir.name}", errors)
        # Canonical terminal RunAssessment <-> Mission Ledger agreement and
        # canonical assessment locations (Mission terminal feedback v0.1).
        check_terminal_feedback(runs_dir, missions_dir, ledger_doc, schemas_dir,
                                repo_root, errors)

    if errors:
        if not args.quiet:
            print("Federation HQ artifact validation FAILED:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print("Federation HQ artifact validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
