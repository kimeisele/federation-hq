"""SHA-bound review-check publisher (federation-hq/review).

The Gate App is a mechanical attestor: it converts an already accepted
canonical Federation HQ review artifact into a Check Run on the exact
reviewed head SHA. It never decides a verdict, never merges, and never
reuses a check from another SHA.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from . import CHECK_RUN_NAME
from .http import GitHubError, request

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _runs_dir() -> Path:
    """Runs directory; overridable via FEDERATION_HQ_RUNS_DIR for tests."""
    import os
    override = os.environ.get("FEDERATION_HQ_RUNS_DIR")
    return Path(override).resolve() if override else (_REPO_ROOT / "runs")


class ReviewGateError(RuntimeError):
    """A validation or publication failure (secret-safe)."""


def _load_artifact_validator():
    """Import the repository's canonical artifact validator (scripts/)."""
    scripts = _REPO_ROOT / "scripts"
    spec = importlib.util.spec_from_file_location(
        "federation_hq_validate_artifacts", scripts / "validate_artifacts.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_yaml_document(path: Path) -> dict:
    validator = _load_artifact_validator()
    try:
        doc = validator.load_document(path)
    except Exception as exc:  # noqa: BLE001 - converted to a gate error
        raise ReviewGateError(f"cannot parse artifact {path.name}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ReviewGateError(f"artifact {path.name} is not an object")
    return doc


def _validate_against_schema(path: Path, schema_name: str) -> dict:
    validator = _load_artifact_validator()
    doc = _load_yaml_document(path)
    schema_path = _REPO_ROOT / "contracts" / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ReviewGateError(f"cannot load schema {schema_name}: {exc}") from exc
    errors: list[str] = []
    validator.validate_value(doc, schema, path.name, errors)
    if errors:
        raise ReviewGateError(
            f"canonical artifact {path.name} fails its schema: {errors[0]}"
        )
    return doc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_str(doc: dict, key: str, where: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise ReviewGateError(f"{where}.{key} missing or not a string")
    return value


def _require_list(doc: dict, key: str, where: str) -> list:
    value = doc.get(key)
    if not isinstance(value, list):
        raise ReviewGateError(f"{where}.{key} missing or not a list")
    return value


def resolve_run_artifacts(run_id: str, review_result_path: Path) -> dict:
    """Resolve and structurally validate the canonical run artifacts.

    Returns {manifest, candidate, repair, review} documents plus file paths.
    """
    run_dir = (_runs_dir() / run_id).resolve()
    if not run_dir.is_dir():
        raise ReviewGateError(f"run directory not found: {run_dir}")
    review_path = review_result_path.resolve()
    if review_path.parent != run_dir:
        raise ReviewGateError(
            f"review artifact must live in the canonical run directory {run_dir}"
        )
    if not review_path.name.startswith("review-result"):
        raise ReviewGateError("review artifact filename must start with 'review-result'")

    manifest_path = run_dir / "run-manifest.yaml"
    candidate_path = run_dir / "repair-candidate.yaml"
    repair_path = run_dir / "repair-result.yaml"
    for path in (manifest_path, candidate_path, repair_path):
        if not path.is_file():
            raise ReviewGateError(f"canonical artifact missing: {path.name}")

    manifest = _validate_against_schema(manifest_path, "run-manifest.schema.json")
    candidate = _validate_against_schema(candidate_path, "repair-candidate.schema.json")
    repair = _validate_against_schema(repair_path, "repair-result.schema.json")
    review = _validate_against_schema(review_path, "review-result.schema.json")

    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "candidate": candidate,
        "repair": repair,
        "review": review,
        "manifest_path": manifest_path,
        "candidate_path": candidate_path,
        "repair_path": repair_path,
        "review_path": review_path,
    }


def validate_review_chain(artifacts: dict, *, run_id: str, repository: str,
                          head_sha: str,
                          expected_repair_hash: str | None = None,
                          expected_review_hash: str | None = None) -> dict:
    """Independently validate the canonical lineage before publishing success.

    Returns a summary dict of verified facts.
    """
    manifest = artifacts["manifest"]
    candidate = artifacts["candidate"]
    repair = artifacts["repair"]
    review = artifacts["review"]

    where = "review-result"
    if review.get("run_id") != run_id:
        raise ReviewGateError(f"{where}: run_id {review.get('run_id')!r} does not match {run_id!r}")
    if review.get("target_repository") != repository:
        raise ReviewGateError(
            f"{where}: target_repository {review.get('target_repository')!r} "
            f"does not match {repository!r}"
        )
    if review.get("reviewer_head_sha") != head_sha:
        raise ReviewGateError(
            f"{where}: reviewer_head_sha {review.get('reviewer_head_sha')!r} "
            f"does not match requested head {head_sha!r}"
        )
    if review.get("verdict") != "approved":
        raise ReviewGateError(
            f"{where}: verdict is {review.get('verdict')!r}, not 'approved'"
        )
    blockers = _require_list(review, "blockers", where)
    if blockers:
        raise ReviewGateError(f"{where}: blockers is not empty: {blockers}")

    # Candidate -> repair -> review lineage.
    if repair.get("run_id") != run_id:
        raise ReviewGateError("repair-result: run_id does not match the run")
    if candidate.get("run_id") != run_id:
        raise ReviewGateError("repair-candidate: run_id does not match the run")
    if repair.get("candidate_id") != candidate.get("candidate_id"):
        raise ReviewGateError("repair-result candidate_id does not match the candidate")
    if review.get("candidate_id") != candidate.get("candidate_id"):
        raise ReviewGateError("review-result candidate_id does not match the candidate")
    if review.get("result_id") != repair.get("result_id"):
        raise ReviewGateError("review-result result_id does not match the repair result")
    if repair.get("repair_head_sha") != head_sha:
        raise ReviewGateError(
            "repair-result repair_head_sha does not match the reviewed head"
        )
    if repair.get("target_repository") != repository:
        raise ReviewGateError("repair-result target_repository does not match")
    if candidate.get("baseline_sha") != repair.get("baseline_sha"):
        raise ReviewGateError("candidate and repair baseline SHA disagree")

    # Hashes: computed from the canonical recorded bytes, must be well-formed
    # and, when pinned by the operator, match the accepted hashes exactly.
    repair_hash = _sha256(artifacts["repair_path"])
    review_hash = _sha256(artifacts["review_path"])
    for label, digest in (("repair-result", repair_hash), ("review-result", review_hash)):
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise ReviewGateError(f"{label} sha256 is malformed: {digest}")
    if expected_repair_hash is not None and expected_repair_hash != repair_hash:
        raise ReviewGateError(
            f"repair-result sha256 {repair_hash} does not match the accepted hash "
            f"{expected_repair_hash}"
        )
    if expected_review_hash is not None and expected_review_hash != review_hash:
        raise ReviewGateError(
            f"review-result sha256 {review_hash} does not match the accepted hash "
            f"{expected_review_hash}"
        )

    # No later accepted review supersedes this one: exactly one review-result
    # artifact in the canonical run directory.
    reviews = sorted(
        p for p in artifacts["run_dir"].iterdir()
        if p.is_file() and p.name.startswith("review-result")
        and p.suffix.lower() in (".yaml", ".yml", ".json")
    )
    if len(reviews) != 1 or reviews[0].resolve() != artifacts["review_path"].resolve():
        raise ReviewGateError(
            "a later accepted review supersedes this one; refusing to attest"
        )

    baseline_sha = _require_str(manifest, "baseline_sha", "run-manifest")
    return {
        "run_id": run_id,
        "repository": repository,
        "baseline_sha": baseline_sha,
        "reviewed_head_sha": head_sha,
        "repair_result_sha256": repair_hash,
        "review_result_sha256": review_hash,
        "verdict": "approved",
        "blocker_count": 0,
        "canonical_record": f"kimeisele/federation-hq runs/{run_id}/",
    }


def verify_remote_pr_head(token: str, repository: str, head_sha: str) -> int | None:
    """Verify the exact remote head is the current head of an open PR.

    Returns the PR number when found; raises on mismatch or absence.
    """
    owner, repo = repository.split("/", 1)
    try:
        pulls = request("GET", f"/repos/{owner}/{repo}/commits/{head_sha}/pulls", token=token)
    except GitHubError as exc:
        raise ReviewGateError(f"cannot inspect remote head: {exc}") from exc
    if not isinstance(pulls, list):
        raise ReviewGateError("unexpected response listing pull requests")
    for pr in pulls:
        if not isinstance(pr, dict) or pr.get("state") != "open":
            continue
        pr_head = (pr.get("head") or {}).get("sha")
        if pr_head != head_sha:
            raise ReviewGateError(
                f"remote PR #{pr.get('number')} head {pr_head} no longer equals the "
                f"reviewed head {head_sha}; a new successful check is required"
            )
        return pr.get("number")
    raise ReviewGateError(
        f"no open pull request contains the reviewed head {head_sha} on the remote"
    )


def existing_successful_check(token: str, repository: str, head_sha: str,
                              run_id: str, app_id: str) -> dict | None:
    """Return the previously published check for (run, repo, head) if present."""
    owner, repo = repository.split("/", 1)
    try:
        data = request(
            "GET", f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs?app_id={app_id}",
            token=token,
        )
    except GitHubError:
        return None
    runs = data.get("check_runs", []) if isinstance(data, dict) else []
    for run in runs:
        if run.get("name") != CHECK_RUN_NAME:
            continue
        output = run.get("output") or {}
        summary = output.get("summary") or ""
        if run.get("status") == "completed" and run.get("conclusion") == "success" \
                and f"run_id: {run_id}" in summary:
            return run
    return None


def publish_success_check(token: str, repository: str, head_sha: str,
                          summary: dict, pr_number: int | None, app_id: str) -> dict:
    """Publish (idempotently) the completed success check on the exact head."""
    owner, repo = repository.split("/", 1)
    existing = existing_successful_check(token, repository, head_sha, summary["run_id"], app_id)
    if existing is not None:
        return {"idempotent": True, "check_run": existing}
    lines = [
        f"run_id: {summary['run_id']}",
        f"repository: {summary['repository']}",
        f"pr_number: {pr_number if pr_number is not None else 'n/a'}",
        f"baseline_sha: {summary['baseline_sha']}",
        f"reviewed_head_sha: {summary['reviewed_head_sha']}",
        f"repair_result_sha256: {summary['repair_result_sha256']}",
        f"review_result_sha256: {summary['review_result_sha256']}",
        f"verdict: {summary['verdict']}",
        f"blocker_count: {summary['blocker_count']}",
        f"canonical_record: {summary['canonical_record']}",
    ]
    data = request(
        "POST",
        f"/repos/{owner}/{repo}/check-runs",
        token=token,
        body={
            "name": CHECK_RUN_NAME,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success",
            "output": {
                "title": f"Federation HQ review approved: {summary['run_id']}",
                "summary": "\n".join(lines),
            },
        },
    )
    if not isinstance(data, dict):
        raise ReviewGateError("check-run publication returned an unexpected response")
    return {"idempotent": False, "check_run": data}


def publish_failure_check(token: str, repository: str, head_sha: str,
                          reason: str) -> dict:
    """Publish a failure check (or return None when head is unknown remotely)."""
    owner, repo = repository.split("/", 1)
    data = request(
        "POST",
        f"/repos/{owner}/{repo}/check-runs",
        token=token,
        body={
            "name": CHECK_RUN_NAME,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "failure",
            "output": {
                "title": "Federation HQ review gate: validation failed",
                "summary": f"reason: {reason[:1000]}",
            },
        },
    )
    return data if isinstance(data, dict) else {}
