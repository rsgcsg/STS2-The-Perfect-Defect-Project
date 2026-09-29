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

### Application and HTTP journey

Application source `8ce8b23a301613e17bb114e062c0df339bb252c5` adds one
explicitly started fixed-seed session, not a general scheduler. It uses the
public Host client through a strategy-free shared loader. Each complete
before-page, offered action and returned result is stored once as an immutable
event; the small mutable journal and terminal report reference these events.
Saving a result cannot silently discard the offered page or grow every later
journal rewrite by the full size of all preceding results.

Independent review found and the author corrected journal-write failures that
could otherwise leave a live child behind. Focused tests distinguish no spawn,
no submission, native submission with a retained event, pending event writing,
confirmed cleanup and cleanup unknown. The exact app-only log at `8ce8b23a`
records 48 Python tests across `test_local_environment.py`,
`test_local_environment_http.py`, `test_host_runtime_client.py` and
`test_project_console.py`, plus 171 project-console JavaScript tests passing,
Ruff clean and mypy clean on five source files. These are separate from the
later combined-source 86-test receipt below.

At clean combined source `2412a42ab03abb4276d358bf44d227dad61b31f3`, a separate
temporary Workbench was configured through the ordinary CLI and opened with
its own state directory. Browser-cookie/Origin/CSRF-authenticated HTTP requests
performed the same six-step Managed v2 sequence, then Stop and report reads.
All six complete before-contexts and results read back through event endpoints;
the terminal report referenced those exact six events. Read-only refresh left
the stopped session unchanged. This took 6.339 seconds, without a throughput
claim. Report ID:
`3837be30e067e4f10fbfa415e345bd12b5cf660aec65b19e21cde9d9b0fed553`.
The local aggregate receipt SHA-256 is
`9defd05c71359fcf40bd6d5fa63f0c5a6a7e25e3b4b3e6c8baab7fe5cc16787f`.
The temporary Workbench subsequently reported `not_running`; no canary Host
process remained. The user's existing Workbench was not replaced for this test.

Safari exposed a separate shell-routing omission at that source: the new
navigation link fell back to the local home page. Therefore the HTTP journey
does not by itself qualify the visible environment entry. This finding is
retained. Shell follow-up `09430edfa363301fc72359a1a847a583e4d3ed00`
registers the local view, keeps cloud navigation separate and obtains local
browser CSRF from authenticated environment status. Its 207 shell/project
JavaScript tests and focused HTTP test passed. These tests do not replace
the subsequent browser verification of the integrated source.

At clean source `497c8746a8c6cf01fd8c181b2ac5647ffa86781c`, Safari's direct
environment link opened the correct page without cloud login. Clicking Start
created an isolated seeded instance; clicking the offered map action reached
combat. This verifies the visible entry and one native submission, not the
six-step sequence through the browser. Later automation could not reliably
address the rerendered window, so the public authenticated HTTP Stop was used.
The resulting report contains exactly one event:
`a97e88ab8ec2e7b0e2faecd28e446fdf044c272097634d465c257f11d4801367`.
At 13:44 UTC the session reported stopped, the temporary Workbench reported
not_running, and a process check found no matching private driver/native child.

The page remains an engineering entry: generic map-node labels and repeated
action-button captions make inspection harder than the native page. Complete
public context is retained, but the readable summary needs a separate
presentation improvement. This is not evidence of a polished all-scenario UI.

### Source checks

On final Host source `35dcf11a`, `npm --prefix components/host-runtime run check`
completed successfully: 237 Node tests passed, 4 exact-game tests were skipped,
and 17 Python tests passed; syntax, docs, repository and temporary package
checks also passed. Skipped proprietary gates are not covered by this result.
The installed journey above is additional local evidence, not hosted CI.

Combined v1/v2 projection and memory-scorer short checks passed 55 tests on the
research integration before the application merge. At combined `2412a42a`,
the six Python files covering environment service/HTTP, shared Host loading,
v1/v2 inputs and memory scoring passed 86 tests; the project console file
passed 171 JavaScript tests. Host identity/BOM and diff checks passed after
its exact source fields were aligned. Final candidate checks and hosted CI
must still be bound to the eventual PR head.

No Human recording, trained v2 policy, memory-benefit result, complete-game
coverage, arbitrary checkpoint restore, MCTS, Windows/Linux native execution
or production distribution is established here. Existing v1 model/data
identities and failed receipts remain unchanged.
