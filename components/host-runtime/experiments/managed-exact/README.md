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
npm run experiment:managed -- save-roundtrip --candidate .local/candidates/<candidate>
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

The installed package's `audit` stages the reviewed assembly-fingerprint source
in a private temporary build directory; it does not write .NET `bin`/`obj`
under the installed package. The STPD Host consumer activation imports its
Python client with bytecode writes suppressed only for that import, so repeated
exact-package validation sees the same bytes.
Other Managed commands that write `.local/` reports and `source-audit` output
still require an explicitly suitable external working/output root when used
from an immutable installation. This is an audit/consumer-startup boundary,
not a claim that every Host CLI command is read-only.

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

`save-roundtrip` is a private qualification probe, not a public restore API or
save library. It starts Defect A0 with a fixed seed (default
`M2H0ST20260929A`) and refuses any starting page other than a complete, quiescent
map. It uses the native `write_continue_save` only at that boundary and requires
the native result to identify `MapRoom`; the candidate's non-map save path does
not establish a pre-room restore contract and is never exercised. The probe
creates its own private temporary directory, accepts no player save path, and
attempts to remove its original save and two independent restore copies on
completion or failure; cleanup failure also fails the probe and is reported
separately from any primary failure. The eight-MiB file bound and every
request/shutdown are finite.

Three exact, distinct runtime processes run sequentially: the seeded reference
writes the save and takes one current bound map action; two fresh processes each
load their own copy and take the same semantic choice using their own fresh
binding. Existing `canonical-player-decision-1` comparison covers the public
Snapshot and complete finite action catalog before and after that one action.
Its established referent normalization permits different opaque process-local
IDs without arbitrary raw-field ignore filters. Candidate identity, quiescence,
save digest and byte size, semantic equality, and clean exits must all pass.
Saving is followed by a new native map read and run-position check; the raw
protocol has no full-page observe command, so this is not a second complete
post-save page observation. Two independent load comparisons check the full
public projection. Native errors, unknown transport outcomes or drift end the
probe without retry. Reports contain identities, hashes and verdicts, never
save bytes or raw pages. The synthetic map-to-map tests exercise the real
projection and comparator but are source/test evidence only; an
exact-game invocation must separately qualify the candidate. Passing does not
prove hidden-state/RNG equivalence, Read payload equality, arbitrary scene or
mid-combat restore, long-horizon replay, shipped-host parity, or a new public
Receipt/Commit contract. No save or restore operation reaches a model menu.

`probe` and `capacity` exercise the upstream-shaped raw protocol and cannot
support Player Environment claims. `pe-probe` and `pe-capacity` use the strict
Connector SDK contract, snapshot-bound finite actions, Host-local operands,
idempotent request ledger, stale refusal, and unknown-no-retry behavior. They
remain partial until cross-Host differential and coverage gates pass.

The combat potion catalog binds `AnyPlayer` through the exact current Player
object and its native Creature in the existing single-player scope. The run
must contain exactly one Player, identical to the current player and potion
owner. Native `PotionModel.IsValidTarget` supplies the target domain independently
of current usability; an unusable held potion keeps its visible facts, valid
targets and any discard action without disabling other supported combat actions.
The public use action targets the existing opaque player referent. Its private
operand is the exact Player identity, never an index or an implicit Self/null
target. Delivery rechecks the current domain, potion slot/object, usability and
native target validity before `EnqueueManualUse`; the native `UsePotionAction`
retains its later target validation. Missing, stale or foreign targets reject;
multiple-player domains and other unsupported target families remain unavailable.
This scoped binding does not implement every effect's child interaction.
Colorless Potion may now open the bounded exact choose-card presentation when its
complete native rendering/context is admitted; other generated cards can remain
unsupported with explicit rendering/detail gaps. Hologram and Seeker Strike use
the exact combat-pile adapter. Deck-enchantment and untyped selectors remain
unsupported. See [selector context](../../docs/MANAGED_SELECTOR_CONTEXT.md).
A delivered parent use is not a claim that its whole effect completed.
Portable helper/projection tests and an exact build establish their stated
source/test/build scope, not native UI parity, a gameplay journey or qualification.

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

The separate generic deck-card text page is admitted only for the pinned
`CardSelectCmd.FromDeckGeneric` callback. A scoped invocation carries its
actual player, preferences, filter and selector into `ICardSelector`, whose
published callback otherwise loses the preferences. The Host presents the
ordered original card objects as sequential select/deselect inputs, preview,
preview return, native-permitted cancellation and confirmation. These steps
change Host-local tentative selection state; only callback completion resumes
the game-owned command and its native effects. Execute-time input rechecks
the exact selector, original card identity, current deck membership and native
filter. The older unscoped `card_select` page remains visible but unavailable
to the text adapter; it does not enumerate card combinations or gain inferred
cancel rights. The purpose-specific deck-upgrade selector retains its own
separate binding and behavior.

