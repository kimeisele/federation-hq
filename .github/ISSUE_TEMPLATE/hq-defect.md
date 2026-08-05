---
name: HQ Defect
about: Report a reproducible defect or drift in Federation HQ
title: "[HQ Defect] <short defect>"
labels: []
assignees: []
---

## Defect report

- **Affected file, workflow, contract or renderer:** `<path>`
- **Exact HQ commit SHA:** `<40 hex chars>`
- **Expected behavior:** ...
- **Observed behavior:** ...

## Reproduction

- **Command:** `...`
- **Exit code:** `<N>`
- **Evidence location:** `<path or log URL>`
- **Reproduces on `main`:** `<yes | no | untested>`

## Impact

- **Impact on released prompts:** `<none | describe>`
- **Impact on existing runs:** `<none | describe>`
- **Minimal repair boundary:** ...

## Constraints

- No unrelated cleanup in the same change.
- No mutation of released prompt files.
- Reports are **claims until reproduced**; the reproduction evidence above is
  required before a repair is accepted.
