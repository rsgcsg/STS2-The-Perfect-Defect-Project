# Experimental D-Simple M2 computation

`ExperimentalDSimpleM2` is a standalone mathematical prototype. It is not a
registered Stage1a recipe, a runtime policy, a training dataset projection, or
native game evidence. The existing D-Simple scorer and its checkpoints are
unchanged.

For one caller-owned observation, `advance(page, memory, ...)` places the old
`K × width` memory, an optional light encoding of the **confirmed previously
executed** action, optional **known public native** feedback, current page
embeddings, and `K` learned write queries into one `TokenCore.contextualize`
call. The output at the write queries proposes the next `K × width` memory.
Query slots have learned identities; no slot is assigned an invented semantic
role. The optional gate applies the same learned sigmoid rule to each slot:
`M_new = gate([M_old, proposal]) * M_old + (1 - gate) * proposal`. Initial
memory is zero. `reset_each_step=True` replaces only the old-memory input with
zero, retaining exactly the same parameter structure as persistent M2.

Each candidate is encoded independently by its own embedding, width-preserving
kernel-3 convolution, GELU, mean pooling, projection, and layer normalization.
An action-conditioned dot attention reads the fixed `K` memory slots. The
readout is `normalize(memory_read + MLP([memory_read, candidate]))`, followed by
a score MLP. The page Transformer is called once per `advance`, regardless of
candidate count;
`score` reads memory without writing it or calling the page Transformer.
Candidate work grows with the complete menu and fixed `K`; this does not claim
constant cost in candidate count or game-level speed.

The caller must bind the exact page and complete legal candidate catalog, call
`advance` at most once for each accepted event, supply only an actually
executed previous action and confirmed public feedback, decide when a new run
starts, and retain/detach memory at an explicit training boundary. This module
cannot verify event provenance, delivery, legality, or the validity of
feedback. In particular, a Human-selected action is not implicitly an actual
delivered action. Omitting either optional input means it was not supplied;
the model does not synthesize one. The `step` convenience call validates the
whole catalog before the write. All inputs are checked for device, dtype,
shape, finite memory values, vocabulary, and token capacity; nothing is
silently truncated. The module and core should be placed on matching
device/dtype. A frozen core is used in evaluation mode but must still preserve
gradients through its input embeddings; `advance` does not use `no_grad`.

With the test-only width-8, one-layer scratch core and vocab 32, measured
parameter counts are 2,305 for `K=1`, 2,361 for `K=8`; enabling the optional
gate adds 136 to either. The core itself has 872 parameters. These counts and
the synthetic gradient/permutation tests establish the computation graph only.
More generally, beyond a supplied core, the ungated module has
`2 × V × d + 12 × d² + (K + 18) × d + 1` parameters, with vocabulary size `V`
and width `d`; the gate adds `2 × d² + d`. The separate action and feedback
embedding tables account for `2 × V × d`, which is expensive at a large Qwen
vocabulary and width. This is only a small-vocabulary prototype. A future Qwen
integration would need its own measured memory/cost study and perhaps a
narrower embedding design; neither is implemented here.
No real-data training, policy quality, native independence, or runtime
qualification has been measured.

## Offline sequence computation (experimental)

`stpd.models.dsimple_sequence_training` adds an in-memory sequence loss and one
optimizer-update function for this M2 prototype. It is deliberately separate
from the existing Stage1a decision-only worker and its shuffled decision plan,
checkpoint schema, export, and registered recipes. It does not admit a source,
tokenize real observations, publish a model, or run a policy.

`MemorySequenceWindow` contains one complete episode **prefix**, beginning at
position 0 with `reset_before=True`. All observed steps must be contiguous and
belong to that episode. The whole window, including every candidate key and
token vector, is checked before any model computation or optimizer update.
Opaque action keys bind a label to exactly one candidate in the supplied full
catalog; the caller remains responsible for proving the catalog is complete
and for keeping labels separate from input tokens. A window beginning partway
through an episode is rejected: finite burn-in cannot honestly reconstruct
an unknown earlier memory state. Padding may surround a contiguous prefix;
missing observations cannot be bridged.
This first compute path admits at most 64 window positions, 32 gradient-bearing
observations, and 65,536 input token IDs summed across the complete window.
It fails on overflow without shortening the candidate catalog. These are
engineering guards, not a measured peak-memory guarantee or a 10k-menu claim.

Burn-in steps write memory under `no_grad`; memory is detached at the first
learn step. Every later observation, including an unlabelled one, writes
memory with gradients across the learn span. Only steps selected by
`loss_mask` contribute listwise loss, averaged over included labels. A bound
label may be present but excluded by the mask; at least one included label is
required. `reset_each_step=True` uses the identical parameter structure as
the persistent model but discards old memory at each observation. The caller
must instantiate and train the two controls independently, with separate
optimizers and equal initial parameters when comparing them.

