# Non-admitted terminal observation, 2026-09-28

## Reproduced installed behavior

The unchanged local Workbench (`7a7312b504526e0edf5086f1a9f816b84939abdf`)
loaded the same small-B registration and explicitly started a short Auto attempt
while the real game was already on its loss screen. Installed Policy Runtime
remained rc.10, source `60d97f7235a4cf8ce4618362c0eca6802eca0e6b`.
The registered manifest excludes `game_over`; native post-game summary controls
are not part of that model's authorized in-run task.

The typed page was `game_over / intro`, `interactive`, with one complete native
summary-navigation choice. The native screen already supplied `result=loss`;
summary score/floor fields were not yet ready. This is not a combat page merely
because a UI button remains enabled.

Run `run-8ed34cbd-84bf-4e5c-8b77-a44d3311f4ef` returned to Human/released with
zero policy calls and zero submissions. Explicit Stop sealed four events:
`mode_changed`, `environment_admitted`, `handoff_to_human`, `stopped`.
The handoff reason was `auto_surface_not_admitted`. Workbench verification
reported pass, but its report displayed no observed terminal page: no full
snapshot had been recorded at the non-admission boundary. Events SHA-256:
`57d189927b0256df0df2029769d697c55437ffebdd1c895fe911914622788189`.
No game input, new training or Human recording occurred in this attempt.
It does not establish model play from run start, victory or policy quality.

## Owning boundary

Connector owns the typed public page and its current legal controls. Native
`GameOverSurfaceReader` separates intro, animation and summary stages;
`NativeUiActionRuntime` binds advance-summary and return to their exact current
native screen. Clicking Continue delivers UI input; it does not prove that the
summary animation finished.

Runtime must stay generic: record the exact non-settling text observation and
existing admission reason, after profile/environment checks. A rejected page is
neither a policy decision nor a causal successor. Evidence validates that event
and its environment; Workbench can report the observed outcome. STPD must not
manufacture a positive action label from this diagnostic observation.

The existing support checks and Human release are correct and remain unchanged.
No hidden auto-click, game-over-specific Runtime legality or model-choice filter
is needed. A simulator Host can provide the same public profile with its own
identity and authoritative bindings; this native check grants it no qualification.

## Compatibility

The old strict Evidence reader does not understand a new observation event.
Any new producer must be paired with the updated verifier. Old sealed runs stay
unchanged and retain their missing observation; the new reader must still accept
them. Source/package checks are distinct from a future local activation.

## Candidate source and checks

- Runtime rc.11 path source: `f0bc40f15b19ba9d6f23876a466261bca6162f0d`,
  tree `446bc41658aa1f985559c81e91da23b6093a1df5`,
  digest `12ab8e08811b144fc599039a8e3873821937cb1fba28e6de9b91b222bc384d5a`.
- Evidence rc.18 path source: `078539d343e2996acc78eac220d61691231e8483`,
  tree `71b5910356e22c184e7c82d23e94e681f237c678`,
  digest `7929e2f86b709a9810a61479b4a35aed041a3894b0916c6fa4ba5db7f5065fb1`.
- The Python consumer pins that exact Evidence Git source in pyproject, lock and
  developer combination. Runtime's historical rc.6 consumer configuration and all
  installed artifacts are unchanged. New producers require the new reader.

On 2026-09-28, `npm --prefix components/policy-runtime run check` passed at
`f0bc40f`: typecheck, 126 tests, build, deterministic package and temporary-installed
CPU/CLI smoke. The package smoke covers existing recovery/text-menu boundaries;
new-event recording is covered by the source Runtime tests, not claimed as a
separate installed native run. Evidence's component check passed all 146 tests at
`078539d`; its source bytes are unchanged in the final combination.

The two focused Python regressions passed at `492b2ae` using Python 3.11.15 and
explicit candidate Evidence/application import paths in an isolated ad hoc uv
environment: sealed terminal reporting and verified import with zero training
rows. This is not a locked installed-consumer or Windows result. Scoped Ruff
passed after correcting three imports and a redundant branch. Final locked
consumer and hosted results belong to the PR checks/closeout receipt.

A parent-production regression failed because the new observation event was
absent. During integration, typecheck caught a duplicate crypto import introduced
by conflict resolution; `f0bc40f` removes it and the full component check above
passed. Neither failure is relabelled as success. Two unchanged old sealed runs
(the four-event terminal attempt and prior 86-event bounded model attempt) also
passed the new verifier, read-only.

The event's reason is a Runtime diagnostic, not Evidence recomputation of policy
eligibility. Evidence verifies shape, environment and event restrictions; it grants
no gameplay permission. Regressions retain zero scoring/submission for unsupported
pages, no events for settling or failed environment identity, and fail-closed
behavior on evidence write failure. Importing a verified observation-only run
archives its evidence and creates no supervised decision.

This repair does not establish from-start whole-game model execution, all-scene
native qualification, Human labels, policy quality or a fast simulator Host.
