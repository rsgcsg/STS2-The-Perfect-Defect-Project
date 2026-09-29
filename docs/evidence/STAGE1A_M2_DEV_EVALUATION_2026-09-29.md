# M2 local development evaluation — observed product receipt

Observed through Safari and existing authenticated local Workbench API. Source `40c647ff84114216ccd164055986646ef0ce41c3`; completed observed 2026-09-29T08:41:28.987574+00:00. No private pages, weights or credentials are included.

- Model: `ebe9dd8387adeb0f7ef9b903106a9ef9c80fcbd90de16a28c5dadfa6b2bd64a9`; existing training operation `e614d9678f4a4cf7a74253bfd17c7655` remained completed and unchanged. No new training.
- Training source: `63c0198a37d18039e47bce29a3abe6e4cd81b09902a9452bf52fb49fb363d3ef` (537 accepted input labels from the supplied full-run recording).
- Dev source: `26e75e6ea837fcc6f616f8617e55677a97d74651c997f95e37795a5529ab70f1`; 31 accepted labels in two recorded fragments. Different fragments are not proof of independent full games.
- Explicit UI evaluation operation: `74b7b81a971c473bb6d7b02e18c8c58f`. User-facing progression: source selector → explicit start → pending → refresh → completed → linked report. No game operation or model load.
- Report: `e109af7fa9ff5a0649f8829f4c6c2f08657e98f9d1410379fdd7a0bb4fe537a8`; evaluation input `badc43c9c8d96b927d5eb60816f38da6e2e7d50da823757413892c2d82bf5fdc`.
- Producer-recorded metrics: 31 decisions, top1 `0.45161290322580644` (14/31), MRR `0.6403225806451613`, NLL `1.3735411701349802`. Exact train/dev rendered overlap count 0; curation semantic overlap false.
- Protocol `independent-source-retrospective-v1`; native-run independence unproven, strict deduplicated benchmark false, model-selection exposure unknown. This is a dev pipeline check, not a memory-benefit/Reset comparison, strategy-quality or win-rate result.
- Source checks: combined evaluator/export/registration 22 passed; existing curation/report regression 19 passed; Node console 160 passed; scoped Ruff/mypy and diff-check passed. Candidate hosted CI has not yet been started.
- Installed service moved deliberately to this isolated candidate, with private locked dependencies and its own node_modules. Existing Evidence rc20 pin retained. Game Mod, native game state, model bytes and existing failed reports unchanged.
- Actual UI found stale model/export text claiming no evaluation globally despite the new report. Presentation correction `2184bf898253f9525c8a2865ad7cf443e99724d5` replaces those global claims with artifact-scoped wording; its Node file has 160 passing tests. Immutable train/export provenance stays unchanged.

## Review and remaining work

Independent review covered the data-owner reservation, private worker precondition, model completion lineage, semantic-overlap diagnostic, CPU-bound child, UI API contracts, and the normal merge of evaluator with the registration candidate. Source/tests do not establish independent games or a beneficial memory model. The worker remains an internal caller-admitted seam; the supported Workbench entry owns current ledger admission. No second permissions ledger or model-specific Host logic was added.

Existing `training` source publication is the local development-data category; this model-specific operation records evaluation use through the existing curation owner. Exact training source/evidence/session/run overlap is refused. Common rendered pages in distinct sources are reported, not automatically treated as identical game origins; Gold/Test protections still inspect the full connected group. This retrospective protocol does not replace ADR-0007's stricter fixed benchmark.

The next model experiment is a separately trained Reset-K1 control using matching admitted input and computation budget. It has not run here. Larger data, K8/Gate, Qwen/Z/O, native whole-game coverage, cross-Host parity, team/distribution and qualified release remain separate work. The local dev report must not be promoted to any of those conclusions.
