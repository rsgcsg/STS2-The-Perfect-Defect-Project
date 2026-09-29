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
pass. Scoped Ruff and mypy passed. Root identity, BOM, patch hygiene and closeout
checks passed on integration source `1849ba78`; the actual planner selected full.
A later candidate's hosted gate remains separate from these local checks.

## Actual local sequence

1. The existing model was unloaded and no training operation was running. The
   Workbench was stopped through its lifecycle owner. The first immediate
   migration attempt returned `already_running`, exit 1, while shutdown was
   still completing. It changed no model metadata. That failure was retained.
2. After an explicit owner status returned `not_running`, the same private
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

## Remaining boundary

The browser's existing Runtime installer still prepares the base Runtime only.
This receipt used the explicit operator migration API and an already validated
local text-profile installation. It does not establish a new user's complete
one-click setup, released rc.12 asset availability, native Reset operation or
cross-machine portability. Raw private paths, records, tokens and weights stay
local. Old stores, packages and archives are preserved for rollback; any return
to an older application must respect its own source/configuration contract.
