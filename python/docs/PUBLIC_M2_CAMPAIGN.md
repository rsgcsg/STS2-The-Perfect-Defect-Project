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
adapter file hash. It does not read training payloads or qualify cloud execution.
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
