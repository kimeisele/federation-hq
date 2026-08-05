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
    if typ == "string":
        if not isinstance(value, str):
            errors.append(f"{where}: expected string, got {type(value).__name__}")
            return
        pattern = schema.get("pattern")
        if pattern:
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{where}: string {value!r} does not match pattern {pattern!r}")
            except re.error:
                pass  # malformed pattern in schema: not a document problem
        return
    if typ == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{where}: expected integer, got {type(value).__name__}")
        return
    if typ == "object":
        if not isinstance(value, dict):
            errors.append(f"{where}: expected object, got {type(value).__name__}")
            return
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
    if typ == "array":
        if not isinstance(value, list):
            errors.append(f"{where}: expected array, got {type(value).__name__}")
            return
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_value(item, items, f"{where}[{index}]", errors, root)
        return
    if typ is None:
        return
    errors.append(f"{where}: unsupported schema type {typ!r}")


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
