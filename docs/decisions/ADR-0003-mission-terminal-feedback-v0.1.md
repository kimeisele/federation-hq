# ADR-0003 — Mission terminal feedback v0.1 (canonical RunAssessment persistence)

Status: accepted (Issue #31, draft PR).

## Context

ADR-0002 step 3 — the first real MissionContract-native run
(federation-hq#29, `mission-20260810-agent-city-moltbook-outbound-fallback-contract`)
— completed successfully and empirically demonstrated a terminal-feedback
convention: `runs/<run-id>/run-assessment.yaml` plus an atomic Mission Ledger
terminal update. Before the pilot, the RunAssessment SCHEMA existed but its
canonical persistence location/mechanism was not specified. This ADR
formalizes the demonstrated convention so a future Director can consume
terminal feedback without guessing storage paths or reading Issue prose.

## Decision

1. **Executed runs** (`run_id != null`): the canonical machine-readable
   terminal feedback artifact is exactly
   `runs/<run-id>/run-assessment.yaml`, validated against
   `contracts/mission/run-assessment.schema.json`. No second assessment
   store.
2. **Pre-run rejections** (`terminal_outcome: mission_rejected`, `run_id:
   null`): the canonical location is `missions/<mission-id>/run-assessment.yaml`
   — adjacent to the exact MissionCandidate + MissionContract; no fabricated
   run id, no empty run directory, no Scout/Repair/Review/Gate artifacts.
3. **One assessment per terminal mission attempt** (v0.1). New attempts use
   the existing Ledger supersession/override semantics and a new mission
   identity where required.
4. **Assessment ↔ execution identity**: `runs/<run-id>/run-assessment.yaml`
   must agree with the directory run id and the run manifest (run_id,
   target_repository, baseline_sha where applicable, and `mission_id` vs
   `mission_input.mission_id` for MissionContract-native runs). Mismatches
   fail validation.
5. **Assessment ↔ Ledger**: the terminal disposition, mission_id, and (for
   executed runs) the related run id must agree with the Mission Ledger item
   of the mission's signal.
6. **Atomic terminal feedback**: the final HQ run-record PR carries together
   the canonical run artifacts, the run assessment, and the Ledger terminal
   update; Git is the transaction boundary. No distributed transactions or
   state service.
7. **`run_record_merge_sha`**: need not be known before the record PR merges;
   Git/PR history is authoritative; no second corrective PR; schema stays
   compatible.
8. **Legacy runs** (#17/#19/#21 and any `maintenance_request` run) are
   grandfathered — the canonical RunAssessment requirement begins with
   MissionContract-native execution under `operator@0.3.0`.

## Consequences

- A future Director can deterministically resolve mission terminal state:
  MissionContract → canonical RunAssessment → matching Ledger disposition.
- The real Pilot 01 assessment is the primary acceptance fixture and remains
  unchanged; a NON-CANONICAL rejection fixture under
  `tests/fixtures/mission_terminal_feedback/rejected_pre_run/` (used by
  pytest with temporary/in-memory ledgers) proves the no-run path without
  polluting live canonical state.
- Validator (`scripts/validate_artifacts.py`) enforces the identity and
  ledger agreement chains; arbitrary assessment locations fail.
- No prompt release change: `operator@0.3.0` already produced the correct
  result and is not mutated.

## Non-goals

No hq-director, no Director prompt/wrapper, no discovery/ranking, no
scheduler, no feedback agent, no event bus, no database, no queue, no
semantic analysis service, no multi-attempt history machinery, no rewrite of
historical runs.
