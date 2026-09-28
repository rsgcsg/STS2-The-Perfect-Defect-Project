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
readout is `normalize(candidate + MLP([candidate, memory_read]))`, followed by a score MLP. The
page Transformer is called once per `advance`, regardless of candidate count;
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
No real-data training, policy quality, native independence, or runtime
qualification has been measured.
