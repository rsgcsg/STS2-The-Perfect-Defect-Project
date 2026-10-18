# Current — P1 upper-chain implementation and semantic recording

Current docs topic: 2026-10-19, `codex/p1-upper-chain-docs-20261019`, based on
root I `4b489108238d1a17bef1dae421d289b29c5883dc`. The old 3aac docs are already
integrated; do not restore that task. Resolve live GitHub refs; actual source and
original records override this file. This is dated routing, not live authority.

Start with the single [chain diagram and requires/provides table](../plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md),
then [P1 section4.1](../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准).
Every profile declares originals-to-profile requires; a new source supports it only
when declared projection of its provides covers those requires. P1 is today's choice.
Reports/PRs identify chain position and one of the four Architecture change classes.
Two different-cause preflight failures trigger a review of the failed connection:
implementation fails requires, or requires are unclear/wrong. Semantic/upper-level
changes remain user decisions. Q connections7/8 and a non-blocking closeout hint wait
until after freeze; this task adds no checker, service, registry, schema or freeze gate.

## Approved definitions and current engineering

The user has approved freeze-definition sections4.1–4.2: original whole-run completeness,
derived completeness and new-route E2/E3 applicability. Direction and recording gate
are also set. The overall [freeze definition](../plans/p1-prefreeze-2026-10-18/SCOPE_v0.3.zh-CN.md)
and final freeze PR are separate pending approval. Deadline remains Oct20 16:30 Brisbane.

P1 reuses mature Human accepted native-action/exact-owner capture plus same-time
complete PB1/all Reads and shared-core derivation. Internal automatic GameActions
retain facts/results without becoming player labels. Source4 is retained for future
profiles, has no new investment/preflight, and its cancellation-only code was removed
from I by824847fb; original packets/branches/failures remain preserved.

Root I is4b489108; official installed f0f050eb/MVIDfa40acad used a strictly checked
cold-load tuple in native03. This is a dated source/install snapshot, not a recording
PASS. Native02 saved16 events/5 accepted/5 terminal/0 pending/0 issue and complete
Reads, but legacy text-input wrote an undeclared stream: Close failed and no pack.
Its immutable prefix later derived5 actions/6 points and strictly matched Managed,
including final successor, with0 unknown/taint/budget. This valid prefix does not
repair failed Close. Native03 ended normally at the menu with zero player actions,
for the old-hook audit; zero-action Close/pack does not pass the recording gate.
B audits all reachable old hooks (Quarantine, nested continuation, act-entered,
carrier failure included); root integrates/builds/backs up/deploys then runs a new session.

## Four requested gap detectors: actual status and known risk

(a) Seen unsupported actions: existing captured accepted-action/PB/match failures emit
issue; missing or bypassed hooks are still under B audit. (b) Every player-wait boundary
followed by accepted decision or explicit end: not fully implemented; accepted-to-terminal
accounting does not count all waiting boundaries. (c) Every inter-decision change explained
by recorded choices and automatic effects: partial lifecycle/successor/sequence checks,
no exhaustive independent effect-accounting proof. (d) Strict Managed mismatch: implemented
for derived trajectories and tested on native02's bounded prefix, not a formal whole run.
A choice never observed by any capture hook can evade(a);(b)/(c) do not currently close
that hole. Managed(d) may expose changed state but cannot prove every omitted choice
was detected. Silent omission remains a known risk, not a complete guarantee. Exact
source anchors and limits are in P1 section4.1.

## User recording and follow-up

Before inviting formal recording, a real engineering short segment must cover Play,
unsubmitted pickup cancel with zero labels, EndTurn and another non-card decision,
with complete originals, offline derivation/verification and strict Managed replay.
No actual segment has passed this complete gate yet. After it passes, the user directly
starts formal English whole games, with no separate Human short run. Check the first
saved opening segment as Human validation without interrupting the game.

A game takes about30 minutes; no need to monitor midway in real time. Convert/check
immediately after each game. On an original gap, report exactly what and at which step;
the user decides continue/restart while engineers repair the recorder and make a new
candidate. Preserve gap originals; they do not count toward at least3 whole games.
A pre-gap segment may count mechanism coverage only if Reference correctness, offline
verification and strict Managed replay all pass on that same occurrence.
Only the user plays formal games. If unavailable or unable to find the entry, wait for
formal play/required decisions while independent work continues.

Oct19 09:00 Brisbane is the core/self-test checkpoint. If missed, report blockers,
remaining work/time, then continue ordinary authorized repairs; plan changes go to user.
Checkpoint scheduling ends after Oct20 09:00; only the deadline automation reports16:30.
A scheduling pause does not stop engineering. At deadline protect any ongoing game or
conversion and complete normal Close/pack; report pending, then actual results afterward.

## Remaining Platform non-claims

English>=3 whole games, all-act/Act3/victory, INFO independent review, final40+20,
product/final two-HostR20, historical convergence and authorized minimal cloud remain.
Console ancestry is excluded from formal games/coverage/training. K3 exact24 archives
and19 reuse rows remain; Q3v3/model/training settings are unchanged. Source/tests,
install/load, gameplay, Human origin and training readiness remain separate claims.
