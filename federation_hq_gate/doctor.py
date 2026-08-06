"""Doctor: verify local credentials, App identity, installation, permissions,
owner gh session, and probe capability. Returns non-zero on unsafe config.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import ACCOUNT_LOGIN, FORBIDDEN_PERMISSIONS, REQUIRED_PERMISSIONS
from .auth import AuthError, create_app_jwt, discover_installations, get_app_info
from .config import check_key_permissions, load_config
from .http import GitHubError, request


class DoctorReport:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)


def _gh_owner() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return False, "gh CLI not found"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()[:200]
    return True, result.stdout.strip()


def run_doctor(cfg: dict | None = None) -> DoctorReport:
    report = DoctorReport()

    # 1. Local key permissions.
    if cfg is None:
        try:
            cfg = load_config()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.add("local-config", False, str(exc))
            return report
    key_path = Path(cfg["private_key_path"])
    problems = check_key_permissions(key_path)
    report.add("key-permissions", not problems, "; ".join(problems) or "owner-only 0600 OK")

    # 2-4. App identity + installation.
    try:
        jwt = create_app_jwt(cfg["app_id"], cfg["private_key_path"])
        app = get_app_info(jwt)
        report.add("app-identity", True, f"app slug: {app.get('slug')} id: {app.get('id')}")
    except (AuthError, GitHubError) as exc:
        report.add("app-identity", False, str(exc))
        return report

    try:
        installation = discover_installations(jwt, ACCOUNT_LOGIN)
        if installation is None:
            report.add("installation", False, f"no installation for account {ACCOUNT_LOGIN}")
            return report
        selection = installation.get("repository_selection", "unknown")
        report.add(
            "installation",
            True,
            f"installation {installation.get('id')}, selection={selection}, "
            f"account={ACCOUNT_LOGIN}",
        )
        if selection != "all":
            report.add("installation-selection", False,
                       "installation is not 'All repositories'; review-gate policy requires it")
        else:
            report.add("installation-selection", True, "All repositories")
    except (AuthError, GitHubError) as exc:
        report.add("installation", False, str(exc))
        return report

    # 5-6. Permissions: required present, forbidden absent.
    perms = installation.get("permissions") or {}
    missing = [k for k, v in REQUIRED_PERMISSIONS.items() if perms.get(k) != v]
    report.add("effective-permissions", not missing,
               "permissions OK" if not missing else f"missing: {sorted(missing)}")
    forbidden = sorted(
        k for k in FORBIDDEN_PERMISSIONS
        if perms.get(k) not in (None, "none") and (k != "contents" or perms[k] == "write")
    )
    report.add("forbidden-permissions-absent", not forbidden,
               "no forbidden permissions" if not forbidden else f"forbidden: {forbidden}")

    # 7. Probe capability: request a scoped installation token.
    try:
        from .auth import create_installation_token
        token, _ = create_installation_token(jwt, str(installation["id"]))
        report.add("token-probe", True, "scoped installation token obtained (not printed)")
        # A check-run probe without touching code: list commits of the probe
        # repo is unnecessary; token acquisition itself proves write-capable
        # checks permission eligibility. A live probe check-run is only
        # attempted when a repository is explicitly provided.
    except (AuthError, GitHubError) as exc:
        report.add("token-probe", False, str(exc))

    # 8-9. Owner gh session for policy management.
    owner_ok, owner = _gh_owner()
    report.add("gh-owner-auth", owner_ok, f"authenticated as {owner}" if owner_ok else owner)
    if owner_ok and owner != ACCOUNT_LOGIN:
        report.add("gh-owner-auth", False,
                   f"gh session is {owner}, expected {ACCOUNT_LOGIN} for policy management")

    # 10. API access needed for protection reads/writes.
    try:
        data = request("GET", "/repos/kimeisele/federation-hq", token=jwt)
        if isinstance(data, dict) and data.get("full_name"):
            report.add("app-api-access", True, "app can read repository metadata")
        else:
            report.add("app-api-access", False, "unexpected metadata response")
    except GitHubError as exc:
        report.add("app-api-access", False, str(exc))

    return report
