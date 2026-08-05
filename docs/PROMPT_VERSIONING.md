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

6. **Every version pins its content hash.** Each registry version records the
   SHA-256 of the exact UTF-8 bytes of its prompt file, and every run-manifest
   prompt pin records the same hash. The validator proves that (a) the registry
   hash matches the referenced prompt file, (b) the run-manifest hash matches
   the registry release, and (c) an edited prompt file without a new version
   and updated release metadata fails validation. Unknown or mismatched hashes
   fail closed. SHA-256 binding is deliberate; no signing, key management, or
   generalized content-addressed store is built.

7. **Old runs remain interpretable.** Because released versions are immutable,
   the artifacts of an old run reference prompt files that still exist with
   their original content. A registry entry for a superseded version remains
   present (possibly marked `superseded_by`) so the historical pin resolves.

## Bootstrap treatment

The `0.1.0` versions that ship with the founding repository merge are
**unreleased bootstrap material** (`status: unreleased_bootstrap` in the
registry). They are the repository's first release candidates: content
corrections made inside the unmerged founding pull request amend the bootstrap
material rather than creating a new version, because no run ever pinned the
draft bytes and the registry has never been merged anywhere. The registry
changelog records each such correction. At the repository's first merge the
bootstrap versions become effective releases; after that, rule 1 applies
normally and corrections require new versions.

## Registry entry shape

```yaml
prompts:
  - id: scout
    name: Unwired Functionality Scout
    versions:
      - version: 0.1.0
        file: scout/v0.1.0.md
        sha256: "<64 lowercase hex chars>"
        status: released          # or unreleased_bootstrap
        released: "2026-08-05"
        changelog: "Initial release for the three-role repair workflow."
```

`version` values are treated as strings (semver) and must be unique within a
prompt id. Prompt ids must be unique across the registry. `sha256` must match
the exact UTF-8 bytes of the referenced prompt file, and `status` must be
`released` or `unreleased_bootstrap`.

## Enforcement

- `scripts/validate_artifacts.py` loads the registry, verifies every referenced
  prompt file exists inside the repository, rejects duplicate ids or duplicate
  versions, requires a changelog rationale and a `status`, and proves the
  `sha256` matches the referenced prompt file's bytes.
- Run manifest validation rejects missing prompt versions, missing or
  mismatched pin hashes, and versions not present in the registry.
- Committed run bundles below `runs/` are discovered and cross-checked
  (run_id, repository/SHA agreement, candidate/result chains, review head,
  exact prompt hashes).
- No test, workflow, or mechanism is weakened to make validation pass; a new
  release must satisfy all rules above.
