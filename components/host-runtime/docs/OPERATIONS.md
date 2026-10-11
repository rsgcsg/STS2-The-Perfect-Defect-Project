# Operations

## Native episode bootstrap transfer

Installed reference drivers require an explicit `--local-root` outside the
immutable package. The path must be absolute, canonical, ordinary and disjoint
from the package; symlinked paths are rejected. The same existing Host factory
uses this root for `profile-templates`, isolated `profiles` and `evidence`.
Its ready line reports the actual local and evidence roots under
`sts2.host-runtime/reference-state-root-1`, before any reset. A consumer requiring
external state checks this readback and refuses an older driver that ignores the
option. No-argument `.local` behavior remains only for an ordinary source checkout.

The ordinary Application derives `<state_dir>/native-host`, independently of
HTTP input. The operator stages the existing immutable `defect-a0-s0` template
there as `profile-templates/defect-a0-s0/{template.json,user-data/}`. Use the
existing template capture/transfer owner; retain its original game identity,
payload digest and bytes. `instantiateProfileTemplate` verifies these facts for
each new isolated generation. Do not put templates, profiles or evidence inside
the installed package, manufacture a missing template, or relax its complete
package-content pin. State preparation is not native execution qualification.

The existing reference driver ready line still means only that its process is
ready. Its additive bootstrap-handoff capability must be present before a
consumer's first reset. `release_bootstrap` calls the existing exact controller
release, returns original runtime/unheld proof and fences reset/step/reacquire
for that driver lifetime. Ordinary legacy behavior is unchanged until transfer.
An optional closed expected installed tuple is resolved by a trusted caller and
forwarded through the existing driver/factory. Host-owned disk and installed
artifact/sidecar checks reject drift before native spawn; actual loaded
capabilities reject remaining tuple/Modset drift before speed preparation,
controller registration or bootstrap. Omitting it preserves legacy behavior.
After transfer an application uses the same Connector/Runtime interface for its
Actor; it sends no Host-driver status/observe/provenance/gameplay RPC while that
Actor is associated. Explicit owned close follows exact Actor Stop/seal and
returns the actual native exit receipt separately from driver termination.
EOF/cancel aborts preparation through the existing factory signal before waiting
queued work. Transport uncertainty quarantines the owned process and never
authorizes Agent startup or automatic reset/retry. Attached personal games have
no owned driver, bootstrap receipt or Host-close permission. Source support is
distinct from package/install/runtime and mode/presentation qualification.

## Install

```bash
npm ci
npm run doctor
```

`npm run setup` is the game-bound Connector installation operation. It consumes
the exact Platform Connector release pinned in `src/connector-release.mjs`,
verifies archive and native artifact identity, and records rollback. The root
boundary check prevents predecessor release URLs from becoming production
authority. Never replace a release pin with a branch or an unverified local
DLL.

Installation is not loading. Fully exit and restart the game process before
using a newly installed artifact. Record the installer result and rollback
directory under local evidence; do not commit them.

## Prepare The Managed Candidate

The Managed runtime is built locally from its immutable upstream plus the
committed patch. No proprietary game files enter Git:

```bash
npm run experiment:managed -- prepare
npm run experiment:managed -- audit --candidate .local/candidates/<exact-candidate>
```

The audit must report the exact game assembly, upstream, patch, Host artifact,
and current Platform identity. Any mismatch is a requalification event, not a
reason to edit an expected hash.

Before STPD training, run the cheap external-consumer smoke:

```bash
npm run smoke:python -- \
  --candidate .local/candidates/<exact-candidate> \
  --max-actions 64 \
  --evidence-file .local/evidence/stpd-environment-smoke/report.json
```

A zero exit and `environment_smoke_pass` prove only the named operational
path. The report is local evidence and must not be committed.

## Start And Stop

Shared-profile operation can alter the active Steam profile. Accept that risk
explicitly:

```bash
npm start -- --shared-profile
```

The development-only isolated profile workflow is staged so the game creates
its own save schema:

```bash
node tools/headless.mjs reset-profile --isolated-profile h1-train
npm run bootstrap:profile -- --isolated-profile h1-train
node tools/headless.mjs enable-profile-mods --isolated-profile h1-train --settings-schema 8 --accept-ea-disclaimer
npm start -- --isolated-profile h1-train --experimental-build
```

Bootstrap records exact game/profile evidence and does not fabricate a save or
claim that Connector was loaded. `--experimental-build` collects evidence; it
does not grant support.

`start` runs in the foreground. It prints readiness only after exact-build,
Host, Connector, Modset, execution, and interactive-snapshot gates pass. Runtime
records and logs are local under `.local/runtime/`.

From another terminal:

```bash
npm run status
npm run stop
```

`stop` signals only the PID whose command still matches the recorded exact
executable and `--headless`; it refuses an ambiguous or reused PID. Any
process-local shutdown secret must never be written to evidence.

## Probes

```bash
npm run probe:shipped -- --shared-profile
npm run probe:menu-control -- --shared-profile
npm run probe:journey -- --shared-profile
npm run bench:capacity -- --template vanilla-clean --workers 1,2,4,8
npm run probe:recovery -- --template vanilla-clean --experimental-build
npm run probe:differential -- --template vanilla-clean --seed H1D1FF01 --experimental-build
npm run soak:reference -- --template vanilla-clean --workers 2 --episodes 2 --actions 8 --experimental-build
npm run drill:update
```

- shipped: boot, loaded identity, headless display, and interactive Snapshot;
- menu-control: delivery, duplicate idempotency, stale refusal, and successor;
- journey: representative surfaces and advertised Reads;
- capacity: concurrent isolated profiles, endpoints, decisions, CPU/RSS, and
  exact identity consistency;
- recovery: crash, new profile generation, runtime replacement, and cleanup;
- differential: independent same-seed Snapshot/Read/action comparison;
- soak: bounded workers, unique runtimes, endpoint release, and profile
  containment;
- update drill: exact identity admission and a fail-closed requalification
  plan.

A generated gate list is not proof that its gates passed. Unknown builds may be
investigated only through explicit experimental commands; normal start remains
fail closed.

## Rollback

Use the exact `rollback_backup` returned by setup:

```bash
npm run rollback -- --backup "<backup directory>"
```

Rollback requires all STS2 processes to be closed. Cold-start afterward and
verify the installed and loaded identities before making a runtime claim.

## Troubleshooting

- Unsupported game runtime: inspect `npm run doctor`; do not edit hashes.
- Existing STS2 process: fully exit game/Steam-launched instances, then retry.
- Endpoint already owned: use `npm run status`; never attach ambiguously.
- Connector endpoint not ready: inspect local session logs and installed
  artifact identity; do not retry unknown mutation.
- Profile acknowledgement required: choose one explicit profile mode.
- Incomplete BoundAction projection, missing successor, settling timeout,
  identity change, or unexpected driver exception: invalidate the episode and
  stop that worker.
- Native shutdown diagnostics outside the exact allowed signature: inspect the
  report; do not broaden a signature merely to make a run pass.
