# Prepared PublicM2 campaigns

`tools/public_m2_campaign.py` executes a versioned JSON configuration of already
prepared PublicM2 runs. It retains one command-scoped verified-source session,
executes one provider request at a time, and resumes the same run from each
accepted checkpoint through five epochs. The numerical worker owns the epoch
1/3/5 model and dev-evaluation stages.

```text
python -I tools/public_m2_campaign.py --config /absolute/campaign.json check
python -I tools/public_m2_campaign.py --config /absolute/campaign.json execute --pause-after-accepted 1
python -I tools/public_m2_campaign.py --config /absolute/campaign.json execute
```

The first command checks configuration, the frozen worker source and the provider
adapter file hash. With a new journal it does not read prepared training payloads.
For an existing journal it also verifies every saved object, which may read stored
request/result bytes containing training input or checkpoints. This integrity
check does not qualify cloud execution or reconstruct numerical progress.
`execute` verifies the actual configured curation owner and prepared run before
building a request. Without an exact operator grant and fresh observation it
waits locally, publishes an approval-request file, and performs no deployment.
The pause count applies to the current process session; rerunning the same command
and unchanged configuration continues the durable journal.

## Configuration and identity

The exact `stpd/m2-campaign-config-v1` fields are defined in
`tools/m2_campaign/config.py`. All paths must be absolute and canonical. A config
contains the frozen worker Python checkout and Producer, configured owner and
store locations, store lock, campaign journal and approval directories, the
provider adapter path/hash, the six worker runtime fields, resource and raw-cost
limits, and an ordered list of prepared-run bindings. Each run binding pins its
dataset, allocation, source view, train/evaluation operation IDs, input identity,
config digest, current admission digests, train/dev counts and request ceiling.
Preparation evidence is required: pin the source owner's intake receipt and the
prepared-run receipt by path/hash. Each run's `preparation_sha256` selects its
prepared record. The application checks the linked input and tokenizer digests,
source/view/allocation and operation identities, config, counts and codec-fit
declarations against the immutable artifacts. These receipts are evidence from
the separately reviewed preparation owner; hashing arbitrary self-authored JSON
does not qualify a source. Resumes reuse the already compiled inputs and do not
refit a tokenizer or change the declared chronology qualification.

The application source and frozen worker source are separate. The CLI explicitly
imports `stpd` and `spireagent` from the frozen checkout and rejects a previously
loaded foreign package. Worker source must be clean and match its committed
`uv.lock`. Updating this application does not rewrite a prepared run's producer,
rebuild its worker image, or make a historical runtime observation current.

Run IDs are identities, not arm labels. There is no mandatory three-run scope or
fixed task graph. Each next request contains at most the configured window limit
(maximum 512), ends at the current epoch boundary, and names the latest accepted
same-run checkpoint. Five epochs and the worker's 1/3/5 stages are the supported
recipe. Other training recipes require their own owner and numerical contracts.
Before creating a request, the backend derives the nominal request count from
the actual train windows and rejects a ceiling too small to finish all five
epochs. Use `--pause-after-accepted` for an intentional intermediate stop.

## Operator and provider boundaries

For a pending attempt, the CLI writes `<attempt>.request.json` in `approval_dir`.
An external operator supplies `<attempt>.grant.json`, its
`<attempt>.approved-sha256` pin, and `<attempt>.fresh.json`. The schemas and exact
fields live in `tools/m2_campaign/authority.py`. Grants bind config, request,
attempt, worker Producer, provider adapter, a positive raw USD reservation and
expiry. The provider-specific identity/resource/network settings are in the
grant's `provider` object; the reviewed adapter validates them.

Fresh observations bind the same request/grant and expire after 300 seconds.
They reference immutable account and billing receipts. The serial lane requires
zero external active GPUs. Conservative workspace exposure explicitly excludes
the candidate reservation, which is added once. Durable historical reservations
are reconstructed after restart and remain charged against the batch envelope;
the application does not infer refunds or spend credits to reduce raw cost.
`budget_scope_id` is the external operator's 32-hex batch identity. A shared store
lock serializes campaigns, and an immutable binding beside the store associates
that budget scope with one exact config and journal. A second journal cannot reset
the same scope's reservations. A new budget scope requires a new operator approval
and must still account for all existing workspace liabilities.
The local lock covers this store. The external operator coordinates other stores
and projects using the same provider account and workspace.

