# Managed text-menu v2 and Workbench environment candidate

Date: 2026-09-29. This is an execution receipt, not Stage1a acceptance.
Source, installed package, native execution and trained-model support are
separate. Private raw pages remain local; this document contains aggregate
results and exact candidate identities.

## Owning paths

- Host Runtime exposes an explicit `text-menu-v2` option through its existing
  Python consumer and Managed driver. Existing calls still use v1 by default.
  Card and target selection are text-owned intentions; only the final bound
  `play` submits to the Managed native host. The complete native catalog remains
  authoritative. Unknown delivery is never retried automatically.
- Python client shutdown has one cleanup owner and shared completion. A second
  caller cannot report success merely because the request channel was closed.
  EOF cleanup, fallback termination and confirmed native-child cleanup are
  distinct outcomes; constructor failures preserve cleanup uncertainty.
- STPD has a separate v2 page projection, retaining ordered public selection
  roles and complete menu bindings. This does not change v1 artifacts, train a
  v2 model, or convert existing Human data into v2 data.

## Exact private installed package

| Field | Observed value |
|---|---|
| Host source revision | `35dcf11ac29495bfac76d34f7af50380f7a25e2a` |
| Host component tree | `c883ad6fab2143cc5dab09b98de3e9c6a77c0c2c` |
| Host source digest | `49498eb3f8942cadf1e0d06268ec483ce405ac9b42d0f284190d1189ecbb8aff` |
| Candidate version | `1.1.0-rc.20` |
| Temporary npm tarball SHA-256 | `f6ef479f1267c5afcfc4741d8e1ad52df8ddd713eb81123c827af46b2874a7d3` |
| Installed package content SHA-256 | `f56978fd016834e5eef2b5afd5c81b8ad94d5c005e0dc36d6b036396116512bd` |
| Managed patch SHA-256 | `bf3ac3d1aadee5ce556687745d2d64d37d0eea47e2b67904ca7c97b080d8b89e` |
| Managed executable SHA-256 | `dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93` |
| Managed executable MVID | `145c95e9-ace0-42b5-bb46-3fef292ac645` |
| Exact game assembly SHA-256 | `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4` |

The package was packed from committed source and installed into a private
temporary directory with scripts disabled. Its locked published SDK remains
`1.1.0-rc.1`; no new public npm or Release asset was published and no normal
consumer pin was changed. The Managed adapter implements the explicit v2
projection; this is not a claim that the released rc.1 SDK has a v2 decoder.

## Bounded installed-package native journey

The public Python client started an isolated Managed instance with Defect A0
and seed `M2H0ST20260929A`. Its identity reported `provenance_pass` with the
expected seed and exact build. The corrected run took 3.956 seconds:

| Request from the current complete menu | Effect domain | Result |
|---|---|---|
| Map `activate` | native input | applied / delivered |
| `select_card` | text menu | applied / no native delivery |
| `cancel_selection` | text menu | applied / no native delivery |
| `select_card` | text menu | applied / no native delivery |
| `select_target` | text menu | applied / no native delivery |
| Final `play` | native input | applied / delivered |

All six request/action bindings, returned successor observations and game
continuity were checked. The four text-only selections kept the same native
Snapshot ID. Final `play` matched the confirmation page's card and target;
its successor cleared the selection. Interactive pages passed the v2 research
projection without dropping candidates. Public close completed, package bytes
remained unchanged, and a subsequent process check found no remaining driver
or native process for this private canary.

The raw private report SHA-256 is
`8fa1a7bc6f3af61c5eb77ee50d72f01d0bcf8c26a7606f81684088fc6f4c18e7`.
An independent reader checked the recorded sequence and bindings. An earlier
test-script attempt incorrectly requested `navigate` instead of the actual
map catalog's `activate`; it failed before any submission, closed successfully,
and remains retained as failed evidence. Production code was not changed to
accommodate that script mistake.

## Checks and limits

On final Host source `35dcf11a`, `npm --prefix components/host-runtime run check`
completed successfully: 237 Node tests passed, 4 exact-game tests were skipped,
and 17 Python tests passed; syntax, docs, repository and temporary package
checks also passed. Skipped proprietary gates are not covered by this result.
The installed journey above is additional local evidence, not hosted CI.

Combined v1/v2 projection and memory-scorer short checks passed 55 tests on the
research integration before the application merge. Host identity/BOM and diff
checks passed after its exact source fields were aligned. Final candidate
checks and hosted CI must still be bound to the eventual PR head.

No Human recording, trained v2 policy, memory-benefit result, complete-game
coverage, arbitrary checkpoint restore, MCTS, Windows/Linux native execution
or production distribution is established here. Existing v1 model/data
identities and failed receipts remain unchanged.
