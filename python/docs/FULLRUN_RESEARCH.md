# Full-Run Research Contracts

> 当前实施范围见 [1a/1b](research/STAGE1A.zh-CN.md)。历史 [S01](research/S0_STAGE1.zh-CN.md)
> 覆盖数据与公共合同、小样训练、恢复、评测、导出和目标机器独立评分；不代表游戏内运行验收。
> [完整模型设计](research/MODEL_DESIGN.zh-CN.md)与[数据管理设计](research/DATA_MANAGEMENT.zh-CN.md)
> 是目标方案；具体实现与验收范围以各阶段记录为准。

The new research contract is versioned separately from historical combat-v0. The latter's
corpora, serializers, checkpoints and scientific protocol remain reproducible and are not
silently relabelled Full-Run.

## Authority

Platform owns semantic execution state S, complete A_sem(S), exact Human choice, native Commit,
causal successor, occurrence/disposition accounting and real-game qualification. STPD consumes
verified projections. H is not S; public BoundActions are not the research legal catalog.
Family/surface labels are stratification metadata, not a second native census or model switch.

`SourceAdapter` is the narrow integration port. `SourceProjection` binds source bytes, adapter,
evidence scope, run continuity proofs and transitions. The retained synthetic adapter emits engineering-only inputs. The current
`PlatformBundle3SourceAdapter` consumes a bounded tar.gz transport containing exact bundle3
members, through the exact Git-pinned Platform Evidence verifier. It emits `platform_verified`,
which means verified source integrity and contracts, not machine-proved Human origin or
scientific qualification. Synthetic and verified inputs cannot be mixed in one Dataset.
A caller-created projection or scope flag is insufficient: admission and publication/load
reproject the stored source bytes through the installed adapter and compare every record.

## ResearchTransitionV1

The typed codec owns `stpd/research-transition-v1`: stable source-record identity, run/episode/
step, domain/family/surface, semantic state, complete catalog, chosen key, Commit reference,
causal successor or terminal, disposition and provenance. Action keys identify candidates
outside model inputs. Runtime/witness IDs remain provenance, not features. Missing evidence,
unknown schema, duplicate candidates and invalid chosen/Commit/successor combinations fail.

Leaving Combat is normal run continuity. Native root/continuation relationships remain
Platform-owned; STPD does not manufacture a new gameplay root for a child selector.

## ResearchTransitionV2 and bundle3 accounting

`stpd/research-transition-v2` extends the typed research boundary without rewriting V1
identities. The installed codec retains the exact decision occurrence (including parent,
causal root and native selector origin), execution catalog authority, frame hashes, canonical
and proof references, run/session identity, capture profile and recording identity.

Native semantic execution catalogs remain distinct from Platform-proved complete public
catalogs at direct-input execution seams. Only the exact canonical `action_space_authority`
selects the projection. STPD never reconstructs a missing catalog, creates a GameAction root
for selectors, or replaces execution state with Human observation. A Commit reference can
name a direct-input/continuation proof and is not misrepresented as a completed GameAction.
The successor references the exact owner-proved frame, not the following row's state.

The source manifest retains all accepted occurrences and terminal dispositions, all explicit
invalidations, journal and owner attestation. Expected cancellations/aborts and diagnostics
remain evidence without becoming fake committed rows. Unknown successors, missing canonical
publication and capture/persistence failures reject Full-Run Dataset admission. Projection
and source preservation remain available for failed/partial sessions; they are not admission.
Every admitted nested child must have its earlier canonical parent in the same run/root.

Native fresh-start and terminal journal witnesses, no recorded lifecycle gap, actual start
before accepted input, and a final game-over successor are required for complete run proofs.
Two verified native runs remain insufficient for the unchanged three-component split gate.

The provisional semantic projection resolves runtime referents from the captured frame,
Reads and execution state; runtime identifiers stay only in provenance. Normal player UI
labels become `visible_label`, never teacher labels. Declared unordered card collections are
sorted as multisets without inventing draw order. Visible persistent player/run facts,
selector pile provenance and materialized Reads remain available. This is not a final
Standard token/compute freeze or a new live-policy compatibility claim.

## Data admission and splits

Admission rejects incomplete/uncommitted decisions instead of silently calling a subset a
Full-Run corpus. Exact duplicate records are counted; same identity with different content,
run/step collisions, missing steps and source-accounting mismatch fail. Source blobs and
manifests remain immutable. A failed admission does not rewrite source evidence.

Whole-run splitting also joins runs connected by repeated semantic decision inputs. Labels,
candidate ordering, runtime IDs and future successors do not enter that grouping key. At
least three independent components are required; the gate is not weakened when data is
insufficient. The split seed and deterministic assignment are bound to the logical dataset.

Canonical storage is Parquet with indexed run/surface/family/split fields plus canonical
transition JSON. Loading verifies payload hashes, every indexed-column/record alignment,
source provenance, admission and the recomputed split/logical identity. The physical artifact
ID additionally binds the exact Parquet bytes and producer; logical identity is independent
of a later equivalent physical encoding. This is not a claim of unmeasured large-corpus scale.

## Provisional model representation

Lite, Standard and Full are explicitly `stpd-fullrun-provisional-v1`. All scenes share one
RUN/DECISION/VISIBLE_ENTITIES/READS state format and kind/subject/arguments action format.
Reads require an admitted semantic projection and evidence reference, which is excluded from
model text. Forbidden runtime/native IDs, timestamps, chosen-label position and future/outcome
fields fail closed. Candidate permutation preserves the corresponding text and semantic key.

Standard is not finally frozen until qualified real Full-Run token/compute profiling. The
synthetic fixtures exercise twelve illustrative decision contexts; they are neither the
Platform surface vocabulary nor native coverage evidence.

## Next consumers and non-claims

ModelView, exact frozen-Qwen FeatureSet, TrainingInput, provider-neutral worker, checkpoints,
models and evaluations consume these immutable identities. ArtifactStore, Registry, analysis
and dashboard remain peripheral. No source/test result here proves a real Full-Run corpus,
Human Gold, pretrained-Qwen advantage, cloud qualification or live-game capability.


## Partial-decision follow-up

Useful verified decisions in a failed/partial source remain preserved and projectable.
A separate versioned decision/segment admission path is the accepted follow-up design in
[B pipeline maintenance](B_PIPELINE_HANDOFF.md#partial-recordings-and-practical-maintenance).
It is not implemented by the current complete-run `admit` function. Do not drop rows, renumber
steps or weaken its checks to claim a partial source is a Full-Run Dataset.
