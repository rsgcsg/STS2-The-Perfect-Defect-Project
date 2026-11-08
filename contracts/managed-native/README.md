# managed-native-compact-1

Compact state output of the Managed native candidate (`components/host-runtime/experiments/managed-exact`)
for the P-REWRITE `fast_chain` (default off). It changes how the native process
reports a state, never what happened in the game or which fields a player sees.
Structure: `managed-native-compact-1.schema.json`. Executable rules and
counterexamples: `python/tests/test_prewrite_wp0_contracts.py` with `fixtures/`
(synthetic, no game data). Producer: WP1. Consumer: WP2 Host.

## Rules

1. **Opt-in.** The native keeps today's full output until the Host sends
   `configure_output`; the reply is a `full` frame. The old chain never sends it.
2. **One field set.** The inlined state contains exactly the fields today's full
   writer emits. New fields go only in `facts` and pass the Connector
   fair-projection review before any consumer reads them.
3. **Skeleton and table.** `skeleton` is today's tree with each object that has a
   `native_ref` at a known position replaced by `{"$obj": "<native_ref>#<view>"}`;
   bodies may hold placeholders too. Inlining every placeholder recursively gives
   the state. Every placeholder resolves; every table key is referenced (no orphans).
   The skeleton is complete in every frame (decision kind, stage, option lists and order).
4. **Delta by output bytes.** A key is upserted iff the sha256 of its body's exact
   written bytes differs from what was last sent, or it is new; it is removed iff no
   placeholder references it. Internal dirty flags never decide membership, so a
   hidden-field change produces no upsert. Downstream reads only the reconstructed
   state, never delta membership or digests.
5. **Per-step check.** `objects_root_sha256` covers the whole table after the frame,
   computed by the native from the current bodies. The Host compares it with the root
   of its carried digests (no body hashing on the hot path).
6. **Reconcile point.** Every `reconcile_interval` steps (default 64) the native
   writes a `reconcile` frame from a fresh full traversal: the whole table plus the
   `upserted`/`removed` a delta would have carried. Any carried key not upserted
   must keep its digest, and the key sets must match.
7. **Instances.** `episode_instance` is new for every run, in-process restart and
   save load; its first frame is `full` and carries `start` (`new_run` or
   `from_save` with the save digest: S4 hook). `seq` increases by one; `base_seq`
   names the held frame.
8. **Divergence fails closed.** Wrong base, seq gap, digest/root/reconcile mismatch,
   unresolved placeholder or orphan = `compact_state_divergence`: no action is sent
   on that state (`not_started`), the game is closed, originals are kept and this
   build's output stops (same rule as an R7 mismatch). No resync-and-continue and no
   switch back to the old chain.
9. **Text cache** (native internal). Key = model id, upgrade/enchant, every current
   DynamicVar value, language and formatting context; the value is what the game's
   own formatter returned. A miss calls the formatter; the cache never builds text.
10. **combat_end fact.** Written once, in the first frame after a combat ends; all
    fields are player-visible; used for the single-combat reward only, not model input.

## Equivalence (T1)

Baseline = today's unchanged full writer (the old native candidate), not the new
writer's full mode. For the same episode, `rename(reconstructed) == rename(old)`
byte for byte, where both sides are re-serialized compactly with key order kept
and `rename` maps every string value matching `^native-[1-9][0-9]*$` to `ref-<k>`
by first appearance across the whole episode, persistent across steps (a
bijection; refs are never deleted). `facts` and `diagnostics` are not compared.
