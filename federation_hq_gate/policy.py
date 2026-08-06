"""Safe multi-repository branch-policy planner and applier.

Two-phase, dry-run by default. Branch-policy reads and writes always use the
repository owner's authenticated ``gh`` session — never the Gate App token.
The Gate App is used only for the bootstrap check-run. No command mutates
all repositories without the explicit plan-hash confirmation.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import CHECK_RUN_NAME

DEFAULT_EXCLUDES: set[str] = set()


class PolicyError(RuntimeError):
    """A policy planning/application failure (secret-safe)."""


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh api``; separated so tests can inject a fake transport."""
    return subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def gh_get(path: str) -> dict | list | None:
    result = _run_gh(["--method", "GET", path])
    if result.returncode != 0:
        raise PolicyError(f"gh GET {path} failed: {(result.stderr or result.stdout)[:300]}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def gh_put(path: str, body: dict) -> dict | None:
    result = subprocess.run(
        ["gh", "api", "--method", "PUT", path, "--input", "-"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PolicyError(f"gh PUT {path} failed: {(result.stderr or result.stdout)[:300]}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def plan_sha256(plan: dict) -> str:
    """Deterministic canonical form of the plan for hash confirmation.

    Volatile audit fields (generated_at) and the self-referential
    plan_sha256 field are excluded so the confirmed hash is stable.
    """
    stable = {k: v for k, v in plan.items() if k not in ("plan_sha256", "generated_at")}
    canonical = json.dumps(stable, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_protection(repo: dict, default_branch: str) -> dict:
    """Capture the protection facts needed for drift detection and backup."""
    classic = None
    try:
        classic = gh_get(f"/repos/{repo['full_name']}/branches/{default_branch}/protection")
    except PolicyError:
        classic = None
    rulesets = None
    try:
        rulesets = gh_get(f"/repos/{repo['full_name']}/rulesets")
    except PolicyError:
        rulesets = None
    return {"classic": classic, "rulesets": rulesets if isinstance(rulesets, list) else None}


def discover_repositories(owner: str, exclusions: set[str]) -> list[dict]:
    """Discover owned, non-fork, non-archived repositories."""
    repos: list[dict] = []
    page = 1
    while True:
        data = gh_get(f"/user/repos?affiliation=owner&per_page=100&page={page}")
        if not isinstance(data, list) or not data:
            break
        repos.extend(r for r in data if isinstance(r, dict))
        if len(data) < 100:
            break
        page += 1
        if page > 10:
            break
    return repos


def build_plan(owner: str, exclusions: set[str]) -> dict:
    """Build the deterministic two-phase policy plan (no mutations)."""
    repos = discover_repositories(owner, exclusions)
    entries = []
    skipped = []
    for repo in repos:
        full = repo.get("full_name", "")
        if not isinstance(full, str) or "/" not in full:
            continue
        if repo.get("fork"):
            skipped.append({"repository": full, "reason": "fork"})
            continue
        if repo.get("archived"):
            skipped.append({"repository": full, "reason": "archived"})
            continue
        if full in exclusions:
            skipped.append({"repository": full, "reason": "explicit exclusion"})
            continue
        default_branch = repo.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            skipped.append({"repository": full, "reason": "no default branch"})
            continue
        perms = repo.get("permissions") or {}
        protection = _normalize_protection(repo, default_branch)
        existing_checks = _existing_required_checks(protection, default_branch)
        skip_reason = None
        if not perms.get("admin"):
            skip_reason = "insufficient owner admin permission for protection writes"
        if skip_reason is None and protection["classic"] is None and protection["rulesets"] is None:
            # A 404 on both endpoints with admin permission usually means the
            # GitHub plan does not support branch protection.
            skip_reason = "branch protection unavailable (plan/permissions limitation)"
        entries.append({
            "repository": full,
            "default_branch": default_branch,
            "protected": protection["classic"] is not None or bool(protection["rulesets"]),
            "existing_required_checks": sorted(existing_checks),
            "protection_snapshot": protection,
            "skip_reason": skip_reason,
        })
    entries.sort(key=lambda e: e["repository"])
    plan = {
        "schema_version": 1,
        "owner": owner,
        "check_name": CHECK_RUN_NAME,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run_default": True,
        "repositories": entries,
        "skipped": sorted(skipped, key=lambda s: s["repository"]),
    }
    plan["plan_sha256"] = plan_sha256(plan)
    return plan


def _existing_required_checks(protection: dict, default_branch: str) -> list[str]:
    checks: set[str] = set()
    classic = protection.get("classic")
    if isinstance(classic, dict):
        rsc = classic.get("required_status_checks") or {}
        for ctx in rsc.get("contexts") or []:
            if isinstance(ctx, str):
                checks.add(ctx)
    for rs in protection.get("rulesets") or []:
        if not isinstance(rs, dict):
            continue
        for rule in rs.get("rules") or []:
            if rule.get("type") == "required_status_checks":
                for entry in rule.get("parameters", {}).get("checks") or []:
                    context = entry.get("context")
                    if isinstance(context, str):
                        checks.add(context)
    return list(checks)


def _protection_changed(planned: dict, now: dict) -> bool:
    """Material drift: default branch or protection snapshot changed."""
    if planned.get("default_branch") != now.get("default_branch"):
        return True
    if planned.get("protection_snapshot") != now.get("protection_snapshot"):
        return True
    return False


def apply_plan(plan: dict, *, expected_sha256: str, app_installation_token_fn,
               dry_run: bool, app_id: str | None = None,
               backup_dir: Path | None = None) -> dict:
    """Apply the confirmed plan per repository; report per-repo outcomes.

    *backup_dir* overrides the before-state backup location (tests use a
    temporary directory; the CLI defaults to the working directory).
    """
    if plan_sha256(plan) != expected_sha256:
        raise PolicyError("plan SHA-256 does not match the confirmed hash; refusing to apply")
    backup: dict[str, dict] = {}
    report = {"plan_sha256": expected_sha256, "dry_run": dry_run, "repositories": []}

    for entry in plan.get("repositories", []):
        full = entry["repository"]
        try:
            if entry.get("skip_reason"):
                report["repositories"].append({
                    "repository": full, "status": "skipped", "reason": entry["skip_reason"],
                })
                continue
            now = _current_state(full)
            if _protection_changed(entry, now):
                report["repositories"].append({
                    "repository": full, "status": "failed",
                    "reason": "repository state changed materially since planning",
                })
                continue
            backup[full] = {"default_branch": entry["default_branch"],
                            "protection": entry["protection_snapshot"]}
            if dry_run:
                report["repositories"].append({
                    "repository": full, "status": "dry_run", "would_configure": True,
                })
                continue
            bootstrap_outcome = _bootstrap_repo(app_installation_token_fn, full,
                                               entry["default_branch"])
            protection_outcome = _configure_protection(full, entry, now, app_id)
            verify = _verify_protection(full, entry["default_branch"])
            report["repositories"].append({
                "repository": full, "status": "configured",
                "bootstrap": bootstrap_outcome, "protection": protection_outcome,
                "verified": verify,
            })
        except PolicyError as exc:
            report["repositories"].append({
                "repository": full, "status": "failed", "reason": str(exc),
            })
            continue  # bounded per-repository failure; do not corrupt the fleet

    backup_path = None
    if backup and not dry_run:
        target_dir = backup_dir or Path.cwd()
        backup_path = target_dir / f"policy-backup-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        backup_path.write_text(json.dumps(backup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["backup_path"] = str(backup_path) if backup_path else None
    return report


def _current_state(full: str) -> dict:
    data = gh_get(f"/repos/{full}")
    default_branch = data.get("default_branch")
    return {
        "full_name": full,
        "default_branch": default_branch,
        "protection_snapshot": _normalize_protection(data, default_branch),
    }


def _bootstrap_repo(app_installation_token_fn, full: str, default_branch: str) -> str:
    from .bootstrap import branch_head_sha, publish_bootstrap_check
    try:
        token = app_installation_token_fn(owner=full.split("/", 1)[0], repo=full.split("/", 1)[1])
        head = branch_head_sha(token, full, default_branch)
        publish_bootstrap_check(token, full, head)
        return "bootstrapped"
    except Exception as exc:  # noqa: BLE001 - recorded per repository
        return f"bootstrap failed: {str(exc)[:200]}"


def _configure_protection(full: str, entry: dict, now: dict, app_id: str | None) -> str:
    """Configure the default-branch protection: approvals 0 + required check."""
    default_branch = entry["default_branch"]
    protection = now["protection_snapshot"]
    classic = protection.get("classic")
    rulesets = protection.get("rulesets") or []
    if rulesets:
        return _configure_via_ruleset(full, default_branch, rulesets, app_id)
    return _configure_via_classic(full, default_branch, classic)


def _configure_via_classic(full: str, default_branch: str, existing: dict) -> str:
    existing = existing or {}
    rsc = existing.get("required_status_checks") or {}
    contexts = [c for c in rsc.get("contexts") or [] if isinstance(c, str)]
    if CHECK_RUN_NAME not in contexts:
        contexts.append(CHECK_RUN_NAME)
    reviews = existing.get("required_pull_request_reviews") or {}
    body = {
        "required_status_checks": {
            "strict": bool(rsc.get("strict", True)),
            "contexts": sorted(contexts),
        },
        "enforce_admins": bool(existing.get("enforce_admins", False)),
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews", False)),
            "require_code_owner_reviews": bool(reviews.get("require_code_owner_reviews", False)),
            "require_last_push_approval": bool(reviews.get("require_last_push_approval", False)),
        },
        "restrictions": existing.get("restrictions"),
        "required_linear_history": bool(existing.get("required_linear_history", False)),
        "allow_force_pushes": bool(existing.get("allow_force_pushes", False)),
        "allow_deletions": bool(existing.get("allow_deletions", False)),
        "required_conversation_resolution": bool(
            existing.get("required_conversation_resolution", False)
        ),
    }
    gh_put(f"/repos/{full}/branches/{default_branch}/protection", body)
    return "classic-configured"


def _configure_via_ruleset(full: str, default_branch: str, rulesets: list,
                           app_id: str | None) -> str:
    """Add a review-gate ruleset without touching existing rulesets.

    The required check is bound to the exact Gate App ID via the ruleset
    ``integration_id`` field; when the App ID is unavailable the binding is
    by the app-owned check name only (recorded in the report).
    """
    existing = [r for r in rulesets if isinstance(r, dict) and r.get("name") == "federation-hq-review-gate-v1"]
    check_entry: dict = {"context": CHECK_RUN_NAME}
    if app_id:
        check_entry["integration_id"] = int(app_id)
    body = {
        "name": "federation-hq-review-gate-v1",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/heads/{default_branch}"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "checks": [check_entry],
                },
            },
        ],
    }
    if existing:
        gh_put(f"/repos/{full}/rulesets/{existing[0]['id']}", body)
        return "ruleset-updated"
    gh_put(f"/repos/{full}/rulesets", body)
    return "ruleset-created"


def _verify_protection(full: str, default_branch: str) -> dict:
    try:
        classic = gh_get(f"/repos/{full}/branches/{default_branch}/protection")
        rulesets = gh_get(f"/repos/{full}/rulesets")
    except PolicyError as exc:
        return {"ok": False, "reason": str(exc)}
    checks = _existing_required_checks(
        {"classic": classic, "rulesets": rulesets}, default_branch
    )
    reviews_ok = True
    if isinstance(classic, dict):
        reviews = classic.get("required_pull_request_reviews") or {}
        reviews_ok = reviews.get("required_approving_review_count") == 0
    return {
        "ok": CHECK_RUN_NAME in checks and reviews_ok,
        "required_check_present": CHECK_RUN_NAME in checks,
        "approval_count_zero": reviews_ok,
    }


def rollback(backup_path: Path) -> dict:
    """Restore before-state protection from a stored backup, verbatim."""
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    results = []
    for full, state in backup.items():
        try:
            protection = state.get("protection") or {}
            classic = protection.get("classic")
            if isinstance(classic, dict):
                gh_put(f"/repos/{full}/branches/{state['default_branch']}/protection", classic)
                results.append({"repository": full, "status": "restored"})
            else:
                results.append({"repository": full, "status": "skipped",
                                "reason": "no classic before-state recorded"})
        except PolicyError as exc:
            results.append({"repository": full, "status": "failed", "reason": str(exc)})
    return {"backup": str(backup_path), "results": results}