The Managed shop text page admits only a finite complete current catalog with
exact entry identities, prices, stock and native actionability facts. For card
removal, the action binds both merchant room and removal entry; a deferred
callback rechecks both, stock and gold before invoking the native purchase.
The game's `OneOffSynchronizer.DoMerchantCardRemoval` still owns gold loss and
deck mutation. When that purchase returns true, the Managed Host calls the
captured entry's `SetUsed()` in place of the absent
`NMerchantCardRemoval.OnCardRemovalUsed()` UI callback; cancellation does not.
The exact private raw-route regression checks cancellation, one confirmed
purchase, stale and missing bindings, and duplicate rejection. This is a
source/build and targeted scenario-control result, not a fair-player journey,
native UI parity, Human evidence or all-selector coverage.
The raw Host `leave_room` action in a merchant room delegates to the same
`DoLeaveShop` owner as `leave_shop`: it requires the exact current room and
refuses to open the map while a purchase operation or card selector is pending.
The exact raw-route regression covers selection and preview, including missing,
stale and current room references, then admits the completed shop exit.

The Managed text adapter preserves a complete game-owned `game_over` observation
as an observed terminal page with zero actions and the exact boolean victory
fact. A missing or non-boolean terminal fact is unsupported; the Host does not
convert it into defeat. An empty nonterminal or unknown page remains
unavailable. This mapping does not decide victory or add a native Commit.

The event-option text page is admitted only when the current native event room,
event instance, and every ordered option have exact private identities and
lock facts. Locked options remain visible without an executable action. The
native choice rechecks room, event, option, index, and unlocked state before
calling the game's `Chosen()` method, including inside the delayed callback;
that callback uses the captured validated option object. A callback failure
after dispatch remains an unknown delivery without automatic retry. Event
completion uses the existing native
proceed/map presentation branch; a same-index replacement or missing identity
does not fall back to an ordinal-only mutation. This is a Managed source/build
candidate, not native UI parity or a full-run qualification.

The treasure text page exposes each current stage separately: open chest,
select one of the complete ordered native relic catalog or skip when the native
single-player fact permits it, then leave the completed room. A missing relic,
duplicate identity, incomplete catalog, or unknown skip fact makes selection
unavailable while retaining the visible rows. Opening rechecks the exact closed
room inside the deferred callback before game-owned reward commands. Selection
requires both observed room and relic identities and still awards through the
native treasure synchronizer. A callback error after dispatch remains unknown
with no retry. This is source/build evidence for the Managed text path, not
native UI parity, a complete journey, or Human qualification.
The raw Host `leave_room` path now requires that exact treasure room and rejects
exit until the native chest and relic stages have completed, including while a
deferred operation or selection remains pending. Event and other room exits retain
their existing contract. The proprietary exact-candidate regression exercises the
raw action path through pre-open, pending relic, and completed stages.

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

The same bounded adapter also admits complete current combat reward, card
reward, and reward-completion leaves. Combat reward generation follows the
native no-reward encounter flag and `RewardsSet.Offer`; a claimed card reward
continues through the game's `CardReward.OnSelect` callback. That callback's
current `CardCreationResult` options and `CardRewardAlternative` options retain
their original order and private native bindings. Skip is available only when
the callback actually offers it; reroll, sacrifice, and later alternatives
return the exact callback object for the game to handle. Missing or changed
choice identity makes the entire card-reward catalog unavailable. Proceed
opens native map presentation, and the subsequent native map vote owns room
exit and skipping any remaining combat rewards. The adapter does not claim
native UI parity or a full-run qualification.
The manifest's Windows tuple remains historical evidence for the previous patch;
the changed source has no Windows build admission.

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

## Fair public projection and private bindings

Managed keeps native object tokens and raw state digests inside its execution
projection. Public Snapshot/interaction IDs bind the runtime and the existing
profile's monotonically advancing projection version; hidden draw order, RNG,
object-allocation counters and raw-state hashes are not identifier inputs.
`observe` and Read reuse that frozen version. Mount/reset and a returned action
successor create a version; map enrichment happens inside the same projection.
This is the existing Snapshot-version boundary, not an implementation or
qualification of a new P1 semantic-occurrence contract.

A session assigns entity aliases only when objects first appear in the public
projection, using the runtime, first public projection version and public
allocation ordinal. Native tokens are private map keys and never alias bytes
or digest inputs. Cards/player/enemies/relics/potions retain their aliases across the existing
hand, deck, action-target and persistent associations that carry private object
identity, and across later versions. Pile Read instances remain snapshot-local
public slots, not a new cross-pile native-identity contract. A new mount resets
the map without reusing prior-run identities. Equal-looking cards remain distinct
when the native action binds distinct instances. Enemy `combat_id` is now an
opaque public alias matching its `entity_id`, not the native allocation number;
consumers must not parse it as a native counter. Action arguments still name
the corresponding public referents. Exact native objects and execute-time
checks remain private and authoritative; public stability does not authorize
stale requests or retry an unknown delivery.

