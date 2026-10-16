# ADR-0017: P1 public information and forward decision profile

Status: Proposed implementation contract; user direction selected 2026-10-15.
Independent contract review and implementation qualification are pending.

## Problem and exact grounding

The user selected one P1 main line with currently reachable public information
attached to decisions, including explicitly offered card-reward groups, fewer
mechanical navigation decisions, and forward progress. The earlier candidate
that retained every native browsing step is superseded for this profile.
Hidden draw order remains forbidden. Payment, chest opening, card/potion use or
other effectful generation must occur before its new information is offered.

Native UI is not a sufficient definition of the new decision boundary. Ordinary
CardReward Skip ends selection without completing the reward. The native
RewardsSet skip affects all unselected rewards; LinkedRewardSet can dispose of
siblings when one is selected. Blindly translating "skip this group" into
"skip the whole set", or flattening linked rewards, changes the wrong choice.
Existing Read/recording/canonicalization helpers also lack complete P1 coverage.

## Decision

Adopt the [P1 contract](../protocols/P1.zh-CN.md) and its
[Host/wire mapping](../protocols/P1_HOST_AND_WIRE.zh-CN.md) as the implementation
candidate for this selected direction. P1 is a declared decision abstraction,
not a byte-for-byte model of UI navigation or a new game-rules engine.

The profile attaches all currently entitled public facts required by its scene,
retains strategic alternatives and information-bearing effect boundaries, and
may lower one semantic intention into several exactly bound native operations.
Mandatory navigation within an already chosen intention can be automatic.
Singleton catalog length alone never authorizes a strategic purchase, discard,
reward refusal, EndTurn or room exit.
A declared forced-input policy may also execute an unchosen mandatory singleton
when the complete meaningful profile relation and native obligation prove that
no cancel, exit, timing or resource-allocation choice remains. It records its
automatic origin and context; it does not create a Human strategy label.
Decline admission must preserve a native completion path and any mandatory
reward obligations. A skippable card-selection screen is not evidence that its
containing RewardsSet permits skipping; DisallowSkipping is an explicit case.

The profile may make a user's semantic refusal irrevocable within the current
reward occurrence. Such a commitment is explicit protocol state visible in the
P1 observation and durably associated with the operation; it is not a hidden
visit counter or an assertion that native OnSkipped already ran. Only existing
qualified native entry points perform game effects. Mechanisms whose deferred
native disposal changes later opportunities require explicit treatment, not
the generic ordinary-reward mapping.

Information sufficiency and reduced navigation loops are engineering targets.
There is no assertion of a strictly Markov state, a universally acyclic game, or
a policy that cannot choose an unproductive sequence. Hidden state remains
hidden; memory and strategy are properties of a declared Agent.

## Owning fact and authority

- STS2 owns rules, effects, RNG, native legality and Commit.
- Connector/declared Host mapping owns the P1 profile's complete semantic
  catalog, protocol commitments, information eligibility, lowering and native
  revalidation. The underlying native/legacy catalog retains its original scope.
- Runtime enforces operation ownership, budgets, cancellation, unknown fencing
  and original evidence. It does not independently filter actions or decide what
  the player should buy, skip or play.
- Host Runtime owns instances, exact identity, preparation and cleanup.
- Annotator/Evidence preserve source facts and lowering events; they do not
  fabricate Human macro choices or silently rewrite earlier recordings.
- Research owns Input/Target, grouping, losses, memory and evaluation. The
  ten-part training proposal is separate and does not authorize a fit.

"Complete catalog" is evaluated for the explicitly selected profile. P1's
intentional abstraction must be reviewed and advertised as such. A consumer may
not silently prune a legacy/native catalog and call the result complete.
Every admitted P1 operation must have a faithful native/protocol realization;
the converse need not hold for native navigation intentionally removed by P1.
Both Hosts must implement the same P1 abstraction and observable guarantees.

## Alternatives considered

Keeping every native browse/cancel step remains useful for legacy profiles and
Human evidence, but is not the new selected default. Silently adding heuristic
skip/take rules to a scorer or Runtime would hide strategy outside the Agent and
is rejected. Recreating game rules or editing game state to force forward
progress is rejected. A second recording, control or training platform is not
needed; extend the existing owners with versioned contracts.

## Evidence and falsification

Required examples include independent versus linked reward groups, skip one
group without losing gold/relics, a later relic modifying remaining options,
cancelable versus committed selections, zero-choice and automatic selection
completion, stale between lowering steps, delivery unknown after a prefix,
lost protocol ledger, same-body revisit versus retransmission, duplicate-card
relations, and all required Read instances from one capture.

Validation progresses through profile conformance, exact native mechanisms,
same-semantic-action cross-Host traces and complete-denominator suites. Human
traces can supply actions and complex scenarios if their captured facts suffice.
A partial historical trace is not a restorable full game state. Reference
native assertions must accompany shared projection comparisons so that two
identical projection bugs do not self-certify.

## Consequences and tradeoffs

P1 can reduce model calls spent navigating and make relevant alternatives
available together. It can also remove reconsideration permitted by the native
UI, increase capture cost and introduce necessary protocol commitment state.
These differences belong in observation, recording, reset/recovery and metrics.
Native microsteps and P1 decisions use separate counters and timing.

No architecture-wide service replacement is required. ADR-0016's roles remain;
this ADR refines the selected environment profile and its consumer migration.

## Compatibility and migration

New profile/revision identities are mandatory. Existing native-logical,
text-menu and old model runs retain their original semantics and results.
Old input/weights are not renamed P1. P1 projections record omissions; Human
navigation or parent/continuation records do not become invented macro labels.
The schema, capture, lowering trace and replay comparator evolve together.

PR178's pending plan/contract text must be reconciled with this direction before
integration; its earlier author head is not modified by this proposal.

## Rollback

Disable admission of the new P1 profile and use an explicitly selected existing
qualified profile. Preserve new/old original evidence and incomplete operations.
Never silently resume an unknown macro under another profile.

## Non-goals

This design does not qualify an installation, change native rules, select a new
default model, approve training, prove Human origin or promise strategy quality.
