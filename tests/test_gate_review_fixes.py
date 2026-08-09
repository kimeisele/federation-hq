"""Regression tests for the review-fix blockers (A/B/C) on the policy apply
safety path: verification fail-closed with restore, durable atomic backup
before mutation, and round-trippable normalized snapshots. Mocks only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import policy  # noqa: E402
from tests.test_gate_policy_safety import _classic, _gh_repo  # noqa: E402

GATE_RULESET = {
    "id": 900, "name": policy.GATE_RULESET_NAME,
    "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [
        {"context": "federation-hq/review", "integration_id": 4528340}]}}],
}


class OrderRecorder:
    """Records operation order and supports a verification-failure mode."""

    def __init__(self, mirror_puts: bool = True, verify_ok: bool = True,
                 reflect_deletes: bool = True) -> None:
        self.gets: dict[str, object] = {}
        self.puts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.events: list[str] = []
        self.mirror_puts = mirror_puts
        self.verify_ok = verify_ok
        self.reflect_deletes = reflect_deletes

    def gh_get(self, path: str):
        if path in self.gets:
            return self.gets[path]
        raise AssertionError(f"unexpected gh GET {path}")

    def gh_put(self, path: str, body: dict):
        self.puts.append((path, body))
        self.events.append(f"put:{path}")
        if self.mirror_puts and (path.endswith("/protection") or "/rulesets/" in path):
            self.gets[path] = body
        elif self.mirror_puts:
            self.gets[path] = body
        return body

    def gh_delete(self, path: str):
        self.deletes.append(path)
        self.events.append(f"delete:{path}")
        if not self.reflect_deletes:
            return
        if "/rulesets/" in path and self.gets.get(
            "/repos/kimeisele/federation-hq/rulesets"
        ) is not None:
            self.gets["/repos/kimeisele/federation-hq/rulesets"] = []
        elif path.endswith("/protection"):
            self.gets[path] = None
        else:
            self.gets.pop(path, None)


def _plan(rec, repos, includes=None):
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = repos
    for repo in repos:
        rec.gets[f"/repos/{repo['full_name']}/branches/{repo['default_branch']}/protection"] = _classic()
        rec.gets[f"/repos/{repo['full_name']}/rulesets"] = []
        rec.gets[f"/repos/{repo['full_name']}"] = repo
    monkey = pytest.MonkeyPatch()
    monkey.setattr(policy, "gh_get", rec.gh_get)
    try:
        return policy.build_plan("kimeisele", set(), includes)
    finally:
        monkey.undo()


def _apply(rec, plan, *, app_id="4528340", bootstrap_ok=True, tmp_path=None):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(policy, "_bootstrap_repo",
                   lambda fn, full, branch: (bootstrap_ok, "bootstrapped" if bootstrap_ok else "boom"))
    monkey.setattr(policy, "gh_get", rec.gh_get)
    monkey.setattr(policy, "gh_put", rec.gh_put)
    monkey.setattr(policy, "gh_delete", rec.gh_delete)
    try:
        return policy.apply_plan(
            plan, expected_sha256=plan["plan_sha256"],
            app_installation_token_fn=lambda **k: "t", dry_run=False,
            app_id=app_id, backup_dir=tmp_path or Path("/tmp"),
        )
    finally:
        monkey.undo()


# ── Blocker A: verification fail-closed with before-state restore ──────────


def test_verification_failure_reported_as_failed_not_configured(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)  # remote never reflects the write
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert "remote verification failed" in entry["reason"]


def test_verification_failure_restores_before_state(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    entry = report["repositories"][0]
    # The before-state classic protection must be restored via PUT.
    restore_puts = [p for p in rec.puts if p[0].endswith("/branches/main/protection")]
    assert restore_puts, "no restore PUT after verification failure"
    assert entry["rollback"]["status"] == "restored"


def test_rollback_failure_reported_explicitly(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])

    protection_put_count = 0

    def failing_put(path, body):
        nonlocal protection_put_count
        if path.endswith("/branches/main/protection"):
            protection_put_count += 1
            if protection_put_count == 1:
                # configure write: succeeds but is not reflected remotely
                return body
            raise policy.PolicyError("restore failed")
        return body

    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", failing_put)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert entry["rollback"]["status"] == "failed"


# ── Blocker B: durable atomic backup before mutation ───────────────────────


def test_backup_written_before_any_mutation(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    original_write_atomic = policy._write_atomic

    def recording_write_atomic(path: Path, data: dict):
        rec.events.append("backup")
        original_write_atomic(path, data)

    monkeypatch.setattr(policy, "_write_atomic", recording_write_atomic)
    _apply(rec, plan, tmp_path=tmp_path)
    # The durable backup write must precede every protection mutation, and
    # the mutation-journal marker persist (a second atomic backup write) must
    # also precede the first protection write.
    assert rec.events[0] == "backup"
    assert rec.puts, "expected at least one protection write"
    first_write = next(i for i, e in enumerate(rec.events) if "put:" in e or "delete:" in e)
    assert any(e == "backup" for e in rec.events[:first_write])


def test_backup_is_atomic_and_durable(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    assert not backup_path.with_suffix(backup_path.suffix + ".tmp").exists()
    backup = json.loads(backup_path.read_text())
    assert "kimeisele/federation-hq" in backup
    assert backup["kimeisele/federation-hq"]["protection"]["classic"] is not None


def test_backup_persists_after_write_for_manual_rollback(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    assert report["repositories"][0]["status"] == "configured"
    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    data = json.loads(backup_path.read_text())
    assert "kimeisele/federation-hq" in data  # durable before-state for manual rollback


# ── Blocker C: round-trippable normalized snapshots ────────────────────────


def test_gate_ruleset_full_representation_fetched_before_mutation(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    plan = policy.build_plan("kimeisele", set(), set())
    snapshot = plan["repositories"][0]["protection_snapshot"]
    gate = [r for r in snapshot["rulesets"] if r["name"] == policy.GATE_RULESET_NAME][0]
    assert "write_safe" in gate  # full representation stored, normalized
    assert "id" not in gate["write_safe"]
    assert gate["write_safe"]["rules"] == GATE_RULESET["rules"]


def test_ruleset_restore_payload_is_normalized_update_payload(monkeypatch, tmp_path):
    rec = OrderRecorder()
    before_gate = {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "deletion"}]}
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(before_gate)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    policy.rollback(backup)
    path, body = rec.puts[0]
    assert path == "/repos/kimeisele/federation-hq/rulesets/900"
    assert body == policy._normalize_ruleset_for_write(before_gate)
    assert "id" not in body and "created_at" not in body


def test_rollback_verification_detects_non_matching_gate_ruleset(monkeypatch, tmp_path):
    rec = OrderRecorder(mirror_puts=False)
    before_gate = {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "deletion"}]}
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(before_gate)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    # The full current ruleset DIFFERS from the stored representation.
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "non_fast_forward"}],
    }
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    verification = report["results"][0]["verification"]
    assert verification["ok"] is False
    assert any("not restored to its previous representation" in p for p in verification["problems"])


# ── Final review fixes: full ruleset fetch, enabled decoding, rollback status


def test_ruleset_verification_fetches_full_gate_ruleset(monkeypatch):
    """Verification uses the individual ruleset endpoint, not the summary."""
    rec = OrderRecorder(mirror_puts=False)
    # List response is a SUMMARY without a rules array.
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}
    ]
    # The FULL representation (with the actual rules) lives on the
    # individual endpoint.
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [
            {"type": "required_status_checks", "parameters": {"required_status_checks": [
                {"context": "federation-hq/review", "integration_id": 4528340}]}},
        ]}
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is True
    assert verify["required_check_app_bound"] is True


def test_ruleset_verification_rejects_wrong_integration_id(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [
            {"type": "required_status_checks", "parameters": {"required_status_checks": [
                {"context": "federation-hq/review", "integration_id": 999}]}},
        ]}
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is False


def test_github_enabled_object_decoding():
    assert policy._github_enabled({"enabled": False}) is False
    assert policy._github_enabled({"enabled": True}) is True
    assert policy._github_enabled(True) is True
    assert policy._github_enabled(False) is False
    assert policy._github_enabled(None) is False
    assert policy._github_enabled({}) is False


def test_normalize_does_not_enable_force_pushes_from_false_object():
    raw = {
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    normalized = policy._normalize_classic_for_write(raw)
    assert normalized["allow_force_pushes"] is False
    assert normalized["allow_deletions"] is False
    assert normalized["enforce_admins"] is True


def test_configure_cannot_enable_force_pushes_from_false_get_state(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = {
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    body = rec.puts[0][1]
    assert body["allow_force_pushes"] is False
    assert body["allow_deletions"] is False


def test_rollback_verified_success_returns_restored(tmp_path, monkeypatch):
    rec = OrderRecorder()
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    assert report["results"][0]["status"] == "restored"
    assert report["results"][0]["verification"]["ok"] is True


def test_rollback_verification_failure_returns_failed(tmp_path, monkeypatch):
    rec = OrderRecorder(reflect_deletes=False)
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    assert report["results"][0]["status"] == "failed"
    assert report["results"][0]["verification"]["ok"] is False


def test_apply_report_distinguishes_policy_and_rollback_outcomes(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)  # remote never reflects the write
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert entry["policy_verification"]["ok"] is False
    assert entry["rollback"]["status"] == "restored"


# ── Classic null semantics preservation ─────────────────────────────────────


def _null_raw() -> dict:
    """A GET response where both protection subsystems are disabled (null)."""
    return {
        "required_status_checks": None,
        "enforce_admins": None,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "required_linear_history": None,
        "allow_force_pushes": None,
        "allow_deletions": None,
    }


def test_required_status_checks_null_remains_null():
    normalized = policy._normalize_classic_for_write(_null_raw())
    assert normalized["required_status_checks"] is None


def test_required_pull_request_reviews_null_remains_null():
    normalized = policy._normalize_classic_for_write(_null_raw())
    assert normalized["required_pull_request_reviews"] is None


def test_rollback_restores_required_status_checks_null(tmp_path, monkeypatch):
    rec = OrderRecorder()
    before_classic = _null_raw()
    before = {"default_branch": "main",
              "protection": {"classic": before_classic, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    policy.rollback(backup)
    body = rec.puts[0][1]
    assert body["required_status_checks"] is None
    assert body["required_pull_request_reviews"] is None


def test_rollback_does_not_create_absent_status_check_protection(tmp_path, monkeypatch):
    """Rollback must not enable status-check protection that did not exist."""
    rec = OrderRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": _null_raw(), "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    policy.rollback(backup)
    body = rec.puts[0][1]
    assert body["required_status_checks"] is None
    assert body["required_pull_request_reviews"] is None


def test_rollback_does_not_create_absent_pr_review_protection(tmp_path, monkeypatch):
    rec = OrderRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": _null_raw(), "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    policy.rollback(backup)
    body = rec.puts[0][1]
    assert body["required_pull_request_reviews"] is None


def test_non_null_sections_still_round_trip():
    raw = _classic(contexts=["ci/build"])
    normalized = policy._normalize_classic_for_write(raw)
    assert normalized["required_status_checks"] == {
        "strict": True, "checks": [{"context": "ci/build"}]}
    assert normalized["required_pull_request_reviews"]["required_approving_review_count"] == 1
    # Re-normalizing the normalized form is idempotent.
    assert policy._normalize_classic_for_write(normalized) == normalized


def test_nullable_enforce_admins_and_force_pushes_retained():
    raw = _null_raw()
    raw["enforce_admins"] = None
    raw["allow_force_pushes"] = None
    normalized = policy._normalize_classic_for_write(raw)
    assert normalized["enforce_admins"] is None
    assert normalized["allow_force_pushes"] is None
    # And objects still decode:
    raw["enforce_admins"] = {"enabled": True}
    raw["allow_force_pushes"] = {"enabled": False}
    normalized = policy._normalize_classic_for_write(raw)
    assert normalized["enforce_admins"] is True
    assert normalized["allow_force_pushes"] is False


# ── Canary live-defect fixes: POST ruleset creation + mutation journal ─────


class PostRecorder(OrderRecorder):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.posts: list[tuple[str, dict]] = []

    def gh_post(self, path: str, body: dict):
        self.posts.append((path, body))
        self.events.append(f"post:{path}")
        # Mirror the created ruleset so post-write verification sees it.
        if path.endswith("/rulesets"):
            created = dict(body)
            created["id"] = 900
            self.gets["/repos/kimeisele/federation-hq/rulesets"] = [
                {"id": 900, "name": created["name"], "rules": created.get("rules", [])}
            ]
            self.gets["/repos/kimeisele/federation-hq/rulesets/900"] = created
        return body


def test_ruleset_creation_uses_post(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    # Existing rulesets but NO Gate ruleset -> creation path.
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 20466659, "name": "agent-federation-baseline-v1", "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    assert any(path == "/repos/kimeisele/federation-hq/rulesets" for path, _ in rec.posts)
    assert not any(path == "/repos/kimeisele/federation-hq/rulesets" for path, _ in rec.puts)


def test_ruleset_update_uses_put_by_id(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    assert any(path == "/repos/kimeisele/federation-hq/rulesets/900" for path, _ in rec.puts)
    assert rec.posts == []


def test_failed_post_reports_failure_not_success(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 20466659, "name": "agent-federation-baseline-v1", "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)

    def failing_post(path, body):
        raise policy.PolicyError("HTTP 404 Not Found")

    monkeypatch.setattr(policy, "gh_post", failing_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert "404" in entry["reason"]


def test_backup_initially_records_mutation_not_started(monkeypatch, tmp_path):
    rec = PostRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    backup_events: list[str] = []
    original_write_atomic = policy._write_atomic

    def recording_write_atomic(path: Path, data: dict):
        backup_events.append("backup")
        if len(backup_events) == 1:  # first persist: before any marker
            assert data["kimeisele/federation-hq"]["policy_mutation_started"] is False
        original_write_atomic(path, data)

    monkeypatch.setattr(policy, "_write_atomic", recording_write_atomic)
    _apply(rec, plan, tmp_path=tmp_path)
    assert backup_events[0] == "backup"
    # The second persist (marker) records mutation-started BEFORE the write.
    backup_path = Path(tmp_path) / [f.name for f in tmp_path.iterdir() if "policy-backup" in f.name][0]
    journal = json.loads(backup_path.read_text())
    assert journal["kimeisele/federation-hq"]["policy_mutation_started"] is True


def test_repo_failing_before_write_remains_mutation_not_started(monkeypatch, tmp_path):
    rec = PostRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    # Bootstrap fails -> apply stops before the marker.
    _apply(rec, plan, tmp_path=tmp_path, bootstrap_ok=False)
    backup_path = Path(tmp_path) / [f.name for f in tmp_path.iterdir() if "policy-backup" in f.name][0]
    journal = json.loads(backup_path.read_text())
    assert journal["kimeisele/federation-hq"]["policy_mutation_started"] is False


def test_rollback_mutation_not_started_performs_zero_writes(monkeypatch, tmp_path):
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": _classic(), "rulesets": []},
              "policy_mutation_started": False}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert result["status"] == "restored"
    assert "no-op-already-original" in result["actions"]
    assert rec.puts == [] and rec.posts == [] and rec.deletes == []


def test_rollback_mutation_not_started_with_external_drift_reports_failure(monkeypatch, tmp_path):
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": _classic(), "rulesets": []},
              "policy_mutation_started": False}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    # External drift: current protection differs from before-state.
    drifted = _classic(contexts=["external-drift-check"])
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = drifted
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert result["status"] == "failed"
    assert "external drift" in result["reason"]
    assert rec.puts == [] and rec.posts == [] and rec.deletes == []


def test_rollback_mutation_started_still_restores(monkeypatch, tmp_path):
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": _classic(), "rulesets": []},
              "policy_mutation_started": True}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert result["status"] == "restored"
    assert any("classic-restored" in a for a in result["actions"])


def test_rollback_classic_absent_is_idempotent_noop(monkeypatch, tmp_path):
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": []},
              "policy_mutation_started": True}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert any("classic-no-op" in a for a in result["actions"])
    assert not any("/branches/main/protection" in d for d in rec.deletes)


def test_rollback_gate_ruleset_absent_is_idempotent_noop(monkeypatch, tmp_path):
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": []},
              "policy_mutation_started": True}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert any("ruleset-no-op" in a for a in result["actions"])
    assert not any("rulesets" in d for d in rec.deletes)
    assert result["status"] == "restored"


def test_crash_window_after_marker_before_write_rolls_back_safely(monkeypatch, tmp_path):
    """Marker true but the remote never changed (crash before GitHub accepted
    the write): rollback must no-op and verify unchanged state."""
    rec = PostRecorder()
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": []},
              "policy_mutation_started": True}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    result = report["results"][0]
    assert result["status"] == "restored"
    assert any("classic-no-op" in a for a in result["actions"])
    assert any("ruleset-no-op" in a for a in result["actions"])
    assert rec.deletes == []
    assert result["verification"]["ok"] is True


# ── Ruleset required-status-check schema boundary (RULESET_SCHEMA_FIX) ─────


def _rs_rule(parameters: dict) -> dict:
    """A required_status_checks ruleset rule (ruleset schema)."""
    return {"type": "required_status_checks", "parameters": parameters}


def _gate_ruleset_full(parameters: dict) -> dict:
    return {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [_rs_rule(parameters)]}


def test_ruleset_create_payload_uses_required_status_checks(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 20466659, "name": "agent-federation-baseline-v1", "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    _, body = rec.posts[0]
    rs_rule = [r for r in body["rules"] if r["type"] == "required_status_checks"][0]
    assert rs_rule["parameters"]["required_status_checks"] == [
        {"context": "federation-hq/review", "integration_id": 4528340}]
    assert rs_rule["parameters"]["strict_required_status_checks_policy"] is True


def test_ruleset_create_payload_has_no_legacy_checks_parameter(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 20466659, "name": "agent-federation-baseline-v1", "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    _, body = rec.posts[0]
    for rule in body["rules"]:
        assert "checks" not in rule.get("parameters", {})


def test_ruleset_update_payload_uses_required_status_checks(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": []}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    path, body = rec.puts[0]
    assert path == "/repos/kimeisele/federation-hq/rulesets/900"
    rs_rule = [r for r in body["rules"] if r["type"] == "required_status_checks"][0]
    assert rs_rule["parameters"]["required_status_checks"] == [
        {"context": "federation-hq/review", "integration_id": 4528340}]
    assert "checks" not in rs_rule["parameters"]


def test_classic_protection_still_uses_checks_schema(monkeypatch, tmp_path):
    rec = PostRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/agent-city")]
    rec.gets["/repos/kimeisele/agent-city/branches/main/protection"] = _classic()
    rec.gets["/repos/kimeisele/agent-city/rulesets"] = []
    rec.gets["/repos/kimeisele/agent-city"] = _gh_repo("kimeisele/agent-city")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_post", rec.gh_post)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    body = rec.puts[0][1]
    rsc = body["required_status_checks"]
    assert rsc["checks"] == [{"context": "federation-hq/review", "app_id": 4528340}]
    assert "required_status_checks" not in rsc  # classic section keeps `checks`


def test_ruleset_bound_accepts_correct_integration_id():
    full = _gate_ruleset_full({"strict_required_status_checks_policy": True,
                               "required_status_checks": [
                                   {"context": "federation-hq/review",
                                    "integration_id": 4528340}]})
    assert policy._required_check_bound(None, full, "4528340") is True


def test_ruleset_bound_rejects_wrong_integration_id():
    full = _gate_ruleset_full({"strict_required_status_checks_policy": True,
                               "required_status_checks": [
                                   {"context": "federation-hq/review",
                                    "integration_id": 999}]})
    assert policy._required_check_bound(None, full, "4528340") is False
    assert policy._required_check_bound(None, full, "999") is True


def test_ruleset_verification_ignores_legacy_checks_schema():
    """A ruleset carrying only the Classic `parameters.checks` field must not
    count as an App-bound required check."""
    full = _gate_ruleset_full({"strict_required_status_checks_policy": True,
                               "checks": [{"context": "federation-hq/review",
                                            "integration_id": 4528340}]})
    assert policy._required_check_bound(None, full, "4528340") is False


def test_ruleset_inventory_reads_required_status_checks():
    protection = {"classic": None, "rulesets": [dict(GATE_RULESET)]}
    assert policy._existing_required_checks(protection) == ["federation-hq/review@4528340"]


def test_ruleset_inventory_preserves_integration_id_when_present():
    unbound = _rs_rule({"required_status_checks": [{"context": "ci/build"}]})
    protection = {"classic": None, "rulesets": [
        {"id": 901, "name": "other-ruleset", "rules": [unbound]}]}
    assert policy._existing_required_checks(protection) == ["ci/build"]


def test_ruleset_inventory_uses_write_safe_full_representation():
    """List summary without a `rules` array -> inventory falls back to the
    normalized full representation stored under `write_safe`."""
    summary = {"id": 900, "name": policy.GATE_RULESET_NAME}
    summary["write_safe"] = {
        "name": policy.GATE_RULESET_NAME, "target": "branch",
        "enforcement": "active", "bypass_actors": [], "conditions": {},
        "rules": [_rs_rule({"required_status_checks": [
            {"context": "federation-hq/review", "integration_id": 4528340}]})],
    }
    protection = {"classic": None, "rulesets": [summary]}
    assert policy._existing_required_checks(protection) == ["federation-hq/review@4528340"]


# ── Classic Branch Protection optional-404 boundary (CLASSIC_404_FIX) ─────


def _not_found_error(path: str) -> policy.PolicyError:
    return policy.PolicyError(f"gh GET {path} failed: gh: Branch not protected (HTTP 404)")


def _http_error(path: str, code: int) -> policy.PolicyError:
    return policy.PolicyError(f"gh GET {path} failed: gh: Server Error (HTTP {code})")


def _gate_full_rs(integration_id: int) -> dict:
    return {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [
            {"context": "federation-hq/review", "integration_id": integration_id}]}}]}


def _rollback_backup(tmp_path, protection: dict) -> Path:
    before = {"default_branch": "main", "protection": protection,
              "policy_mutation_started": True}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    return backup


def test_gh_get_optional_not_found_swallows_only_404(monkeypatch):
    def gh_get(path):
        if path == "/404":
            raise _not_found_error(path)
        if path == "/403":
            raise _http_error(path, 403)
        if path == "/ok":
            return {"ok": True}
        raise policy.PolicyError("gh GET /transport failed: Connection refused")

    monkeypatch.setattr(policy, "gh_get", gh_get)
    assert policy.gh_get_optional_not_found("/404") is None
    assert policy.gh_get_optional_not_found("/ok") == {"ok": True}
    for path in ("/403", "/transport"):
        with pytest.raises(policy.PolicyError):
            policy.gh_get_optional_not_found(path)


def test_verify_protection_ruleset_only_classic_404_ok(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = _gate_full_rs(4528340)

    def gh_get_404(path):
        if path.endswith("/branches/main/protection"):
            raise _not_found_error(path)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_404)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is True
    assert verify["required_check_app_bound"] is True
    assert verify["approval_count_zero"] is True


def test_verify_protection_classic_403_fails_closed(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_403(path):
        if path.endswith("/branches/main/protection"):
            raise _http_error(path, 403)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_403)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is False
    assert "403" in verify["reason"]


def test_verify_protection_classic_500_fails_closed(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_500(path):
        if path.endswith("/branches/main/protection"):
            raise _http_error(path, 500)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_500)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is False
    assert "500" in verify["reason"]


def test_normalize_protection_classic_404_means_absent(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_404(path):
        if path.endswith("/branches/main/protection"):
            raise _not_found_error(path)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_404)
    normalized = policy._normalize_protection(_gh_repo("kimeisele/federation-hq"), "main")
    assert normalized["classic"] is None


def test_normalize_protection_classic_403_propagates(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_403(path):
        if path.endswith("/branches/main/protection"):
            raise _http_error(path, 403)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_403)
    with pytest.raises(policy.PolicyError):
        policy._normalize_protection(_gh_repo("kimeisele/federation-hq"), "main")


def test_rollback_classic_404_current_is_idempotent_noop(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_404(path):
        if path.endswith("/branches/main/protection"):
            raise _not_found_error(path)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_404)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(_rollback_backup(tmp_path, {"classic": None, "rulesets": []}))
    result = report["results"][0]
    assert result["status"] == "restored"
    assert any("classic-no-op" in a for a in result["actions"])
    assert rec.deletes == []


def test_rollback_classic_403_fails_not_noop(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_403(path):
        if path.endswith("/branches/main/protection"):
            raise _http_error(path, 403)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_403)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(_rollback_backup(tmp_path, {"classic": None, "rulesets": []}))
    result = report["results"][0]
    assert result["status"] == "failed"
    assert "403" in result["reason"]
    assert rec.deletes == []


def test_verify_rollback_classic_404_accepted_when_before_absent(monkeypatch):
    rec = OrderRecorder()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_404(path):
        if path.endswith("/branches/main/protection"):
            raise _not_found_error(path)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_404)
    result = policy._verify_rollback(
        "kimeisele/federation-hq", "main", {"classic": None, "rulesets": []})
    assert result["ok"] is True


def test_verify_rollback_classic_403_fails(monkeypatch):
    rec = OrderRecorder()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []

    def gh_get_403(path):
        if path.endswith("/branches/main/protection"):
            raise _http_error(path, 403)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_403)
    result = policy._verify_rollback(
        "kimeisele/federation-hq", "main", {"classic": None, "rulesets": []})
    assert result["ok"] is False
    assert any("403" in p for p in result["problems"])


def test_verify_protection_classic_path_still_works(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/agent-city/branches/main/protection"] = {
        "required_status_checks": {"strict": True, "checks": [
            {"context": "federation-hq/review", "app_id": 4528340}]},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }
    rec.gets["/repos/kimeisele/agent-city/rulesets"] = []
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    verify = policy._verify_protection("kimeisele/agent-city", "main", "4528340")
    assert verify["ok"] is True
    assert verify["required_check_app_bound"] is True


def test_verify_protection_ruleset_only_fetches_full_endpoint_and_binding(monkeypatch):
    """Ruleset-only verification fetches the individual Gate ruleset endpoint
    (the list summary has no rules) and verifies integration_id == app id."""
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = _gate_full_rs(4528340)
    calls: list[str] = []

    def gh_get_tracked(path):
        calls.append(path)
        if path.endswith("/branches/main/protection"):
            raise _not_found_error(path)
        return rec.gh_get(path)

    monkeypatch.setattr(policy, "gh_get", gh_get_tracked)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is True
    assert "/repos/kimeisele/federation-hq/rulesets/900" in calls
    # Wrong integration id on the same full representation fails closed.
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = _gate_full_rs(999)
    verify_bad = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify_bad["ok"] is False