Optional `previous_actual_action` and `public_feedback` tensors are strictly
caller-owned. The sequence code never turns `label_key`, a Human choice, or
unknown delivery into an executed action or native feedback. They may be
omitted while the observed page still updates memory. No source in this
module proves execution, event continuity, episode identity, or eligibility.
The tiny synthetic cue regression checks only the computation and optimizer
path; it is not a policy-quality or generalization result.

### Bounded long-episode TBPTT

`train_memory_episode` accepts a caller-owned `MemorySequenceEpisode` with all
observations from position 0 in order and an explicit initial reset. It
preflights the **entire** episode, including every candidate binding and token,
before the first optimizer step. The caller must set positive total observation
and input-token ceilings; chunk ceilings default to 32 observations and 65,536
input tokens and may be lowered. A step that cannot fit its chunk, an exceeded
total ceiling, a missing initial reset, any later reset, or an episode without
labels is rejected rather than shortened. This path does not change the older
64-position full-prefix loss and one-update API or its bounds.

Each chunk reads the carried memory value and writes every observation in
order. An unlabelled observation still writes memory; only labelled steps score
the complete supplied candidate catalog and contribute listwise loss. Chunks
without labels write under `no_grad` and do not update parameters. After each
chunk, memory is detached; a labelled chunk makes one optimizer update. The
returned scalar is the mean of the losses seen at all labelled observations,
measured as training proceeded. There is no gradient across a chunk boundary,
and memory carried after an update was computed with the preceding weights.
This is the usual TBPTT approximation, **not** exact replay or full-history
gradient descent. Every call begins with zero memory, so episodes are isolated.
The `reset_each_step=True` comparison uses the same model structure with its
own optimizer and initial weights.

This is only a local synthetic compute path. It does not resolve incomplete
pages, admission of the observed source, split independence,
real-data training, export, runtime registration, or policy quality. The
observed-source bridge owns its bounded prefix and whole-episode projections;
the TBPTT function does not convert windows into episodes.

## Observed-source bridge (experimental)

`stpd.fullrun.memory_sequence_bridge.project_memory_windows` converts a
**caller-verified** `ObservedInputView` with a fixed tokenizer into M2 windows.
It uses the existing current-page text projection and `encode_texts`, retaining
the complete current catalog and exact action keys. Each output records its
source, stream, reset reason and ordered event IDs separately from the tensor
window. A window starts at an explicit observation reset and contains every
observed event up to its end; this is a memory episode, not proof of a complete
native game run. Missing pages, absent resets, bad bindings and over-limit
segments produce diagnostics rather than a shortened or repaired history.
Accepted choices, including a valid system-navigation choice in a verified
source, may contribute loss. An unlabelled but observed page still writes
memory. A witnessed Human input, navigation and an unknown delivery never
become `previous_actual_action` or `public_feedback`; both optional inputs
remain `None` in this bridge.

`project_memory_episodes` reuses the same caller-verified view, current-page
projection, fixed tokenizer, ordered reset-origin segments, exact choice
binding and source-event IDs. It emits whole `MemorySequenceEpisode` values
for bounded TBPTT. The caller explicitly sets total model-observation and
input-token ceilings; a whole segment over either limit is diagnosed without
shortening the history or candidate catalog. The older window projection and
its 64/32-step limits remain separate.

The episode bridge normally rejects a settling page. An explicit
`max_settling_events` allowance permits only source-visible pages marked
`status=settling` with the current text-menu profile, complete public facts,
an unavailable empty executable catalog, no selected action, and the same
runtime/environment identity as interactive observations on both sides.
Such a page retains its event ID in the ordered source mapping but produces
no model step, score, token input, memory write or reset. `step_event_ids`
aligns one-to-one with model steps; `settling_event_ids` names only verified
skips. The allowance is per episode and defaults to zero. Missing observations,
other unprojectable pages and identity drift cannot use this skip. Numeric
source-sequence gaps alone never prove settling or authorize a skip. The
episode bridge still neither proves a causal successor nor invents previous
executed actions or feedback.

The bridge does **not** establish source trust, training purpose, ledger
authorization, independent-run splits or real-data admission. In particular,
the Human source manifest's `purpose=bc_input_observation` is not a training
claim: the SpireAgent curation owner keeps that claim in its ledger. A future
caller must check that owner and fixed split before training. The current
Human witness source admits native-input labels; the generic bridge preserves
a separately verified navigation label without manufacturing one from Human
evidence. No M2 worker, checkpoint, export or runtime path is added here.
