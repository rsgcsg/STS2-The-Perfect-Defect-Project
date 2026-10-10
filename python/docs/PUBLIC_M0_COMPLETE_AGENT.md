# Optional Public M0 card-reward Agent program

This contract owns one optional strategy composition over the existing Public M0
scorer and complete current generic Snapshot1 catalog. It changes final choice,
not weights, model inputs, raw neural scores, action availability or Connector
authority. The original model and baseline Agent remain selectable.

The reviewed motivation is the recorded M0 outer Claim → inner alternative →
same pending outer card reward cycle. Selecting a card instead is an optional
strategy comparison. This is not a defect repair, a learned-quality result, a
claim about why the model preferred Skip, or causal evidence for a deck-Read
experiment. Actual reward exit and subsequent game progress require a separate
bounded real-game comparison at the same selected seed and exact candidate.

## Versioned composition

With no program, config-v1/adapter1.0.0 TaskNone and
config-v2/adapter1.1.0 ready-summary behavior retain their existing meaning.
Common registration still defaults to the existing summary Task with no program.
Only an explicit `program` argument creates config-v3/adapter1.2.0 with exact
mirrored `program_spec` in the config and `adapter_config.public_m0`:

```json
{
  "schema": "stpd/public-m0-agent-program-v1",
  "id": "card-reward-take-v1",
  "input_schema": "sts2.player-environment/snapshot-1",
  "scores": "nonterminal-complete-raw-neural-v1",
  "selection": "NN-best-current-selectable-card-v1",
  "zero_eligible": "neural-argmax-v1"
}
```

Config-v3 includes `runtime_device`, `program_spec` and a mandatory nullable
`task_spec`; the same task value is mirrored in its manifest. Default task is the
unchanged ready standard summary. Explicit TaskNone remains possible without
relabeling either original baseline schema. Unknown, missing, cross-version or
non-mirrored descriptors reject before model use. Program and task matching
create distinct Agent selections; no old selection is mutated or adopted.
Visible catalog/label/version fields identify the optional complete Agent and
its raw-score/final-chooser meaning. No new service or GUI route is introduced.

## Current typed card qualification and final choice

The complete public projection, original full ordered C, digest/count and whole
support checks precede program use. The scorer still consumes exactly the same
current Snapshot and scores every original candidate once. Every returned
nonterminal neural score retains its exact value and original position.

The program applies only to ordinary `card_reward_selection`, stage `ready`,
content schema `sts2.player-environment/surface/card_reward_selection-1`, matching
surface kind and `reward_flow`/`card_reward` context. Its selectable IDs come from
the current public `selectable_card_entity_ids`, not a legality calculation.
Current public cards and visible typed `card` referents must agree uniquely.
Each selectable card must have exactly one current C row with verb `select`,
the matching subject referent and empty arguments. Duplicate, missing, mismatched
or ambiguous claimed typed facts reject the whole decision. Opaque IDs join
public facts and C; they never become consumer-created native operands.

When at least one such current candidate exists, final `selected_index` is the
highest original neural score among those candidates, with original C order for
ties. Alternatives such as Skip or Reroll remain in C and retain their raw neural
scores. No catalog filtering, masking, score boost, reordering, reroll, action
rewrite or native coordinate/index mutation occurs. The complete Agent's final
chooser is explicitly declared; the choice is not presented as the original
model's unrestricted argmax.

Legitimate zero-selectable-card/alternatives-only pages use the explicitly
declared unchanged neural argmax. An inconsistent nonempty selectable claim is
an error, not zero eligibility. This rule therefore does not promise to remove
every possible reward loop or preserve the strategy value of skipping poor cards.
All other interactions use unchanged neural argmax. Ready-summary Task
abstention remains unchanged, including its declared program-zero placeholders;
these terminal placeholders are not neural scores.

There is no history, proposed-action bookkeeping, delivery inference, additional
Read, hidden state, retry or exploration. Runtime and Connector retain controller,
complete catalog, execute-time revalidation, Receipt and successor ownership.
Port1 and its existing three-field output remain unchanged; Runtime already
accepts a valid selected index without requiring it to equal score argmax.

## Identity, validation and evidence limits

The conservative Public M0 `python-owner-source-and-lock-v1` digest remains in
force. Its scope improvement is deferred to an independent increment; this change
does not weaken legacy checks or claim old code pins still match new source.
Public M2's declared import closure includes shared M0 installation source, so a
changed shared file legitimately changes that consumer code pin as well. Its
neural nine-file identity and model bytes remain separate and unchanged.

Faithful source checks cover complete raw-score preservation, card preference
despite higher Skip/Reroll scores, ID/order changes, current selectability,
ambiguous/malformed rejection, legitimate zero-card neural argmax, unchanged
baseline/default and summary, strict version/descriptor binding, and common
explicit registration plus actual Port1 producer/reader bytes. Synthetic tests
do not load original weights, register a real model, launch a game or qualify
actual reward exit. Root separately owns independent review, final aggregate
gates, package/identity reconciliation and bounded actual same-seed comparison.
