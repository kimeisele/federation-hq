"""Regression tests for the Gate authentication boundary.

The App JWT is valid only for /app endpoints; repository resources must be
accessed with a scoped Installation Access Token, and repository-scoped
token creation must fail closed rather than silently falling back to an
all-repository token. No live GitHub mutations.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import auth, doctor  # noqa: E402

JWT = "fake-jwt"
INSTALL_TOKEN = "ghs_INSTALL_TOKEN"

REQUIRED_PERMS = {
    "metadata": "read",
    "contents": "read",
    "pull_requests": "read",
    "checks": "write",
}


class RecordingCurl:
    """Records (method, url, body, auth_header) for every request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, str | None]] = []
        self.responses: dict[str, object] = {}
        self.default = {}

    def route(self, fragment: str, response: object) -> None:
        self.responses[fragment] = response

    def run(self, args: list[str]) -> object:
        method = args[args.index("--request") + 1] if "--request" in args else "GET"
        url = args[args.index("--url") + 1]
        body = None
        if "--data" in args:
            body = json.loads(args[args.index("--data") + 1])
        auth_header = None
        if "--header" in args:
            headers = [args[i + 1] for i in range(len(args)) if args[i] == "--header"]
            for h in headers:
                if h.startswith("Authorization: Bearer "):
                    auth_header = h[len("Authorization: Bearer "):]
        self.calls.append((method, url, body, auth_header))
        matches = [
            (fragment, response)
            for fragment, response in self.responses.items()
            if fragment in url
        ]
        if matches:
            # Longest fragment wins so a specific endpoint is not shadowed by
            # a shorter prefix route.
            payload = max(matches, key=lambda pair: len(pair[0]))[1]
        else:
            payload = self.default
        return _Completed(json.dumps(payload) if payload is not None else "", 200)


class _Completed:
    def __init__(self, stdout: str, status: int) -> None:
        self.stdout = f"{stdout}\n{status}"
        self.stderr = ""
        self.returncode = 0


def _installation() -> dict:
    return {
        "id": 152231415,
        "account": {"login": "kimeisele"},
        "active": True,
        "repository_selection": "all",
        "permissions": dict(REQUIRED_PERMS),
    }


@pytest.fixture()
def doctor_env(tmp_path: Path, monkeypatch) -> tuple[RecordingCurl, Path]:
    config_dir = tmp_path / "gate-config"
    config_dir.mkdir()
    monkeypatch.setenv("FEDERATION_HQ_CONFIG_DIR", str(config_dir))
    key = config_dir / "private-key.pem"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    os.chmod(key, 0o600)
    (config_dir / "config.json").write_text(json.dumps({
        "app_id": "4528340", "installation_id": "152231415",
        "private_key_path": str(key),
    }))
    os.chmod(config_dir / "config.json", 0o600)

    curl = RecordingCurl()
    curl.route("/app/installations", [_installation()])
    curl.route("/app/installations/152231415/access_tokens", {"token": INSTALL_TOKEN})
    curl.route("/repos/kimeisele/federation-hq", {"full_name": "kimeisele/federation-hq"})

    monkeypatch.setattr(auth, "create_app_jwt", lambda app_id, key: JWT)
    monkeypatch.setattr(doctor, "create_app_jwt", lambda app_id, key: JWT)
    monkeypatch.setattr("federation_hq_gate.http._run_curl", curl.run)
    monkeypatch.setattr(auth, "_resolve_repository_id_via_gh", lambda owner, repo: 4528340)
    monkeypatch.setattr(doctor, "_gh_owner", lambda: (True, "kimeisele"))
    return curl, config_dir


def _calls_by_url(curl: RecordingCurl) -> dict[str, list]:
    by_url: dict[str, list] = {}
    for method, url, body, auth_header in curl.calls:
        by_url.setdefault(url, []).append((method, body, auth_header))
    return by_url


def test_doctor_never_calls_repos_with_app_jwt(doctor_env):
    curl, _ = doctor_env
    report = doctor.run_doctor()
    assert report.ok, [c["detail"] for c in report.checks if not c["ok"]]
    for _m, url, _b, authh in curl.calls:
        if "/repos/" in url:
            assert authh != JWT, f"/repos call used the App JWT: {url}"


def test_repository_metadata_probe_uses_installation_token(doctor_env):
    curl, _ = doctor_env
    report = doctor.run_doctor()
    assert report.ok
    repo_calls = [c for c in curl.calls if "/repos/" in c[1]]
    assert repo_calls, "no repository metadata probe performed"
    for _m, url, _b, authh in repo_calls:
        assert authh == INSTALL_TOKEN, f"repository probe used {authh!r}, expected installation token"


def test_scoped_token_creation_sends_repository_restriction(doctor_env):
    curl, _ = doctor_env
    doctor.run_doctor()
    token_calls = [c for c in curl.calls if "access_tokens" in c[1]]
    assert token_calls, "no installation-token request"
    _, _, body, auth = token_calls[0]
    assert auth == JWT  # creating tokens is an App-level operation
    assert body["repository_ids"] == [4528340]
    assert body["permissions"] == REQUIRED_PERMS


def test_scoping_fails_closed_when_repo_id_resolution_fails(monkeypatch):
    curl = RecordingCurl()
    curl.route("/app/installations/1/access_tokens", {"token": INSTALL_TOKEN})
    monkeypatch.setattr("federation_hq_gate.http._run_curl", curl.run)

    def boom(owner, repo):
        raise auth.AuthError("cannot resolve repository id")

    monkeypatch.setattr(auth, "_resolve_repository_id_via_gh", boom)
    with pytest.raises(auth.AuthError, match="cannot resolve repository id"):
        auth.create_installation_token(JWT, "1", owner="kimeisele", repo="federation-hq")
    # Fail closed: no token request at all (never an unrestricted fallback).
    assert not [c for c in curl.calls if "access_tokens" in c[1]]


def test_app_jwt_used_only_for_app_level_endpoints(doctor_env):
    curl, _ = doctor_env
    doctor.run_doctor()
    for _m, url, _b, authh in curl.calls:
        if url.startswith("https://api.github.com/app") or "access_tokens" in url:
            assert authh == JWT, f"App-level endpoint {url} used {authh!r}"
        else:
            assert authh != JWT, f"repository endpoint {url} used the App JWT"


def test_doctor_prints_no_token_or_secret(doctor_env, capsys):
    curl, _ = doctor_env
    report = doctor.run_doctor()
    assert report.ok
    output = capsys.readouterr().out
    assert INSTALL_TOKEN not in output
    assert "fake-jwt" not in output
    assert "ghs_" not in output


def test_repo_id_resolution_uses_single_path_argument(monkeypatch):
    """gh api takes one path argument; the full path is a single arg."""
    captured: list = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return _Completed("1324232895", 200)

    monkeypatch.setattr("federation_hq_gate.auth.subprocess.run", fake_run)
    repo_id = auth._resolve_repository_id_via_gh("kimeisele", "federation-hq")
    assert repo_id == 1324232895
    assert captured[0] == ["gh", "api", "repos/kimeisele/federation-hq", "--jq", ".id"]
