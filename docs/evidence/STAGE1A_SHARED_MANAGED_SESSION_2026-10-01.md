# Shared Managed Host session — bounded engineering evidence

This packet connects the Workbench and Policy Runtime to one Host-owned native
child. It does not qualify a complete game, native-Human recording, model quality,
remote team hosting, or the current production installation.

## Ownership and change

The previous Workbench owned a JSONL child directly. Starting another driver for a
model would create another game; sharing its stdin would race response readers.
The Host now owns a loopback service and the existing serialized driver. A
Workbench or model client attaches to that service and obtains an exclusive
controller lease. Gameplay still uses the current complete Connector text menu.
Runtime Human/Stop releases control without closing the Host. Host close is a
separate explicit manager operation.

Manager recovery uses the original claim request or retained lease epoch, plus
service, runtime and game-continuity identities. It must not release a newer
owner after a lost response. Claim attribution is manager-only and appears only
when it matches the driver's current held lease. Unknown delivery is not retried
or relabelled. Runtime Evidence, Workbench client segments and native Annotator
Human witnesses retain different meanings.

## Earlier temporary packages

These were built and installed in private test directories, not published or
installed over the production Workbench/Runtime.

| Component | Source revision | Component tree | Archive SHA-256 |
|---|---|---|---|
| Host rc.23, final claim attribution | `c5f1c33879c9cab419f8366b736e43e39d466dcb` | `4376a33dc7ca2deeeaca13c4493e3fc12f1577ec` | `414496e996dbbabb65213df143daa57d7719b8a3004687e450c591bd54db703e` |
| Policy Runtime rc.16 | `69aedcba6dc362c9c908353ac18a603f4b58f63a` | `41839e944f9281dcffa58869f51dadc9c842b0dc` | `2baf3836405f776b1538986dbc6522fddeabec73afd874ed189cb9846bb96dea` |

Host source digest is
`7fc036c790274fcb546e3a5e2e42a0dbf770c50d5ca5111dbc1d38a57f71486e`;
installed package content digest is
`02d76835afd1ca5034ce26e513bd645bd105b41d70bc18eed30ba1b09719dad6`.
Runtime source digest is
`121405c35bf598eb666e4a19a9c0c416dd131c7292479e95d5f2ebb98ae65ff1`;
installed package content digest is
`ef01503f22fe6ae80fa27709f0c9cfa03a3ec4209d96e64952a7c8440053f6ca`.
Source and installed-content digests use different scopes. Installed Host
readback truthfully has null Git revisions; App-verified selected provenance is
recorded separately. Runtime does not claim independent archive verification.

Evidence rc.24 is installed from exact Git
`77e52d6c40d6c1f3c2caba9c29e029f0ffc94a37` in the private Python 3.11.15
environment. The import resolves to its installed site-packages, while
`stpd`/`spireagent` resolve to the isolated source worktree. The lock sync changed
only the intended Evidence pin, not other dependency versions.

## Executed component checks

All commands below exited 0 unless an earlier failure is explicitly retained.

- `npm --prefix components/host-runtime run check`: final Host component above,
  283 Node tests, 279 passed and four exact-game tests skipped without their
  candidate environment; 24 Python SDK tests passed; syntax, documentation,
  repository boundaries and installed package smoke passed.
- `npm --prefix components/policy-runtime run check`: Runtime source above,
  181 tests passed, typecheck/build/deterministic package/temporary installed
  smoke passed. The preceding candidate's installed smoke correctly failed
  because the embedded version constant still said rc.15; the rc.16 constant
  and documentation were corrected before this final check. That failure is
  retained, not presented as an initial clean pass.
- `npm --prefix components/evidence run check`: 186 tests passed for Evidence
  source `77e52d6c`; later App/Host changes do not change that component identity.

## First real shared-instance probe

The first probe used Host source `d45b13709975bc5497b204264b60ddc2b45ea070`,
archive `6396ce07289477b7e74a738a08fca04c804200ca08f27e9139c63d5a97c5e2d1`,
before the manager-only claim attribution addition. It cannot qualify that later
addition. Runtime and Evidence are the exact temporary candidates above.

