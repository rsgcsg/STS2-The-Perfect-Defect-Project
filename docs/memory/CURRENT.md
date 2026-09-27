# Current project context, 2026-09-28

Use `rsgcsg/STS2-The-Perfect-Defect-Project` for new work. Reviewed source anchor:
`develop@26fd8cb3a7a20887e64f25c96b67f646bdbb3a71` (through PR #69).
Resolve live GitHub refs and deployed identity before work; current source/runtime
authorities override this file. It is a bounded handoff, not the running-version owner.

## What is implemented and what was observed

- **Game / Human:** the last verified installation is Game Mod rc.17 / Annotator
  rc.13, installed from `94d76ffd5a56727e344813e31bc14ed4a7a16aeb`.
  Its separately closed mouse canary has 9 accepted Human input labels, including
  two same-input proved cancellations, and 3 canonical decisions. Two other
  cancellation attempts remain unproved; the final action lacks a successor
  before session close and stays unknown. This is narrow mouse evidence, not
  full-game qualification. [PR #63's bounded receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/63#issuecomment-5856320923)
  preserves installed identity separately from its source integration.
- **Workbench:** the Mod opens the same no-login local Workbench. PRs #61/#62
  add explicit verified recording import and sample preview. The active service
  source is `0b9d49451a955bd2454c53b21544911e260c5bc1`, the same tree as
  reviewed PR #67 integration. Its separate local activation preserved the profile
  and 328 existing artifact identities/payload descriptors. Actual browser use
  confirmed 9 input labels / 3 canonical decisions, then explicitly created one
  3-decision training dataset with insufficient independent-run split status.
  No training or Gold claim followed. The earlier 5-label/zero-canonical import
  remains intact. [PR #67 activation receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/67#issuecomment-5857559811)
  separates source/test, application activation and game qualification.
- **Data:** PR #54 adds the shared curation authority; #55 separates Human-input BC
  split semantics; #57 adds observed-input sequences and the rc.17 Evidence consumer.
  Existing records keep their identities. Human input labels are not canonical
  transitions or proof of gameplay effects. Sequence reading does not grant training use.
- **Models:** PR #49 adds explicit small-B shared-observation training selection.
  PR #59 adds an experimental observation-only GRU memory prototype and independent
  reset control with synthetic tests. Its
  [Python run](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36315089482)
  passed; it is not a registered training recipe, full M1, learned quality result,
  or replacement for installed models. Candidate scoring is linear in action count
  at fixed input lengths, not linear in all sequence tokens.
- **Team:** PR #60 documents one Workbench and host-neutral team coordination.
  Cloud, LAN and a designated team computer are deployment choices. Current compute
  remains a fixed provider target; no general multi-host scheduler is claimed.

## Current packet and next gaps

- Local recording import and sample preview are implemented and narrowly observed.
  PR #64 integrates the verified local canonical source path, with full Linux/Windows
  checks. PR #66 adds a persistent per-store use ledger and explicit dataset
  preview/publication; PR #67 simplifies the result/source navigation. Both have
  their own hosted Python Linux/Windows evidence and exact-tree merge receipts.
  In-place preparation of the existing library completed: one known-use and one
  unknown-use dataset, no unprojected sources. Payloads were not relocated or copied.
  PR #65 separately integrates spool cleanup ordering.
- PR #68 is merged: metadata catalog, exact dev report
  summary and model/view links, with sealed-test/unknown-format rejection. It reads
  recorded metrics and parent identities, not complete training lineage or quality.
  Its source review, synthetic browser checks and hosted Python checks must remain
  separate from the active PR #67 Workbench; it is not automatically installed.
- PR #69 is merged: an explicit three-step scratch small-B command, a store-owned
  operation slot, durable use recording, and exact model/report links. Its
  [Python checks](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36335640672)
  tested merge object `abd644263c7e1696400bf1420e5a7e07ab5a17fd` (Linux 1471 passed / 2 skipped;
  Windows 1433 passed / 40 skipped; both 21 subtests). Integration
  [36337070054](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36337070054)
  reused that exact tree with fresh guards; it was not a second Python execution.
  The canonical public-BC path requires public-H bindings and a nonempty grouped
  train/dev split. The synthetic browser receipt remains separate from the active
  PR #67 Workbench and from real-data training, inference registration or quality.
- The Human-input local-library candidate reuses the original verified recording
  manifests and the same purpose/use ledger. It connects typed Human SOURCE
  publication to the existing explicit small-B worker; it does not manufacture
  canonical transitions. Saving a source does not establish an eligible train/dev
  split. Human v2 conservatively collapses sessions sharing visible input; more
  recordings do not guarantee more groups. Reports retain unknown native-run
  independence. This follow-up needs its own source review, selected hosted checks
  and application activation; PR #69's green does not cover it.
- Human-input BC is a separate data mode. Input labels must not fill missing
  canonical actions or causal successors. Existing configured stores keep their
  history. The user superseded the temporary read-only preference: continue using
  the single existing library, without old/new tabs, relocation or duplicate payloads.
  Explicit preparation attaches the persistent use owner in place. Verifiable old
  training/test Dataset purposes stay recorded; historical training use is not
  reconstructed. Unknown old use restricts Gold where
  relevant, rather than blocking all ordinary training/test selection.
  Gold reservations are not a claim of global history completeness across arbitrary
  stores or of scientifically validated labels. Interrupted publication checks for
  an exact existing result on explicit request; it never silently publishes again.
- Complete the training-job, report/analysis and archive journey on existing services.
  Keep everyday controls simple; expose detailed identity and recovery information
  when needed. Do not build a general cluster manager ahead of this working path.
- Fill remaining native input families and causal successor coverage separately.
  Native drag-back/_Process/untargeted paths require exact owner evidence, not timing
  guesses. Full-scene execution and model quality remain independent gates.

## Remaining Platform non-claims

The [in-run text plan](../plans/TEXT_STS2_IN_RUN.md), [collaboration contract](../AI_COLLABORATION.md),
[Stage1a table](../plans/STAGE1A_TASKS.zh-CN.md), and
[B0 historical handoff](../evidence/STAGE1A_B0_HANDOFF_2026-09-23.md) retain their roles.
No real-data training, paid compute or broad research admission is granted by this
status note. Preserve failed evidence, datasets, Gold reservations, model weights and
old provenance. Source/test, installed/live, Human and scientific claims remain distinct.
