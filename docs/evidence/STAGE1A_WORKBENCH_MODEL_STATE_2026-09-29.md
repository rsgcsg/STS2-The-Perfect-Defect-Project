# Durable local Workbench model state, 2026-09-29

This receipt records a candidate implementation and a real local application
migration/registration. It is not a game action, native qualification, public
release or one-click initial environment setup.

## Ownership and source

The shipped registry and adapter code stay source-owned. Private text Runtime
profiles, registered selections and binding records now belong to the configured
application state directory (`state_dir/models`). A checkout switch with identical
adapter bytes may retain those records; a changed adapter digest still requires
explicit fresh registration. Native manifests and historical bindings are not
silently rebased.

The independently reviewed implementation is `d263834126f28638606545cfd6249c5d47730446`.
The real migration and registration below ran from clean combined source
`1849ba78fe63fe80cec6245e5f901d2831cffa7f`, whose Python tree matched the reviewed
implementation. The isolated environment used Python 3.11.15 and the unchanged
lock SHA-256 `ac0c9af1e21ed72059ebba8c7a0366ebc68e26755ef22af38f02787fb84d3654`.
Private development dependencies and locked Python consumer SDK dependencies
were prepared in that checkout only; no production package was installed.

## Faithful repair and checks

The first independent review found that a partial archive or pin publication
could strand a retry. Publication now writes and syncs a complete private staging
file/tree, then publishes without overwriting a different existing owner record.
Failure cleanup removes only this operation's temporary files. Tests inject
interrupted archive and pin writes and retry through the same owning API.

At `d2638341`, the scoped Python suite completed with 198 passed and 1 skipped.
The skip requires a native Windows batch launcher; it is not a local Windows
pass. A separate root scoped Ruff/mypy check on later clean source
`c2e546b4513a876913e0fad87605c2ed256afa9c` passed; these are not attributed to
the earlier pytest log. Root identity, BOM, patch hygiene and closeout
checks passed on integration source `1849ba78`; the actual planner selected full.
A later candidate's hosted gate remains separate from these local checks.

## Actual local sequence

1. The existing model was unloaded and no training operation was running. The
   Workbench was stopped through its lifecycle owner. The first immediate
   migration attempt returned `already_running`, exit 1, while shutdown was
   still completing. It changed no model metadata. That failure was retained.
2. After the operator observed owner status `not_running`, the same private
   application configuration imported two known legacy checkout metadata roots
   through `project migrate-model-state`. Both commands exited 0 at
   2026-09-29 10:03:07 UTC. No machine-wide search or automatic migration ran.
3. Archive `d42d280657f10087d07697f6295958dfe388a69978fcfd04a8a6e36df2952311`
   contains four files and one historical selection. Archive
   `0085bb77de25102bc72d4f537f8e697e8c6de8e18260172d2e744567ebf519dc`
   contains eight files and three historical selections. Every archived file
   was independently hash-compared with its inventory and retained original;
   all matched. Old selections remain non-loadable history.
4. Both imports validated the existing installed Runtime and imported the same
   unchanged `text-menu-m2-runtime-v1.json` pin, SHA-256
   `3453b93f5fd5347257b267f7de2cd85c5c3c26990a227c58753489343038418b`.
   They did not copy old adapter bindings into the new active roster.
5. The new Workbench initially reported the Reset model as not registered. One
   explicit `POST /api/local-model-registrations/register` returned HTTP 200
   after 8.611 seconds at 10:04:43 UTC. Model
   `14a4f5df0a8ef5d6205e4bb71201b3e4f16224468838ef13be75bd7e5a8b7dec`
   became selection `local-text-m2-f7017db8e5074a3f8275488c3669afe5`,
   profile `text-menu-m2-v1`, `status=registered`, `loaded=false`.
6. The new roster and binding files were verified under application-owned
   storage. No model was loaded, no mode changed, and no gameplay was submitted.

## Explicit preparation candidate

A subsequent independently reviewed source increment
`6a5492b1edca8bc6e03c5654fd865000d8d35a4f` adds two explicit fixed-profile
preparation buttons and an owning application operation. A fully verified current
developer-kit may supply its pinned text/M2 Runtime pair; an ordinary checkout
can only reuse an already verified private installation. Missing assets remain
an explicit unavailable condition. The service never registers or loads a model
automatically, takes an arbitrary browser path, replaces a conflicting pin or
rebinds an old model manifest.

Independent review found a gap between the stopped-runtime check and operation
admission. The same operation lock now owns that admission, with worker rechecks
before publication/install and final state updates. Three deterministic
interleavings cover the original gap and late state changes. Root checks at the
clean exact source above ran 162 Python tests and 165 Node console tests, all
passed with exit 0; diff hygiene passed. The original 159/165 precommit results
are not used as exact final-head receipts.

The earlier application at clean `c2e546b4` was restarted after the migration;
read-only status retained the same registered selection and `loaded=false`.
Before the preparation candidate switch, root observed model idle/unloaded,
training completed, and stopped that Workbench through its lifecycle owner. An
explicit owner read-back at 2026-09-29 10:44:02 UTC reported `not_running`.
No Runtime Stop or game action was issued by this Workbench switch.

## Actual preparation button

At clean source `cf92b0bb5c894e9cbf4a5dbbf9a0235474500d8b`, the operator
opened the model page in Safari and explicitly clicked the memory-model
environment preparation button once. Instance
`f96a13148e568a1cffa18ab887580be8` recorded operation
`ffb5dcf5f0334b0a97edeccbc35251d8`. Read-back at
2026-09-29 10:45:48 UTC returned `completed`, profile `text-menu-m2-v1`,
`ready`, `reused=true`, application `idle` and `loaded=false`. The browser
then displayed the completed operation and verified preparation message.
This exercised the exact existing-install reuse path, not fresh package download
or first installation. It neither registered nor loaded a model and submitted
no game action.

A separately reviewed UI-only increment `29b7ff9700fd383a2c2c1efb2cfd96dcdd81f4d3`
removes duplicate expanded history from the model-control page. It summarizes
only the returned slice (at most 100 records), separately counting passed,
failed and unknown verification, with the existing full-detail page retained.
Its 166 Node tests passed, including a 14-record mixed-status fixture and
render-without-POST assertions. This is not a claim about the complete archive
or chronological latest run.

## Browser verification of the combined candidate

At clean source `127883c7bf23a52adc8c81f1eb3d72e3af33f1cc`, the owned
Workbench was started as instance `9193fbdac6f2b24147d5f59e28d86b8d`.
Safari displayed the retained Reset-K1 selection, unloaded model state and
compact history: 12 returned records, 10 verified, 2 failed, 0 unknown.
Clicking the history link opened the existing evaluations page with the full
reports, including retained failures and separate offline development results.
No model, training or gameplay command was issued during this check. An API
read-back also reported `idle`, `loaded=false` and `runtime=null`. Root checks on
that exact source passed the 166-test Node file, identity, BOM, diff hygiene,
closeout and the planner; the planner selected full. This is a local browser
and application-state receipt, not a hosted full or native gameplay result.

## Remaining boundary

The migration receipt used the explicit operator API and an already validated
local text-profile installation. The preparation button reused the verified existing installation as recorded
above. Fresh selected-kit installation is covered by synthetic tests only.
This does not establish a new user's complete
one-click setup, released rc.12 asset availability, native Reset operation or
cross-machine portability. Raw private paths, records, tokens and weights stay
local. Old stores, packages and archives are preserved for rollback; any return
to an older application must respect its own source/configuration contract.
