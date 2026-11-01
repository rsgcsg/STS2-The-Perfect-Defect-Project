# U1 native environment repair, 2026-11-01

Owner: U1. This first delivery repairs five existing Managed implementation
failures without changing P1 r1.5, Input-v2, rewards, game rules or model weights.
The fixed training snapshot contains 79 unknowns; these five causes account for
64 of them and 7,135 recorded decisions. All original unknown outcomes and
qualification states remain unchanged. This is a repair of future execution,
not recovery or replay of those games.

| First incorrect fact | Owning repair | Native regression |
| --- | --- | --- |
| Rest page published while its heal reward was still waiting | Resolve the current native child through the existing decision resolver after rest/selector actions | Original TinyMailbox/DreamCatcher rewards, explicit decline, suspended parent and subsequent proceed |
| ScrollBoxes bundle omitted from reward successor wait | Include the same native pending bundle in that wait | Original reward callback, two complete packs, explicit preview/confirm, stale owner refusal |
| Manual zero-minimum selector rejected as unqualified | Admit original min=0 only with explicit manual confirmation | No automatic completion, foreign-card refusal, original callback returns the explicit empty set |
| Crusher/Rocket death dereferenced absent audio scene | Remap exactly one audio call per original method, only omitting null audio in TestMode | Original death, surviving ally and CrabRage block; exact signature/call-count admission |
| Terminal published before original death/end tasks completed | Register Kill before its synchronous portion and wait for both Kill and OnEnded | Synchronous/asynchronous death, end commit, win-before-Kill, native failure after win and missing-end unknown |

The terminal task wait shares the existing five-second native commit bound;
it never turns HP=0 or a missing task into an outcome. Exceptions remain errors.
The old nonmanual zero-minimum transition remains unsupported. Existing bundle
publication, native callbacks, game RNG and finite native action admission remain
the authorities.

The component fixture uses privileged StartRun/EnterRoom setup and native rest
history. Its game producers, original effects and Host serialization are real;
the process transport into the public session is substituted with those exact
serialized bytes. The normal public-basis encoder, strict SDK, frozen P1 r1.5
reducer, Input-v2 projector and MStd scorer consume the complete catalogs.
This establishes that seam, not Reference scene rendering or gameplay quality.

## Validation and identity

The normal clean prepare reproduces the Mac artifact and runs the existing
Godot reference coverage gate. The full exact-game build gate passes. On the
clean candidate, the selected native regressions pass 21/21 with no skips;
the Python evaluation consumer suite passes 97/97. The U1 regression checks
12 original serialized observations, complete finite model scores and an
unwitnessed terminal that retains unknown/no actions. Before/after artifact
hashes stay identical. Scoped non-author review accepted the five repairs after
the two terminal ordering counterexamples were repaired and retested.

The manifest records current Mac and separate x64 build SHA/MVID/patch identities
and preserves the Host58 tuples as historical. A cross-build on Mac does not
prove execution on Linux. Package, actual Linux execution, CI and the final
delivery receipt are recorded separately when reached; no installation or
current RL3 adoption is claimed here.

For the native seam, prepare a fresh candidate through the normal Host command,
then set `STS2_MANAGED_TEST_CANDIDATE` to it and run
`node --test components/host-runtime/test/managed-u1-native.test.mjs` from the
repository. This needs the exact local game, .NET 9 and the normal locked
worktree Python dependencies; public CI skips this proprietary native fixture.
Portable CI still validates the manifest, patch inventory and public source.
On failure retain the error and original record; do not retry unknown input.
Rollback selects the preserved Host58 package/candidate in a new registration.

## Parent work remains open

The complete inventory has 129 distinct current/historical records. Crystal
Sphere and Trial confirmation need new public capability and P1 semantics;
the user approved P1 r1.8 and a new explicitly bound InputSpec/compatibility
package that retains the network and old weights. First deliver the five repairs
above; Sphere and Trial proceed separately, never as an RL3 hot swap. If visible
Sphere artwork cannot be faithfully represented, return the concrete gap to the
user before choosing another representation.

Historical Knight settling, Tanx transformed preview, Stop/refresh boundary,
typed evidence verification, Architect scene/RNG and treasure timing remain
separate obligations. Neither this source batch nor its tests close them.

## Host60 follow-up: settling reads and late native child inputs

The PREP-B original at floor20 retained a delivered End turn and a complete,
zero-action settling combat page. Its visible inventory includes Toasty Mittens.
No later native task/child state was recorded: the precise historical timing is
unproved, and the original remains unknown without replay.

Two fresh Knight/Mittens component counterexamples isolate a future failure:
when the original hand-selector continuation arrives during the final combat
fallback, that loop omitted the current child; when it arrives after the action
returns, normal capture kept the cached settling page forever. Host60 reuses the
same native child resolver during/after fallback and adds read-only
`observe_decision`, which pumps already-delivered work and calls that resolver.
Normal capture, observe and text observe refresh only untainted settling pages.
Canonical native facts that have not changed retain snapshot/sequence/Reads;
new facts receive a new projection. Snapshot-addressed Read keeps its binding.
No input is repeated and the existing stall timeout is unchanged.

Fresh native cases pass 28/29 checks respectively. The second invokes production
native command dispatch after the original hand producer is released following
an action return. Its exact serialized bytes traverse normal capture, strict
SDK, frozen P1 r1.5 and the actual Input-v2/MStd full-catalog consumer. The
transport is substituted at this component seam; it is not a normal gameplay or
Reference differential run. Earlier U1 and M2e native regressions also pass on
the stable candidate. Non-author review found and accepted a repaired shutdown
race: all three async read routes recheck the episode after refresh, preventing
publication after an interrupting close. Three new race checks and the existing
driver suite pass 22/22; focused Python consumers pass 117/117.

Reproduce with normal prepare and `STS2_MANAGED_TEST_CANDIDATE`, then run
`node --test --test-concurrency=1 components/host-runtime/test/managed-settling-native.test.mjs`.
The portable counterpart is `managed-settling.test.mjs`. Keep generated native
fixtures and original evidence local. Mac normal prepare reproduces the exact
manifest tuple; separate Linux/package/CI evidence belongs to the final receipt.
P1/InputSpec, gameplay rules, RNG, model weights and the active RL3 registration
are unchanged. Roll back to the preserved Host59 combination in a new isolated
registration. This follow-up is an implementation repair, not a new P1 feature.
