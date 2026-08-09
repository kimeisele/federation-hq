"""Focused tests for the Federation HQ GitHub App Review Gate v0.2.

All tests use mocks and local fixtures; no live GitHub mutations are made.
The canonical fixtures are the accepted Pilot Run 01 artifacts, copied into
temporary run directories and mutated for negative cases.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import auth, config, policy, review_check  # noqa: E402

PILOT_RUN = "run-20260806-agent-city-brain-tier-drift"
PILOT_HEAD = "9eb7f461a2859de5367812115f3a106926c47761"
REPO = "kimeisele/agent-city"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def run_dir(tmp_path: Path, monkeypatch) -> Path:
    """Copy the canonical Pilot Run 01 artifacts into a temp run dir."""
    src = REPO_ROOT / "runs" / PILOT_RUN
    dst = tmp_path / "runs" / PILOT_RUN
    shutil.copytree(src, dst)
    monkeypatch.setenv("FEDERATION_HQ_RUNS_DIR", str(tmp_path / "runs"))
    return dst


class FakeCurl:
    """Deterministic fake transport for the curl-based HTTP client."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: dict[tuple[str, str], dict | list | None] = {}
        self.default = {"ok": True, "id": 1, "html_url": "https://github.com/example/check"}

    def route(self, method: str, path_fragment: str, response):
        self.responses[(method, path_fragment)] = response

    def run(self, args: list[str]) -> object:
        method = args[args.index("--request") + 1] if "--request" in args else "GET"
        url = args[args.index("--url") + 1]
        body = None
        if "--data" in args:
            body = json.loads(args[args.index("--data") + 1])
        self.calls.append((method, url, body))
        for (m, frag), response in self.responses.items():
            if m == method and frag in url:
                status = 200
                if isinstance(response, tuple):
                    response, status = response
                payload = "" if response is None else json.dumps(response)
                return _Completed(payload, status)
        payload = json.dumps(self.default)
        return _Completed(payload, 200)

    def posted_bodies(self, path_fragment: str) -> list[dict]:
        return [b for m, u, b in self.calls if m == "POST" and path_fragment in u and b]


class _Completed:
    def __init__(self, stdout: str, status: int) -> None:
        self.stdout = f"{stdout}\n{status}"
        self.stderr = ""
        self.returncode = 0


@pytest.fixture()
def fake_curl(monkeypatch):
    fake = FakeCurl()
    monkeypatch.setattr("federation_hq_gate.http._run_curl", fake.run)
    return fake


def _open_pr(head: str, number: int = 2712) -> list[dict]:
    return [{"number": number, "state": "open", "head": {"sha": head}}]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Exact-head success and publication ──────────────────────────────────────


def test_exact_head_success_publishes_success_check(run_dir, fake_curl):
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/pulls", _open_pr(PILOT_HEAD))
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/check-runs", {"check_runs": []})
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    summary = review_check.validate_review_chain(
        artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
    )
    assert summary["verdict"] == "approved"
    assert summary["reviewed_head_sha"] == PILOT_HEAD
    pr = review_check.verify_remote_pr_head("token", REPO, PILOT_HEAD)
    assert pr == 2712
    result = review_check.publish_success_check(
        "token", REPO, PILOT_HEAD, summary, pr, "12345"
    )
    assert result["idempotent"] is False
    body = fake_curl.posted_bodies("/check-runs")[-1]
    assert body["name"] == "federation-hq/review"
    assert body["head_sha"] == PILOT_HEAD
    assert body["conclusion"] == "success"
    assert f"run_id: {PILOT_RUN}" in body["output"]["summary"]
    assert "review_result_sha256:" in body["output"]["summary"]


def test_changed_remote_head_fails(run_dir, fake_curl):
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/pulls",
                    _open_pr("0" * 40))  # PR head moved away
    with pytest.raises(review_check.ReviewGateError, match="no longer equals"):
        review_check.verify_remote_pr_head("token", REPO, PILOT_HEAD)


def test_no_open_pr_containing_head_fails(run_dir, fake_curl):
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/pulls", [])
    with pytest.raises(review_check.ReviewGateError, match="no open pull request"):
        review_check.verify_remote_pr_head("token", REPO, PILOT_HEAD)


def test_wrong_repository_fails(run_dir):
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="target_repository"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository="kimeisele/other", head_sha=PILOT_HEAD
        )


