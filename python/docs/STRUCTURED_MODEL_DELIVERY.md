# Structured model delivery and fixed-model evaluation

`stpd.structured_policy_installation` binds the current closed S-M2-0 export to
caller-owned exact Policy Runtime environment requirements and support. It shares
the reviewed `structured_export` verifier and `StructuredPolicyAdapter` validator.
Only the existing sampled text-menu-v2 projection, I/F off and NDJSON port 2 are
admitted. New profiles require an explicit versioned owner extension; this API does
not qualify native-flat input or full-scene execution.

The public binding signature is `bind_structured_export(root, export_path,
config_path, manifest_path, *, manifest_id, policy, requirements, support,
binding_root=None)`. It returns the closed configuration and Policy Manifest.
`validate` rechecks that pair; `inspect` reports package/backend readiness.
Application registry entries are trusted and server-bound: pass resolved absolute
configuration and manifest paths to `arguments(entry)`. Arguments always select
`stpd.policy.structured_port --package ... --manifest ...`; downloaded metadata
cannot choose a module, executable or extra argument. Binding is installation
metadata preparation, not activation or runtime qualification.

Packages require their own canonical manifest, safe tensor-tree weights, exact
weights shape/dtype/parameter validity, graph/projection/runtime and declared
trusted source identity. Loading does not require private training payloads or
traverse training ancestry. Stored old exports retain their original identity;
changed trusted code invalidates compatibility until explicitly migrated and
reverified. No model ID or package code pin is relabelled to make an old model new.

`stpd.workers.structured_evaluation` provides a separate immutable evaluation path.
Construct `StructuredEvaluationRequest(source_id, model_id, operation_id,
partition, intent="fixed_model_descriptive")`, where `model_id` is the Store Model
artifact ID, `partition` is exactly `dev` or `test`, and `operation_id` is the
application's 32-hex operation ID. `prepare_structured_evaluation(store, request,
producer, *, authority=None)` verifies a single heldout partition through E2's
`verify_protocol_source_partition` and the model's own closed package, then
publishes an `analysis` evaluation-input Manifest. The full archived raw joins and
source bytes are reverified; a parser flag never grants heldout qualification.

`run_structured_evaluation(store, evaluation_input_id, producer, *, authority=None)`
reverifies input bindings, loads fixed weights, starts zero W per declared run and
replays the actual reset/advance sequence from its partition start. It performs no
optimizer updates. It publishes an `offline_evaluation` Manifest with one immutable
report payload, preserving exact model/package/weights, source schema/partition,
producer distinctions, original source IDs, complete candidate scores, labels,
loss, rows, actual run/group boundaries and known-label denominators. Its claims
are engineering descriptive: three groups alone establish no independent
scientific generalization or model strength. Replaying the same immutable input
with the same producer returns the same immutable result identity.

Applications own permissions, original-source use/Gold restrictions, explicit
heldout claims, reservations, attempt authority and final selection before invoking
these APIs. An injected `authority.assert_current()` is checked at preparation,
per evaluation row, before immutable payload/publication, and before returning a
candidate. Applications may additionally wrap Store writes with their existing
attempt fence. The domain creates no job ledger and does not select an application
completion slot. Evaluation inputs/results parent only model and their selected
heldout source; they do not add evaluation data to a model's training ancestry.
Restart means declared fixed-weight replay, never optimizer resume or test-driven
model selection. Existing standalone combined synthetic training checks preserve
their historical engineering scope.