The hash-pinned provider module exports `create_provider(settings)` and implements
`prepare`, `submit`, `poll`, and `stop_and_confirm` from the core protocol. It
alone translates approved provider settings into deployment, exact function
identity checks, SDK serialization, transport, and verified terminal cleanup.
An approved provider is executable code and must receive independent review;
a file hash is not a sandbox. Import/construction must be local-only.

## Recovery and evidence

The journal persists request, grant, deployment intent, target, submission intent,
handle, result, stop proof and acceptance in that order. Result acceptance uses
the real public-M2 typed acceptor and rechecks mutable owner authority. Only a
validated terminal result selects run completion. A missing handle after submit
intent never triggers an automatic replacement submission. A saved handle is
polled; a saved result is stopped and accepted idempotently.

On interrupt or error the CLI attempts cleanup of the exact saved target. An
unknown deployment without a returned target, unconfirmed stop, corrupt journal,
or stopped call without a durable result remains blocked for external
reconciliation. Deleting that journal to force a retry is unsafe. The journal's
one-writer lock prevents concurrent commands for that campaign. The store lock
must be shared with other store writers; the operator owns any wider local CPU
or workspace concurrency policy.

Resource checks bound observed peak RSS, available disk, approval wait and command
duration between lifecycle steps. They are cooperative checks; long external
calls also need provider timeouts and an independently supervised execution
deadline. Closing a terminal is not proof that remote compute has stopped.

Successful pause/completion writes a timestamped summary containing run, stage,
checkpoint, model, weights digest and dev-evaluation identities. Reservations are
not final billable charges. Portable synthetic tests, cloud execution, billing
settlement, data chronology, held-out independence, model quality and installation
into a Runtime are separate evidence levels. A stage evaluates the dev membership
already present in its prepared input. Evaluation against a different membership
uses the separate public-M2 evaluation owner and its distinct producer contract.

This command supplies the prepared-run execution lane. It does not prepare new
data, authorize cloud spending, install model exports, or register a Workbench
training service. Those remain their existing owners' responsibilities.

## Root-approved adapter transition after an unsubmitted prepare

A deployment intent without a returned target remains unknown. The application
can retire that slot only through an exact external recovery approval, complete
read-only provider observations and matching local evidence. It never issues
that approval or probes the provider itself. The supported recovery boundary is
the latest attempt in `deploy_intent`, with a saved request and grant, no target,
handle, result, stop or accepted marker, and no historical submission evidence.
Other unknown outcomes stay blocked.

```text
python -I tools/public_m2_campaign.py --config /absolute/new-settings.json recover \
  --receipt /absolute/root-recovery.json --receipt-sha256 <exact-root-approved-sha256>
```

`recover` uses only bounded metadata and source reads. It does not import the
worker, initialize the configured owner, open the artifact store, construct the
provider or perform a cloud call. An external operator must review and approve
the exact receipt hash; hashes are same-user drift checks, not signatures.

Only the `provider_adapter` path/hash may change between settings versions.
Every other decoded field must remain identical: run/input/config and seed
digest, Producer, runtime, window and epoch limits, owner/store paths, admission,
preparation evidence, budget scope and raw envelopes. Original
`journal/settings.json`, attempt rows, request/grant bytes, immutable history,
accepted markers and the store's budget-scope file remain unchanged. Origin
settings remain the budget binding; new attempts require separately issued
grants for the current settings and new UUID. Historical grants are checked
against their issuing settings version and all holds remain reserved.

The exact receipt fields are defined by `RECEIPT_FIELDS` in
`tools/m2_campaign/recovery.py`. The receipt schema is
`stpd/m2-campaign-recovery-receipt-v1`; its action is
`abandon_unsubmitted_prepare_and_transition`.

