"""Focused tests for the Review Gate policy-apply safety fixes.

Covers: bootstrap-failure fail-closed, Classic App-ID check binding,
include-only canary scope, and mechanism-aware rollback. Mocks only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import policy  # noqa: E402

GATE_RULESET = {
    "id": 900, "name": policy.GATE_RULESET_NAME,
    "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [
        {"context": "federation-hq/review", "integration_id": 4528340}]}}],
}
UNRELATED_RULESET = {"id": 901, "name": "other-ruleset", "rules": []}


def _gh_repo(full: str, default_branch: str = "main", admin: bool = True) -> dict:
    return {
        "full_name": full, "default_branch": default_branch,
        "fork": False, "archived": False,
        "permissions": {"admin": admin},
    }


def _classic(existing_checks: list | None = None, contexts: list | None = None) -> dict:
    rsc: dict = {"strict": True}
    if existing_checks is not None:
        rsc["checks"] = existing_checks
    if contexts is not None:
        rsc["contexts"] = contexts
    return {
        "required_status_checks": rsc,
        "required_pull_request_reviews": {"required_approving_review_count": 1},
    }


class Recorder:
    def __init__(self, reflect_deletes: bool = True) -> None:
        self.gets: dict[str, object] = {}
        self.puts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.default_state: dict | None = None
        self.reflect_deletes = reflect_deletes

    def gh_get(self, path: str):
        if path.endswith("/branches/main/protection") and "branches/main/protection" in self.gets:
            return self.gets[path]
        if path in self.gets:
            return self.gets[path]
        if self.default_state is not None:
            return self.default_state
        raise AssertionError(f"unexpected gh GET {path}")

    def gh_put(self, path: str, body: dict):
        self.puts.append((path, body))
        # Mirror successful writes into the GET map so post-write remote
        # verification observes the applied state.
        self.gets[path] = body
        return body

    def gh_delete(self, path: str):
        self.deletes.append(path)
        if not self.reflect_deletes:
            return
        # Reflect the deletion so rollback verification observes it.
        if "/rulesets/" in path and self.gets.get(
            "/repos/kimeisele/federation-hq/rulesets"
        ) is not None:
            self.gets["/repos/kimeisele/federation-hq/rulesets"] = []
        elif path.endswith("/protection"):
            self.gets[path] = None  # deleted protection reads back as absent
        else:
            self.gets.pop(path, None)


def _plan_for(rec: Recorder, repos: list[dict], includes: set[str] | None = None,
              exclusions: set[str] | None = None) -> dict:
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = repos
    for repo in repos:
        rec.gets[f"/repos/{repo['full_name']}/branches/{repo['default_branch']}/protection"] = _classic()
        rec.gets[f"/repos/{repo['full_name']}/rulesets"] = []
        rec.gets[f"/repos/{repo['full_name']}"] = repo
    return policy.build_plan("kimeisele", exclusions or set(), includes)


@pytest.fixture()
def rec(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    return rec


def _apply(plan: dict, rec: Recorder, *, app_id: str = "4528340",
           bootstrap_ok: bool = True) -> dict:
    monkey = pytest.MonkeyPatch()
    monkey.setattr(policy, "_bootstrap_repo",
                   lambda fn, full, branch: (bootstrap_ok, "bootstrapped" if bootstrap_ok else "boom"))
    try:
        return policy.apply_plan(
            plan, expected_sha256=plan["plan_sha256"],
            app_installation_token_fn=lambda **k: "t", dry_run=False,
            app_id=app_id, backup_dir=Path("/tmp"),
        )
    finally:
        monkey.undo()


# ── Defect 1: bootstrap failure stops protection mutation ──────────────────


def test_bootstrap_failure_causes_zero_protection_writes(rec):
    plan = _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(plan, rec, bootstrap_ok=False)
    assert report["repositories"][0]["status"] == "failed"
    assert "bootstrap failed" in report["repositories"][0]["reason"]
    assert rec.puts == [] and rec.deletes == []


def test_bootstrap_success_permits_protection_write(rec):
    plan = _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(plan, rec, bootstrap_ok=True)
    assert report["repositories"][0]["status"] == "configured"
    assert rec.puts, "expected a protection write after successful bootstrap"


# ── Defect 2: Classic App-ID binding ───────────────────────────────────────


def test_classic_required_check_contains_exact_app_id(rec):
    plan = _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    _apply(plan, rec)
    path, body = rec.puts[0]
    assert path.endswith("/branches/main/protection")
    checks = body["required_status_checks"]["checks"]
    assert {"context": "federation-hq/review", "app_id": 4528340} in checks


def test_existing_app_bound_checks_preserved(rec):
    _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic(
        existing_checks=[
            {"context": "ci/build", "app_id": 111},
            {"context": "legacy"},
        ]
    )
    plan2 = policy.build_plan("kimeisele", set(), set())
    _apply(plan2, rec)
    checks = rec.puts[0][1]["required_status_checks"]["checks"]
    assert {"context": "ci/build", "app_id": 111} in checks  # binding preserved
    assert {"context": "legacy"} in checks  # unbound preserved as unbound
    assert {"context": "federation-hq/review", "app_id": 4528340} in checks


def test_missing_app_id_fails_closed(rec):
    plan = _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(plan, rec, app_id=None)
    assert report["repositories"][0]["status"] == "failed"
    assert "App ID unavailable" in report["repositories"][0]["reason"]
    assert rec.puts == [] and rec.deletes == []


def test_verification_rejects_wrong_app_id():
    classic = {"required_status_checks": {"strict": True, "checks": [
        {"context": "federation-hq/review", "app_id": 999},
    ]}}
    assert policy._required_check_bound(classic, [], "4528340") is False
    assert policy._required_check_bound(classic, [], "999") is True


def test_unbound_context_is_not_accepted_as_app_bound():
    classic = {"required_status_checks": {"strict": True, "contexts": ["federation-hq/review"]}}
    assert policy._required_check_bound(classic, [], "4528340") is False


# ── Defect 3: include-only canary scope ────────────────────────────────────


def test_include_only_plan_contains_exactly_requested_repositories(rec):
    repos = [
        _gh_repo("kimeisele/federation-hq"),
        _gh_repo("kimeisele/agent-city"),
        _gh_repo("kimeisele/other"),
    ]
    plan = _plan_for(rec, repos, includes={"kimeisele/federation-hq", "kimeisele/agent-city"})
    configured = {e["repository"] for e in plan["repositories"] if not e.get("skip_reason")}
    assert configured == {"kimeisele/federation-hq", "kimeisele/agent-city"}
    skipped = {s["repository"]: s["reason"] for s in plan["skipped"]}
    assert skipped["kimeisele/other"] == "not included"


def test_unknown_include_fails(rec):
    repos = [_gh_repo("kimeisele/federation-hq")]
    with pytest.raises(policy.PolicyError, match="not an owned repository"):
        _plan_for(rec, repos, includes={"kimeisele/ghost"})


def test_owner_mismatch_fails(rec):
    repos = [_gh_repo("kimeisele/federation-hq")]
    with pytest.raises(policy.PolicyError, match="does not match plan owner"):
        _plan_for(rec, repos, includes={"other-org/repo"})


def test_include_exclude_contradiction_fails(rec):
    repos = [_gh_repo("kimeisele/federation-hq")]
    with pytest.raises(policy.PolicyError, match="include/exclude contradiction"):
        _plan_for(rec, repos, includes={"kimeisele/federation-hq"},
                  exclusions={"kimeisele/federation-hq"})


def test_plan_hash_changes_with_include_scope(rec):
    repos = [_gh_repo("kimeisele/federation-hq"), _gh_repo("kimeisele/agent-city")]
    a = _plan_for(rec, repos, includes={"kimeisele/federation-hq"})
    b = _plan_for(rec, repos, includes={"kimeisele/federation-hq", "kimeisele/agent-city"})
    assert a["plan_sha256"] != b["plan_sha256"]


def test_apply_cannot_operate_outside_plan_contents(rec):
    plan = _plan_for(rec, [_gh_repo("kimeisele/federation-hq")])
    tampered = json.loads(json.dumps(plan))
    tampered["repositories"].append({"repository": "kimeisele/agent-city",
                                     "default_branch": "main",
                                     "protection_snapshot": {"classic": {}, "rulesets": []}})
    with pytest.raises(policy.PolicyError, match="does not match the confirmed hash"):
        policy.apply_plan(tampered, expected_sha256=plan["plan_sha256"],
                          app_installation_token_fn=lambda **k: "t",
                          dry_run=False, app_id="4528340", backup_dir=Path("/tmp"))


# ── Defect 4: mechanism-aware rollback ─────────────────────────────────────


def test_rollback_deletes_gate_ruleset_created_by_apply(rec, tmp_path):
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    # Before: no gate ruleset, no classic. After apply: gate ruleset exists.
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.default_state = None
    report = policy.rollback(backup)
    assert report["results"][0]["status"] == "restored"
    assert any("ruleset-deleted" in a for a in report["results"][0]["actions"])
    assert "/repos/kimeisele/federation-hq/rulesets/900" in rec.deletes
    # Classic was never created and current classic is absent -> no-op, no DELETE.
    assert any("classic-no-op" in a for a in report["results"][0]["actions"])
    assert "/repos/kimeisele/federation-hq/branches/main/protection" not in rec.deletes


def test_rollback_restores_gate_ruleset_updated_by_apply(rec, tmp_path):
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    before_gate = {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "deletion"}]}
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(before_gate)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    report = policy.rollback(backup)
    assert any("ruleset-restored" in a for a in report["results"][0]["actions"])
    # Blocker C: restore uses the normalized write-safe update payload, not
    # the raw GET/list object (no id, no read-only fields).
    expected = policy._normalize_ruleset_for_write(before_gate)
    assert rec.puts[0] == ("/repos/kimeisele/federation-hq/rulesets/900", expected)
    assert "id" not in rec.puts[0][1]


def test_unrelated_rulesets_remain_untouched(rec, tmp_path):
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(UNRELATED_RULESET)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(UNRELATED_RULESET)]
    policy.rollback(backup)
    assert rec.puts == []
    # No Gate ruleset and no classic protection exist anywhere -> zero deletes.
    assert rec.deletes == []


def test_rollback_removes_classic_created_on_unprotected_branch(rec, tmp_path):
    # Apply created classic protection on a previously unprotected branch, so
    # the current remote HAS protection while the before-state had none.
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    report = policy.rollback(backup)
    assert "/repos/kimeisele/federation-hq/branches/main/protection" in rec.deletes
    assert any("classic-removed" in a for a in report["results"][0]["actions"])


def test_rollback_restores_classic_exactly(rec, tmp_path):
    before_classic = _classic(contexts=["ci/build"])
    before = {"default_branch": "main",
              "protection": {"classic": before_classic, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = before_classic
    report = policy.rollback(backup)
    # Blocker C: restore uses the normalized, write-safe payload (contexts
    # become checks entries; read-only fields are absent).
    expected = policy._normalize_classic_for_write(before_classic)
    assert rec.puts[0] == ("/repos/kimeisele/federation-hq/branches/main/protection",
                           expected)
    assert report["results"][0]["verification"]["ok"] is True


def test_rollback_verification_failure_reported(monkeypatch, tmp_path):
    rec = Recorder(reflect_deletes=False)  # the remote delete does not stick
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    # After rollback the gate ruleset is still present remotely -> verify fails.
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    verification = report["results"][0]["verification"]
    assert verification["ok"] is False
    assert report["results"][0]["status"] == "failed"
    assert any("still present" in p for p in verification["problems"])
