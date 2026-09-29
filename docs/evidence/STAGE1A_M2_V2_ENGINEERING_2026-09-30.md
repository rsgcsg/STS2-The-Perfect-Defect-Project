# M2 v2 engineering chain, 2026-09-30

This is a candidate engineering receipt, not Stage1a acceptance, Human evidence,
a new independent dataset, a memory-benefit result or a full-game policy result.
The source dependency is PR107's exact `d0b458327d19f50661843b0d119a7eede2c2eb62`;
its hosted result is separate from this research/application increment.

## Shared boundaries

The report importer validates the immutable report and every ordered event,
complete current-page menu and exact selected action against the archived Host,
candidate, game, session and seed identities. Imported sources are explicitly
`managed_control_input_stream`, `engineering_control`, actor `unverified`.
Archive consistency is not independent actor attestation. Nothing promotes these
records to Human demonstrations or invents a causal successor label.

The new M2 profile uses `text-menu-v2` and renderer
`stpd/m2-canonical-current-page-v2`. The verified source report and training-input
projection config bind this profile through the immutable tokenizer/run/checkpoint
lineage; export and online scoring validate that identity explicitly. This does
not mean every artifact or raw tokenizer has a separate profile field. Existing v1 data,
configuration bytes and default entrypoints remain v1. Old Runtime package
binding/port entrypoints reject v2; there is no implicit consumer promotion.

Memory remains observation-only in this candidate. No artificial executed-action
or feedback input is added. Candidate scoring reads one common memory state and
does not write persistent memory once per candidate. Repeated observation uses
the existing cache. The six-step run is not a study of memory effectiveness.

## Actual six-step training and export

Executed 2026-09-29 14:52:19–14:52:24 UTC on clean source
`b21bfcf0ce20c0644c3207c08db24223ce49ac2a`, with Python 3.11.15,
Torch 2.13.0 and two CPU threads. The isolated development lock digest was
`ac0c9af1e21ed72059ebba8c7a0366ebc68e26755ef22af38f02787fb84d3654`.
The private bounded driver called the real import, purpose owner, observed-run
preparation, `execute_memory_run`, export and package verification APIs. Exit 0.

| Object | Exact immutable identity |
|---|---|
| Existing sealed Managed report | `a608d995442b014e64fecb18e3a0b140f5e1fc01d7e1feefab645abb96b2cc1a` |
| Typed engineering source | `260865a769943d7e97f6b8b1e01c2d7655a42f97bead42d490f88ab36bf3b58e` |
| Training input | `845d6843f1124aa05579c3ae2c4dcf70ab11f31a3ad212d7df5bd544d2d4d81a` |
| Run | `6270caf60ad34f59544bc80369f1332d5bf12af478eb3bf4de8414ad3e1aecc0` |
| Checkpoint | `c5769ea12920c6cd3a104fd223b962aa7b65bd429da45c088f6137b2c0e0e054` |
| Model | `bfbda14406d5385dc8834573caf0f14b9d57b9be9a5a4b661f17368ec3dde591` |
| Result | `f8150ff8fdd02fa5df34028dcd3d870a0f152dd3e78e343433fc5d89ac2284fa` |
| Portable model.json SHA256 | `da16d900b4512411c9cd633d9d82a091caf1f6adc3dd5395739d3a3e61f0613b` |
| Weights SHA256 | `9aef2e6d681413222963e9e3625cb8b90105fe689d5ad74e6ec036780b11851f` |
| Tokenizer SHA256 | `37d491e14efa34bb6fe943d524df75ccf14266f0f165011b6c51a777b8a19b54` |

One episode contained six observations, with candidate counts `[1,6,3,6,3,3]`.
Page token counts were `[5868,2588,2697,2588,2697,2787]`; no truncation occurred.
The training purpose was reserved before tokenizer preparation, with exactly one
source-use and one run-use record for this operation. All six causal-successor
masks are false; the sequence is not marked a complete trajectory.

The driver compared all action IDs in their original order and all scores from
the reloaded offline model against the exported online scorer at every step.
Scores were finite and compared using FP32 `rtol=1.3e-6`, `atol=1e-5`;
repeated-observation cache outputs matched exactly. An independent read-only
review subsequently reloaded the immutable source/run/result lineage and
reverified the portable package, hashes and purpose/use ledger. This is replay
parity on the training episode, not an independent dev evaluation.

## Actual bounded model-to-Managed run

Executed 2026-09-29 15:01:01–15:01:11 UTC from clean source
`8245d55ebe8119492992917300cfebbf52cbcd80`, using the preceding exact portable
model and the temporary Host package described in the
[Managed Workbench receipt](STAGE1A_MANAGED_TEXT_V2_WORKBENCH_2026-09-29.md).
The package retains its original `35dcf11a` source identity; newer test-only
source commits do not relabel that installed temporary package.

The actual CLI was `python/tools/managed_memory_smoke.py` with explicit private
Host/package-pin/candidate/model paths, `--input-profile text-menu-v2`,
`--seed M2H0ST20260929A --max-policy-calls 8 --max-submissions 6
--max-observations 10 --max-seconds 60`. Exit 0. Paths remain private; no weights
or raw observations are published here.

The model chose every action using the complete current menu. Result:
one Defect A0 episode, six observations, six policy calls, six submissions,
two confirmed native deliveries and `submission_budget_exhausted`.
The remaining submissions changed only text interaction state. The dedicated
environment was closed by the runner. No terminal was observed. The same seed
was used deliberately for an integration check, not a generalization claim.

The synthetic runner regression also checks the real Host unknown-result shape:
two text selections precede a native leaf whose bound action remains in the
unknown receipt. It stops without retry, preserves unknown classification and
closes the child. The earlier fixture incorrectly invented unknown delivery
on a text-only action; that fixture and its consumer check were corrected before
this run. Host/Connector legality and protocol were not weakened.

## Remaining work

Workbench report admission and the new profile's automated regressions belong
to this candidate; their final combination/hosted checks must be recorded on its
own PR. A source-test result is not a fresh production install. Explicit v2
Workbench training recipes, native Policy Runtime admission, matched Reset/dev
experiments and cross-host scenario coverage still require their own owner
work. The six engineering choices do not close the roughly-10k Human-data target.
No old model, Human recording, test split, game save or installed user environment
was rewritten by these experiments.
