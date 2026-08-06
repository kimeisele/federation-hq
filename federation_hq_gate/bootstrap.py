"""Bootstrap command: publish a labelled registration check on the default
branch via the Gate App. This is bootstrap evidence, not an approved repair
review.
"""
from __future__ import annotations

from . import CHECK_RUN_NAME
from .http import request


def default_branch(token: str, repository: str) -> str:
    owner, repo = repository.split("/", 1)
    data = request("GET", f"/repos/{owner}/{repo}", token=token)
    if not isinstance(data, dict) or not isinstance(data.get("default_branch"), str):
        raise RuntimeError("cannot resolve default branch")
    return data["default_branch"]


def branch_head_sha(token: str, repository: str, branch: str) -> str:
    owner, repo = repository.split("/", 1)
    data = request("GET", f"/repos/{owner}/{repo}/branches/{branch}", token=token)
    commit = data.get("commit") if isinstance(data, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str):
        raise RuntimeError("cannot resolve branch head SHA")
    return sha


def publish_bootstrap_check(token: str, repository: str, head_sha: str) -> dict:
    """Publish a neutral bootstrap check on the current default-branch head."""
    owner, repo = repository.split("/", 1)
    data = request(
        "POST",
        f"/repos/{owner}/{repo}/check-runs",
        token=token,
        body={
            "name": CHECK_RUN_NAME,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "neutral",
            "output": {
                "title": "Federation HQ review gate: bootstrap registration",
                "summary": (
                    "This is registration/bootstrap evidence only: it records that the "
                    "federation-hq/review Gate App is installed and can publish checks on "
                    "this repository. It is not an approved repair review."
                ),
            },
        },
    )
    return data if isinstance(data, dict) else {}
