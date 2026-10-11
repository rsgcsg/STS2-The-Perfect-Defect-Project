# Full-Run data chain

Current authority is the exact source and schema, not a historical green
report. ADR-0006 owns decision identity; ADR-0007 owns delivery and disposition.
The table describes the Human causal/compatibility recording lane; it is not
a universal grammar or training prerequisite for all project records.

| Data | Current writer and purpose | Consumers / retention |
| --- | --- | --- |
| manifest 2, capture profile 2 | Immutable session/environment/profile identity; decision schema 2, disposition 1 and close 1 are explicit capabilities | Annotator audit, calibration, bundle and Evidence; current |
| semantic trace 4 | Sole SemanticBoundaryTracker lifecycle and dispositions; H, S and successor have distinct referenced roles | Causal audit, canonical projection, calibration; current authority |
| canonical transition 3 | Durable proved decisions, exact action, parent/root/native-context lineage and H/S/successor references | Default export, bundle 3, calibration; sole canonical truth |
| execution action-space 3 | Exact native execution membership at S, independently of H admission | Canonical validator and external consumers; never a consumer legality engine |
| frame / Read 2 / content blobs | Frozen fair-player observations and state-bound Reads, content-addressed | Causal/Read validators, calibration and bundles; current evidence |
| invalidation 2 | Accepted capture/persistence failures, unsupported inputs and internal diagnostics with explicit disposition | Current audit, failure counters, incident analysis; not a second decision ledger |
| journal 2 | Native start/resume/end and recording lifecycle facts | Run-boundary qualification and bundle; polling in-progress is diagnostic only |
| session close receipt 1 | Successful close after evidence flush; required by manifest close capability 1 | Audit and bundle; missing receipt is not a completed close |
| coverage 2 / recording status 4 / event batch 2 | Derived summaries and bounded live presentation | UI and tooling; no proof authority |
| native semantic diagnostic 1 / performance profile | Membership calibration and measured costs | Diagnostic tools; unknown is not automatically canonical failure |
| session bundle 3 | Canonical export, complete immutable raw session, hashed producer audit and identity | Evidence typed verifier and transfer; default collection format |
| decision record 2 / bundle 2 | Explicit restricted compatibility projection after canonical append | STPD human_annotator importer and existing Evidence reader; retain until consumer migration |
| trace 1–3 / canonical 2 / action-space 1–2 / ledger 1–2 / record and bundle 1 | Original schema-specific historical evidence | Archival readers and regression fixtures; no new producer, no upgrade of bytes or claims |

Native [Source3](../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md) uses the
existing Annotator Recorder/store/worker with the Connector's frozen public facts
and original input bases. Its ordered captures/declarations do not become
canonical-3 causal proof. Policy Runtime separately writes actual Agent protocol
offers, stored sampled inputs and event/ACK/directive/result joins through
`AgentRunEvidence`; see [original sample evidence](../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md#original-sample-evidence).
Source3 is an optional collection overlay, not a prerequisite for direct Agent
records. Their distinct [Source3 research views](../python/docs/research/SOURCE3_ORDERED_NATIVE_TRAINING.md)
and [direct sampled admission](../python/docs/NATIVE_AGENT_SAMPLED_ADMISSION.md)
retain their own source proof, projection and use rules. None rewrites the
canonical-3/trace-4 or restricted record-2/archive roles above.

The internal inline semantic event is a validator representation, not another
current durable stream. Historical fixture writers live in tests. Exact weak-key
native bindings, async invocation scopes and task completion tables transport
identity; they do not decide proof. Native per-surface adapters remain where
STS2 exposes different authoritative seams. No later frame, timeout, FIFO or
latest-root inference repairs missing H, execution state or successor.

`CausalRoot != DecisionOccurrence`: a selector decision has its own observation,
execution/action-space and action evidence. Human-parent children retain exact
parent and root IDs. Independent native blocking choices retain their native
context without a fabricated Human or GameAction parent. A future STPD importer
must consume that contract explicitly; its existing record-2 importer does not
preserve all canonical-3 decisions. STPD alone owns projection, admission,
training targets, rewards and policy. Platform changes here do not qualify
training or model-driven Full Runs.

Real failures are distinct accepted in-scope decisions with an authoritative
unresolved or failed-closed disposition. Native cancellation/abort, presentation
cancel, unsupported/non-decision and internal diagnostic invalidation are not
failures. Exact witness IDs prevent counting the same failed decision twice.
If disposition persistence/accounting fails, the UI shows unavailable, never
an inferred zero. These counters cannot prove that an unobserved input never
occurred: the final Human gate includes unexplained-loss review.

See [collection and incident response](ANNOTATOR_COLLECTION.md),
[UI](UI_INTERACTION_SPEC.md), [testing](TESTING.md), and
[Annotator contract](../components/annotator/docs/DATA_CONTRACT.md).
