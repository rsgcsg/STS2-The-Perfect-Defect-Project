# P1 C1 atomic capture foundation

This additive, read-only profile is `p1-atomic-capture-c1-1`. It is a capture
foundation for P1, **not a full P1-A observation or an Actor admission profile**.
The current capture revision is `c1-2`: Draw remains an unordered multiset,
while discard/exhaust retain their native visible sequence. The reader still
accepts original `c1-1` archives with their original all-unordered semantics;
it rejects either revision paired with the other revision's ordering claims.
`agent_admissible` and `creates_mutation_authority` are always false. The original
native-logical, text-menu, Source3, Snapshot and Read contracts keep their meanings.

## Capture and coverage

The initial supported capture scope is an ordinary ready `combat_turn` in the
player play phase with its current `CombatLiveContext`. Settling, child selectors
and other owners remain unsupported diagnostics. A typed draft selector gates
Read materialization before it happens. One synchronous main-thread `BuildSnapshot`
materializes the public base, `run_deck` and `combat_piles`. It does not call the
public Read API repeatedly, await, navigate the UI or execute inputs. Every
advertised Read is represented; a materialization failure stays a missing
required instance. Non-combat scenes return explicitly unsupported diagnostics.

Card display details are separate `surface_card` instances for every current
hand/deck/draw/discard/exhaust card occurrence. Instances keep their entity and
display context; neither kind nor card definition is a deduplication key. The
same exact native `CardModel` object reused in deck and hand has two display
instances and one entity relation. Distinct native clones retain distinct
aliases; this slice does not infer cross-clone identity from a shared definition.
Aggregate parent references point to instances in this same packet. Empty piles
remain available empty aggregates. A description uses the fixed typed native
`GetDescriptionForPile` formatter for that context; failure is not replaced by a
generic description and called complete. No upgrade clone is created.

`native_basis` preserves the legacy `snapshot-1` schema and BoundAction meaning.
It is explicitly labelled `legacy_native_basis_not_p1_semantic_catalog`. P1
semantic actions are not created or submitted by this API. A payload change
changes the frozen byte digest even if the legacy Snapshot/read descriptors
keep the same ID. Capture retention does not extend native action validity.

`coverage` distinguishes every required instance, current base/parent context,
legacy native relation and aggregate. `scope_status` describes only this capture
subset. `p1_status` stays `unsupported`: current-act map, P1 semantic catalog,
P1 commitments, complete public detail closure (including keywords/afflictions
and all contextual previews), and native noninterference qualification are
explicit required gaps. A complete C1 subset is not a complete P1 offer.

## Atomicity and fairness boundary

The callback compares exact run/room/combat/player/input-owner references,
modal/overlay/map/menu/hand owner, turn/phase/mode, public resources and card-pile
membership before and after extraction/serialization. A nested capture, second
Snapshot build or known native input prefix invalidates the acquisition even
if the owner later returns to the same object. Inconsistency returns an error,
not an automatic second capture. Native getters and the broader game's mutation
surface still require exact runtime qualification; these checks do not prove
all game state is transactional.

Native references stay in the private synchronous callback. Private draw
membership/order is compared only to reject drift, never serialized or hashed
into a public identity. Public Draw projection sorts the existing allowed
values and independently random opaque entity aliases; new Read-instance and
capture IDs are random. The final digest covers frozen public bytes only.
The native basis and aggregate Read retain the current per-zone ordering.
Original `c1-1` archives cannot recover previously discarded order and do not
acquire the newer presentation-order guarantee merely by being readable.

## HTTP and retained bytes

| Operation | Route | Meaning |
| --- | --- | --- |
| GET | `/api/player-environment/p1-capture/capabilities` | Scope, missing full-P1 requirements and fixed bounds |
| GET | `/api/player-environment/p1-capture/current?input_profile=p1-atomic-capture-c1-1` | One synchronous capture; optional `expected_snapshot_id` is a legacy-basis check, not a payload-equivalence proof |
| GET | `/api/player-environment/p1-capture/read?capture_id=…&cursor=…&max_bytes=…` | Retained bytes only; no native queue/read |
| POST | `/api/player-environment/p1-capture/release` | Release this profile's retained bytes; no game mutation |

The new profile reuses the one existing `SealedObservationStore` owner, byte
capacity, cursor implementation, monotonic TTL and chunk mechanics. Maximums:
10,000 Read instances, 8 MiB per capture, 64 MiB/32 retained captures shared
with text-menu-v2, 120 seconds TTL; chunks are 1 KiB–1 MiB (default 64 KiB).
Capacity and oversize are explicit failures; no truncation or live-capsule
eviction. Cross-profile read/release is rejected. Old routes keep their exact
schemas and default profile. Expiration does not recapture or authorize input.

## SDK, recording and comparison

`PlayerEnvironmentRestClient` adds `p1CaptureCapabilities`,
`readCurrentP1Capture`, `readP1Capture`, `releaseP1Capture` and `getFullP1Capture`.
The convenience method assembles/validates exact bytes and then releases
retention. For multiple readers, obtain one handle, call exported
`assembleP1Capture` for each reader, then release explicitly. Every reader
validates profile, contiguous chunks, total bytes, final SHA-256, source/session,
required coverage and instance relations. Each source-backed instance must join
exactly one advertised Read by ID, kind, content schema and target; every
advertisement must be retained, including unsupported Reads in partial packets.
Derived card details have no advertised source ID. A complete scope additionally
requires the advertised deck/pile aggregates, the existing typed native combat
context, and details that preserve their same-context source card facts. Missing
optional display text may be supplied by a detail for reader compatibility (the
current native producer conservatively marks absent source text unsupported).
Nullable nontext facts cannot be invented; public numbers must be finite and
known counters/IDs retain their native integer types. Dynamic values are not compared
across different display contexts. Unknown Read schemas cannot claim available
content, but remain explicit unsupported diagnostics. It never submits actions or acquires
a controller. Text-menu-v2 and C1 use the same byte-assembly implementation.

`serializeP1CaptureRecord` writes an archival representation of the same exact
public text and original capture metadata. `readP1CaptureRecord` verifies and
reads that archival copy without native/HTTP access; it does not renew an
expired live handle. Existing application storage can persist this string.
Comparison receives the same verified packet and entity/context relations,
not separately rebuilt live Reads. No Human origin, Source3 ordered-prefix
association, actual Agent consumption or native occurrence proof is inferred
from this standalone diagnostic record. Those integrations retain their own
owners and must explicitly bind this versioned capture before stronger claims.

## Validation boundary

Portable producer/store/SDK tests cover >8 same-kind details, repeated entities
in distinct contexts, payload-only changes, byte immutability, multiple readers,
required missing data, reentry, expiry and cross-profile isolation. The committed
fixture is emitted by the C# public packet serializer and checked by the strict
SDK reader. Exact compilation and owner tests are separate from installed,
loaded, live-game, Human, full-P1 and two-Host conformance qualification.
