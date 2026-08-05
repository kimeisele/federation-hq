# Repair Pipeline (v0.1.0)

This document defines the initial repair workflow implemented by Federation
HQ: three execution and judgment roles coordinated by the **HQ Operator**. In
v0.1.0 the workflow is **manually coordinated** — no dispatcher, no automatic
model invocation, no autonomous PR creation, no autonomous merging. The
Operator posts structured assignments and decisions into the run's
coordination Issue (see `docs/COORDINATION_PROTOCOL.md`); an external human or
agent reads and executes them.

## Roles

| Role | Prompt | Responsibility |
|------|--------|----------------|
| HQ Operator | `prompts/operator/v0.1.0.md` (`operator@0.1.0`) | Coordinate one run via its GitHub Issue; exactly one active assignment at a time; no merge authority, no semantic repair judgment |
| Unwired Functionality Scout | `prompts/scout/v0.1.0.md` (`scout@0.1.0`) | Investigate a bounded functionality gap and select exactly one candidate |
| Targeted Repair Builder | `prompts/repair/v0.1.0.md` (`repair@0.1.0`) | Repair exactly the selected candidate in the target repository |
| Independent Repair Reviewer | `prompts/review/v0.1.0.md` (`review@0.1.0`) | Independently check the exact remote head and record a verdict |

The four prompts are released versions pinned by each run manifest. A run
never mixes prompt versions: all roles in a run use the versions pinned at run
creation. The Operator never performs Scout, Repair, or Review work in the
same run and never maintains more than one active assignment per run.

## State machine

```text
requested
→ scouting
→ candidate_selected
→ repair_in_progress
→ repair_submitted
→ independent_review
→ approved | changes_requested | blocked | invalid_candidate
```

| State | Meaning | Advanced by |
|-------|---------|-------------|
| `requested` | A bounded maintenance request exists; the run manifest pins it as `maintenance_request` | Operator opens the run (`run_opened`) |
| `scouting` | Scout investigating the target repository | Operator `assignment` to Scout |
| `candidate_selected` | Scout recorded exactly one candidate | Operator `artifact_acceptance` of the Scout submission |
| `repair_in_progress` | Repair Builder working the selected candidate | Operator `assignment` to Repair |
| `repair_submitted` | Repair result and PR/branch evidence recorded | Operator `artifact_acceptance` of the Repair submission |
| `independent_review` | Reviewer checking the exact remote head | Operator `assignment` to Reviewer |
| `approved` | Verdict: reviewer approved the exact reviewed head | Operator `artifact_acceptance` of the Review submission |
| `changes_requested` | Verdict: reviewer requires changes | Operator `artifact_acceptance` of the Review submission; Repair may be re-assigned |
| `blocked` | Verdict: work cannot proceed (missing evidence, unreachable state, external dependency) | Operator `artifact_acceptance` of the Review submission, or Operator `run_closed` terminalizing an unrecoverable worker blocker |
| `invalid_candidate` | Verdict: the candidate was not a real defect or was out of scope | Operator `artifact_acceptance` of the Review submission |

Transitions are driven by the Operator's coordination messages and recorded by
writing the corresponding artifact into the run's directory under `runs/` and
updating the run manifest's `pipeline_state`. **Only Operator control
messages advance state**: worker `artifact_submission` and `blocked` messages
keep `state_before == state_after`; `run_opened` keeps `requested →
requested`; `rework_request` keeps state unchanged; `run_closed` closes the
Issue at an already-terminal state. Assignment and acceptance transition
tables are defined in `docs/COORDINATION_PROTOCOL.md`. Transition history is
retained in the run manifest notes; a simple monotonic append is sufficient in
v0.1.0.