def test_blocked_verdict_fails(run_dir):
    _rewrite(run_dir / "review-result.yaml", {"verdict": "blocked"})
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="not 'approved'"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
        )


def test_non_empty_blockers_fail(run_dir):
    _rewrite(run_dir / "review-result.yaml", {"blockers": ["evidence unreachable"]})
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="blockers is not empty"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
        )


def test_malformed_artifact_fails(run_dir):
    (run_dir / "review-result.yaml").write_text("kind: federation_hq_review_result\nverdict: x\n")
    with pytest.raises(review_check.ReviewGateError, match="fails its schema"):
        review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")


def test_mismatched_artifact_hash_fails(run_dir):
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="does not match the accepted hash"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD,
            expected_review_hash="0" * 64,
        )


def test_broken_lineage_fails(run_dir):
    _rewrite(run_dir / "review-result.yaml", {"result_id": "other-result"})
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="result_id does not match"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
        )


def test_superseded_review_fails(run_dir):
    (run_dir / "review-result-2.yaml").write_text(
        (run_dir / "review-result.yaml").read_text()
    )
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    with pytest.raises(review_check.ReviewGateError, match="supersedes"):
        review_check.validate_review_chain(
            artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
        )


def test_idempotent_repeated_publication(run_dir, fake_curl):
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/pulls", _open_pr(PILOT_HEAD))
    fake_curl.route("GET", f"/commits/{PILOT_HEAD}/check-runs", {
        "check_runs": [{
            "name": "federation-hq/review", "status": "completed", "conclusion": "success",
            "output": {"summary": f"run_id: {PILOT_RUN}"},
        }]
    })
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    summary = review_check.validate_review_chain(
        artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=PILOT_HEAD
    )
    result = review_check.publish_success_check(
        "token", REPO, PILOT_HEAD, summary, 2712, "12345"
    )
    assert result["idempotent"] is True
    assert fake_curl.posted_bodies("/check-runs") == []


def test_no_success_leakage_from_older_sha(run_dir, fake_curl):
    new_head = "1" * 40
    fake_curl.route("GET", f"/commits/{new_head}/pulls", _open_pr(new_head, 9999))
    fake_curl.route("GET", f"/commits/{new_head}/check-runs", {"check_runs": []})
    _rewrite(run_dir / "repair-result.yaml", {"repair_head_sha": new_head})
    _rewrite(run_dir / "review-result.yaml", {"reviewer_head_sha": new_head})
    artifacts = review_check.resolve_run_artifacts(PILOT_RUN, run_dir / "review-result.yaml")
    summary = review_check.validate_review_chain(
        artifacts, run_id=PILOT_RUN, repository=REPO, head_sha=new_head
    )
    review_check.publish_success_check("token", REPO, new_head, summary, 9999, "12345")
    body = fake_curl.posted_bodies("/check-runs")[-1]
    assert body["head_sha"] == new_head  # a brand-new check on the new SHA only


# ── Auth and permissions ────────────────────────────────────────────────────


def test_forbidden_app_permission_detection():
    installation = {"permissions": {"administration": "write"}}
    with pytest.raises(auth.AuthError, match="forbidden permission"):
        auth._validate_installation_permissions(installation)


def test_missing_required_permission_detection():
    installation = {"permissions": {"metadata": "read"}}
    with pytest.raises(auth.AuthError, match="lacks required permission"):
        auth._validate_installation_permissions(installation)


def test_scoped_installation_token_request(fake_curl, monkeypatch):
    monkeypatch.setattr(
        "federation_hq_gate.auth._resolve_repository_id_via_gh", lambda owner, repo: 42
    )
    fake_curl.route("POST", "/access_tokens", {"token": "ghs_fake"})
    jwt = "fake-jwt"
    token, _ = auth.create_installation_token(
        jwt, "7", owner="kimeisele", repo="agent-city"
    )
    assert token == "ghs_fake"
    body = fake_curl.posted_bodies("/access_tokens")[-1]
    assert body["repository_ids"] == [42]
    assert body["permissions"] == {
        "metadata": "read", "contents": "read",
        "pull_requests": "read", "checks": "write",
    }


# ── Policy plan and apply ───────────────────────────────────────────────────


