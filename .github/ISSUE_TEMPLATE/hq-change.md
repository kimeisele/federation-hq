---
name: HQ Change
about: Propose an intentional change to Federation HQ itself
title: "[HQ Change] <short change>"
labels: []
assignees: []
---

## Change summary

- **Affected canonical files:** `<paths>`
- **Current behavior:** ...
- **Proposed behavior:** ...
- **Rationale and evidence:** ...

## Impact

- **Compatibility impact:** ...
- **Prompt-version impact:** `<which released prompts are affected and how>`
- **Schema or contract impact:** `<contracts/ files affected>`
- **Migration impact on existing runs:** `<whether existing runs/remain interpretable>`

## Explicit non-goals

- ...

## Acceptance criteria

- ...

## Invariants

- Released prompts are **not edited in place**; corrections require a new
  version (see `docs/PROMPT_VERSIONING.md`).
- Workflow changes are **not inserted opportunistically into unrelated runs**.
- **Amend existing canonical documents** (`docs/BOUNDARIES.md`,
  `docs/REPAIR_PIPELINE.md`, `docs/PROMPT_VERSIONING.md`, ADRs) before adding
  new architecture documents.
- Deferred ideas in `README.md` are **not automatically authorized** by this
  Issue; implementation requires an explicit scoped decision.