**Blocker terminalization.** A worker reports an inability to continue with a
`blocked` message that leaves state unchanged; the worker does not decide the
terminal state. After verifying the blocker is unrecoverable, the Operator
responds with `run_closed` terminalizing any non-terminal state to `blocked`
(`requested | scouting | candidate_selected | repair_in_progress |
repair_submitted | independent_review | changes_requested → blocked`). That
closure requires `in_reply_to` (the blocked report), one concrete worker
recipient, and a body identifying the blocker; `artifact_ref` may be null.
The Review-result path remains valid: an `artifact_acceptance` may record
`independent_review → blocked` with a canonical `review_result`, and
`run_closed` then closes `blocked → blocked` with that artifact.

## Invariants

1. **Scout selects exactly one candidate.** The scout prompt produces a single
   `repair_candidate` artifact. Multiple candidates may be investigated; exactly
   one is selected and recorded.
2. **Repair Builder may repair only that candidate.** The repair prompt receives
   the frozen candidate and may not expand scope to adjacent defects, refactors,
   or unrelated cleanup.
3. **Reviewer independently checks the exact remote head.** The reviewer must
   fetch the target repository and check the exact `reviewer_head_sha` recorded
   in the run — not a local diff the builder describes, not a summary.
4. **New commits invalidate previous approval.** Approval is scoped to the exact
   reviewed head SHA. Any new commit on the reviewed branch or PR invalidates
   the previous `approved` verdict and requires a fresh review.
5. **No role merges its own work.** A role that authored content never merges,
   pushes to the protected target branch, or approves its own output.
6. **Existing red CI is compared baseline-versus-head.** Red CI that existed at
   the baseline SHA is recorded as `baseline_failures`; failures not present at
   baseline are recorded as `newly_introduced_failures`. The repair result must
   contain both lists and the reviewer must verify both against the exact head.
7. **No admin bypass is permitted.** Neither Federation HQ operators nor any
   role may use administrative merge bypass, force-push to protected branches,
   or direct pushes where a pull request is required — in either Federation HQ
   or target repositories.
8. **Every run binds its original maintenance request.** The run manifest
   records the bounded request (`maintenance_request`: text, source,
   created_at, optional source reference) as the durable original scope. The
   Scout candidate may clarify it but may not replace or silently broaden it.

## Artifacts per run

A run lives in `runs/run-<date>-<slug>/` and accumulates:

1. `run-manifest.yaml` (or `.json`) — pins target repository, baseline SHA,
   the original `maintenance_request`, the `coordination` Issue reference, and
   the exact prompt versions (operator, scout, repair, review) with content
   hashes.
2. `repair-candidate.<ext>` — the single selected candidate.
3. `repair-result.<ext>` — head SHA, PR reference, commands and outcomes,
   baseline/newly-introduced failure lists.
4. `review-result.<ext>` — reviewer head SHA and verdict.

All artifacts are validated against the schemas in `contracts/` by
`scripts/validate_artifacts.py`. Coordination messages are validated against
`contracts/coordination-message.schema.json` and posted as comments on the
run's coordination Issue (`docs/COORDINATION_PROTOCOL.md`); they are audit
context, not canonical proof. See `runs/README.md` for naming and layout.

## Run-record branch lifecycle

For each real run the Operator maintains, alongside the coordination Issue: one
dedicated Federation HQ run-record branch, one draft run-record PR, and the
canonical directory `runs/<run-id>/`. Accepted submissions are copied
**byte-for-byte** into `runs/<run-id>/` and committed on that branch; roles
proceed from accepted artifacts committed on the branch, without requiring a
merge into `main` between Scout, Repair, and Review. The Operator never edits
the semantic content of a submission, never merges the run-record PR, and
never pushes to protected `main`. At a terminal state the completed
run-record PR is handed to an independent Integrator for review and normal
merge.

## Deferred

- Automatic state advancement (event-driven transitions).
- Automatic dispatch of role agents.
- Autonomous PR creation or merging in target repositories.
- Scheduled scanning or automatic candidate selection.

These are documented as deferred possibilities, not promised commitments.
