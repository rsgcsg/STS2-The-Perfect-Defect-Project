# Managed Exact Candidate

This directory admits one reproducible, exact-build experiment with separately
pinned platform baselines, derived from
[`wuhao21/sts2-cli`](https://github.com/wuhao21/sts2-cli). It is a candidate
Host, not the Reference Host and not a qualified trainer.

The committed authority is:

- `manifest.json`: upstream revision, primary macOS tuple, separately identified
  platform candidate tuples, required local files,
  semantic shims, and allowed/non-allowed claims;
- `patches/sts2-cli-d11aa88-v01110.patch`: the complete reviewable source
  delta from that immutable MIT-licensed upstream revision;
- repository tests and `tools/managed-exact.mjs`: preparation, identity,
  runtime, Player Environment, binding, reset, and capacity gates.

No game DLL, asset, localization payload, save, or generated candidate is
committed. `prepare` discovers the user's exact installation, copies required
files only into ignored `.local/`, refuses a modified runtime `sts2.dll`,
builds with .NET 9+, and records provenance.

```bash
npm run experiment:managed -- prepare
npm run experiment:managed -- audit --candidate .local/candidates/<candidate>
npm run experiment:managed -- native-gates --candidate .local/candidates/<candidate>
npm run experiment:managed -- pe-probe --candidate .local/candidates/<candidate> --episodes 3 --max-actions 600
npm run experiment:managed -- engine-lab --candidate .local/candidates/<candidate> --episodes 5
npm run experiment:managed -- pe-profile --candidate .local/candidates/<candidate> --profile qualification --episodes 5
npm run experiment:managed -- pe-profile --candidate .local/candidates/<candidate> --profile training --episodes 5
npm run experiment:managed -- pe-capacity --candidate .local/candidates/<candidate> --workers 1,2,4,8 --episodes 3 --max-actions 600
npm run experiment:managed -- scenarios list
npm run experiment:managed -- scenarios show --scenario-id managed-engineering-map-combat-six-v1
npm run experiment:managed -- repeatability --candidate .local/candidates/<candidate> --scenario scenario.json
npm run experiment:managed -- repeatability --candidate .local/candidates/<candidate> --scenario-id managed-engineering-map-combat-six-v1
```

`repeatability` accepts an existing `sts2.headless/scenario-1` JSON descriptor
with `start_interaction_kind` and starts two sequential, independent Managed
processes against the same exact candidate. Its report records each runtime
instance ID, game-reported seed, candidate artifact identity, common semantic
event comparison, and first divergence. The cooperative scenario deadline
defaults to 120 seconds and can be changed with `--scenario-timeout-ms`;
operation timeout is controlled by `--timeout-ms`. The driver checks remaining
time at asynchronous transport boundaries and before and after synchronous
reads.
Candidate process startup/runtime identity and shutdown retain their own finite
existing bounds, so `--scenario-timeout-ms` is not a strict end-to-end wall
clock. Incomplete evidence, unknown delivery, a trajectory without a delivered
action, or changed candidate/runtime identity cannot pass. This
tests same-candidate repeatability only; it does not establish native Connector
text-menu coverage or full-game determinism. Synthetic driver tests validate
the comparator and are not native runtime qualification.

The built-in `managed-engineering-map-combat-six-v1` entry is named `工程重复性`.
It is metadata for the previously measured same-seed map-to-combat six-action
engineering scenario, bound to its exact game, semantic target, Managed
candidate artifact, Ironclad character, `read_policy: none`, and fixed action
and discovery limits. The saved report records game/candidate identity and the
scenario, but not character; Ironclad is pinned because that CLI path defaulted
to Ironclad. This is not Defect-character evidence, model evaluation, or a
gameplay qualification. `scenarios list` and `scenarios show` only print
committed metadata and do not discover a game or inspect a candidate. Built-in
repeatability refuses unknown IDs, contradictory overrides, and game/target/
candidate identity mismatches before starting a Managed runtime. A custom
`--scenario scenario.json` remains supported and is never replaced by a
built-in fallback.

The built-in catalog is not a save library. Managed manifest entries such as
`load_save` and `write_continue_save` do not provide Host save listing, copying,
branching, replay, or restoration. Native run saves remain game-owned; profile
templates represent user-data configuration and are not run checkpoints.

`probe` and `capacity` exercise the upstream-shaped raw protocol and cannot
support Player Environment claims. `pe-probe` and `pe-capacity` use the strict
Connector SDK contract, snapshot-bound finite actions, Host-local operands,
idempotent request ledger, stale refusal, and unknown-no-retry behavior. They
remain partial until cross-Host differential and coverage gates pass.

The bounded Managed text-menu deck-upgrade page uses only the pinned
`CardSelectCmd.FromDeckForUpgrade(Player, CardSelectorPrefs)` test-selector
seam. A scoped exact invocation carries the game's full preferences to the
same synchronous selector callback, where the original card objects become
private operands. The page exposes one-card selection, a separate preview
confirmation, preview return, and selection cancellation only when the native
preferences permit it. An unbound or changed selector stays unsupported; the
Host never infers cancellation from `min_select` or enumerates card combinations.
This is a Headless input presentation for that command, not a native UI or
Human-evidence claim. Method/signature or callback-order drift requires a new
exact-build review and native differential probe.

The raw Managed combat action path and its Player Environment projection admit
play/end-turn mutations only while the exact current run, living player turn,
CombatManager play phase, enabled-action state, not-ready-to-end state, and
ActionQueueSynchronizer `PlayPhase` agree. A closed native window projects no
combat mutations and is reported as settling; a dependency-free contract test
checks each player-turn/action-queue blocker. This is source-level coverage,
not a live combat rejection receipt.

The `text-menu-v1` Managed adapter can expose the complete current combat leaf
catalog as direct semantic actions (`play`, `use`, `end_turn`, and supported
potion discard). Card and potion target arguments retain the current visible
enemy referent and the adapter dispatches the existing exact Player Environment
binding. MPE admits only its supported native target bindings; AnyAlly, potion
target types outside the exact native binding set, non-creature target selection
without a published target referent, and unknown target kinds make the entire
MPE combat catalog unavailable. The text adapter admits only a structurally
complete MPE combat catalog. The menu stays at its existing root;
it does not invent a card cursor, held-card, target-confirm, potion-popup, or
information-read action. A direct semantic `play(card, target)` is not the
Connector Live Godot sequence of holding a card and confirming a target, so
cross-Host trajectory equality is not claimed. This is source/test coverage,
not a native runtime, cross-Host, or full-game qualification.

`engine-lab` measures the exact in-process game-owned semantic loop without a
Player Environment or consumer. `pe-profile` separates training overhead from
strict qualification overhead. `pe-sharded-capacity` is retained as an
experimental topology command, but current evidence rejects one Node
supervisor per runtime as the default because it does not improve aggregate
throughput and materially increases memory. None of these performance commands
promotes semantic evidence.

Privileged commands such as `enter_room` are scenario controls. They are used
only by named targeted gates and never enter Player Environment actions.
Passing those gates is not a fair-player journey.

When changing the candidate:

1. edit a disposable checkout at the manifest's exact upstream revision;
2. run its C# build and targeted runtime gates;
3. regenerate the complete patch with
   `git diff --binary --no-ext-diff`;
4. run `prepare` into a new directory;
5. verify the prepared source patch, unmodified game SHA, artifact SHA/MVID,
   and runtime identity before collecting evidence.

Never transfer evidence across a platform baseline, changed patch, artifact, game assembly,
runtime instance, or canonical adapter source.
