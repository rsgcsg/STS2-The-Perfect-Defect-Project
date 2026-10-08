# Source recording v3: ordered original input bases

Status: E2 contract candidate, 2026-10-09, based on accepted protocol Source2
`447f30084765cd662afbdb744f30479b18cbc41e` and the independent Source order audit.
This document freezes the new wire for consumer implementation. Producer, physical
hooks, independent acceptance and native runtime qualification remain separate.

One existing Source store, epoch ledger, worker, serial Connector projector,
auditor and bundle packer select v2 or v3 through narrow format adapters. V2 bytes,
strict readers and fixtures retain their meanings. No v2 input can be backfilled
with an ordinal. Source declarations remain metadata; no actor kind attests Human
origin, actual model Consume, Commit, effects, successor or research admission.

## Exact schemas and field sets

V3 replaces each Source2 schema suffix `-2` with `-3`, including the capture
profile, recording manifest, attachment epoch, segment, boundary, public
observation, input witness, close receipt, audit, coverage, accounting failure
and bundle. The profile ID is `native-logical-source-v3`; the Evidence descriptor
is `source-session-bundle-v3`. Manifest `schema_version` and
`source_schema_version`, and bundle `schema_version`, are 3. The existing
recording command3 and status5 remain their additive operational contracts.
`source_v2` status is the shared Source attachment projection, not an order claim;
its exact profile comes from the manifest. Native `input_profile` remains
`native-logical-v1`. Publication profile and definition digest remain Source2's
`native-logical-publication-profile-v1` /
`c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf`.

Every V3 object has the exact Source2 fields and explicit nullable members,
with the following closed additions. No other extension is admitted.

| Object | Added fields |
| --- | --- |
| Native input witness | `input_prefix_ordinal`, `basis_order` |
| Attachment epoch | `after_input_ordinal` |
| Source segment | `after_input_ordinal` |
| Source boundary | `after_input_ordinal` |
| Native seal (every predecessor/boundary/close occurrence) | `after_input_ordinal` |
| Paused interval | `after_input_ordinal`, `through_input_ordinal` |
| Final drain | `after_input_ordinal` |
| Close receipt | `final_input_prefix_ordinal` |

All new ordinals/cuts are canonical unsigned U64 decimal strings, including
`"0"` for an initial cut. Input ordinals start at `"1"`. `basis_order` has exactly
`status` and explicit nullable `reason_code`. Status is `native_prefix_frozen`
with reason null, or `unproven` with reason
`source_prefix_capture_order_unproven`. A missing capture can retain
`native_prefix_frozen` when native acquisition was sound but later encoding,
capacity, retention or disk admission failed. `unproven` cannot carry a capture,
catalogue, selected action or exact mapping: it requires `capture_missing`.
The separate order reason preserves the original outcome's native reason. Terminal delivery
and bounded native stages retain their actual original meaning in either case.

The profile's complete field set, limits, full requested scope and non-claims
are Source2's exact set. The native capture/catalogue references, declaration,
environment and native transition objects keep their exact Source2 field sets.
This supplement records order and native acquisition evidence only.

## Admission and fences

The existing filesystem-free ledger assigns a session-global immutable
`input_prefix_ordinal` exactly once after successful active admission and before
native input, encoder work or terminal completion. Actor/epoch changes never
reset it. Capacity rejection before issuing a token fails original accounting;
it cannot consume a hidden ordinal or silently create a hole. Every issued
ordinal gets one original terminal row, including unmapped, rejected, unknown
and capture-missing inputs. Per-stream `sequence` continues to order durable
appends; terminal rows may persist in the order 2,1.

Each metadata admission stamps the existing original Hub position together
with the last issued input ordinal under the same short metadata gate. No
native callback occurs between reading a command's original cut and admitting
its Source metadata. Initial epoch and segment cuts are 0. Epoch handoff seals
the predecessor at k and starts the successor at k. Pause/resume, actor switch,
launch/terminal and Close are fenced at their own original k. Inputs cannot be
admitted while paused; a closed paused interval therefore has equal input cuts.
Late completion changes no stamp. Close's final ordinal equals the last issued
ordinal and the last epoch's seal/drain cut. Older epochs retain their own cut.

