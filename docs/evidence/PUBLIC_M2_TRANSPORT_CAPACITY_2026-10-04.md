# Public M2 formal-shape transport capacity correction

This is a local synthetic engineering incident, discovered before the first GPU
submission. It does not establish trained-model or cloud transport qualification.

The candidate at `21f7a0431e8503eb27bf2dd5ad8d6a6d1aae86d3` passed tiny-model
train, checkpoint, resume, dev and export tests. Those tests did not establish that
the formal model's combined result would fit the bounded remote protocol. A CPU
synthetic update with vocabulary 7,765, width 384, two layers, six heads, feedforward
1,536 and eight memory slots produced a 108,067,322-byte Adam checkpoint and an
approximately 36,015,707-byte stage export. Their independent payloads already
exceeded the 134,217,728-byte result limit before framing and manifests.

The first incorrect fact was the assumed result capacity, owned by the research
remote transport and its provider adapter. A small numerical fixture was not a
faithful capacity regression. The observed consequence would have been a rejected
epoch-stage result after the worker performed training; the checkpoint would not
be durable locally. No GPU training was submitted with this candidate.

The correction keeps the existing uncompressed binary framing and bounded request
semantics. Remote, provider and the private durable journal must agree on the
result cap; the private controller must reject disagreement before owner admission
or provider submission. It does not alter model shape, input, memory, windows,
training targets or checkpoint/export contents. The final exact wire measurements
and executable release smoke accompany the correcting source change.

Before a paid candidate run, the local release check must serialize a formal-shape
synthetic Adam checkpoint, epoch 1/3/5 stage deltas and resumed requests, including
headers, manifests and the actual transport encoding. Check valid boundary frames,
over-limit rejection and corrupt payload rejection. Inspect the provider's existing
large-payload path separately; local serialization is not proof of cloud acceptance.

The existing image and prepared run retain their original producer identity as an
untrained technical candidate. A changed source requires a new producer, image and
run binding. Historical receipts are not rewritten. Any subsequent build, probe or
training requires a separate current cost reservation and execution authorization.

## Why the limits exist

These are application resource guards, except for the provider execution timeout
and separately authorized monetary budget. A byte or time guard does not establish
a dollar cap. Identity, hashing, continuity and unknown-delivery checks are
correctness requirements; increasing resource limits does not relax them.

| Limit and owner | Purpose / origin | Measured demand and margin | Exceeding it | Evidence needed to adjust |
| --- | --- | --- | --- | --- |
| Request 256 MiB; remote/provider | Self-set bound on input, resume state and event-history transfer | Largest input 144,644,396 B + checkpoint about 108,067,323 B + tokenizer 322,329 B; conservative 6,100-event capacity frame with 2 MiB metadata allowance is 261,419,066 B, leaving 7,016,390 B | Reject locally before submission | Exact-pack each production request; repeat largest-input/history capacity and SDK/RAM checks when scope or schema changes |
| Result 192 MiB candidate; remote/provider/journal | Self-set bound on durable checkpoint and export transfer; replaces insufficient 128 MiB | Synthetic epoch-5 result 144,098,758 B; 57,227,834 B remains before the separate small provider envelope | Reject; do not discard required artifacts to fit | Formal-shape epoch 1/3/5 wire smoke, exact/over-limit and corruption tests, matching caps at every layer |
| Window 98,304 tokens; engine | Self-set aggregate activation/work bound across full observation and action encodings | Train maximum 93,026 (5,278 spare); dev maximum 95,356 (2,948 spare); not a measured GPU-memory guarantee | Reject whole input; no text/action truncation | Largest backward window and no-grad dev window on the chosen GPU; keep the same agreed window semantics across arms |
| 512 windows per attempt; remote | Self-set finite work and recovery boundary | Largest epoch has 1,023 windows, requiring at least two attempts; smaller tiers have 352/681 | Reject request; split at valid window checkpoints | Measured duration, cost and result size with the same recovery/epoch boundaries |
| Function 800 s, startup 120 s; pilot controller/provider | Explicit provider lifetime settings, plus separate absolute client/cleanup deadlines | Actual GPU throughput unknown; no duration margin claimed yet | Preserve unknown status and persisted checkpoints; stop/query the exact App, never silently resubmit | Real bounded pilot timings and a fresh total-cost reservation; include startup, dev/export and termination |
| At least 2 GiB free; private controller | Self-set local disk headroom: max(2 GiB, 3 × incoming bytes + 3 × result cap) | First-pilot space is checked live; all-attempt retention demand is a separate calculation | Stop before new persistence/submission; no automatic deletion | Count retained request/result/CAS bytes across the complete batch; check actual disk and peak RAM separately |

The known SDK path uses binary framing and pickle bytes with blob upload/download,
not base64 or an inline giant gRPC message. Local inspection establishes the selected
path, not successful service acceptance at these sizes. The first real returned
checkpoint/stage remains the cloud transport gate.

The request capacity check uses equal-length synthetic payloads, 6,000 window
events plus 100 lifecycle events, and explicit nonsemantic padding. The separate
metadata-only inspection found 25,201 escaped bytes across the six existing source,
input, experiment and run manifests; the added 2 MiB allowance is over 83 times
that total. This is a conservative size probe for the current batch, not a valid
training request or a promise for arbitrary future manifests. Production still
checks the actual complete frame before any provider submission. Both request and
result body transport caps agree between remote and provider; the provider's small
outer envelope has a separate derived allowance.

## Separate follow-up efficiency work

The 512-window cap implies at least 55 attempts for the six five-epoch arms under
the present whole-epoch boundaries. Startup, transfer and retained journal bytes
must be included in batch estimates; the per-attempt free-space check alone does
not promise enough disk for all retained attempts.

Current explicit resume at epoch 1 or 3 regenerates that stage's dev/export to
reconcile a possible crash between checkpoint and readout. The synthetic five-epoch
sequence therefore returns readout epochs `[1, 1, 3, 3, 5]`. This can add two dev
passes per arm (50,604 decision evaluations across six arms), beyond the three
distinct requested readouts. Reusing an already verified immutable stage closure
is a separate optimization to evaluate before the full batch; this capacity repair
does not remove its validation or silently count repeated work as free. The first
pilot ends at epoch 1 and does not incur those later repeated readouts.
