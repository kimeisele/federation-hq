# Federation HQ Coordination Protocol v0.1

The **HQ Operator and GitHub-native Coordination Protocol** turns the
previously manual chat/copy-paste coordination of the three-role repair
workflow into structured, attributable GitHub-native coordination.

Design principle: prompt-as-code and workflow-as-code using plain Markdown,
GitHub Issues, structured comments, Git commits, exact SHAs, and existing
artifact validation. No runtime, no scheduler, no webhooks, no message broker.

## Actors and channels

| Channel | Role |
|---------|------|
| GitHub Issue (one per run) | **Operational control channel** — original request, structured comments, audit context |
| Federation HQ run-record branch + draft record PR | **Recording channel** — accepted artifact bytes are committed here, byte-for-byte, before the next role starts |
| Committed run artifacts (`runs/<run-id>/`) | **Canonical record** — accepted state, bound to exact Federation HQ commit SHAs and hashes |

Roles:

- `operator` — the HQ Operator (coordination; issues assignments, accepts or
  rejects submissions as a whole, advances state, closes runs).
- `scout` — Unwired Functionality Scout (execution and judgment).
- `repair` — Targeted Repair Builder (execution and judgment).
- `review` — Independent Repair Reviewer (execution and judgment).

## Message envelope

All coordination communication uses one shared envelope,
`federation_hq_coordination_message`, structurally validated by
`contracts/coordination-message.schema.json` and checked against the protocol
contract by `scripts/validate_artifacts.py`. The envelope binds:

- protocol version (exactly `0.1.0`),
- message ID,
- run ID,
- sender role and recipient role (only `operator`, `scout`, `repair`,
  `review`),
- message type,
- `in_reply_to` and/or `supersedes` (append-only correction semantics),
- target repository,
- relevant baseline SHA,
- state before and state after,
- referenced artifact kind and path,
- exact Federation HQ commit SHA (or `null` before canonical recording),
- artifact SHA-256,
- prompt used (for submissions),
- message body,
- timestamp.

Supported message types (limited, not a universal messaging language):

- `run_opened`
- `assignment`
- `artifact_submission`
- `artifact_acceptance`
- `rework_request`
- `blocked`
- `run_closed`

The schema and checker provide structural contract validation only. Semantic
validation of live GitHub comments is out of scope for v0.1.

## Essential semantics

1. **One run maps to exactly one coordination Issue.**
2. **The Issue body starts with the original maintenance request and run
   identity**, and links the run record under `runs/<run-id>/`.
3. **Only the Operator emits control messages**: `run_opened`, `assignment`,
   `artifact_acceptance`, `rework_request`, and `run_closed`. Only the worker
   roles emit `artifact_submission` and `blocked`; a worker message is always
   addressed to `operator`, and an assignment is always addressed to one
   worker role.
4. **Every assignment references exact accepted input artifacts** (canonical
   path, Federation HQ commit SHA on the run-record branch, SHA-256).
5. **Every submission references delivered bytes, not a future HQ commit**: a
   worker's `artifact_submission` carries the Operator-accessible delivery
   path, the exact SHA-256 of the submitted bytes, `prompt_used`, and
   `hq_commit_sha: null`. Workers never claim their submission is already
   canonical.
6. **The Operator records accepted bytes without rewriting them**: it copies
   the exact submitted bytes into the run's dedicated run-record branch under
   `runs/<run-id>/`, commits them, and references that exact branch commit in
   the `artifact_acceptance`. Acceptance requires the submitted hash and the
   recorded hash to match.
7. **Acceptance and the next assignment are separate messages.** The
   `artifact_acceptance` records the accepted state; the next role is
   dispatched by its own `assignment`.
8. **Rejected submissions remain visible** in the Issue history and are
   superseded by a new submission; a `rework_request` replies to the rejected
   submission and does not by itself advance state.
9. **Only the Operator's control messages advance canonical pipeline state.**
   Worker `artifact_submission` and `blocked` messages keep
   `state_before == state_after`.
10. **A state change is valid only when permitted by
    `docs/REPAIR_PIPELINE.md`** and the transition tables below.
11. **A new Repair head invalidates an earlier Review approval**; re-review is
    required before any new approval.
12. **An Issue comment alone never proves code correctness.** Committed
    artifacts are canonical; Issue comments are transport and audit context.

