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
    if "coordination-message" in path.name:
        check_coordination_message(doc, path.name, errors)


def validate_examples(
    examples_dir: Path,
    schemas_dir: Path,
    repo_root: Path,
    errors: list[str],
    registry: dict | None = None,
) -> None:
    """Validate every example artifact against its schema."""
    for path in sorted(examples_dir.iterdir()):
        if not path.is_file():
            continue
        validate_artifact(path, schemas_dir, repo_root, errors, registry)


# ── Committed run bundle checks ────────────────────────────────────────────

RUN_ARTIFACT_PREFIXES = (
    "run-manifest",
    "repair-candidate",
    "repair-result",
    "review-result",
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
