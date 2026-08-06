"""GitHub App authentication: JWT, installation discovery, scoped tokens.

JWT signing uses ``openssl dgst -sha256 -sign`` (RS256) via subprocess; all
HTTP goes through the curl-based client. Token format and length are never
assumed; errors never leak secrets.
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path

from . import FORBIDDEN_PERMISSIONS, REQUIRED_PERMISSIONS
from .http import GitHubError, request

# Application/vnd.github+json requires the full header; the app endpoints
# additionally accept the raw Accept used by the client.

_SCOPE_PERMISSIONS = dict(REQUIRED_PERMISSIONS)


class AuthError(RuntimeError):
    """Authentication or authorization failure (secret-safe)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_app_jwt(app_id: str, private_key_path: str | Path) -> str:
    """Create a short-lived RS256 app JWT via the openssl CLI."""
    header = {"alg": "RS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"iat": now, "exp": now + 540, "iss": str(app_id)}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(private_key_path)],
            input=signing_input.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AuthError("openssl is required for JWT signing but was not found") from exc
    if proc.returncode != 0:
        raise AuthError("openssl failed to sign the app JWT (invalid private key?)")
    signature = _b64url(proc.stdout)
    return f"{signing_input}.{signature}"


def get_app_info(jwt: str) -> dict:
    """Return the app's own identity (GET /app)."""
    data = request("GET", "/app", token=jwt)
    if not isinstance(data, dict):
        raise AuthError("unexpected response from /app")
    return data


def discover_installations(jwt: str, account_login: str) -> dict | None:
    """Find the installation for *account_login*; return it or None."""
    data = request("GET", "/app/installations", token=jwt)
    if not isinstance(data, list):
        raise AuthError("unexpected response from /app/installations")
    for inst in data:
        account = inst.get("account") or {}
        if account.get("login") == account_login:
            return inst
    return None


def _validate_installation_permissions(installation: dict) -> None:
    """Reject installations granting forbidden permissions."""
    perms = installation.get("permissions") or {}
    forbidden_granted = sorted(
        key for key in FORBIDDEN_PERMISSIONS
        if perms.get(key) and perms[key] != "none"
    )
    if forbidden_granted:
        raise AuthError(
            "Gate App installation grants forbidden permission(s): "
            + ", ".join(forbidden_granted)
        )
    missing = [
        key for key, value in REQUIRED_PERMISSIONS.items()
        if perms.get(key) != value
    ]
    if missing:
        raise AuthError(
            "Gate App installation lacks required permission(s): " + ", ".join(missing)
        )


def _resolve_repository_id(owner: str, repo: str, token: str) -> int | None:
    try:
        data = request("GET", f"/repos/{owner}/{repo}", token=token)
    except GitHubError:
        return None
    if isinstance(data, dict) and isinstance(data.get("id"), int):
        return data["id"]
    return None


def create_installation_token(
    jwt: str,
    installation_id: str,
    *,
    owner: str | None = None,
    repo: str | None = None,
) -> tuple[str, dict]:
    """Create an installation token, scoped to one repository when possible.

    Returns (token, response). The requested permissions are the runtime
    minimum; an installation that grants forbidden permissions is rejected
    before any token is requested.
    """
    body: dict = {"permissions": dict(_SCOPE_PERMISSIONS)}
    if owner and repo:
        repo_id = _resolve_repository_id(owner, repo, jwt)
        if repo_id is not None:
            body["repository_ids"] = [repo_id]
    data = request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        token=jwt,
        body=body,
    )
    if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not data["token"]:
        raise AuthError("installation token request returned no usable token")
    return data["token"], data


def installation_token(cfg: dict, *, owner: str | None = None, repo: str | None = None) -> str:
    """Convenience: full flow from config to a usable installation token."""
    jwt = create_app_jwt(cfg["app_id"], cfg["private_key_path"])
    installation = discover_installations(jwt, "kimeisele")
    if installation is None:
        raise AuthError(
            "no GitHub App installation found for account kimeisele; "
            "run setup-app and install the app on the account"
        )
    _validate_installation_permissions(installation)
    token, _ = create_installation_token(
        jwt, str(installation["id"]), owner=owner, repo=repo
    )
    return token
