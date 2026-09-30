# Row-2 native mouse input canary

Date: 2026-09-30 (Australia/Brisbane). A bounded user-operated recording,
independently read and verified after closure. This is not a Full Run,
zero-unknown execution claim or new memory-model qualification. Raw game
observations and the bundle remain private; only identities and aggregate
results are recorded here.

## Exact running identity

The installed source is accepted PR117 `de23ce167fddc5bfe149cd29f2773299b261845d`,
tree `5ff4b641ac5c6f072203c9b782c8877c2adae86d`, also the tree of accepted
develop `a887e4f1c75eb48066d00c49b279abee51abd30c`. It is not PR119's
confirmed-interaction memory candidate.

- STS2: `v0.111.0/41cef1ea`, macOS arm64.
- Mod DLL SHA-256: `188d080e15712ddadf461c5aa883534464b3b393eb37dfb6f3ea1e7dbcbdd9df`.
- Mod MVID: `23880303-49fd-4f11-be2d-1ad7c5ff471f`.
- Connector / Annotator source: `330fc960c1a42f70988e976465c4baaa0674c4c7`.
- CollectionTool release: `a4ae13461633f51cfa4cebc85ddec12b630db61c6dae4f459a05140ce4d89af9`.
- Capture profile: `human-full-run-read-rich-v4`, contract digest
  `1ed42e6709aaa46b9d38f2f72ce778e731d17aee3f06bf0153ea369b936ff459`.

The session was created at `2026-09-30T01:02:47.525321Z` and closed at
`2026-09-30T01:03:22.047765Z`. The user reported completion of the requested
mouse take/cancel/play/end-turn capture. Human origin is an explicit owner
attestation; a verifier cannot infer it from file integrity.

## Separate populations and results

| Population | Observed result |
|---|---|
| Row-2 Human input witnesses | 10, all `accepted_input` / `exact_unique` |
| Input types | 6 `begin_card_play`, 2 mouse `cancel_card_play`, 1 mouse `confirm_target`, 1 `end_turn` |
| Capture order / completed append watermark | Ordinals 1–10; corresponding watermarks 0–9 |
| Frozen menus | Complete, interactive; external controller inactive |
| Native accepted / committed actions | 3 |
| Durable canonical transitions | 2: play and end turn |
| Unresolved final successor | 1, explicitly `session_closed_before_successor_boundary` |
| Canonical invalid records / invalidations | 0 / 0 |
| Recorded Read results | 14 successful, 0 failed |

These are different populations, not interchangeable success counts. Cancelling
an unsubmitted card interaction is a real Human input, not a delivered card
play. Closing the recorder before the last successor boundary does not erase
the final Commit or invent its successor. This stream is partial Human input
witness evidence; it does not prove that every physical input was captured.

## Executed verification

1. The fixed producer CLI `dotnet <verified-tool>/sts2-human-annotator.dll audit
   <closed-session>` returned exit 0: `pass`, 2 valid canonical records,
   0 invalid records, 0 invalidations, empty errors.
2. `CollectionTool` verified the pinned release identity and executable inventory,
   then packed the closed session into a new private directory with
   `human_origin_attested`. Original recording bytes were not edited.
3. Accepted-source `verify_human_session_bundle` returned `pass`, no findings,
   using the `human-session-bundle-v3` typed verifier. Independent review also
   called the typed input-stream verifier: all 10 rows passed.
4. The closed input stream count and SHA-256 matched its close receipt:
   `d844551c59665096e6d663ac089811c2996d1043a7368f896a370f66bdae152f`.

The verified bundle content ID is
`2c2f595e9f2cdce7b85d325628659be2c5e896343c14528179afa381700baebc`.
The final verification observation was `2026-09-30T01:09:56.635032Z`.

## Candidate consumer replay, separate from native capture

A private read-only probe of candidate `ea06ab2c0dc3deedfccc524173dc713c13a40484`
used the public verified-bundle importer, source publisher,
`load_observed_input_view` and `project_memory_episodes`. Writes were confined
to a new private temporary result directory and artifact store; no training/dev
purpose admission was made.
It produced 1 episode, 10 steps, 9 prior-action inputs and no diagnostics.
The first step has no history. Each later history input exactly matches the
earlier completed witness's frozen menu action, with its physical completion
sequence at or below the current row's captured watermark. The two cancellations
remain labels at steps 2/4 and become prior inputs at steps 3/5.

Using each actual prefix in turn gave 45 comparisons: prior page, candidate,
label and history tensors were unchanged when later rows were absent. Feedback
was null and delivery/causal-successor masks stayed false. A fixed 256-byte
tokenizer with no fitted merges and a tiny untrained shape were used only for
tensor-contract validation. Model advance, scoring and optimizer entry points
were guarded against invocation. No forward pass or training ran; original
bundle hashes and executable sources remained unchanged. This is consumer
replay evidence for this small stream, not model-package compatibility or a
proof of every ordering case.

No tests of potion, terminal, nested out-of-order capture, fresh-game memory
reset, model policy quality or cross-platform native behavior follow from this
capture. No data was uploaded, automatically admitted for training, or used to
rewrite old evidence. PR119 source/CI and its future installed model behavior
remain separate gates.