The installed Host's public Python SDK launched one Managed instance, explicitly
reset it to seed `M2SHARED20261001`, then a manual protocol client acquired,
observed and released it. Existing D-Simple confirmed-interaction K1 weights
were bound to this Host without training. Runtime One-Step made one policy call,
returned `text_native_delivered`, zero errors, Human/released/untainted. Runtime
Stop sealed its evidence while the Host remained alive. The manual protocol
client acquired and observed the same instance and game continuity, then
released it. Finally the manager explicitly closed the Host. Both processes
exited normally; the private probe exited 0.

- Policy run: `run-6799a2b7-895b-43d2-b763-93babfdb8460`.
- Host service: `managed_service_1e8641f44d9940a399ca60762f601a20`.
- Runtime instance: `685e8f7c58ca4d4eb52763fbb3de3ddb`.
- Game continuity: `managed_episode_3353989dc17449b5a1d88c58025ba0a3`.
- Evidence: 11 events, typed verification passed with no findings;
  content ID `67fba31dd7aa117b331dee6ab2306ea92bc1301041aa1348334f8a421549beb3`.
- Existing weights SHA-256:
  `d9ad730a1ac9f8cff0238ab97b680ce08a552785864838f362b67dac0d92b78e`.

Independent read-only inspection checked the actual 11-event sequence: admitted
environment/input/decision, held controller, one native dispatch, applied and
delivered native-input result, matching observed successor, acknowledged release,
One-Step completion and Stop. Attestation matched expected and actual identity;
the five evidence file checksums matched, and the manifest was complete and
append-only. The manual-client before/after and final Host-close assertions are
in the probe receipt, not a separately recorded HTTP transcript. The successor
is the observed result snapshot, not a newly claimed causal Human transition.

The clients in this probe are not native Human input. The probe establishes
one delivered model action and controller handoff, not a quality score, a full
trajectory, native-desktop parity or a model completing a game. Private page
payloads, model weights, credentials and full logs are not published.

## Final application-path probe

At `2026-09-30T16:21:01Z` (2026-10-01 Australia/Brisbane), the private
application probe completed with exit 0 on clean App source
`5ba319c5cca95ce0fdbc7935cb20bee0fa6c538a`, Python 3.11.15 and installed
Evidence 0.1.0rc24. Script SHA-256 at launch was
`4de82b7d8d612a3a8133aaef8ed51959086e86f3f29f12c17acb0c33a6ebb5c0`.
The invocation used the private environment's Python, the isolated App root,
`--expected-app-head` bound to that commit, separate private/original project
arguments and a private receipt directory. It used the final Host, Runtime and
Evidence packages listed above; neither production installation nor original
model data was replaced.

The actual application classes, not only SDK clients, performed this sequence:

1. `LocalEnvironmentService` started the Host and observed a complete menu.
2. Ending the first Workbench segment archived that segment and left Host alive.
3. `LocalModelRegistration` verified the existing export read-only and registered
   it in the private project. `LocalModelService` loaded it and executed One-Step.
4. One policy call produced one applied native action, zero errors and
   Human/released/untainted state. Stop sealed 11 events; typed verification
   passed with no findings.
5. Workbench resumed the same service/runtime/game continuity, observed it and
   archived a second segment. Explicit environment close returned `closed`.

Exact identities:

- Policy run: `run-42723ff8-e622-4d49-81dd-ad7b41f940f1`.
- Host service: `managed_service_3073d5b438b34f509b04138948aa70bc`.
- Runtime instance: `4144cecedd104e57abf2f3e1a542c6df`.
- Continuity: `managed_episode_bf79a179a58145539acb1bf2cb68121b`.
- Workbench segments: `23d5a865524240e795a065c75f807c38` and
  `98604cadd0ae41b38f22aa4e8d129be8`.
- Segment report artifacts:
  `25309ecec2dac040fa811073e218f3f358501b464bda7599d1058eee5161e76c` and
  `00ca4ddd7088e547ae184a702f30226883ecb6f1dfceb5e5c6992de266423e34`.
- Runtime startup code SHA-256:
  `30dc32dfc9b733a085adfce39dc66c93aaef38b12e72531cae4ed00a146a13ce`.
- Policy manifest SHA-256:
  `8d45eb72271d5533dc91584b325af2a61817aed41c6dd25af237187392fe59d4`.