When sorted by verified prefix ordinal, input epoch order and same-epoch
publication cuts cannot regress. For a segment/epoch begun after k, its inputs
have ordinal >k and precede that segment/epoch's next fence. Original actor and
epoch tokens remain authoritative at equal publication watermarks. Boundary
stream sequence orders boundary-only ties; timestamps, UUIDs, encoding revision
and terminal append sequence never establish native input order.

## Native Begin/Freeze proof

The Connector's synchronous producer-local guard starts before any native
Prepare callback. It records original attachment/epoch and Hub reserved cut,
and active ancestor native acquisitions. It seals only after detached immutable
facts and their serial encoder queue position have been acquired. Encoder
completion is unrelated: a delayed encoder alone does not invalidate order.

An input prefix is unsafe if an ancestor publication/input is reserved but not
native-frozen, or a nested publication/input/lifecycle/actor change occurs before
its own seal. A publication overlapping such reentry also emits an
original-position missing Source result, so it cannot form a gap-free prefix.
These cases preserve the original input token and native terminal, emit
`unproven` / `capture_missing`, and never move the original `pre_position`.
The guard holds no lock across Prepare, native callbacks, encoding or disk I/O.
It never uses Hub `completed_through`, later Current, polling, timer matching,
native replay or retroactive backfill.

## Physical and protocol producer

Existing Annotator native Prefix/accepted completion/Finalizer calls forward
typed private owner/subject/argument references through neutral Native Foundation.
No duplicate Harmony patch or Connector import of Annotator is introduced.
Physical prefix calls fresh fair-player Prepare before the body and extracts
exact private witness matches in that original turn. The same serial projector
maps zero, one or many matches into the complete original public catalogue:
unmapped, exact or ambiguous respectively. It constructs no native operand.

One opaque invocation token retains original Source ownership through late
callbacks. For GameAction families, RequestEnqueue binds the exact object and
OnEnqueued proves accepted delivery for that binding. Scope finalization without
an accepted callback or exact pending carrier yields unknown. Task completion
alone never proves acceptance. Protocol dispatch has one lexical try/finally
dedup scope around its original leaf.Dispatch; an unrelated later physical
invocation is admitted normally. Source close turns pending original inputs
unknown without changing their actor, epoch, ordinal or basis seal.

## Evidence API and consumer merge

The new public Evidence API is `SourceSessionBundleV3`,
`SourceSessionBundleV3Verifier`, `verify_source_session_bundle_v3(path, expected=None)`.
It returns the verified original `observations`, `inputs`, `segments`, `epochs`,
`boundaries`, `final_drains` and `final_input_prefix_ordinal`; input dictionaries
retain all original fields and bytes. V2 exports retain their strict V2 parser.
The new adapter calls the same format-aware verification engine as V2; C# and
Python remain independent implementations of that engine's contract.

Structural bundle verification permits explicit gaps and unproven/missing inputs
with complete accounting. Continuous research admission is stricter and belongs
to STPD: it cannot carry across required missing/unproven bases. A mechanical
merge places original publication p before the verified input bases with
`pre_position.publication_index=p`, sorted by ordinal, and before publication p+1.
Explicit fences establish segment/epoch/pause/close membership. Full C and all
unlabelled observations are retained. Existing native occurrence/revision/coherence
validation still runs on the merged frames without revision rewriting. Repeated
same qualified occurrence deduplicates according to the consumer's versioned
ProjectionSpec; a declared actor change alone is not a native reset proof.

## Frozen examples and remaining gates

`contracts/fixtures/source-session-v3.json` is a synthetic wire example, not a
packer-emitted bundle or actual native history. It fixes same-watermark inputs
A=1,B=2 with durable row sequence B,A; pause/actor/resume at cut1; a successor
epoch at cut2; Close with an unknown pending ordinal3; and both unsafe native
reentry cases. Producer tests must use actual prefix/Prepare/publication seams,
including delayed encoder-only positive, held disk nonblocking admission,
physical/protocol dedup, zero/many mapping and strict malformed ordinal/cut
negatives. An actual C# packed V3 golden is independently verified in Python.

Resource limits remain Source2's 128 tokens, 32 metadata packets, 128 MiB charged
copies/scratch, two-second original deadline, two retiring epochs, 256 epochs and
three-second close barrier grace. Source/test acceptance precedes exact build,
install/load/non-interference and a small physical canary. V3 format capability
does not qualify native coverage or Human origin and does not approve G2/V1.
