"""Safe multi-repository branch-policy planner and applier.

Two-phase, dry-run by default. Branch-policy reads and writes always use the
repository owner's authenticated ``gh`` session — never the Gate App token.
The Gate App is used only for the bootstrap check-run and the App-ID binding
of the required check. No command mutates all repositories without the
explicit plan-hash confirmation.

Safety invariants enforced here:
- bootstrap failure stops all protection mutation for that repository;
- Classic required checks carry the exact Gate App ID and are never
  downgraded to unbound contexts;
- ``--include`` limits the configurable plan to exactly the requested
  repositories (unknown, owner-mismatched, or contradictory includes fail);
- rollback restores the mechanism actually changed (ruleset created ->
  deleted, ruleset updated -> restored, classic created -> removed,
  classic updated -> restored) and verifies the remote result.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import CHECK_RUN_NAME

GATE_RULESET_NAME = "federation-hq-review-gate-v1"


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


def gh_delete(path: str) -> None:
    result = _run_gh(["--method", "DELETE", path])
    if result.returncode != 0:
        raise PolicyError(f"gh DELETE {path} failed: {(result.stderr or result.stdout)[:300]}")


def plan_sha256(plan: dict) -> str:
    """Deterministic canonical form of the plan for hash confirmation.

    Volatile audit fields (generated_at) and the self-referential
    plan_sha256 field are excluded so the confirmed hash is stable. The
    include/exclude scope IS part of the hash.
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


def discover_repositories(owner: str) -> list[dict]:
    """Discover owned repositories (fork/archived filtered by the caller)."""
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


def _validate_includes(owner: str, includes: set[str], exclusions: set[str],
                       known: set[str]) -> None:
    """Validate include-only scope: exact matching, owner, unknown, contradiction."""
    for full in sorted(includes):
        if "/" not in full:
            raise PolicyError(f"invalid include {full!r}: expected OWNER/REPO")
        include_owner, _ = full.split("/", 1)
        if include_owner != owner:
            raise PolicyError(
                f"include {full!r} owner does not match plan owner {owner!r}"
            )
        if full not in known:
            raise PolicyError(f"include {full!r} is not an owned repository of {owner!r}")
        if full in exclusions:
            raise PolicyError(
                f"include/exclude contradiction: {full!r} is both included and excluded"
            )


def build_plan(owner: str, exclusions: set[str], includes: set[str] | None = None) -> dict:
    """Build the deterministic two-phase policy plan (no mutations).

    When *includes* is non-empty, only the explicitly included repositories
    may enter the configurable plan; everything else is recorded as skipped
    with reason "not included".
    """
    repos = discover_repositories(owner)
    known = {r.get("full_name") for r in repos if isinstance(r.get("full_name"), str)}
    includes = includes or set()
    if includes:
        _validate_includes(owner, includes, exclusions, known)

    entries = []
    skipped = []
    for repo in repos:
        full = repo.get("full_name", "")
        if not isinstance(full, str) or "/" not in full:
            continue
        if includes and full not in includes:
            skipped.append({"repository": full, "reason": "not included"})
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
        existing_checks = _existing_required_checks(protection)
        skip_reason = None
        if not perms.get("admin"):
            skip_reason = "insufficient owner admin permission for protection writes"
        if skip_reason is None and protection["classic"] is None and protection["rulesets"] is None:
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
        "includes": sorted(includes),
        "exclusions": sorted(exclusions),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run_default": True,
        "repositories": entries,
        "skipped": sorted(skipped, key=lambda s: s["repository"]),
    }
    plan["plan_sha256"] = plan_sha256(plan)
    return plan


