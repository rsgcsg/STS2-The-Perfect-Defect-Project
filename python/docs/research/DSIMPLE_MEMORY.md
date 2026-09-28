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
