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
3. **Skeleton and table.** `skeleton` is today's tree with each tabled object replaced
   in place by `{"$obj": "<native_ref>#<view>"}`; bodies may hold placeholders too.
   Inlining every placeholder recursively gives the state. Every table key is reachable
   from the skeleton, every placeholder resolves and placeholders never form a cycle.
   The skeleton is complete in every frame (decision kind, stage, option lists and
   order, piles).
4. **Only ref-carrying objects are tabled (fair information).** An object goes into
   the table only if the body today's writer emits at that position carries a
   `native_ref` field, and that field must equal the ref in the key. Draw, discard and
   exhaust piles stay inline in the skeleton exactly as today's `FairPileProjection`
   writes them: no `native_ref`, no index, draw pile ordered by visible content only.
   A pile is structural, not a view name: on the hot path any table placeholder inside
   a pile container (a skeleton `draw_pile`/`discard_pile`/`exhaust_pile`, or any object
   with `ordering_semantics`) diverges, an object with `ordering_semantics` is itself
   never a table body (under any key or view), and view names containing pile, draw, discard
   or exhaust are refused by the schema. The wire never says which instance is in a
   pile.
5. **Delta by output bytes.** A key is upserted iff the sha256 of its body's emitted
   bytes differs from what was last sent for that key, or nothing is on record for it;
   it is removed iff it is no longer reachable. Removing a key deletes its last-sent
   record on both sides (writer and Host), so an object that becomes reachable again
   is always re-sent in full. Internal dirty flags never decide membership, so a
   hidden-field change produces no upsert. Downstream reads only the reconstructed
   state, never delta membership or digests.
6. **Digest bytes.** A body digest is the sha256 of the exact UTF-8 byte slice of that
   body's JSON value inside the emitted line, with whatever escaping and number format
   the writer uses; it is never computed over a re-serialisation. The root
   `objects_root_sha256` is the sha256 of the lines `"<key>\t<digest>\n"` of the whole
   table after the frame, keys in byte order; the native computes it from its current
   bodies.
7. **Hot path and sampled checks.** On every frame the Host checks seq and base,
   key/`native_ref` binding, no placeholder inside a pile, reachability and cycles,
   that the reconcile point is not
   overdue, and the root over its carried digests; it does not re-hash bodies. Schema
   validation and per-body digests over the raw bytes run on the sampled path (about 1%
   of steps chosen before the game, every R7 game). The reference test runs both on
   every frame.
8. **Reconcile point.** `reconcile_interval` counts frames (state outputs) of one
   episode instance. A `reconcile` frame comes no later than that many frames after the
   last `full` or `reconcile` frame (the native may write one earlier). It is built
   from a fresh full traversal: the whole table plus the `upserted`/`removed` a delta
   would have carried. Any carried key not upserted must keep its digest, and the key
   sets must match.
9. **Instances.** `episode_instance` is new for every run, in-process restart and
   save load; its first frame is `full`, has `seq` 0 and carries `start` (`new_run` or
   `from_save` with the save digest: S4 hook). `seq` increases by one; `base_seq`
   names the held frame. A delta removes only held keys and never removes and upserts
   the same key; a reconcile's `removed` keys are held and absent, its `upserted` keys
   are present with a changed digest.
10. **Divergence fails closed.** Wrong base, seq gap, digest/root/reconcile mismatch,
    unresolved placeholder, cycle, orphan, key/ref mismatch, placeholder in a pile,
    tabled pile, overdue reconcile, or writer bookkeeping that breaks rules 5 and 9
    (removed key not held, removed and upserted, unchanged upsert, bad full `seq`/`start`) =
    `compact_state_divergence`: no action is sent on that state (`not_started`), the
    game is closed, originals are kept and this build's output stops (same rule as an
    R7 mismatch). No resync-and-continue and no switch back to the old chain.
11. **Text cache** (native internal). Key = model id, upgrade/enchant, every current
    DynamicVar value, language and formatting context; the value is what the game's
    own formatter returned. A miss calls the formatter; the cache never builds text.
12. **combat_end fact.** Written once, in the first frame after a combat ends; all
    fields are player-visible; `encounter_id` is the encounter that just ended (all its
    monsters already shown), never a pre-rolled future one; `room_kind: event` is a
    combat started by an event. Used for the single-combat reward only, not model input.

## Equivalence (T1)

Baseline = today's unchanged full writer (the old native candidate), not the new
writer's full mode. For the same episode, `rename(reconstructed) == rename(old)`
byte for byte, where both sides are re-serialized compactly with key order kept
and `rename` maps every string value matching `^native-[1-9][0-9]*$` to `ref-<k>`
by first appearance across the whole episode, persistent across steps (a
bijection; refs are never deleted). `facts` and `diagnostics` are not compared.