The v0.1 posture is **tamper-evident and attributable** via GitHub actor and
timestamp, stable Issue/comment URLs, exact target-repository SHAs, Federation
HQ commit SHAs, artifact SHA-256, and append-only correction semantics. No
signature or key infrastructure is claimed.

## Run-record branch lifecycle

For each real run:

- one GitHub coordination Issue,
- one dedicated Federation HQ run-record branch,
- one draft Federation HQ run-record PR,
- one canonical directory under `runs/<run-id>/`.

The Operator may:

- create and update the run-record branch,
- copy submitted artifacts **byte-for-byte** into `runs/<run-id>/`,
- commit those exact bytes,
- update the draft run-record PR,
- reference exact branch commit SHAs in acceptance messages.

The Operator may not:

- edit the semantic content of a worker submission,
- merge the run-record PR,
- modify prompts, contracts, application code, or unrelated files as part of a
  run,
- push to protected `main`.

A role proceeds from an accepted artifact committed on the run-record branch;
a merge into `main` is **not** required between Scout, Repair, and Review. At a
terminal state, the completed run-record PR is handed to an independent
Integrator for review and normal merge.

## Dispatch in v0.1

"Dispatch" means **posting a structured role assignment into the run Issue**.
An external human or agent context reads that assignment and executes it.
Federation HQ does not invoke, wake, or schedule models or agents; human
message routing is reduced but not yet fully eliminated, because no external
wake-up mechanism exists.

## Message/state semantics

Only the Operator's control messages advance canonical pipeline state. The
transition tables:

### Run opening

```text
run_opened:
requested → requested
```

### Assignment

```text
Scout assignment:        requested → scouting
Repair assignment:       candidate_selected → repair_in_progress
Review assignment:       repair_submitted → independent_review
Repair re-assignment
after accepted
changes_requested:       changes_requested → repair_in_progress
```

### Artifact submission (worker report; state unchanged)

```text
artifact_submission:
state_before == state_after

Scout submission:   scouting → scouting
Repair submission:  repair_in_progress → repair_in_progress
Review submission:  independent_review → independent_review
```

### Artifact acceptance (Operator control message)

```text
Scout artifact accepted:    scouting → candidate_selected
Repair artifact accepted:   repair_in_progress → repair_submitted
Review artifact accepted:   independent_review → approved
                            independent_review → changes_requested
                            independent_review → blocked
                            independent_review → invalid_candidate
```

### Rework request (no state advance)

```text
rework_request:
state_before == state_after
```

After an accepted Review verdict of `changes_requested`, a new Repair
`assignment` performs the transition `changes_requested → repair_in_progress`.

### Run closure (terminal state already accepted)

```text
run_closed:
approved → approved
blocked → blocked
invalid_candidate → invalid_candidate
```

`run_closed` closes the Issue; it does not dispatch another role.

## Copyable comment examples

Post these as Issue comments on the run's coordination Issue. Replace the
placeholders in angle brackets. The examples form one coherent sequence for
the same run; `in_reply_to` IDs chain correctly and the acceptance examples
preserve the submitted SHA-256.

### 1. Assignment (Operator → Scout)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0001
run_id: run-20260805-widget-service-sorting

sender_role: operator
recipient_role: scout
message_type: assignment

in_reply_to: null
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: requested
state_after: scouting

artifact_ref:
  kind: run_manifest
  path: runs/run-20260805-widget-service-sorting/run-manifest.yaml
  hq_commit_sha: "abcdef0123456789abcdef0123456789abcdef01"
  sha256: "4fefd629922d16c627396bc185ac375a805a015081abe4a225a3849ceaacd9a4"

body: |
  Investigate the bounded maintenance request in the run manifest and return
  exactly one repair-candidate artifact.

created_at: "2026-08-05T19:50:00+02:00"
```

### 2. Artifact submission (Scout → Operator, delivered bytes, not yet canonical)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0002
run_id: run-20260805-widget-service-sorting

sender_role: scout
recipient_role: operator
message_type: artifact_submission

in_reply_to: msg-20260805-0001
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: scouting
state_after: scouting

prompt_used: scout@0.1.0
artifact_ref:
  kind: repair_candidate
  path: run-output/run-20260805-widget-service-sorting/repair-candidate.yaml
  hq_commit_sha: null
  sha256: "a40132d2663520e3ce85347f6f9fe0ba2e49f22b13b85337fb96bf8fcaaf7128"

body: |
  Submitting the single selected repair candidate for structural validation.
  Bytes delivered at the run-output path above; not yet recorded canonically.

created_at: "2026-08-05T20:05:00+02:00"
```

