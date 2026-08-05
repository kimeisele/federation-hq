# runs/

Durable record of scoped repair runs. Each run is one bounded repair workflow
execution (Scout → Repair → Review) coordinated by the HQ Operator against one
target repository.

## Layout

```text
runs/
  README.md
  run-<YYYYMMDD>-<slug>/
    run-manifest.yaml        # pins target repo, baseline SHA, maintenance request, coordination Issue, prompt versions+hashes
    repair-candidate.yaml    # the single selected candidate
    repair-result.yaml       # head SHA, PR reference, commands, failures
    review-result.yaml       # reviewer head SHA, verdict, blockers
```

`<slug>` is a short lowercase identifier (for example
`run-20260805-widget-service-scope-bug`). Every file in a run directory is
validated against the schemas in `contracts/` by
`scripts/validate_artifacts.py`; see the run's manifest for the authoritative
pins.

## Coordination

Each run maps to exactly one coordination GitHub Issue (created from the
`.github/ISSUE_TEMPLATE/hq-run.md` template). The Issue body carries the
original maintenance request and run identity; structured coordination
messages (assignment, artifact_submission, artifact_acceptance,
rework_request, blocked, run_closed) are posted as comments per
`docs/COORDINATION_PROTOCOL.md`. Issue comments are transport and audit
context — the committed artifacts in this directory, bound to exact
Federation HQ commit SHAs and hashes, are canonical.

## Ground rules

- One run directory per run; a run is immutable once its review-result is
  recorded. Amendments to a closed run create a new run or a follow-up note in
  the run manifest — never edits to a recorded review verdict.
- `run-manifest.yaml` records `pipeline_state` as the workflow advances
  (see `docs/REPAIR_PIPELINE.md` for the state machine).
- The Operator maintains exactly one active role assignment per run and posts
  `artifact_acceptance` only after structural validation, reference checks,
  recording under an exact HQ commit, and hashing.
- Evidence references in artifacts point at exact locations: commit SHAs,
  PR heads, command outputs. They are claims to verify, not proof.
- No example run is checked in; `examples/` contains the illustrative
  artifacts. The first real run directory is created when the first
  maintenance request is processed.