| Fields | Required binding |
| --- | --- |
| `origin_settings_sha256`, `from_settings_sha256`, `to_settings_sha256`, `to_settings` | Immutable origin, current version and new exact settings bytes; `to_settings` is `{path,sha256}` |
| `journal_root`, `budget_scope_id` | The existing campaign, never a new journal or budget |
| `before_state_sha256`, `before_history` | Exact current state and latest immutable snapshot; history is `{name,sha256}` |
| `attempt`, `request_sha256`, `grant_sha256` | Exact UUID/run/audit ordinal/slice and saved bytes |
| `provider_adapter_before`, `provider_adapter_after` | Full old and new `{path,sha256}` adapter bindings |
| `implementation_closure`, `provider_observation`, `local_prepare_evidence` | Immutable, separately reviewed `{path,sha256}` evidence |
| `reservations` | Every saved grant, in journal order: `{attempt_id,grant_sha256,raw_ceiling_usd}`; no omitted or reduced hold |
| `retained_raw_ceiling_usd`, `replacement_limit` | Failed attempt's unchanged positive hold and integer `1` |
| `issued_at_utc`, `expires_at_utc` | Current external approval window; observation age at commit must be at most 300 seconds |

Recovery JSON is canonical sorted ASCII JSON with a trailing newline. Source
closure schema `stpd/m2-campaign-implementation-closure-v1` has `schema` and a
bounded `files` list of `{path,sha256,size}`. It includes the exact application
entrypoint, all eight `m2_campaign` runtime modules including `recovery.py`, and
the selected adapter. Additional reviewed dependency files may be pinned. Each
file is at most 1 MiB, with at most 64 files. The operator's outer source freeze
must independently pin the application and its validator before launching it;
the internal check does not certify code that is already executing. Execution
rechecks the active closure before worker/provider imports and while opening its
journal session. Original application provenance remains in the retained outer
source freeze (which may also be included as an extra closure file). The worker
Producer is separate and unchanged.

Provider observation schema
`stpd/m2-campaign-unsubmitted-prepare-observation-v1` has exactly:

```text
schema, config_sha256, attempt_id, request_sha256, grant_sha256, provider,
deployment_name, observed_at_utc, complete, exact_name_absent,
active_apps, active_gpu, active_tasks, active_containers, active_sandboxes,
historical_outcome, historical_cost
```

`provider` equals the complete saved grant's provider object. Deployment name
is the exact `stpd-public-m2-<attempt UUID>`. The operator must attest complete
workspace enumeration and complete exact-name lookup, not an empty partial
page. `complete` and `exact_name_absent` must be true, all five active counts
must be integer zero, and historical outcome/cost must be `unknown`. A current
empty workspace does not establish that deployment never happened or cost zero.

Local evidence schema `stpd/m2-campaign-local-prepare-evidence-v1` has exactly
`schema`, `attempt_id`, `request_sha256`, `grant_sha256`, `directory`, `files`
and `no_submit_evidence`. It binds the exact `journal/provider/<attempt UUID>`
directory and its complete file inventory, with entries `{name,sha256,size}`.
The inventory must contain exactly `grant.json` and `runtime.json`. Any diagnostic,
mutation intent, target, handle, result, unknown file, subdirectory or symlink
blocks this route. Diagnostic filenames alone cannot establish that mutation was
impossible; interpreting another adapter's failure requires separately reviewed
recovery policy. Inventory bytes are rechecked immediately before committing;
each of the two files is at most 1 MiB. The saved `runtime.json` hash must also
equal the original grant's `provider.runtime_receipt.sha256`, not just the root
inventory. A missing or mismatched runtime pin blocks recovery.

Recovery does not read or decode the training request body. It binds the original
saved request reference and immutable snapshots. Subsequent execution checks
every saved object hash before worker bootstrap or provider construction; object
corruption therefore blocks the replacement before owner/worker use or payment.

One immutable `recoveries/<sequence>.json` event is the recovery commit point.
It CAS-pins the receipt, new settings, original snapshot and evidence, and marks
the unchanged failed row `abandoned_before_submit`, with unknown historical
outcome/cost and a retained hold. It is neither acceptance nor a fabricated stop
receipt. Replaying the same receipt returns the same event without another
permit; a different receipt cannot reconcile that row again. Expiry does not
undo an already committed event.

The replacement is the same run and exact slice with a fresh UUID. The new row
atomically consumes the permit through `replacement_for`. Audit ordinals count
all rows, including abandoned prepare attempts. `max_requests` counts logical
request slots, excluding only explicitly resolved unsubmitted prepares; it
still limits the numerical recipe without releasing any financial reservation.
An interrupted replacement append cannot cause a second paid dispatch. Only
actual accepted markers determine checkpoint/optimizer progress and completion.
