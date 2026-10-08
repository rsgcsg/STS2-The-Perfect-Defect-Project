# STS2 Connector TypeScript Client

Strategy-free strict validators, REST calls and controller lease mechanics for
the STS2 Player Environment protocol. This package does not score actions,
reconstruct legality, interpret game effects or retry `unknown` delivery.

Consumers provide their own explicit product identity when constructing
`EnvironmentControllerSession`.

`prefetchPlayerEnvironmentDecisionBundle` can eagerly fetch advertised Reads
for memoryless consumers. It verifies snapshot, runtime, environment, kind and
target coherence and returns the original observation plus Read responses. It
does not normalize game semantics, select actions or create authority.

The explicit `textMenuV2Capabilities`, `observeTextMenuV2`,
`observeTextMenuV2Context`, `submitTextMenuV2`, and `textMenuV2Result` methods
route `input_profile=text-menu-v2` through the existing strict v2 decoders.
Default and `text-menu-v1` methods retain their prior profiles.

Install a published npm version when available, or the exact SDK tarball
attached to the matching STS2 Connector GitHub Release. A sibling checkout is
a development convenience only and is not a product dependency.

The additive sealed-observation methods are `sealedObservationCapabilities()`,
`readCurrent({expectedSnapshotId?})`, `readSealed({captureId,cursor,maxBytes?})`,
`releaseSealed(captureId)`, and `getFullTextMenuV2({expectedSnapshotId?,maxBytes?})`.
The full method returns `FullTextMenuV2Observation` with `context`, separate
`capture` metadata, and the exact original public `serializedSnapshot` text. It
checks same-capture byte coverage, digest, UTF-8 and strict text-v2 schema, then
releases retention. Direct chunk callers manage their own release. A settling or
partial Snapshot is still an operational observation: consumers gate their Model's
readiness and completeness themselves. Capture metadata is not Model input or
mutation authority. See the [S0 contract](../../docs/TEXT_MENU_PROFILE.md#s0-immutable-observation-reads-additive-source-candidate).

`controlSnapshot()` reads the canonical `PLAYER_ENVIRONMENT_CONTROL_ROUTE`
(`/api/player-environment/controller`) and uses `decodePlayerControlSnapshot`.
The strict status envelope validates runtime identity, registered clients and
any held lease. Native serialization can omit a null `controller`; omitted and
explicit null both mean no current controller. A malformed response fails decoding
and must never be treated as successful controller release.

The additive `NativeLogicalSession` uses the existing `PlayerEnvironmentRestClient`
and `EnvironmentControllerSession`. Bootstrap with `session.capabilities()`, then
call `controller.register(capabilities.session, capabilities.control_policy)` once.
Passive `attach`, `current`, `read`, `list`, `resolve`, `events`, `await`, `renew`,
`retain`, `release` and `detach` reuse that registration without acquiring a lease.
`submit` obtains credentials from the same controller owner. `submit` and `result`
return an explicit `pending` or `terminal` lookup; neither retries or polls.

`getFullCurrent` and `getFullEvent` assemble the exact sealed observation and the
whole ordered action relation. They validate contiguous bytes, SHA-256, strict
UTF-8/JSON, source scope/session/generation, catalog count/digest and public operand
references. The optional `maxActions` rejects the whole relation before catalog
allocation when it exceeds consumer capacity. `getCurrentCapture` and `getCapture`
accept an explicit eager scope and return its captured observation; they do not
fetch a catalog or expose an action relation. Scope omission remains explicit.

The returned capture owns its reader pin and retained byte reservations until
`await capture.dispose()`. Use `try/finally` around consumption. An optional
`NativeLogicalByteBudget` admits observation bytes before allocation and each
catalog response ceiling before its request, then shrinks the page reservation
to actual encoded bytes. Disposal and failed assembly release only their owned
pin and reservations. Capture/reference metadata stays outside Model input.

`controller.releaseControl()` hands off mutation control while preserving client
registration and passive subscription history. It fences pending credentials
immediately and releases a known lease independently of a slow renewal. An
uncertain response blocks new acquisition; `reconcileControl()` reads the original
authority once. A still-held lease requires explicit release. Snapshot absence
cannot resolve an unidentified timed-out acquisition. `close()` ends the owner.

These methods implement and test the [native logical source contract](../../docs/NATIVE_LOGICAL_PROFILE.md).
Shared producer fixtures, a synthetic HTTP listener, 200/500-action transfers and
10,000-action protocol pressure do not qualify native hook coverage, live gameplay
or Model inference performance. Existing legacy profiles retain their routing.
