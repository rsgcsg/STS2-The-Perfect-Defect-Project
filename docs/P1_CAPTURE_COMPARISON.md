# Offline C1 archive comparison

This project engineering tool compares the content of two declared original
`p1-atomic-capture-c1-1` archival records. Connector's current built SDK exclusively
validates each record, exact public-byte digest and complete C1 coverage. The
command performs local file reads only. Host Runtime keeps runtime qualification;
Connector keeps capture/validation authority. No SDK normalization API, Host SDK
pin, legacy comparator, native producer or installed artifact changes here.

Build the exact checkout's SDK, then run:

```sh
npm --prefix components/connector/sdk/typescript ci
npm --prefix components/connector/sdk/typescript run build
node tools/compare-p1-captures.mjs \
  --reference /ABS/reference-capture.json \
  --candidate /ABS/candidate-capture.json \
  --reference-report /ABS/reference-original-report.json \
  --candidate-report /ABS/candidate-original-report.json \
  --declaration /ABS/comparison-declaration.json
```

The single JSON output has `c1_scope_match` (exit 0), `c1_scope_mismatch` (10),
`c1_incomparable` or `same_record` (20). `same_record` never means independent
agreement. Reusing a capture identity with changed valid-digest bytes is
incomparable. Missing records/reports, invalid UTF-8/integrity/profile/revision,
partial/out-of-scope captures and invalid associations fail closed. There is no
legacy-event fallback. The first difference contains a bounded path/reason and
no payload values. Original record/report digests and verified capture/producer
identities remain separate provenance.

## Required external declaration

The declaration is an operator assertion associating original files with one
explicit scenario boundary. Use original file SHA-256 values, not hashes of
parsed/reformatted JSON. The following is a shape guide; replace every symbolic
value with the original record/report value before use:

```json
{
  "schema": "sts2.project/p1-c1-comparison-declaration-1",
  "semantic_target": {
    "protocol_version": "1.0.0",
    "information_policy_id": "player_visible_v1",
    "game_build": { "version": "v0.111.0", "commit": "41cef1ea", "main_assembly_hash": 1010476334 }
  },
  "scenario": {
    "scenario_id": "DECLARED_SCENARIO",
    "seed": "ORIGINAL_SEED",
    "observation_boundary": "EXPLICIT_SAMPLE_BOUNDARY",
    "alignment_basis": "externally_asserted"
  },
  "reference": {
    "record_sha256": "ORIGINAL_RECORD_SHA256",
    "report_sha256": "ORIGINAL_HOST_REPORT_SHA256",
    "capture_id": "ORIGINAL_CAPTURE_ID",
    "capture_sha256": "ORIGINAL_PUBLIC_BYTES_SHA256",
    "source_snapshot_id": "ORIGINAL_SNAPSHOT_ID",
    "session": { "runtime_instance_id": "ORIGINAL_RUNTIME", "environment_fingerprint": "ORIGINAL_ENVIRONMENT" }
  },
  "candidate": {
    "record_sha256": "ORIGINAL_RECORD_SHA256",
    "report_sha256": "ORIGINAL_HOST_REPORT_SHA256",
    "capture_id": "ORIGINAL_CAPTURE_ID",
    "capture_sha256": "ORIGINAL_PUBLIC_BYTES_SHA256",
    "source_snapshot_id": "ORIGINAL_SNAPSHOT_ID",
    "session": { "runtime_instance_id": "ORIGINAL_RUNTIME", "environment_fingerprint": "ORIGINAL_ENVIRONMENT" }
  }
}
```

Each original report must bind `loaded_identity.host.runtime_instance_id` to its
capture and contain `loaded_identity.protocol`, the declared game build,
`host_kind`, implementation source revision/artifact SHA-256/module MVID, and
`episode_provenance.actual_seed`. The tool verifies these exact facts. It does
not reconstruct Host environment hashing, authenticate the declaration or infer
sample alignment from equal seed. Report/capture association is externally
asserted, with no producer witness in the current archive contract.

Consequently every output retains `environment_alignment=externally_asserted`,
`alignment_verified=false` and `cross_host_qualification=incomparable`, including
content matches. There is no actual paired-Host, causal same-state, native
noninterference, Human/Source3, legality or research-admission claim.

## Compared content and conservative limits

The tool compares the entire preserved native basis, all required Read instances,
aggregate cardinalities, detail content, entity/context/parent relationships,
coverage and explicit full-P1 gaps. Runtime/environment identity, generation,
capture times/ordinal and Snapshot sequence/observation time stay provenance.
Source Snapshot aliases still bind consistently between the packet and basis.

Only the closed contract-path and known referent-property schema policy in `tools/p1-capture-comparison.mjs` identifies opaque
aliases. Entity and referent aliases share the native public-entity namespace;
Read-instance, legacy Read, action, interaction and Snapshot aliases have their
own bijections. Repeated IDs cannot split; distinct IDs cannot collapse.
Comparison covers declared typed alias relations only (`relation_scope`); undeclared
relation inference is unsupported. Extension values are compared literally
(`extension_semantics=literal_values_no_undeclared_relation_inference`). A nonnull
referent `properties_schema` outside the finite supported map is incomparable,
even if its property content is null or absent. A new schema needs explicit
review of its producer meaning and alias relations. The current finite map covers
`card`, `hand`, `enemy`, `companion`, `potion_state`, `orb`, `potion`,
`playable_card`, `usable_potion`, `relic` and `player`, each under
`sts2.player-environment/referent/<role>-1`. Ordinary combat emits the eight
context/surface roles `playable_card`, `usable_potion`, `player`, `hand`,
`companion`, `potion_state`, `orb` and `enemy`; the map also retains the already
supported typed card/potion/relic shapes. A target-only referent with no property
schema requires no property interpretation.

Definition IDs, labels, descriptions and unknown extension locations remain literal,
even when a field in an extension is named `entity_id`.
No text substitution or kind/definition deduplication occurs. Known ordinary
combat paths include orb identities in the current player, `orb-1` properties
and nested `player-1` properties, plus owned-potion card previews in persistent
state. Orb values/queue positions and preview content remain semantic values.

Read instances and coverage are keyed collections, including each occurrence and
its parent and all coverage links. Every other sequence retains its declared
order, including native actions, referents, hand and deck. Only card arrays
explicitly labelled `ordering_semantics=unordered_multiset` permit permutation.
Unique candidate signatures select a pair, then all fields and global alias
relations are compared. Each collection freezes its signature alias view before
matching, so later bijection growth cannot stale already indexed keys. Ambiguous signatures return incomparable; the tool does
not search alternative graphs. An order-only diagnostic mismatch does not establish broader P1 inequivalence.
Unknown ordering or additional mechanism support
requires an explicit new reviewed comparison scope. Depth is capped at 64 and
comparison visits at 500,000; documents are capped at 64 MiB and SDK record bounds
still apply. A structurally unsupported document is incomparable.

## Source/test boundary

`node --test --test-concurrency=2 tools/test/p1-capture-comparison.test.mjs` exercises
the real CLI and built SDK serializer/reader. It participates in existing
`check:s0-tests`; `precheck:s0` already builds that SDK. The committed C# producer
fixture is partial and must refuse comparison. Complete positive packets follow
the existing SDK complete-scope fixture and remain explicitly synthetic, with
synthetic original Host reports. These tests do not qualify native captures or
claim a complete Managed C1 producer, child-selector capture or full P1.

Rollback removes these root engineering tools/tests, their gate entry and this
document. Existing archives, SDK APIs, legacy CLI behavior and running components
retain their original contracts and identities.