def _existing_required_checks(protection: dict) -> list[str]:
    """Collect existing required checks preserving their App bindings.

    Returns strings ``context`` or ``context@app_id`` so bindings survive
    planning and are never silently dropped.
    """
    checks: set[str] = set()
    classic = protection.get("classic")
    if isinstance(classic, dict):
        rsc = classic.get("required_status_checks") or {}
        for entry in rsc.get("checks") or []:
            if isinstance(entry, dict) and isinstance(entry.get("context"), str):
                ctx = entry["context"]
                checks.add(f"{ctx}@{entry['app_id']}" if entry.get("app_id") else ctx)
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

    Safety: the hashed plan's repository list is the only scope apply may
    touch; a bootstrap failure marks the repository failed and performs zero
    protection writes; the Gate App ID is required for Classic App binding.
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

            # Defect 1: bootstrap MUST succeed before any protection write.
            bootstrap_ok, bootstrap_detail = _bootstrap_repo(
                app_installation_token_fn, full, entry["default_branch"]
            )
            if not bootstrap_ok:
                report["repositories"].append({
                    "repository": full, "status": "failed",
                    "reason": f"bootstrap failed; no protection write performed: {bootstrap_detail}",
                })
                continue

            if app_id is None:
                report["repositories"].append({
                    "repository": full, "status": "failed",
                    "reason": "Gate App ID unavailable; refusing protection write (fail closed)",
                })
                continue
            protection_outcome = _configure_protection(full, entry, now, app_id)
            verify = _verify_protection(full, entry["default_branch"], app_id)
            report["repositories"].append({
                "repository": full, "status": "configured",
                "bootstrap": bootstrap_detail, "protection": protection_outcome,
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


def _bootstrap_repo(app_installation_token_fn, full: str, default_branch: str) -> tuple[bool, str]:
    from .bootstrap import branch_head_sha, publish_bootstrap_check
    try:
        token = app_installation_token_fn(owner=full.split("/", 1)[0], repo=full.split("/", 1)[1])
        head = branch_head_sha(token, full, default_branch)
        publish_bootstrap_check(token, full, head)
        return True, "bootstrapped"
    except Exception as exc:  # noqa: BLE001 - recorded per repository
        return False, str(exc)[:200]


def _configure_protection(full: str, entry: dict, now: dict, app_id: str) -> str:
    """Configure the default-branch protection: approvals 0 + App-bound check."""
    default_branch = entry["default_branch"]
    protection = now["protection_snapshot"]
    classic = protection.get("classic")
    rulesets = protection.get("rulesets") or []
    if rulesets:
        return _configure_via_ruleset(full, default_branch, rulesets, app_id)
    return _configure_via_classic(full, default_branch, classic, app_id)


def _configure_via_classic(full: str, default_branch: str, existing: dict | None,
                           app_id: str) -> str:
    """Set Classic protection: approvals 0 + the required check bound to the
    exact Gate App ID. Existing checks and their App bindings are preserved;
    app-bound checks are never downgraded to unbound contexts."""
    existing = existing or {}
    rsc = existing.get("required_status_checks") or {}
    checks: list[dict] = []
    # Preserve existing checks with their original App bindings.
    for entry in rsc.get("checks") or []:
        if isinstance(entry, dict) and isinstance(entry.get("context"), str):
            preserved: dict = {"context": entry["context"]}
            if entry.get("app_id"):
                preserved["app_id"] = entry["app_id"]
            checks.append(preserved)
    for ctx in rsc.get("contexts") or []:
        if isinstance(ctx, str):
            checks.append({"context": ctx})
    # Add or refresh the Gate check with the exact App ID.
    gate_entries = [c for c in checks if c["context"] == CHECK_RUN_NAME]
    for entry in gate_entries:
        entry["app_id"] = int(app_id)
    if not gate_entries:
        checks.append({"context": CHECK_RUN_NAME, "app_id": int(app_id)})

    reviews = existing.get("required_pull_request_reviews") or {}
    body = {
        "required_status_checks": {
            "strict": bool(rsc.get("strict", True)),
            "checks": checks,
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
                           app_id: str) -> str:
    """Add/update the review-gate ruleset, App-bound, without touching others."""
    existing = [r for r in rulesets if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME]
    body = {
        "name": GATE_RULESET_NAME,
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
                    "checks": [{"context": CHECK_RUN_NAME, "integration_id": int(app_id)}],
                },
            },
        ],
    }
    if existing:
        gh_put(f"/repos/{full}/rulesets/{existing[0]['id']}", body)
        return "ruleset-updated"
    gh_put(f"/repos/{full}/rulesets", body)
    return "ruleset-created"