### 3. Artifact acceptance (Operator → Scout, separate from the next assignment)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0003
run_id: run-20260805-widget-service-sorting

sender_role: operator
recipient_role: scout
message_type: artifact_acceptance

in_reply_to: msg-20260805-0002
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: scouting
state_after: candidate_selected

artifact_ref:
  kind: repair_candidate
  path: runs/run-20260805-widget-service-sorting/repair-candidate.yaml
  hq_commit_sha: "0123456789abcdef0123456789abcdef01234567"
  sha256: "a40132d2663520e3ce85347f6f9fe0ba2e49f22b13b85337fb96bf8fcaaf7128"

body: |
  Candidate accepted as a whole: structural validation passed, references
  checked, exact submitted bytes recorded on the run-record branch at the
  commit above with a matching SHA-256. Repair will be dispatched separately.

created_at: "2026-08-05T20:10:00+02:00"
```

The next role is dispatched by a **separate** assignment message, for example
`operator → repair`, `state: candidate_selected → repair_in_progress`,
`in_reply_to: msg-20260805-0003` — never by the acceptance itself.

### 4. Rework request (Operator → Repair, replies to the rejected submission)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0006
run_id: run-20260805-widget-service-sorting

sender_role: operator
recipient_role: repair
message_type: rework_request

in_reply_to: msg-20260805-0005
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: repair_in_progress
state_after: repair_in_progress

artifact_ref:
  kind: repair_result
  path: run-output/run-20260805-widget-service-sorting/repair-result.yaml
  hq_commit_sha: null
  sha256: "264d3805acf9626c6de3317180ba9570974592a9aef425ff86e0da18e1833c64"

body: |
  Submission rejected as a whole: newly_introduced_failures references a
  non-existent output file. Re-submit a corrected repair-result.

created_at: "2026-08-05T21:00:00+02:00"
```

### 5. Blocked (worker → Operator, report only, state unchanged)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0100
run_id: run-20260805-widget-service-sorting

sender_role: repair
recipient_role: operator
message_type: blocked

in_reply_to: msg-20260805-0004
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: repair_in_progress
state_after: repair_in_progress

artifact_ref: null

body: |
  Cannot proceed: the candidate references evidence_locations that do not
  exist in the target checkout at the baseline SHA.

created_at: "2026-08-05T21:15:00+02:00"
```

### 6. Run closure (Operator → last active worker, terminal state unchanged)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: msg-20260805-0012
run_id: run-20260805-widget-service-sorting

sender_role: operator
recipient_role: review
message_type: run_closed

in_reply_to: msg-20260805-0011
supersedes: null

target_repository: acme/widget-service
baseline_sha: "9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e"

state_before: approved
state_after: approved

artifact_ref:
  kind: review_result
  path: runs/run-20260805-widget-service-sorting/review-result.yaml
  hq_commit_sha: "0123456789abcdef0123456789abcdef01234567"
  sha256: "1560a69d11e377cc60c4e235488d67b505d3177b67594b1903e5e2f2be5ff886"

body: |
  Run closed as approved. The coordination Issue is closed with this message;
  the committed run bundle remains canonical and the run-record PR is handed
  to the independent Integrator for review and merge.

created_at: "2026-08-05T22:00:00+02:00"
```

## Message usage rules

- Post messages as GitHub Issue comments on the run's Issue; never edit a
  previous comment to alter history.
- Corrections use a new message with `in_reply_to` (reply) or `supersedes`
  (replaces the referenced message's claim).
- A worker `artifact_submission` always carries `hq_commit_sha: null` (the
  submitted bytes are not yet canonical), the delivery path, the exact
  SHA-256, and `prompt_used`.
- An `artifact_acceptance` always references the canonical
  `runs/<run-id>/...` path, the exact run-record branch commit, and the same
  SHA-256 the worker submitted.
- Acceptance and the next assignment are separate messages; only an
  `assignment` dispatches a role.
- Only the Operator's control messages advance state; worker reports keep
  `state_before == state_after`.
- The Operator maintains at most **one active role assignment per run**.
- The Issue is closed only when the run reaches a terminal state (`approved`,
  `blocked`, or `invalid_candidate`), via `run_closed`.
