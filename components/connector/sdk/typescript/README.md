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
