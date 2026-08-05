# ADR-0001: Three-role repair workflow as separate prompts and separate roles

- **Status:** Accepted
- **Date:** 2026-08-05
- **Applies to:** `prompts/scout/v0.1.0.md`, `prompts/repair/v0.1.0.md`,
  `prompts/review/v0.1.0.md`

## Context

The first supported Federation HQ workflow separates three responsibilities:
(1) identifying and precisely describing a bounded defect (scout), (2) repairing
exactly that defect (repair), and (3) independently verifying the repair against
the exact remote head (review).

A tempting alternative is a single master prompt that switches mode based on an
input flag or instruction — one "repair agent" that scouts, builds, and reviews
depending on how it is invoked.

## Decision

Scout, Repair, and Review are **separate prompt versions** (`scout@0.1.0`,
`repair@0.1.0`, `review@0.1.0`) and **separate roles**, not modes of one master
prompt.

## Rationale

1. **Evidence independence.** The entire value of the review step is that it is
   independent of the repair work. A reviewer running inside the same prompt as
   the builder inherits the builder's framing: the same assumptions, the same
   reading of the code, the same blind spots. A separate prompt cannot fully
   guarantee independence of the underlying model, but it removes the shared
   reasoning artifact — the reviewer re-derives the state of the world from the
   repository and evidence rather than continuing a builder's chain of thought.

2. **Versioning independence.** The three responsibilities evolve at different
   rates. Scouting guidance (how to bound a defect) changes for different
   reasons than review guidance (how to re-verify a head). One master prompt
   forces a single version to cover all three, so every clarification to review
   bumps the version of scouting too — invalidating pins on runs that never
   needed the change. Separate versions let a run pin `scout@0.1.0`,
   `repair@0.1.2`, `review@0.1.1` independently, which is exactly what
   `run_manifest.prompt_pins` records.

3. **Accountability and boundary enforcement.** A master prompt with a mode
   switch makes the no-scope-expansion rule harder to state and enforce: the
   "repair" mode can silently slip into "scout" behavior when it encounters
   something interesting. Separate prompts make each role's allowed and
   forbidden actions explicit, reviewable, and testable in isolation.

4. **No role merges its own work.** With separate roles, the prohibition is
   structural — the artifact flow is linear (candidate → result → review) and
   each step is produced by a distinct prompt pin. A single-prompt design makes
   "did the reviewer really act independently" a matter of runtime discipline
   rather than recorded structure.

5. **Bounded blast radius of change.** A defect in the review prompt is a
   version bump of one file. A defect in a master prompt is a version bump that
   touches every run of the workflow.

## Consequences

- Each role prompt is longer than a mode section would be: role, inputs,
  bootstrap rules, allowed/forbidden actions, evidence requirements, output
  contract, stop conditions, no-scope-expansion rule are all stated per prompt.
- Run manifests must pin three versions instead of one; the validator enforces
  that all three pins exist.
- The tradeoff accepted: some shared content (evidence rules, repository-native
  instruction discovery) is duplicated across the three prompts rather than
  factored into a shared preamble. In v0.1.0 this duplication is deliberate —
  it keeps each prompt self-contained and immutable, which version independence
  requires. If a shared preamble is ever introduced, it must be versioned and
  pinned itself, and this ADR must be revisited.

## Alternatives considered

- **One master prompt with a mode switch.** Rejected for reasons 1–5 above.
- **One prompt, one role, model decides.** Rejected: loses the explicit
  three-phase artifact contract and makes scope creep a prompt-interpretation
  question rather than a contract violation.
