# Python Consumer

This package is a strategy-free external consumer of the same canonical Player
Environment used by Headless qualification. It does not define game legality,
rewards, tensors, or STPD policy.

```bash
python3 -m pip install -e consumers/python
sts2-headless-smoke --candidate .local/candidates/<exact-candidate>
```

`ManagedPlayerEnvironment` exposes `reset`, `observe`, state-bound `read`, and
exact BoundAction `step`. For the opt-in Managed text route it also exposes
`observe_text_menu()` and
`submit_text_menu(action_id, expected_snapshot_id, expected_game_continuity_id,
request_id=None)`. Observation returns one
`sts2.player-environment/text-menu-observation-context-1` object containing a
`text-menu-v1` `snapshot` and `game_continuity_id` from the same Host session.
Submit an advertised `snapshot.menu_actions.actions[].action_id` with that exact
snapshot and continuity ID. Reuse a mutation request ID only for an exact retry;
an `unknown` delivery must never be retried or switched to raw `step`.
A malformed or missing driver reply after a submission closes the consumer
process before any later mutation. A known delivered action whose successor
projection fails reports `delivered` with no successor and requires process
replacement.
A valid `unknown` text result is returned intact to the caller and then closes
the child before another request. Applied text-menu navigation has no native
delivery; only applied native input reports `delivered`.

The continuity ID is a Host-owned Managed episode epoch, rotated only after a
successful reset. A failed reset makes both raw and text routes unavailable
until a successful reset; close ends the session. A second reset, even with the
same seed, cannot reuse old text actions or request IDs. `FiniteActionView` is a
consumer projection over the complete raw action catalog. The sync and threaded
vector coordinators only manage independent environments; each still has one
Host-local binding/executor.

The current JSONL driver is a development transport for the managed candidate,
not a new gameplay protocol, Reference HTTP lease, native RunState proof, or
release support claim. The locked Connector SDK validates raw snapshots; this
candidate's locked SDK package does not yet export the public text observation
context decoder. The Host builds its existing context shape around its own real
text adapter and same-session snapshot without claiming that absent decoder ran.
