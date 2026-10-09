# Native Source3 collect-only CLI

`python -m spireagent.workbench project collect-source3` is one bounded application
attempt using an explicit public demonstration teacher. It creates no model,
training data admission, reservation, upload or training job. Run it only after
review and the separate exact Host/game/package gates for the selected checkout.
The existing project configuration, Node dependencies, built native Connector SDK,
private Host profile template `defect-a0-s0`, and exact authorized game installation
must already be available. This command does not install or bootstrap those tools.

```sh
python -m spireagent.workbench project collect-source3 \
  --config /ABS/project.json \
  --game-directory /ABS/authorized-game \
  --host-local-root /ABS/private-host-state \
  --output /ABS/new-private-attempt \
  --seed EXPLICITSEED \
  --target-choices 100 --max-submissions 120 --deadline-ms 900000 \
  --plan-only
```

`--plan-only` validates read-only metadata without creating an Application, child,
SDK session or game process. Remove it for the explicitly authorized attempt.
Unsupported experimental game/Connector candidates require the corresponding
`--experimental-build-acknowledged` / `--experimental-connector-acknowledged`
flags in addition to their separate qualification. Paths must be absolute and
contain no symlink; output must be a new private directory. Targets and submissions
are bounded to 1–300 and the deadline to 45 minutes. The endpoint comes from the
existing project configuration and must be loopback. The executable, native SDK,
Host implementation, template, character (Defect) and ascension (A0) are fixed.

The CLI holds the existing Workbench instance lock and one genuine Application.
Its original `control_native_recording` Start/Close methods retain shared Source
and Model admission. A running Workbench or nonquiescent Model blocks collection.
No GUI/server/pair setup, direct recording-task mutation or alternate admission
service is introduced. Unknown Start admits zero teacher choices. An accepted
owned Start is closed at most once, including exceptions before SourceReady.
Unknown Close remains unknown and is never repeated automatically.

The fixed Node child owns isolated Host startup, bootstrap handoff and native SDK
execution. It attaches with an empty eager scope in scoped mode and the fixed
publication profile's required seams. Before each teacher decision it acquires
complete fresh Current and the complete ordered original C, including low-ranked
members. Event cursors/gaps and bounded async waits are advisory wake/accounting;
they create no full-publication-history claim and no backfill. Source accounting,
original input integrity, control, runtime, generation and action-binding failures
still stop. One original action ID and basis are returned by the Python parent;
Node rechecks membership and binds/executes it through the original SDK. Only
known pending Result is read, using the same original request ID (at most 40 reads
and two seconds). Unknown delivery never authorizes another submission.

The pure STPD teacher `native-public-demonstration-v1` is explicit, deterministic
and unlearned. Its disclosed scripted inspection state admits at most 12 browsing
choices, then progresses through typed current map/card/focus/confirm/reward
members. Missing required public semantics stops the attempt. It cannot replace
or act as a hidden fallback for a learned natural-evaluation journey.

Before SDK admission, a private `submission-intent-NNNN.json` records and syncs
its original request ID, ordinal, complete basis and original action. This is an
intent, with admission/delivery unobserved. An admitted transport failure retains
that same ID and its explicit unresolved/unknown/no-retry disposition in
`submission-outcome-NNNN.json`, the final child receipt and parent report.
`quiesced.json` preserves the original quiescence identity/counts before Close;
none of these records authorizes retry or infers an outcome from later Current.
A failed Close-request diagnostic write is recorded and still permits the
original App Close call. The at-most-once flag is set only when that owner call
is actually offered; an unknown offered Close is never repeated.

Attachment starts at the SDK subscription's original `starting_cursor`; subsequent
Events and Renew replies supply their own `next_cursor`. A collector exception
also writes private `collector-failure.json` with the active phase and bounded
error/cause details. Field lengths and cause depth are bounded, and the existing
UTF-8 diagnostic byte budget still applies. Diagnostic write failure cannot
replace the original request disposition or prevent owner cleanup. These files
are operational diagnostics, never recording originals or training input.

Private `request.json`, exact original result files and `report.json` preserve
attempt disposition, teacher state, counts, cancellation, controller release and
actual Host/child exit receipts. `actual_choices` counts received original Result
messages; `submissions` separately counts SDK submission admissions, including an
unresolved final admission. Known delivered choices do not prove native execution,
effect, successor, Human origin or eligible unique N. `eligible_unique_N` and
`N_exclusions` remain unknown and `admission=not_run`; target 100 never proves N=100.
A ready native terminal summary or unsupported/unknown/budget stop preserves a
partial attempt. Recording starts after Host bootstrap, so the prefix is declared
partial and never strict native-start/full history.

SIGINT/SIGTERM/EOF/deadline cleanup is owned before launch; repeated signals request
cleanup without escaping it. The child releases its original controller, detaches
its subscription and closes its exact Host. Parent retains actual child exit
separately. A missing receipt, active child, unknown Close or cleanup failure stays
unknown. The durable collection-operation marker blocks unresolved predecessors;
there is no automatic restart, resume, process-name kill or unknown retry.

The Node/Python pipe journal is operational evidence. Only Recorder-owned raw
Source3 originals go through the existing separate import, verification, ordered
projection, partition/reservation and training APIs. No collection report upgrades
source/test checks into installed, live, Human, research or G2/V1 qualification.

Focused portable checks:

```sh
node --test tools/test/native-source3-collector.test.mjs
node --test components/host-runtime/test/source3-episode-lifecycle.test.mjs
python -m pytest -q python/tests/test_native_source3_collection.py \
  python/tests/test_native_public_teacher.py
```

Tests use the production compositions with injected exact process/SDK/Recorder
owners, the genuine Application admission path, and synthetic public inputs.
They do not collect or train on actual Source3 or qualify an actual game.
