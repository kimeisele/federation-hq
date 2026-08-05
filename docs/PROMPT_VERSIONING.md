# Prompt Versioning

This document defines how Federation HQ versions, releases, and pins role
prompts. It exists so that a run recorded today remains interpretable — and
reproducible in spirit — years from now, after prompts have changed.

## Model

Prompts are content-addressed by **id** and **semantic version**:

- `scout@0.1.0`
- `repair@0.1.0`
- `review@0.1.0`

The id is the role. The version is a semantic version (`MAJOR.MINOR.PATCH`).

## Rules

1. **Released prompt versions are immutable.** Once a version is listed in
   `prompts/registry.yaml` with a release date, its file content never changes.
   Corrections and improvements are new versions, never edits to a released
   file. A released version may be deprecated, but never mutated.

2. **Versions are explicit semantic identifiers.** Version strings are
   `MAJOR.MINOR.PATCH`:
   - `MAJOR` — breaking change: the prompt's output contract, allowed/forbidden
     actions, or evidence requirements change in a way that invalidates prior
     artifacts.
   - `MINOR` — additive change: new guidance that does not invalidate prior
     artifacts.
   - `PATCH` — clarification with no behavioral change.

3. **Every prompt has a registry entry.** `prompts/registry.yaml` records each
   prompt id, its versions, the file path of each version, the release date,
   and the changelog rationale for that version.

4. **Every release requires changelog rationale.** No version is added to the
   registry without a `changelog` entry stating why it exists and what changed
   relative to the previous version. A release without a rationale is not a
   release.

5. **Run manifests pin exact prompt versions.** A run manifest records the exact
   `id` and `version` of the scout, repair, and review prompts used in that run.
   The validator rejects run manifests with missing or non-released prompt
   versions.

6. **Old runs remain interpretable.** Because released versions are immutable,
   the artifacts of an old run reference prompt files that still exist with
   their original content. A registry entry for a superseded version remains
   present (possibly marked `superseded_by`) so the historical pin resolves.

## Registry entry shape

```yaml
prompts:
  - id: scout
    name: Unwired Functionality Scout
    versions:
      - version: 0.1.0
        file: scout/v0.1.0.md
        released: "2026-08-05"
        changelog: "Initial release for the three-role repair workflow."
```

`version` values are treated as strings (semver) and must be unique within a
prompt id. Prompt ids must be unique across the registry.

## Enforcement

- `scripts/validate_artifacts.py` loads the registry, verifies every referenced
  prompt file exists inside the repository, and rejects duplicate ids or
  duplicate versions.
- Run manifest validation rejects missing prompt versions and versions not
  present in the registry.
- No test, workflow, or mechanism is weakened to make validation pass; a new
  release must satisfy all rules above.
