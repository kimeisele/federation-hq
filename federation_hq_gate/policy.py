"""Safe multi-repository branch-policy planner and applier.

Two-phase, dry-run by default. Branch-policy reads and writes always use the
repository owner's authenticated ``gh`` session — never the Gate App token.
The Gate App is used only for the bootstrap check-run and the App-ID binding
of the required check. No command mutates all repositories without the
explicit plan-hash confirmation.

Safety invariants enforced here (including the review-fix pass):
- bootstrap failure stops all protection mutation for that repository;
- post-write remote verification failures are reported as ``failed`` (never
  ``configured``) and the repository's before-state is restored, with the
  rollback outcome reported explicitly;
- the before-state backup is durably written (atomically) BEFORE the first
  protection/ruleset mutation, so a process failure after any write still
  leaves enough information for manual rollback;
- snapshots are normalized, write-safe representations: raw GET/list
  responses are never replayed as mutation payloads (ruleset list objects
  are summaries; the Gate ruleset's full representation is fetched from the
  individual endpoint; Classic responses are reduced to the writable
  fields);
- Classic required checks carry the exact Gate App ID and are never
  downgraded to unbound contexts;
- ``--include`` limits the configurable plan to exactly the requested
  repositories (unknown, owner-mismatched, or contradictory includes fail);
- rollback restores the mechanism actually changed (ruleset created ->
  deleted, ruleset updated -> restored from the normalized representation,
  classic created -> removed, classic updated -> restored) and verifies the
  remote result.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def _write_atomic(path: Path, data: dict) -> None:
    """Persist data atomically (tmp file + rename) so a crash never leaves a
    partial backup."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def plan_sha256(plan: dict) -> str:
    """Deterministic canonical form of the plan for hash confirmation.

    Volatile audit fields (generated_at) and the self-referential
    plan_sha256 field are excluded so the confirmed hash is stable. The
    include/exclude scope IS part of the hash.
    """
    stable = {k: v for k, v in plan.items() if k not in ("plan_sha256", "generated_at")}
    canonical = json.dumps(stable, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Normalized, write-safe representations ─────────────────────────────────


def _github_enabled(value) -> bool:
    """Decode GitHub's boolean-or-{"enabled": bool} protection values.

    ``bool({"enabled": false})`` is ``True`` in Python, which would invert
    protection state; this helper handles boolean, ``{"enabled": ...}``,
    and null/missing consistently.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        enabled = value.get("enabled")
        if isinstance(enabled, bool):
            return enabled
        return bool(enabled)  # pragma: no cover - defensive for odd payloads
    return bool(value)  # null/missing -> False


def _normalize_restrictions(raw) -> dict | None:
    """Reduce restrictions to writable login/slug strings."""
    if not isinstance(raw, dict):
        return None
    normalized: dict = {}
    for key in ("users", "teams", "apps"):
        entries = raw.get(key) or []
        normalized[key] = [
            e.get("login", e.get("slug")) for e in entries if isinstance(e, dict)
        ] if key != "apps" else [
            e.get("slug") for e in entries if isinstance(e, dict)
        ]
    return normalized


def _normalize_pr_allowances(raw) -> dict | None:
    """Reduce PR-review bypass allowances to writable login/slug strings."""
    if not isinstance(raw, dict):
        return None
    normalized: dict = {}
    for key in ("users", "teams", "apps"):
        entries = raw.get(key) or []
        normalized[key] = [
            e.get("login", e.get("slug")) for e in entries if isinstance(e, dict)
        ] if key != "apps" else [
            e.get("slug") for e in entries if isinstance(e, dict)
        ]
    return normalized


def _normalize_classic_for_write(raw: dict | None) -> dict | None:
    """Reduce a Classic Branch Protection GET response to the writable fields.

    Raw responses carry read-only fields (``url`` and friends) and encode
    several settings as ``{"enabled": bool}`` objects; both must never be
    replayed or mis-decoded.
    """
    if raw is None:
        return None
    rsc = raw.get("required_status_checks") or {}
    reviews = raw.get("required_pull_request_reviews") or {}
    checks: list[dict] = []
    for entry in rsc.get("checks") or []:
        if isinstance(entry, dict) and isinstance(entry.get("context"), str):
            preserved: dict = {"context": entry["context"]}
            if entry.get("app_id"):
                preserved["app_id"] = entry["app_id"]
            checks.append(preserved)
    for ctx in rsc.get("contexts") or []:
        if isinstance(ctx, str):
            checks.append({"context": ctx})
    normalized = {
        "required_status_checks": {
            "strict": bool(rsc.get("strict", True)),
            "checks": checks,
        },
        "enforce_admins": _github_enabled(raw.get("enforce_admins", False)),
        "required_pull_request_reviews": {
            "required_approving_review_count": reviews.get(
                "required_approving_review_count", 0
            ),
            "dismiss_stale_reviews": _github_enabled(
                reviews.get("dismiss_stale_reviews", False)
            ),
            "require_code_owner_reviews": _github_enabled(
                reviews.get("require_code_owner_reviews", False)
            ),
            "require_last_push_approval": _github_enabled(
                reviews.get("require_last_push_approval", False)
            ),
        },
        "restrictions": _normalize_restrictions(raw.get("restrictions")),
        "required_linear_history": _github_enabled(
            raw.get("required_linear_history", False)
        ),
        "allow_force_pushes": _github_enabled(raw.get("allow_force_pushes", False)),
        "allow_deletions": _github_enabled(raw.get("allow_deletions", False)),
        "block_creations": _github_enabled(raw.get("block_creations", False)),
        "required_conversation_resolution": _github_enabled(
            raw.get("required_conversation_resolution", False)
        ),
        "lock_branch": _github_enabled(raw.get("lock_branch", False)),
        "allow_fork_syncing": _github_enabled(raw.get("allow_fork_syncing", False)),
    }
    bypass = reviews.get("bypass_pull_request_allowances")
    if isinstance(bypass, dict):
        normalized["required_pull_request_reviews"][
            "bypass_pull_request_allowances"
        ] = _normalize_pr_allowances(bypass)
    return normalized


def _normalize_ruleset_for_write(raw: dict) -> dict:
    """Reduce a ruleset GET response to the documented update payload fields."""
    return {
        "name": raw.get("name"),
        "target": raw.get("target", "branch"),
        "enforcement": raw.get("enforcement", "active"),
        "bypass_actors": raw.get("bypass_actors", []),
        "conditions": raw.get("conditions"),
        "rules": raw.get("rules", []),
    }


def _normalize_protection(repo: dict, default_branch: str) -> dict:
    """Capture normalized, write-safe protection facts for drift + backup.

    Classic protection is reduced to its writable fields. Rulesets are stored
    as summaries; when a Gate ruleset exists its FULL representation is
    fetched from the individual ruleset endpoint and stored normalized so it
    can be restored exactly.
    """
    classic_raw = None
    try:
        classic_raw = gh_get(f"/repos/{repo['full_name']}/branches/{default_branch}/protection")
    except PolicyError:
        classic_raw = None
    rulesets_raw = None
    try:
        rulesets_raw = gh_get(f"/repos/{repo['full_name']}/rulesets")
    except PolicyError:
        rulesets_raw = None

    rulesets: list[dict] = []
    for rs in rulesets_raw or []:
        if not isinstance(rs, dict):
            continue
        summary: dict = {
            "id": rs.get("id"),
            "name": rs.get("name"),
            "rules": rs.get("rules", []),
        }
        if rs.get("name") == GATE_RULESET_NAME and rs.get("id") is not None:
            full = gh_get(f"/repos/{repo['full_name']}/rulesets/{rs['id']}")
            if isinstance(full, dict):
                summary["write_safe"] = _normalize_ruleset_for_write(full)
        rulesets.append(summary)
    return {
        "classic": _normalize_classic_for_write(classic_raw),
        "rulesets": rulesets,
    }


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
        if skip_reason is None and protection["classic"] is None and protection["rulesets"] == []:
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
    """Collect existing required checks preserving their App bindings."""
    checks: set[str] = set()
    classic = protection.get("classic")
    if isinstance(classic, dict):
        rsc = classic.get("required_status_checks") or {}
        for entry in rsc.get("checks") or []:
            if isinstance(entry, dict) and isinstance(entry.get("context"), str):
                ctx = entry["context"]
                checks.add(f"{ctx}@{entry['app_id']}" if entry.get("app_id") else ctx)
    for rs in protection.get("rulesets") or []:
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


def _collect_backup(plan: dict) -> dict:
    """Build the before-state backup from the drift-verified plan entries."""
    backup: dict[str, dict] = {}
    for entry in plan.get("repositories", []):
        if entry.get("skip_reason"):
            continue
        backup[entry["repository"]] = {
            "default_branch": entry["default_branch"],
            "protection": entry["protection_snapshot"],
        }
    return backup


def apply_plan(plan: dict, *, expected_sha256: str, app_installation_token_fn,
               dry_run: bool, app_id: str | None = None,
               backup_dir: Path | None = None) -> dict:
    """Apply the confirmed plan per repository; report per-repo outcomes.

    Safety: the hashed plan's repository list is the only scope apply may
    touch; the before-state backup is written atomically BEFORE any
    mutation; a bootstrap failure marks the repository failed and performs
    zero protection writes; a post-write verification failure is reported as
    ``failed`` (never ``configured``) and the before-state is restored, with
    the rollback outcome reported explicitly.
    """
    if plan_sha256(plan) != expected_sha256:
        raise PolicyError("plan SHA-256 does not match the confirmed hash; refusing to apply")
    backup = _collect_backup(plan)
    report = {"plan_sha256": expected_sha256, "dry_run": dry_run, "repositories": []}

    # Blocker B: durable, atomic before-state backup BEFORE any mutation.
    backup_path = None
    if backup and not dry_run:
        target_dir = backup_dir or Path.cwd()
        backup_path = target_dir / f"policy-backup-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        _write_atomic(backup_path, backup)
    report["backup_path"] = str(backup_path) if backup_path else None

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
            if dry_run:
                report["repositories"].append({
                    "repository": full, "status": "dry_run", "would_configure": True,
                })
                continue

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

            _configure_protection(full, entry, now, app_id)
            verify = _verify_protection(full, entry["default_branch"], app_id)

            # Blocker A: verification failure is NEVER reported as configured;
            # the before-state is restored and the rollback outcome reported.
            if not verify["ok"]:
                restore = _rollback_repo(full, backup[full])
                report["repositories"].append({
                    "repository": full, "status": "failed",
                    "reason": "remote verification failed after protection write",
                    "policy_verification": verify,
                    "rollback": restore,
                })
                continue

            report["repositories"].append({
                "repository": full, "status": "configured",
                "bootstrap": bootstrap_detail,
                "protection": "configured",
                "verified": verify,
            })
        except PolicyError as exc:
            report["repositories"].append({
                "repository": full, "status": "failed", "reason": str(exc),
            })
            continue  # bounded per-repository failure; do not corrupt the fleet
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
        return _configure_via_ruleset(full, default_branch, app_id)
    return _configure_via_classic(full, default_branch, classic, app_id)


def _configure_via_classic(full: str, default_branch: str, existing: dict | None,
                           app_id: str) -> str:
    """Set Classic protection: approvals 0 + the required check bound to the
    exact Gate App ID. Existing checks and their App bindings are preserved;
    app-bound checks are never downgraded to unbound contexts."""
    existing = existing or {}
    rsc = existing.get("required_status_checks") or {}
    checks: list[dict] = []
    for entry in rsc.get("checks") or []:
        if isinstance(entry, dict) and isinstance(entry.get("context"), str):
            preserved: dict = {"context": entry["context"]}
            if entry.get("app_id"):
                preserved["app_id"] = entry["app_id"]
            checks.append(preserved)
    gate_entries = [c for c in checks if c["context"] == CHECK_RUN_NAME]
    for entry in gate_entries:
        entry["app_id"] = int(app_id)
    if not gate_entries:
        checks.append({"context": CHECK_RUN_NAME, "app_id": int(app_id)})

    reviews = existing.get("required_pull_request_reviews") or {}
    review_body = {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews": _github_enabled(reviews.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": _github_enabled(
            reviews.get("require_code_owner_reviews", False)
        ),
        "require_last_push_approval": _github_enabled(
            reviews.get("require_last_push_approval", False)
        ),
    }
    bypass = reviews.get("bypass_pull_request_allowances")
    if isinstance(bypass, dict):
        review_body["bypass_pull_request_allowances"] = bypass
    body = {
        "required_status_checks": {
            "strict": bool(rsc.get("strict", True)),
            "checks": checks,
        },
        "enforce_admins": _github_enabled(existing.get("enforce_admins", False)),
        "required_pull_request_reviews": review_body,
        "restrictions": existing.get("restrictions"),
        "required_linear_history": _github_enabled(
            existing.get("required_linear_history", False)
        ),
        "allow_force_pushes": _github_enabled(existing.get("allow_force_pushes", False)),
        "allow_deletions": _github_enabled(existing.get("allow_deletions", False)),
        "block_creations": _github_enabled(existing.get("block_creations", False)),
        "required_conversation_resolution": _github_enabled(
            existing.get("required_conversation_resolution", False)
        ),
        "lock_branch": _github_enabled(existing.get("lock_branch", False)),
        "allow_fork_syncing": _github_enabled(existing.get("allow_fork_syncing", False)),
    }
    gh_put(f"/repos/{full}/branches/{default_branch}/protection", body)
    return "classic-configured"


def _configure_via_ruleset(full: str, default_branch: str, app_id: str) -> str:
    """Add/update the review-gate ruleset, App-bound, without touching others."""
    rulesets = gh_get(f"/repos/{full}/rulesets") or []
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
    """Verify the required check is present AND bound to the exact App ID.

    For the ruleset path the FULL Gate ruleset representation is fetched from
    the individual ruleset endpoint: the list endpoint returns summaries that
    must not be assumed to contain the ``rules`` array.
    """
    try:
        classic = gh_get(f"/repos/{full}/branches/{default_branch}/protection")
        rulesets = gh_get(f"/repos/{full}/rulesets")
    except PolicyError as exc:
        return {"ok": False, "reason": str(exc)}
    gate_full = None
    if isinstance(rulesets, list):
        gate = [
            r for r in rulesets
            if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME and r.get("id")
        ]
        if gate:
            gate_full = gh_get(f"/repos/{full}/rulesets/{gate[0]['id']}")
    bound = _required_check_bound(classic, gate_full, app_id)
    reviews_ok = True
    if isinstance(classic, dict):
        reviews = classic.get("required_pull_request_reviews") or {}
        reviews_ok = reviews.get("required_approving_review_count") == 0
    return {
        "ok": bound and reviews_ok,
        "required_check_app_bound": bound,
        "approval_count_zero": reviews_ok,
    }


def _required_check_bound(classic, gate_ruleset_full, app_id: str) -> bool:
    """True when the Gate check exists bound to the exact App ID.

    *gate_ruleset_full* is the full representation from the individual
    ruleset endpoint (or None when no Gate ruleset exists); list-summary
    ``rules`` fields are never inspected.
    """
    if isinstance(classic, dict):
        rsc = classic.get("required_status_checks") or {}
        for entry in rsc.get("checks") or []:
            if entry.get("context") == CHECK_RUN_NAME:
                return entry.get("app_id") == int(app_id)
        for ctx in rsc.get("contexts") or []:
            if ctx == CHECK_RUN_NAME:
                return False  # unbound context is not an App-bound check
    if isinstance(gate_ruleset_full, dict):
        for rule in gate_ruleset_full.get("rules") or []:
            if rule.get("type") == "required_status_checks":
                for entry in rule.get("parameters", {}).get("checks") or []:
                    if entry.get("context") == CHECK_RUN_NAME:
                        return entry.get("integration_id") == int(app_id)
    return False


# ── Rollback ───────────────────────────────────────────────────────────────


def rollback(backup_path: Path) -> dict:
    """Restore the mechanism actually changed, then verify the remote state.

    Ruleset created by apply -> deleted exactly that ruleset. Ruleset updated
    by apply -> restored from the stored normalized write-safe representation.
    Unrelated rulesets are never touched. Classic protection updated ->
    restored from the normalized representation; classic protection created
    on a previously unprotected branch -> removed. Verification failures are
    reported.
    """
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    results = []
    for full, state in backup.items():
        results.append(_rollback_repo(full, state))
    return {"backup": str(backup_path), "results": results}


def _rollback_repo(full: str, state: dict) -> dict:
    """Restore one repository's before-state; verify and report explicitly."""
    try:
        before = state.get("protection") or {}
        default_branch = state["default_branch"]
        actions: list[str] = []

        before_gate = [
            r for r in (before.get("rulesets") or [])
            if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
        ]
        current_rulesets = gh_get(f"/repos/{full}/rulesets") or []
        current_gate = [
            r for r in current_rulesets
            if isinstance(r, dict) and r.get("name") == GATE_RULESET_NAME
        ]
        if before_gate:
            # Restore the exact previous representation from the normalized,
            # write-safe payload stored in the backup.
            write_safe = before_gate[0].get("write_safe") or _normalize_ruleset_for_write(
                before_gate[0]
            )
            ruleset_id = before_gate[0].get("id")
            if ruleset_id is None:
                raise PolicyError("gate ruleset before-state lacks an id; cannot restore")
            gh_put(f"/repos/{full}/rulesets/{ruleset_id}", write_safe)
            actions.append("ruleset-restored")
        elif current_gate:
            gh_delete(f"/repos/{full}/rulesets/{current_gate[0]['id']}")
            actions.append("ruleset-deleted")

        before_classic = before.get("classic")
        if isinstance(before_classic, dict):
            gh_put(
                f"/repos/{full}/branches/{default_branch}/protection",
                _normalize_classic_for_write(before_classic),
            )
            actions.append("classic-restored")
        else:
            gh_delete(f"/repos/{full}/branches/{default_branch}/protection")
            actions.append("classic-removed")

        verification = _verify_rollback(full, default_branch, before)
        if verification["ok"]:
            status = "restored"
        else:
            # Rollback mutation succeeded but remote verification failed.
            status = "failed"
            actions.append("verification-failed")
        return {
            "repository": full, "status": status,
            "actions": actions, "verification": verification,
        }
    except PolicyError as exc:
        return {"repository": full, "status": "failed", "reason": str(exc)}


def _verify_rollback(full: str, default_branch: str, before: dict) -> dict:
    """Check the remote state matches the normalized before-state."""
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
        if before_gate and current_gate:
            full_current = gh_get(f"/repos/{full}/rulesets/{current_gate[0]['id']}")
            expected = before_gate[0].get("write_safe") or _normalize_ruleset_for_write(
                before_gate[0]
            )
            if isinstance(full_current, dict) and _normalize_ruleset_for_write(
                full_current
            ) != expected:
                problems.append("gate ruleset not restored to its previous representation")

        try:
            classic_raw = gh_get(f"/repos/{full}/branches/{default_branch}/protection")
        except PolicyError:
            classic_raw = None
        current_classic = _normalize_classic_for_write(classic_raw)
        expected_classic = _normalize_classic_for_write(before.get("classic"))
        if isinstance(before.get("classic"), dict) and current_classic != expected_classic:
            problems.append("classic protection not restored exactly")
        if before.get("classic") is None and current_classic is not None:
            problems.append("classic protection still present after rollback")
    except PolicyError as exc:
        problems.append(str(exc))
    return {"ok": not problems, "problems": problems}