`RunSimulator.VisiblePile` first builds the existing visible card facts. Its
pure `FairPileProjection` helper strips non-visible DTO fields and canonicalizes
only Draw before returning the raw pile view. No original Draw index or native
object token is present in that read-only view. MPE independently projects its
allowed visible-card fields before canonicalizing Draw and assigning public
read-instance IDs, so private inputs cannot enter a sort key, tie-break or hash.
Equal visible occurrences retain their multiplicity and separate public read
instances. Visible card text, cost, upgrade/enchantment facts already supported
by MPE remain observable. Discard/exhaust keep their existing presented order
and current contract; this fix does not select a new P1 ordering policy or
add missing card-detail/revealed-order capabilities.

The engineering probe also treats these aliases only as reference lookup keys.
Its semantic selection key excludes combat/entity/referent and envelope IDs;
it never interprets an opaque combat alias as a number or ordering value.
Fully equal public semantic actions remain distinct BoundActions and use the
published complete catalog's relative order for a final tie. Their exact private
bindings remain separate. This rule depends on that order; it does not establish
arbitrary-order invariance, unique correspondence for equal-valued entities
across Hosts, or a new canonical-decision comparison contract.

The regression compares the complete public Snapshot plus every materialized
Read, including all identities and serialized digests, at a fixed public
projection version while changing only hidden Draw permutation/native counters.
It also checks already allocated aliases, visible multiplicity/details, real
public changes, cross-view/next-version object links, reset identity, private
operand binding and stale refusal. The compiled helper's actual JSON is fed
through MPE and its strict SDK validation with synthetic card DTOs. That check
mocks the CardModel/rendering source and native execution; exact compilation
and later runtime qualification remain separate. No new P1 macro, selector,
training, native-UI, full-game or cross-Host qualification follows.

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


## Prepared initial profile

Manager reset/start may carry one `initial_progress` snapshot from the fixed
Reference `normal-sp-all-unlocked-v1` preparation. `ManagedInitialProgress` loads
that exact JSON/SHA through the native memory save store and `InitProgressData`
before the original Standard player/run initializer. Native run identity records
its input SHA and actual frozen UnlockState comparison. There is no arbitrary
save path or in-run import command. Managed presentation TestMode is retained.
`initial-progress-tests` exercises the native owner without Launch or game input.

Fresh P1 suites require the exact Reference prepared `initial_progress` object
(`recipe`, `progress_json`, `progress_sha256`) through
`tools/p1-suite.mjs --initial-progress FILE`. Recipe/SHA stay in suite provenance;
progress JSON is private setup data, never a P1 action or label. The native mount
verifies input SHA and actual run UnlockState before publishing the episode.

The same prepared progress exposed a real initial-scene divergence: seed
SWNBPYW69BAC starts Underdocks in the native lobby, while the old Managed
initializer forced the default Overgrowth act list. StartRun now calls native
ActModel.GetRandomList with the lobby's seeded `act_selection` RNG and the actual
UnlockState. This preserves the Standard lobby default random-act selection.

The current full candidate built from integration f434 uses patch 43e223e5 and artifact
0426384c / MVID1115859e-f311-4e2e-b103-0b3c91168250. The manifest retains the
previous8397 and a70 builds in history. Source/build inspection accepts only the
new measured identity. Installed product, Reads and strict cross-Host gameplay
qualification remain separate actual gates.

The exact `CardSelectCmd.FromHand` / `FromHandForUpgrade` callback now retains
its original player/run/combat domain, preferences, filter, offered card objects,
selector and pending Task. The private presentation stages the pinned
`NPlayerHand` select/replace/deselect transitions and requires the original
explicit confirmation. Only then does it return the original selected objects
to the native parent, which owns discard/exhaust/upgrade effects and RNG. It
publishes the existing `combat_hand_card_selection` surface, current HUD and
card details; it does not repurpose the one-card pile selector. Native zero and
forced-one paths still finish before this callback. Unbound callbacks,
unsupported presentation preferences, stale ownership or incomplete rendering
expose no complete catalog. Component checks cover native callbacks and original
object identity; full replay qualification remains bound to its saved report.

### Captured personal initial progress (M3)

Manager-only `captured-native-progress-v1` accepts exact UTF-8 sealed initial
progress, verified by SHA, native parse/reload and the native frozen UnlockState.
It writes only the TestMode memory store, never a personal profile. The existing
`normal-sp-all-unlocked-v1` recipe keeps its complete-baseline assertions. The
stdio driver forwards explicit `--ascension 0..10`; default remains 0. These
private launch inputs never become Agent/P1 observation fields.

Use the [recording diagnostic SOP](../../../../python/docs/MANAGED_RECORDING_REPLAY.zh-CN.md)
for exact-original replay, optional idle Q dispatch and the Reference coverage
plan. A first-combat prefix or matching graph does not qualify a whole game.
