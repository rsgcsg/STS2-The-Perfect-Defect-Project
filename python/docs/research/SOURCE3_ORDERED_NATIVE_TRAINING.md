# Ordered Source3 native training representation

The code-owned contracts are `stpd/ordered_source_spec.py`. This representation
is separate from historical text/S0, Source2 and synthetic native sources.
It is an implementation candidate, not a producer, runtime, Human, training-use
or scientific acceptance receipt.

## Model input and supervision

The existing structured M2 encodes the current native logical observation's
persistent content, interaction kind/stage/prompt/content, all authorized public
referents, actual focus, and complete finite native catalog. It uses the existing
`stpd-native-structured-full-reference-v1` InputSpec: I and F are off. Catalog
arguments score the complete C; they do not become a second observation writer.
Identifiers, ordinals, timestamps, operational receipts, requests, provenance and
future labels do not enter model features.

An ordered training step contains `observation`, `catalog`, nullable
`chosen_action_id`, `reset_before`, nullable `reset_reason`, and `evidence`.
The observation/catalog values come from independently verified original blob
references. `evidence` retains the original raw artifact, bundle content ID,
run/epoch/segment, publication cut, input prefix ordinal, stream row, input ID
and full capture/catalog references. It is lineage, never a model feature.

N is cross entropy at an original input basis against the complete original C.
It requires the original Source outcome to be `exact`, `match_count=1`,
`delivery=delivered`, and the entire selected action to equal exactly one catalog
member. Matching an action ID alone is insufficient. Publications, empty catalogs,
unmapped/ambiguous choices, rejected/partial/unknown delivery all have no N label;
complete ordered bases still consume memory. No artificial Wait target is added.
Z and O are unsupported here: delivery, the next observed page, and a terminal
summary cannot manufacture causal-successor or future outcome supervision.

The first fixed recipe is K1/d96/carry/N with the existing CPU2/TBPTT4 engine.
The same closed graph/reset presets also support K8 and reset before each actual
advance. Carry preserves W values across completed chunks; gradients are detached
at TBPTT boundaries. Missing a local N label does not freeze shared parameters:
later labelled losses can send gradients through unlabelled frames inside the
same chunk. Training this loss does not itself prove that memory learned useful
history, and a detached checkpoint cannot restore an old autograd graph.

## Original order and known losses

Only the Evidence owner's typed Source3 verifier can admit originals. Source3's
native before-body input prefix ordinal and original lifecycle input fences order
input acquisitions. Terminal persistence `sequence` is retained as a reference,
never used as input order. The merge visits publication p, then each independent
pre-capture with original pre-cut p in input-ordinal order, then publication p+1.
This visits every admitted original exposure once, without independent-anchor
prefix duplication. The existing occurrence/revision/coherence validator remains
mandatory; this adapter never rewrites revisions or backfills from Current.

The versioned conservative admission retains the complete recorded prefix of an
original attachment epoch. A missing/incomplete publication or input basis,
unproven basis order, or recording pause excludes that event and the remaining
epoch. A new original attachment epoch starts a new explicit W reset. An actor
change alone supplies no native reset proof. Omitted rows and boundaries remain
in the immutable admission report and raw archive. Capacity failures reject
materialization; they do not truncate history or silently discard labels.

This is recorded public-capture history. It preserves full captured public facts
and catalogs but does not prove every internal game frame was captured, a Human
attended to it, or a particular Agent acquired/acknowledged it. Epoch context and
seam coverage remain in original metadata. Declared Human, Agent protocol and
Agent native-UI cohorts are separate; source kind never grants permission or
machine proof of Human origin. Legacy recordings cannot be backdated into Source3.

## Lineage, use and evaluation

The shared ArtifactStore retains original archive → exact immutable admission
analysis → isolated projected partition → training input → run/attempt →
checkpoint/model. Verification replays each selected original archive and compares
the projected bytes and report. Changed verifier/spec/code produces new identities;
old report bytes are not upgraded. The ordinary projected parser proves model
semantics, not the raw archive join; its capsule-byte metric remains false.

The application uses the existing curation/use/Gold ledger. Original runs,
timeline relationships, sources and prior exposure remain protected across
reprojection or copies. Train, dev and test are whole-run related partitions;
an unseen test claim additionally needs current exposure and permission evidence.
Caches may reuse immutable bytes or structural projections under exact
spec/code/input identities; they cannot reuse trainable encoder activations or W
across optimizer updates as though those values used current parameters.

N evaluation reports eligible unique original input choices as its denominator,
with unlabelled and excluded counts separately. A whole-game recorded-capture
denominator additionally requires original new-launch and terminal boundaries and
an uninterrupted admitted epoch. That is recorded coverage, not native gameplay,
Human, Commit/successor, victory, policy quality or G2/V1 qualification. A larger
cohort and a natural whole-game evaluation remain separate actual execution gates.
