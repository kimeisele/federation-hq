# Federation HQ GitHub App Review Gate (v0.2)

One private GitHub App (`federation-hq-review-gate`), installed once on the
personal account `kimeisele` with **All repositories**, publishes a SHA-bound
Check Run named `federation-hq/review` on the exact reviewed head. Target
repositories then require that app-owned check instead of a write-capable
human approving reviewer.

The Gate App is a **mechanical attestor**: it converts an already accepted
canonical Federation HQ review artifact into a Check Run. It never decides a
verdict, never merges, never pushes, and never modifies branch protection.

## 1. One-time app creation and installation (owner action)

```bash
python -m federation_hq_gate setup-app
```

This runs the GitHub App Manifest flow: it opens the app-creation URL, listens
only on `127.0.0.1` for the callback code, exchanges it for the App ID,
private key and webhook secret, and stores credentials outside the repository
under `~/.config/federation-hq-gate/`. The private key is written with
owner-read/write-only permissions and is never printed or committed.

If the browser flow is not usable, use the deterministic fallback:

```bash
python -m federation_hq_gate setup-app --manual          # prints exact fields to enter
python -m federation_hq_gate setup-app --manual-store --app-id <ID> --installation-id <ID> --pem-path <PEM>
```

After creation, install the app on account `kimeisele` with **All
repositories**:
`https://github.com/apps/federation-hq-review-gate/installations/new`

The App's default permissions are exactly:

```yaml
metadata: read
contents: read
pull_requests: read
checks: write
```

Forbidden (never granted): `administration: write`, `contents: write`,
`actions: write`, `workflows: write`, `secrets: read`, `members: write`.

## 2. Command to run afterward

```bash
python -m federation_hq_gate doctor
```

Must report all checks `ok` before any publication or policy step.

## 3. Credential location and permissions

* Config dir: `~/.config/federation-hq-gate/` (override:
  `FEDERATION_HQ_CONFIG_DIR`)
* `config.json` — app ID, installation ID, key path (mode `0600`)
* `private-key.pem` — PEM private key, **owner read/write only** (mode `0600`)

Environment override (all optional): `FEDERATION_HQ_APP_ID`,
`FEDERATION_HQ_INSTALLATION_ID`, `FEDERATION_HQ_PRIVATE_KEY_PATH`.
No secret may be committed, copied into the repository, or placed in a
tracked `.env` file.

## 4. Dry-run policy planning

```bash
python -m federation_hq_gate policy plan --owner kimeisele --output policy-plan.json
```

Read-only: lists owned non-fork non-archived repositories, their default
branch and current protection, preserves existing required checks, records
skip reasons (forks, archived, explicit `--exclude`, insufficient permission
or plan limits), and prints the deterministic `plan_sha256`. No mutations.

## 5. One-hash-confirmed fleet rollout

```bash
python -m federation_hq_gate policy apply \
  --plan policy-plan.json \
  --confirm-plan-sha256 <HASH>
```

Per repository: re-checks for drift (aborts that repository if materially
changed), writes a before-state backup, bootstraps `federation-hq/review` via
the Gate App, sets human approving-review count to `0`, adds
`federation-hq/review` as a required status check (bound to the exact Gate App
ID via rulesets where supported), preserves all unrelated required checks,
adds no bypass actors, changes no collaborators, verifies the result remotely,
and reports per-repository outcomes — continuing past bounded failures.

Rollback:

```bash
python -m federation_hq_gate policy rollback --backup policy-backup-<TIMESTAMP>.json
```

## 6. Normal per-run publication

```bash
python -m federation_hq_gate publish-review-check \
  --repository kimeisele/agent-city \
  --head-sha <EXACT_HEAD_SHA> \
  --run-id <RUN_ID> \
  --review-result runs/<RUN_ID>/review-result.yaml
```

Validates, before publishing success: canonical review schema; repository
equality; exact reviewed-head equality; verdict `approved`; empty blockers;
candidate→repair→review lineage; well-formed hashes; the remote PR head still
equals the reviewed head; the artifact belongs to the run; no later review
supersedes it. Then publishes `federation-hq/review` (status `completed`,
conclusion `success`) on the exact head. Publication is idempotent for the
same run/repository/head; any mismatch publishes a failure conclusion and
never reuses a check from another SHA. The Operator runs this **after** the
canonical review artifact is accepted — never before.

## 7. Rollback

`policy rollback` restores the stored before-state protection per repository.
A changed remote head is never re-attested; a new head requires a new
successful review cycle.

## 8. Threat model and forbidden permissions

The App cannot write code, modify workflows/actions, read secrets, manage
members, or administer repositories. It cannot merge, push, modify branches
or branch protection, or approve as a human reviewer. Branch-policy changes
use the repository owner's authenticated `gh` session only — never the Gate
App token. An app-owned check can only be published by this App; a changed
head therefore always requires a completely new successful check. A check
run is attestation evidence, not proof of code correctness.
