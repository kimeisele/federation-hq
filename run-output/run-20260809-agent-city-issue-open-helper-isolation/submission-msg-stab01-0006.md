kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-stab01-0006
run_id: run-20260809-agent-city-issue-open-helper-isolation

sender_role: repair
recipient_role: operator
message_type: artifact_submission

in_reply_to: msg-stab01-0005
supersedes: null

target_repository: kimeisele/agent-city
baseline_sha: "89bf10eea690617f38627e63554a89457a90ed71"

state_before: repair_in_progress
state_after: repair_in_progress

prompt_used: repair@0.1.0
artifact_ref:
  kind: repair_result
  path: run-output/run-20260809-agent-city-issue-open-helper-isolation/repair-result.yaml
  hq_commit_sha: null
  sha256: "448c96e05ea24e176440c71b19ed69047832884c14c8b41db509e0d8e4ebe405"

body: |
  Repair submission (isolated hq-repair worker; repair@0.1.0, sha256
  0606d8ed... verified against run-manifest pin). Worked from pinned baseline
  89bf10eea690617f38627e63554a89457a90ed71 and accepted candidate
  run-20260809-agent-city-issue-open-helper-isolation-c1 (SHA-256
  d8bfd57d...; canonical runs/.../repair-candidate.yaml).

  DEFECT (per accepted candidate): TEST ISOLATION / SETUP —
  tests/test_issue_binding.py::test_issue_open_and_bound_helpers is a stale
  pre-fallback local-only contract that constructs a real CityIssueManager
  and asserts is_issue_open(99) is False, but the manager's INTENTIONAL
  live-GitHub fallback (city/issues.py:529-538, Issue #743 fix) answers
  untracked numbers with live state; kimeisele/agent-city issue #99 is OPEN,
  so the test failed deterministically with an authenticated gh CLI.
  Production fallback kept intact.

  REPRODUCTION (baseline): focused test -> 1 failed in 18.93s
  (AssertionError at tests/test_issue_binding.py:107, assert True is False);
  whole file -> 1 failed, 6 passed in 20.65s.

  REPAIR (test-only, bounded surface tests/test_issue_binding.py): patched
  the module-level gh wrapper city.issues._gh_run (return_value=None) on this
  single test, following the repository's own isolation convention
  (tests/test_discussions.py patches city.discussions_bridge._gh_graphql;
  tests/test_campaign_recruitment.py patches city.bounty.create_bounty). The
  test now deterministically verifies the read-only helpers against its local
  fixture with no network/live-GitHub dependence. No production code changed;
  no assertion/issue-number semantics changed; no broad mocking framework.

  HEAD: commit 599735f69a33af6485c1d1307f098240b0b27ec8 on branch
  repair/run-20260809-issue-open-test-isolation; PR #2715
  (https://github.com/kimeisele/agent-city/pull/2715) opened against main,
  MERGEABLE, no admin/force/branch-protection changes.

  VERIFICATION (head): focused -> 1 passed in 11.29s; whole file ->
  7 passed in 17.59s; tests/test_campaigns.py + tests/test_campaign_recruitment.py
  -> 19 passed in 9.27s. Broader suite (python3 -m pytest tests -q
  --continue-on-collection-errors): baseline 27 failed, 1927 passed, 1
  skipped, 6 errors (632.11s) vs head 28 failed, 1926 passed, 1 skipped,
  6 errors (669.50s). The delta: the target test now passes; the two apparent
  new head failures (test_heartbeat_campaign_bootstrap.py::
  test_heartbeat_cli_smoke_with_campaign_manifest — subprocess SIGKILLed;
  test_scalable_metabolism.py::test_metabolize_all_scales_to_100_agents —
  >30s pytest-timeout) were concurrent-run resource-contention artifacts and
  BOTH pass in isolation at head (2 passed in 39.80s) AND at baseline (2
  passed in 35.94s). newly_introduced_failures: [] (empty). The 6
  federation_v1 collection errors (untracked fixture
  tests/fixtures/federation_v1/keys/test_keys.json) apply identically at
  both SHAs — recorded as an environment limitation, not a repair defect.

  Artifact written to the operator-provided run-output directory
  (run-output/run-20260809-agent-city-issue-open-helper-isolation/
  repair-result.yaml), schema-valid per contracts/repair-result.schema.json
  (validated with scripts/validate_artifacts.py and jsonschema); SHA-256
  above. Not yet canonical. Repair stops here; no further protocol actions
  taken.

created_at: "2026-08-09T16:39:30Z"
