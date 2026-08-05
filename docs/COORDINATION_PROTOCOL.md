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
`contracts/coordination-message.schema.json`. The envelope binds:

- protocol version (`0.1.0`),
- message ID,
- run ID,
- sender role and recipient role,
- message type,
- `in_reply_to` and/or `supersedes` (append-only correction semantics),
- target repository,
- relevant baseline or reviewed SHA,
- state before and state after,
- referenced artifact kind and path,
- exact Federation HQ commit SHA,
- artifact SHA-256,
- message body,
- timestamp.

Supported roles: `operator | scout | repair | review`.

Supported message types (limited, not a universal messaging language):

- `run_opened`
- `assignment`
- `artifact_submission`
- `artifact_acceptance`
- `rework_request`
- `blocked`
- `run_closed`

The schema provides structural validation only. Semantic validation of live
GitHub comments is out of scope for v0.1.

## Essential semantics

1. **One run maps to exactly one coordination Issue.**
2. **The Issue body starts with the original maintenance request and run
   identity**, and links the run record under `runs/<run-id>/`.
3. **Only the Operator emits control messages**: `run_opened`, `assignment`,
   `artifact_acceptance`, `rework_request`, and `run_closed`.
4. **Worker roles emit** `artifact_submission` or `blocked`.
5. **Every assignment references exact accepted input artifacts** (path,
   Federation HQ commit SHA, SHA-256).
6. **Every submission identifies the prompt version used and the exact output
   artifact** it claims to produce.
7. **The Operator never repairs malformed submissions by rewriting them**;
   it rejects as a whole and requests bounded rework.
8. **Rejected submissions remain visible** in the Issue history and are
   superseded by a new submission.
9. **`artifact_acceptance` references the accepted HQ path, HQ commit SHA, and
   SHA-256**; a submission becomes accepted only after structural validation,
   reference verification, recording under an exact HQ commit, and the
   acceptance message.
10. **A state change is valid only when permitted by
    `docs/REPAIR_PIPELINE.md`.**
11. **A new Repair head invalidates an earlier Review approval**; re-review is
    required before any new approval.
12. **An Issue comment alone never proves code correctness.** Committed
    artifacts are canonical; Issue comments are transport and audit context.

The v0.1 posture is **tamper-evident and attributable** via GitHub actor and
timestamp, stable Issue/comment URLs, exact target-repository SHAs, Federation
HQ commit SHA, artifact SHA-256, and append-only correction semantics. No
signature or key infrastructure is claimed.

## Dispatch in v0.1

"Dispatch" means **posting a structured role assignment into the run Issue**.
An external human or agent context reads that assignment and executes it.
Federation HQ does not invoke, wake, or schedule models or agents; human
message routing is reduced but not yet fully eliminated, because no external
wake-up mechanism exists.

## Copyable comment examples

Post these as Issue comments on the run's coordination Issue. Replace the
placeholders in angle brackets. Each example is one complete
`federation_hq_coordination_message`; omit fields with `null` for
`in_reply_to`/`supersedes`.

### Assignment (Operator → role)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0001>
run_id: <run-20260805-widget-service-sorting>

sender_role: operator
recipient_role: scout
message_type: assignment

in_reply_to: null
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: requested
state_after: scouting

artifact_ref:
  kind: run_manifest
  path: runs/<run-id>/run-manifest.yaml
  hq_commit_sha: <abcdef0123456789abcdef0123456789abcdef01>
  sha256: <64 lowercase hex chars>

body: |
  Investigate the bounded maintenance request in the run manifest and return
  exactly one repair-candidate artifact.

created_at: <2026-08-05T19:50:00+02:00>
```

### Artifact submission (role → Operator)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0002>
run_id: <run-20260805-widget-service-sorting>

sender_role: scout
recipient_role: operator
message_type: artifact_submission

in_reply_to: <msg-20260805-0001>
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: scouting
state_after: candidate_selected

prompt_used: scout@0.1.0
artifact_ref:
  kind: repair_candidate
  path: runs/<run-id>/repair-candidate.yaml
  hq_commit_sha: <0123456789abcdef0123456789abcdef01234567>
  sha256: <64 lowercase hex chars>

body: |
  Submitting the single selected repair candidate for structural validation.

created_at: <2026-08-05T20:05:00+02:00>
```

### Artifact acceptance (Operator)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0003>
run_id: <run-20260805-widget-service-sorting>

sender_role: operator
recipient_role: scout
message_type: artifact_acceptance

in_reply_to: <msg-20260805-0002>
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: candidate_selected
state_after: repair_in_progress

artifact_ref:
  kind: repair_candidate
  path: runs/<run-id>/repair-candidate.yaml
  hq_commit_sha: <0123456789abcdef0123456789abcdef01234567>
  sha256: <64 lowercase hex chars>

body: |
  Candidate accepted as a whole after structural validation and reference
  checks. Routing to repair.

created_at: <2026-08-05T20:10:00+02:00>
```

### Rework request (Operator → role)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0004>
run_id: <run-20260805-widget-service-sorting>

sender_role: operator
recipient_role: repair
message_type: rework_request

in_reply_to: <msg-20260805-0002>
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: repair_submitted
state_after: repair_in_progress

artifact_ref:
  kind: repair_result
  path: runs/<run-id>/repair-result.yaml
  hq_commit_sha: <0123456789abcdef0123456789abcdef01234567>
  sha256: <64 lowercase hex chars>

body: |
  Submission rejected as a whole: newly_introduced_failures references a
  non-existent output file. Re-submit a corrected repair-result.

created_at: <2026-08-05T21:00:00+02:00>
```

### Blocked (any role → Operator)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0005>
run_id: <run-20260805-widget-service-sorting>

sender_role: repair
recipient_role: operator
message_type: blocked

in_reply_to: <msg-20260805-0001>
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: repair_in_progress
state_after: blocked

artifact_ref: null

body: |
  Cannot proceed: the candidate references evidence_locations that do not
  exist in the target checkout at the baseline SHA.

created_at: <2026-08-05T21:15:00+02:00>
```

### Run closure (Operator)

```text
kind: federation_hq_coordination_message
protocol_version: 0.1.0
message_id: <msg-20260805-0006>
run_id: <run-20260805-widget-service-sorting>

sender_role: operator
recipient_role: all
message_type: run_closed

in_reply_to: <msg-20260805-0003>
supersedes: null

target_repository: <acme/widget-service>
baseline_sha: <9f2c1e8a4b7d3f6c5e2a9b8c7d6e5f4a3b2c1d0e>

state_before: independent_review
state_after: approved

artifact_ref:
  kind: review_result
  path: runs/<run-id>/review-result.yaml
  hq_commit_sha: <0123456789abcdef0123456789abcdef01234567>
  sha256: <64 lowercase hex chars>

body: |
  Run closed as approved. The coordination Issue is closed with this message;
  the committed run bundle remains canonical.

created_at: <2026-08-05T22:00:00+02:00>
```

## Message usage rules

- Post messages as GitHub Issue comments on the run's Issue; never edit a
  previous comment to alter history.
- Corrections use a new message with `in_reply_to` (reply) or `supersedes`
  (replaces the referenced message's claim).
- `artifact_ref` is `null` only for `blocked` messages and for
  `run_opened`/`assignment` messages that reference the run manifest itself.
- The Operator maintains at most **one active role assignment per run**.
- The Issue is closed only when the run reaches a terminal state (`approved`,
  `blocked`, or `invalid_candidate`).
