---
name: HQ Run
about: Open and coordinate one bounded cross-repository maintenance run
title: "[HQ Run] <short bounded objective>"
labels: []
assignees: []
---

## Run identity

- **Run ID:** `<run-YYYYMMDD-<slug>>`
- **Target repository:** `<owner/repository>`
- **Baseline SHA:** `<40 hex chars>`
- **Coordination protocol version:** `0.1.0`

## Bounded maintenance request

<!-- State the original request exactly. The Scout candidate may clarify it but may not replace or silently broaden it. -->

- **Request text:** ...
- **Source:** `<human_operator | issue | other>`
- **Source reference (optional):** `<issue URL or durable reference>`

## Pinned prompt releases

- **Scout:** `scout@0.1.0`
- **Repair:** `repair@0.1.0`
- **Review:** `review@0.1.0`
- **Operator:** `operator@0.1.0`

<!-- Exact content hashes are recorded in the run manifest, not in this Issue. -->

## Run record

- **HQ run path (planned or committed):** `runs/<run-id>/`
- **HQ run-record branch:** `<hq-run-record-branch>`
- **HQ draft record PR:** `<record PR URL>`
- **Latest accepted HQ record commit SHA:** `<40 hex chars or "none">`
- **Target branch or PR reference:** `<branch or PR URL when available>`

## Baseline state

- **Current pipeline state:** `requested`
- **Active role assignment:** `none`
- **Known baseline failures:** `<check names or "none">`

## Constraints and stop conditions

- ...

## Canonical record

> This Issue is the operational coordination thread. Accepted artifacts committed under `runs/<run-id>/`, bound to exact Federation HQ commit SHAs and hashes, are canonical.

<!-- Structured coordination messages (assignment, artifact_submission,
artifact_acceptance, rework_request, blocked, run_closed) are posted as
comments below per docs/COORDINATION_PROTOCOL.md. -->
