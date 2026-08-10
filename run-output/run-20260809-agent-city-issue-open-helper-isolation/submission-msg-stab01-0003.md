kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-stab01-0003
run_id: run-20260809-agent-city-issue-open-helper-isolation

sender_role: scout
recipient_role: operator
message_type: artifact_submission

in_reply_to: msg-stab01-0002
supersedes: null

target_repository: kimeisele/agent-city
baseline_sha: "89bf10eea690617f38627e63554a89457a90ed71"

state_before: scouting
state_after: scouting

prompt_used: scout@0.1.0
artifact_ref:
  kind: repair_candidate
  path: run-output/run-20260809-agent-city-issue-open-helper-isolation/repair-candidate.yaml
  hq_commit_sha: null
  sha256: "d8bfd57d8c1741989ea12083e11c152feda13e5ce10bf5b7a68d16c23aaa299e"

body: |
  Scout findings (baseline 89bf10ee, clean checkout at /tmp/agent-city-s01):

  DIAGNOSIS: TEST ISOLATION / SETUP defect — not a production-code defect.
  tests/test_issue_binding.py::test_issue_open_and_bound_helpers (lines
  101-109) builds a real CityIssueManager() and asserts is_issue_open(99)
  is False from its local fixture only, but CityIssueManager.is_issue_open()
  (city/issues.py:529-538) answers untracked numbers with a LIVE GitHub
  query via _gh_run(["issue","view",99,...]) -> returns True when the issue
  is open on GitHub. kimeisele/agent-city issue #99 is OPEN live ("[Campaign]
  Internet adaptation", verified `gh issue view 99`), so the assertion fails
  deterministically in any environment with a working authenticated gh CLI.

  REPRODUCTION (exact pinned baseline): `python -m pytest
  tests/test_issue_binding.py::test_issue_open_and_bound_helpers -q` ->
  1 failed in 18.54s, AssertionError at tests/test_issue_binding.py:107;
  whole file -> 1 failed, 6 passed in 17.38s. Environment dependence proven:
  the identical test PASSES (1 passed in 21.05s) when a stub `gh` exiting 1
  is prepended to PATH — outcome is determined by ambient external state, not
  the fixture (also note __post_init__ city/issues.py:91-108 performs a live
  `gh repo view` on every construction).

  PRODUCTION FALLBACK IS INTENTIONAL AND REQUIRED (evidence): added in
  4987d10 "feat: federation recruitment via DHARMA bounties" (2026-03-23) as
  the "Issue #743 fix" for issues outside the local 100-issue tracking
  window; recruitment targets reference external issues #360/#131/#348
  (campaigns/default.json:40-61) and CampaignRegistry._compute_gaps /
  evaluate / _find_reusable_issue_number (city/campaigns.py:169-172,
  236-242, 319-330) depend on the live answer for out-of-window numbers;
  issue #743 is itself an OPEN campaign issue. The test predates the
  fallback (added with the local-only helper in d02c09e, 2026-03-08) and was
  never updated, so it asserts the stale pre-fallback contract and is the
  only test in the suite exercising the fallback path through a real manager;
  the repo convention elsewhere is to mock the issue manager
  (tests/test_campaigns.py:74-75, 147-148, 195-196) or exercise local cells
  only (tests/test_layer2.py, test_layer3.py). The repo's own baseline
  evidence documents this node (docs/MAINTENANCE_CAMPAIGN_RECRUITMENT_
  BASELINE.json: known_red_baseline, deterministic_second_run true;
  docs/MAINTENANCE_BASELINE_22_TRIAGE.md Issue Binding row).

  SELECTED CANDIDATE (exactly one): run-20260809-agent-city-issue-open-
  helper-isolation-c1 — the focused test must be isolated from live GitHub
  state so it deterministically verifies the read-only helpers against its
  local fixture; production is_issue_open() fallback semantics unchanged.
  Per assignment, NOT prescribed: removing the fallback, changing the
  assertion, global GitHub mocking, or changing issue 99.

  Artifact written to the operator-provided run-output directory (schema-
  valid per contracts/repair-candidate.schema.json, validated with
  scripts/validate_artifacts.py and jsonschema); SHA-256 above. Not yet
  canonical. Scout stops here; no further protocol actions taken.

created_at: "2026-08-09T16:14:30Z"
