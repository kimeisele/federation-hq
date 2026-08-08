"""Minimal GitHub REST client over the repository's ``curl`` subprocess convention.

No runtime dependencies. All responses are parsed as JSON; errors carry the
HTTP status and a redacted message, never raw secrets.
"""
from __future__ import annotations

import json
import subprocess

from .config import redact

API_BASE = "https://api.github.com"


class GitHubError(RuntimeError):
    """A GitHub API error with status and (redacted) message."""

    def __init__(self, method: str, url: str, status: int, body: str):
        self.method = method
        self.url = url
        self.status = status
        super().__init__(f"GitHub {method} {url} -> HTTP {status}: {redact(body)[:300]}")


def _run_curl(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run curl; separated so tests can inject a fake transport."""
    return subprocess.run(
        ["curl", "--silent", "--show-error", *args],
        capture_output=True,
        text=True,
    )


def request(method: str, path: str, *, token: str | None = None,
            body: dict | None = None, base: str = API_BASE) -> dict | list | None:
    """Perform one GitHub API request.

    Returns the parsed JSON body (or None for 204/empty). Raises GitHubError
    on non-2xx.
    """
    url = f"{base}{path}"
    args = ["--request", method, "--url", url, "--write-out", "\n%{http_code}"]
    if token:
        args += ["--header", f"Authorization: Bearer {token}"]
    args += ["--header", "Accept: application/vnd.github+json",
             "--header", "X-GitHub-Api-Version: 2022-11-28"]
    if body is not None:
        args += ["--data", json.dumps(body)]
        args += ["--header", "Content-Type: application/json"]

    result = _run_curl(args)
    if result.returncode != 0:
        raise GitHubError(method, path, 0, result.stderr or "curl failed")

    parts = result.stdout.rsplit("\n", 1)
    status = int(parts[-1].strip() or "0") if parts[-1].strip().isdigit() else 0
    payload = parts[0].strip()
    if status and 200 <= status < 300:
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise GitHubError(method, path, status, f"non-JSON response: {exc}") from exc
    raise GitHubError(method, path, status, payload)


def paginate(method: str, path: str, *, token: str | None = None,
             per_page: int = 100) -> list[dict]:
    """Walk link-style pagination via the per_page + page query convention."""
    items: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        data = request(method, f"{path}{sep}per_page={per_page}&page={page}", token=token)
        if data is None:
            break
        batch = data if isinstance(data, list) else data.get("items", [])
        items.extend(batch for batch in batch if isinstance(batch, dict))
        if len(batch) < per_page:
            break
        page += 1
        if page > 20:  # defensive cap
            break
    return items