Original model metadata, tokenizer, weights and export-operation hashes matched
before/after. Existing weights are the same `d9ad730a…` artifact above; no model
training occurred. Both Workbench reports are client-operation segments, not
native Human recordings or independent seeded games. This is one bounded
application-path execution, not browser UX, arbitrary scene support, full-game
completion or policy-quality qualification.

## Application checks and final gate

On `5b4e89c1e7f5bc9414c813206c5d0b3f0bb2c95e`:

- Private Python `-m pytest -q -ra` over `test_local_environment.py`,
  `test_local_environment_http.py`, `test_local_models.py`,
  `test_local_model_registration.py`, `test_managed_model_target.py` and
  `test_confirmed_interaction_memory.py`: 244 passed, exit 0.
- `node --test python/tests/console_project.test.mjs`: 218 passed, exit 0.
- Scoped Ruff: passed, exit 0.

Correct-project mypy then found five concrete boundary/type errors. The final
App commit `5ba319c5` explicitly narrows identity fields, rejects a malformed
nested submit result as unknown while still releasing control, and narrows the
validated profile path. Its 60 environment/HTTP regressions, scoped Ruff and
six-source-file mypy passed (exit 0). The broader 244-test result belongs to the
preceding App commit; it is not relabeled as a final-head full suite. A prior
wrong-directory mypy run also failed to load project overrides; both failures
remain in local logs.

Final identity/BOM and repository checks use the topic's actual committed
candidate. Hosted full CI remains a separate required gate, recorded on the PR;
no previous PR's green status qualifies this combination.


## Subsequent exact App canary and repair boundary

The earlier receipts above retain their original source and package identities.
After canonical path preparation, a fresh App canary at `a676b398` reached the
advertised combat setup but failed before policy inference because guarded
Managed observation lacked a Runtime controller lease. Runtime now obtains a
short observation lease and releases it before inference, preserving an existing
Auto lease and tainting uncertain acquisition/release without retry.

The next attempt at `8b7fe925` failed model loading before policy inference:
extracting a shared M0 encoder changed a legacy M2 implementation fingerprint.
The exact old M2 source bytes were restored at `08656cf5`; strict fingerprint
validation was preserved. The actual existing export then loaded with the same
CPU scores and unchanged file hashes. Neither failed attempt is acceptance.

A fresh private project at `08656cf5d681cb73a9ff42fe44e5229f90e7e39a` then used:

- Host rc.23 source `50d88ee44b60566473116ea29864fc9ab485b8fe`, archive SHA-256
  `5a253b08484c576855bd24a8fef539aa50c7d373eac837848a41a66802add62a`.
- Runtime rc.17 source `95ae821cc5733259f50cd1edcfc8a7dcdd6914c4`, archive SHA-256
  `5882c557282b8f3256264758ee9fb42ece6878cf2838f7db56b9c4ec28603103`.
- Prepared Managed artifact SHA-256
  `dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93`,
  MVID `145c95e9-ace0-42b5-bb46-3fef292ac645`.

After an explicitly recorded setup action entered an advertised monster node,
the actual App registered and loaded the existing model. Three model decisions
produced two menu navigations and one native card-play delivery. The sealed run
`run-0c2f9e73-df2a-4209-a619-3c464f3c2157` contains 33 events; typed Evidence
rc.24 verification passed with no findings. Its receipt's last-step policy-call
counter is 1; the event sequence establishes three total decisions. Six controller
acquire/release pairs completed, returning Runtime to Human/released/untainted.

The real owning Workbench client then reclaimed its menu and submitted its old
action once. Host rejected `stale_or_unadvertised_action`, with no native delivery.
This direct client probe is neither a browser interaction nor an archived
Workbench operation, and intervening native play means it does not isolate
owner-only invalidation. Two Workbench client segments were archived/stopped on
the same Host environment. Model stop and explicit Host close succeeded; all
four original export/operation file hashes matched before and after.

Independent audit checked the typed run, both immutable report payloads, shared
environment/lineage, package inventories, Runtime code digest and original file
hashes. Source `08656cf5` subsequently passed hosted CI `36820121927`. The canary
remains bound to that source and those packages: later training-journal or
integration changes do not inherit a new runtime execution claim. It establishes
bounded Managed App execution, not native GUI coverage, Human qualification,
complete-game policy capability, memory benefit or Stage1a completion.
