# Mission Foundation v0.1 — Director Boundary

Status: current (Mission Foundation #23, Operator consumption #25,
MissionContract Pilot 01 #29, terminal feedback #31 all integrated). This document
defines the boundary between mission formulation and mission execution, the
signal identity model, the prompt composition contract, and the intended
future relationship. It does NOT implement the Mission Director.

## Formulation vs execution

```
Signals
   ↓
MissionCandidate                     (what work, if any, is worth doing?)
   ↓
Policy + Ledger decision             (docs/HQ_MISSION_POLICY.md + ledger)
   ├─ no_mission_warranted
   ├─ duplicate / wont_fix / rejected
   └─ MissionContract
           ↓
       existing Operator             (how is an accepted bounded mission executed?)
           ↓
       existing run
           ↓
       RunAssessment
           ↓
       Mission Ledger update
```

- **Mission formulation** (future Director or human): answer "what bounded
  work, if any, is worth doing next?" using cheap, bounded, structured
  signals. Output: MissionCandidate → MissionContract.
- **Mission execution** (existing Operator): answer "given an accepted
  bounded mission, how is it executed correctly?" using the existing
  Scout → Repair → Review → Gate → Integrator path.
- The two responsibilities are NEVER merged. A future Director may CREATE a
  bounded Recon Mission through the existing Operator path; it does not
  perform deep source-code recon itself.

**Director ≠ Scout ≠ Operator ≠ Reviewer.**

## One execution path, not two

If deciding whether a candidate is worthwhile requires deep source
inspection, the correct action is:

```text
create a bounded Recon Mission
→ submit it through the existing Operator path
```

NOT:

```text
Director reads the whole repository and becomes an unbounded Scout
```

## Signal identity model (v0.1)

Honest, limited, no magic:

- First ingestion assigns an **immutable internal `signal_id`** (HQ-owned).
  It never changes after assignment.
- **source_kind** (test_node / ci / github_issue / run_assessment /
  ledger_disposition / human_request / workflow_failure / other),
  **repository**, and **source_native_ref** (e.g. `tests/foo.py::test_bar`,
  CI job URL, issue number) are indexed as the initial reference.
- **aliases** attach evidence-backed alternative source-native references
  (e.g. after a test rename) when continuity is established.
- **observations/evidence** are separate from identity: `last_observed_evidence`
  records what was seen, not what the signal is.
- v0.1 does NOT attempt semantic rename detection, embeddings, or
  similarity infrastructure. A renamed reference becomes a new
  source-native ref; continuity is attached as an alias only on evidence.

`tests/foo.py::test_bar` is NOT treated as a universally immutable key.

## Prompt composition contract (documented, not enforced at runtime)

Worker instructions conceptually resolve from:

```text
canonical role prompt
+ MissionContract
+ Run Manifest
+ active assignment
+ accepted upstream canonical artifacts where applicable
```

The Operator is a deterministic assembler/dispatcher of canonical intent and
state — NOT the semantic author of a fresh arbitrary worker prompt. Immutable
released prompt versions are NOT modified by this slice; runtime wiring of
MissionContract into the prompt composition is a later bounded slice.

## Declared vs mechanically enforced scope

MissionContract `bounded_scope` is **declared guidance** (scope_enforcement:
`declared`): it instructs Scout/Repair/Review but is not mechanically
enforced in v0.1. Prose is never documented as hard enforcement. A future
slice may add mechanically enforced allowlists if the repository gains a
path-based enforcement mechanism.

## Mission rejection

`status: mission_rejected` on a MissionContract is a terminal framing
disposition: the mission framing/scope itself is invalid, unsafe, duplicate,
unsupported, or evidence-inadequate (POL-10). It is distinct from Scout
finding no defect. v0.1 validates the representation; runtime enforcement is
a later slice.

## Canonical artifacts vs this layer

MissionCandidate / MissionContract / RunAssessment / Ledger are NEW
non-execution artifacts. The canonical execution records under `runs/<run-id>/`
and the existing coordination protocol are unchanged. Retrospective fixtures
in `examples/mission/retrospective/` project real past runs (#17 / #19 / #21)
into the new representations and are clearly NON-CANONICAL examples — the
historical run artifacts are never rewritten.

## Terminal feedback interface (v0.1)

A future mission formulation / Director resolves "what happened to mission
X?" deterministically:

```
MissionContract
→ canonical terminal RunAssessment
→ matching Mission Ledger disposition
```

Canonical locations (validated by `scripts/validate_artifacts.py`):

```
Executed run:        runs/<run-id>/run-assessment.yaml
Rejected before run: missions/<mission-id>/run-assessment.yaml
Mission Ledger:      mission/ledger.yaml
```

Rules (see ADR-0003):

- Executed assessments must agree with the run directory, the run manifest
  (run_id, target_repository, mission_id via `mission_input`, baseline_sha)
  and the Ledger (mission_id, disposition, related_run_ids).
- Pre-run rejections use `terminal_outcome: mission_rejected`, `run_id:
  null`, `ledger_disposition: rejected`, and require no run-manifest or
  execution artifacts; no fabricated run id, no empty run directory.
- One canonical terminal assessment per mission attempt (v0.1).
- `run_record_merge_sha` need not be self-recorded: Git/PR history is
  authoritative for the run-record merge commit.
- Legacy `maintenance_request` runs (#17/#19/#21 and older) are
  grandfathered — the canonical RunAssessment requirement begins with
  MissionContract-native execution.

RunAssessment + Ledger = the terminal feedback interface consumed by future
mission formulation. The Director does not own or rewrite historical
assessment facts.


## HQ Director v0.1 (implemented, Issue #33)

The formulation role is now real (ADR-0004): `director@0.1.0` +
`.omp/agents/hq-director.md` (spawns only `hq-operator`) and the smallest
`.omp/agents/hq-operator.md` adapter (spawns only `hq-scout`, `hq-repair`,
`hq-review`, `hq-integrator`). The Director:

- receives a FINITE explicit signal set; one invocation = one decision
  cycle; at most one MissionContract; selecting none is a valid outcome;
- reads exact canonical Policy + Ledger (+ canonical terminal RunAssessments
  where relevant); cheap evidence only — no deep recon; a bounded Recon
  Mission routes through the Operator path;
- never silently reopens a terminal prior Ledger disposition (POL-04);
  never self-scores or ranks; ambiguous selection -> BLOCKED / none;
- hands an accepted canonical MissionContract to an ISOLATED `hq-operator`
  by canonical references only (Mission ID, paths, exact formulation merge
  commit C, admission Ledger path + pre-formulation commit B + SHA-256,
  cycle Issue) — the Operator independently performs `operator@0.3.0`
  admission against Candidate@C + Contract@C + AdmissionLedger@B; the
  Director NORMAL-merges its own validated formulation PR as the
  canonicalization bridge (never target/run-record integration);
- never becomes the Operator, Scout, Reviewer, or Integrator.

No synthetic Director state lives in `mission/ledger.yaml` or `missions/`;
Director tests/smoke use `tests/fixtures/director/` + temporary/in-memory
Ledger state. The first REAL Director-selected mission is a separate Pilot.
