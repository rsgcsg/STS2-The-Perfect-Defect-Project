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

## Exact temporary packages

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

Final Workbench-path validation and the topic's hosted checks must be reported
on their own actual candidate. They are not implied by the preceding probe.