def _verify_protection(full: str, default_branch: str, app_id: str) -> dict:
    """Verify the required check is present AND bound to the exact App ID."""
    try:
        classic = gh_get(f"/repos/{full}/branches/{default_branch}/protection")
        rulesets = gh_get(f"/repos/{full}/rulesets")
    except PolicyError as exc:
        return {"ok": False, "reason": str(exc)}
    bound = _required_check_bound(classic, rulesets, app_id)
    reviews_ok = True
    if isinstance(classic, dict):
        reviews = classic.get("required_pull_request_reviews") or {}
        reviews_ok = reviews.get("required_approving_review_count") == 0
    return {
        "ok": bound and reviews_ok,
        "required_check_app_bound": bound,
        "approval_count_zero": reviews_ok,
    }


def _required_check_bound(classic, rulesets, app_id: str) -> bool:
    """True when the Gate check exists bound to the exact App ID."""
    if isinstance(classic, dict):
        rsc = classic.get("required_status_checks") or {}
        for entry in rsc.get("checks") or []:
            if entry.get("context") == CHECK_RUN_NAME:
                return entry.get("app_id") == int(app_id)
        for ctx in rsc.get("contexts") or []:
            if ctx == CHECK_RUN_NAME:
                return False  # unbound context is not an App-bound check
    for rs in rulesets or []:
        if not isinstance(rs, dict):
            continue
        for rule in rs.get("rules") or []:
            if rule.get("type") == "required_status_checks":
                for entry in rule.get("parameters", {}).get("checks") or []:
                    if entry.get("context") == CHECK_RUN_NAME:
                        return entry.get("integration_id") == int(app_id)
    return False


def rollback(backup_path: Path) -> dict:
    """Restore the mechanism actually changed, then verify the remote state.

    Ruleset created by apply -> deleted exactly that ruleset. Ruleset updated
    by apply -> restored to its exact previous representation. Unrelated
    rulesets are never touched. Classic protection updated -> restored
    exactly; classic protection created on a previously unprotected branch ->
    removed (unprotected state restored). Verification failures are reported.
    """
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    results = []
    for full, state in backup.items():
        try:
            before = state.get("protection") or {}
            default_branch = state["default_branch"]
            actions: list[str] = []

            # Rulesets: restore the exact previous representation of the Gate
            # ruleset, or delete it when apply created it.
            before_gate = [
                r for r in (before.get("rulesets") or [])
                if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
            ]
            current_rulesets = gh_get(f"/repos/{full}/rulesets")
            current_gate = [
                r for r in (current_rulesets or [])
                if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
            ]
            if before_gate:
                gh_put(f"/repos/{full}/rulesets/{before_gate[0]['id']}", before_gate[0])
                actions.append("ruleset-restored")
            elif current_gate:
                gh_delete(f"/repos/{full}/rulesets/{current_gate[0]['id']}")
                actions.append("ruleset-deleted")

            # Classic: restore exactly, or remove when apply created it.
            before_classic = before.get("classic")
            if isinstance(before_classic, dict):
                gh_put(
                    f"/repos/{full}/branches/{default_branch}/protection",
                    before_classic,
                )
                actions.append("classic-restored")
            else:
                gh_delete(f"/repos/{full}/branches/{default_branch}/protection")
                actions.append("classic-removed")

            # Verify the resulting remote state.
            verification = _verify_rollback(full, default_branch, before)
            results.append({
                "repository": full, "status": "restored",
                "actions": actions, "verification": verification,
            })
        except PolicyError as exc:
            results.append({"repository": full, "status": "failed", "reason": str(exc)})
    return {"backup": str(backup_path), "results": results}


def _verify_rollback(full: str, default_branch: str, before: dict) -> dict:
    """Check the remote state matches the before-state after rollback."""
    problems: list[str] = []
    try:
        current_rulesets = gh_get(f"/repos/{full}/rulesets") or []
        before_gate = [
            r for r in (before.get("rulesets") or [])
            if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
        ]
        current_gate = [
            r for r in current_rulesets
            if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
        ]
        if before_gate and not current_gate:
            problems.append("gate ruleset missing after rollback")
        if not before_gate and current_gate:
            problems.append("gate ruleset still present after rollback")

        try:
            classic = gh_get(f"/repos/{full}/branches/{default_branch}/protection")
        except PolicyError:
            classic = None
        before_classic = before.get("classic")
        if isinstance(before_classic, dict) and classic != before_classic:
            problems.append("classic protection not restored exactly")
        if before_classic is None and classic is not None:
            problems.append("classic protection still present after rollback")
    except PolicyError as exc:
        problems.append(str(exc))
    return {"ok": not problems, "problems": problems}
