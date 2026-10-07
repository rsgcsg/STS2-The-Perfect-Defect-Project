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