def _gh_repo(full: str, default_branch: str = "main", fork: bool = False,
             archived: bool = False, admin: bool = True) -> dict:
    return {
        "full_name": full, "default_branch": default_branch,
        "fork": fork, "archived": archived,
        "permissions": {"admin": admin},
    }


def _run_gh_canned(responses: dict) -> object:
    def run(args: list[str]) -> object:
        path = args[-1]
        if path in responses:
            payload = responses[path]
            return _Completed(json.dumps(payload), 200)
        raise AssertionError(f"unexpected gh call: {path}")
    return run


def test_deterministic_policy_plan(monkeypatch):
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [
            _gh_repo("kimeisele/agent-city", "main"),
            _gh_repo("kimeisele/federation-hq", "master"),
            _gh_repo("kimeisele/forked", fork=True),
            _gh_repo("kimeisele/archived", archived=True),
        ],
        "/repos/kimeisele/agent-city/branches/main/protection": {
            "required_status_checks": {"strict": True, "contexts": ["existing-check"]},
            "required_pull_request_reviews": {"required_approving_review_count": 1},
        },
        "/repos/kimeisele/agent-city/rulesets": [],
        "/repos/kimeisele/federation-hq/branches/master/protection": {
            "required_status_checks": {"strict": True, "contexts": []},
            "required_pull_request_reviews": {"required_approving_review_count": 1},
        },
        "/repos/kimeisele/federation-hq/rulesets": [],
    }
    monkeypatch.setattr(policy, "gh_get", lambda path: responses[path])
    plan_a = policy.build_plan("kimeisele", set())
    plan_b = policy.build_plan("kimeisele", set())
    assert policy.plan_sha256(plan_a) == policy.plan_sha256(plan_b)
    repos = {e["repository"]: e for e in plan_a["repositories"]}
    assert "kimeisele/agent-city" in repos
    assert repos["kimeisele/agent-city"]["default_branch"] == "main"
    assert repos["kimeisele/federation-hq"]["default_branch"] == "master"
    assert repos["kimeisele/agent-city"]["existing_required_checks"] == ["existing-check"]
    skipped = {s["repository"]: s["reason"] for s in plan_a["skipped"]}
    assert skipped["kimeisele/forked"] == "fork"
    assert skipped["kimeisele/archived"] == "archived"


def test_policy_plan_exclusions(monkeypatch):
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [
            _gh_repo("kimeisele/agent-city"),
        ],
        "/repos/kimeisele/agent-city/branches/main/protection": {},
        "/repos/kimeisele/agent-city/rulesets": [],
    }
    monkeypatch.setattr(policy, "gh_get", lambda path: responses[path])
    plan = policy.build_plan("kimeisele", {"kimeisele/agent-city"})
    assert plan["repositories"] == []
    assert plan["skipped"][0]["reason"] == "explicit exclusion"


def test_plan_hash_mismatch_aborts():
    plan = {"repositories": []}
    with pytest.raises(policy.PolicyError, match="does not match the confirmed hash"):
        policy.apply_plan(plan, expected_sha256="0" * 64, app_installation_token_fn=lambda **k: "t",
                          dry_run=True)


def test_repository_drift_between_plan_and_apply(monkeypatch):
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [_gh_repo("kimeisele/agent-city")],
        "/repos/kimeisele/agent-city/branches/main/protection": {"required_status_checks": {"strict": True, "contexts": []}},
        "/repos/kimeisele/agent-city/rulesets": [],
    }
    monkeypatch.setattr(policy, "gh_get", lambda path: responses[path])
    plan = policy.build_plan("kimeisele", set())
    # Drift: default branch changed on the remote.
    responses["/repos/kimeisele/agent-city"] = _gh_repo("kimeisele/agent-city", "develop")
    responses["/repos/kimeisele/agent-city/branches/develop/protection"] = {"required_status_checks": {"strict": True, "contexts": []}}
    responses["/repos/kimeisele/agent-city/rulesets"] = []
    report = policy.apply_plan(plan, expected_sha256=plan["plan_sha256"],
                               app_installation_token_fn=lambda **k: "t", dry_run=False)
    assert report["repositories"][0]["status"] == "failed"
    assert "changed materially" in report["repositories"][0]["reason"]


