# Native logical access lifetime and indexed-page source evidence

This packet is source-only. It does not qualify native exposure, a loaded Mod,
a complete game, Human origin or G2/V1. Base: root integration
`0b198824e86306b5e6a733e72016e91b6d185044`; topic:
`codex/e1-v1-access-lifetime`. The native logical contract remains the owning
[profile specification](../NATIVE_LOGICAL_PROFILE.md).

Subscription renewal preserves its IDs, accepted scope, generation, starting
cursor, publication history and wait-ID admission history. Event cursors are
stable signed positions; active resource/generation/retention checks still apply.
Expiry and detach cannot be revived, and renewal extends neither capture pins
nor a wait deadline or controller authority. Current, Context, Retain and
Release expose typed, coherence-checked transport values over the existing store.

The catalog page path uses the retained row index, original encoded member byte
lengths and an empty response envelope to size each candidate. Unfiltered access
reuses the original count/digest and decodes selected rows. Prefix filtering uses
a bounded scan and computes the complete ordered filtered digest without a cache.
One final serialization verifies response size; growing-page serialization has
been removed.

A local .NET 9 Release benchmark acquired all 10,000 synthetic members with
`limit=10000` and `max_page_bytes=1048576`. The same fixed IDs, multilingual labels,
subject and target relation and warmup were used before and after. Both runs
verified the full ordered membership, two page sizes `1048529` and `1043149`,
relation bytes `2090561`, and digest
`39654fc4806a5bcf5d1f49610fddfdd13ebf3a8af210a7fc3f95112b07b59169`.

| Source implementation | Elapsed ms | Total allocated bytes |
| --- | ---: | ---: |
| Base growing-page path | 6699.393 | 6129164976 |
| Indexed page path | 68.5492 | 65937760 |

These are single local source measurements, including acquisition and final
response encoding. Allocation uses `GC.GetTotalAllocatedBytes(true)` and is not
peak retention or physical-memory measurement. This is not game, network or
Model performance evidence. Temporary receipts and the exact benchmark harness
are under `/tmp/e1-access-lifetime-benchmark/`; focused conformance receipt is
`/tmp/e1-access-lifetime-test-results/access-lifetime.trx`. Tests preserve exact
member bytes/order, encoded envelope accounting, full/filtered digests, cursor
boundaries, continuous renewal across several leases and capture expiry.

Rollback is reverting this source packet before deployment. Native bridge,
REST/SDK composition and their exact qualification remain separate gates.