def test_apply_preserves_existing_checks_and_uses_default_branch(monkeypatch, tmp_path):
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [
            _gh_repo("kimeisele/agent-city", "master"),
        ],
        "/repos/kimeisele/agent-city": _gh_repo("kimeisele/agent-city", "master"),
        "/repos/kimeisele/agent-city/branches/master/protection": {
            "required_status_checks": {"strict": True, "contexts": ["ci/build"]},
            "required_pull_request_reviews": {"required_approving_review_count": 1,
                                              "dismiss_stale_reviews": False,
                                              "require_code_owner_reviews": False,
                                              "require_last_push_approval": False},
            "enforce_admins": False,
            "required_conversation_resolution": True,
        },
        "/repos/kimeisele/agent-city/rulesets": [],
    }
    monkeypatch.setattr(policy, "gh_get", lambda path: responses[path])
    plan = policy.build_plan("kimeisele", set())
    puts: list[tuple[str, dict]] = []

    def fake_gh_put(path, body):
        puts.append((path, body))
        responses[path] = body  # reflect the write for remote verification
        return body

    monkeypatch.setattr(policy, "gh_put", fake_gh_put)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False, app_id="42",
        backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    path, body = puts[0]
    assert path == "/repos/kimeisele/agent-city/branches/master/protection"
    checks = body["required_status_checks"]["checks"]
    assert {"context": "ci/build"} in checks
    assert {"context": "federation-hq/review", "app_id": 42} in checks
    assert body["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert body["required_conversation_resolution"] is True
    assert body["allow_force_pushes"] is False


def test_partial_fleet_failure_continues(monkeypatch, tmp_path):
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [
            _gh_repo("kimeisele/good"),
            _gh_repo("kimeisele/bad"),
        ],
        "/repos/kimeisele/good": _gh_repo("kimeisele/good"),
        "/repos/kimeisele/bad": _gh_repo("kimeisele/bad"),
        "/repos/kimeisele/good/branches/main/protection": {},
        "/repos/kimeisele/good/rulesets": [],
        "/repos/kimeisele/bad/branches/main/protection": {},
        "/repos/kimeisele/bad/rulesets": [],
    }
    monkeypatch.setattr(policy, "gh_get", lambda path: responses[path])
    plan = policy.build_plan("kimeisele", set())

    def fake_put(path, body):
        if "bad" in path:
            raise policy.PolicyError("boom")
        responses[path] = body  # reflect the write for remote verification
        return body

    monkeypatch.setattr(policy, "gh_put", fake_put)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    report = policy.apply_plan(plan, expected_sha256=plan["plan_sha256"],
                               app_installation_token_fn=lambda **k: "t", dry_run=False,
                               app_id="42", backup_dir=tmp_path)
    statuses = {r["repository"]: r["status"] for r in report["repositories"]}
    assert statuses["kimeisele/good"] == "configured"
    assert statuses["kimeisele/bad"] == "failed"


def test_rollback_restores_before_state(monkeypatch, tmp_path):
    backup = {
        "kimeisele/agent-city": {
            "default_branch": "main",
            "protection": {"classic": {
                "required_status_checks": {"strict": True, "contexts": ["ci/build"]},
            }},
        }
    }
    path = tmp_path / "backup.json"
    path.write_text(json.dumps(backup))
    puts: list[tuple[str, dict]] = []
    gets: dict[str, object] = {}
    gets["/repos/kimeisele/agent-city/rulesets"] = []

    def fake_put(ppath, body):
        puts.append((ppath, body))
        gets[ppath] = body  # reflect the write for rollback verification
        return body

    monkeypatch.setattr(policy, "gh_put", fake_put)
    monkeypatch.setattr(policy, "gh_get", lambda path: gets[path])
    report = policy.rollback(path)
    assert report["results"][0]["status"] == "restored"
    assert puts[0][0] == "/repos/kimeisele/agent-city/branches/main/protection"
    assert puts[0][1]["required_status_checks"]["checks"] == [{"context": "ci/build"}]


# ── Secret redaction ────────────────────────────────────────────────────────


def test_secret_redaction():
    assert config.redact("token ghp_abcdef12345 leaked") == "token <redacted>"
    assert config.redact("no secrets here") == "no secrets here"
    assert config.redact("-----BEGIN RSA PRIVATE KEY-----xxxx") == "<redacted>"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _rewrite(path: Path, changes: dict) -> None:
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
