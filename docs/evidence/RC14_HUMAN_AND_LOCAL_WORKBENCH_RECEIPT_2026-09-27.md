# RC14 Human and local Workbench receipt, 2026-09-27

This is a bounded source, verification and runtime receipt. It includes only
public identities, aggregate counts, commands and evidence limits. Raw
observations, bundles, private paths, credentials and model weights are omitted.

## Human text-input closeout

- Session: `session-20260927T055010Z-ecf7bfcd76e24037bec43288050d4b73`.
  The owner attested mouse input; the attestation explicitly has
  `machine_verifiable=false`. The close receipt reports the session closed.
- Producer identity: Game Mod `0.2.0-rc.14`, DLL SHA-256
  `bfb14ab382e7db1688340376e82ab5ed9bc0accd049bd1d6ce943109b2f374fe`, MVID
  `1bb73b36-6faf-4347-9f6d-665604d0f513`, native runtime
  `8e58eb96fdd44dd98db540cf145a2427`, Game Mod source
  `a76f3bf5cc4ed5f04d6dbb6ec013081eeac529c5`, Connector source
  `a76f3bf5cc4ed5f04d6dbb6ec013081eeac529c5`, and Annotator source
  `22b26d4099595b6d99df326161087dfa8e16e151`. The prior [RC14 install and
  cold-load receipt](TEXT_MODEL_WORKBENCH_PROGRESS_2026-09-27.md) records the
  exact install context; these identities bind the separate RC14 capture.
- The core compatibility audit passed with 5 valid records, 0 invalid records,
  0 invalidations and no errors. This count is not the native or canonical count.
- The native-semantic audit passed with 15 accepted/successful, exact-once
  action memberships. Separately, the canonical stream contains 15 proved
  transitions: 13 `PlayCardAction` and 2 `EndPlayerTurnAction`. The membership
  audit is not itself causal-successor proof; these counts are distinct from
  the 5 core compatibility records and 18 text-input labels.
- The separate text-input stream contains 18 begin labels. Its verifier passed;
  the Evidence CLI serialized all 18 immutable rows. The five-count difference
  between begin labels and card-play transitions is not cancellation evidence.
  Mouse target-selection and cancel labels were not captured.
- The faithful CLI regression fixed by [PR #51](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/51)
  reproduced a `mappingproxy` JSON serialization failure. The Evidence component
  suite passed 145 tests. The private-bundle command shape was
  `npm run evidence -- verify-human-bundle <private-bundle-dir>`; it exited 0,
  reported pass, and emitted all 18 rows. PR #51 head
  `f508d7a1a05c5c93aaf52b98929367fe2494c839` merged normally to develop as
  `9f895e8b533a25abe971d169000ae81760b8e76d` with tree
  `6794fe8feda674e099329176859e40ea37634190`. Hosted CI run
  [36301622023](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36301622023)
  completed successfully, including Linux, Windows and portable jobs.

## Separate RC15 installed and loaded identity

The installed Game Mod was `0.2.0-rc.15`, artifact SHA-256
`bbb865799bf24cb78ec45e6c7cee11db5164825e94d1af36afbfb4f53f08c3c6`, MVID
`0b833363-7a80-4fc2-9e3c-e1d430c456de`. The loaded report returned `pass` with
no errors. The game was later normally stopped before the subsequent RC16 preparation below.
This RC15 result binds Platform source `37dbddce66c3b2809f68cf3dc2cfb5e70fea64bf`
and digest `33655ea8bcc91aa91fbb9dc218abe09243a9dcc9a80e93d43a784741d877a277`,
Connector source `a76f3bf5cc4ed5f04d6dbb6ec013081eeac529c5`, Annotator source
`22b26d4099595b6d99df326161087dfa8e16e151`, and Live UI source
`b05264d0cde28f94a9cc3123abb8ead53aebd364` with digest
`9b4b679960f6ad5464d10ab5973ab412b36cb691dcff119394ddf5046f63c862`.
This RC15 identity is separate from the RC14 Human session; no evidence was
transferred between them.

## Local Workbench entry and process lifetime

PR #50 integrated the Mod-to-Workbench entry at develop merge
`ea25a735891e310be69cea436e962ec13f3c6392` (source
`eedc01a2457706bca608dc524efed7f917075039`). Its full run
[36298069381](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36298069381)
passed; integration run
[36301126913](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36301126913)
reused the verified identical tree and passed fresh guards.

The root Agent used CUA to click the in-game Workbench button, opening the local
Workbench at port `60317` without cloud login. That CUA smoke browsed “本机
资料” through entries 1–25 of 326 and opened an experiment detail containing
metadata only. After CUA normally quit the game, the lifecycle doctor reported
`game_running=false`; the same Workbench instance
(`66cd4b895988f0afff750c898654135d`) remained healthy and CUA reloaded the same
metadata view. This prior-instance result demonstrates local browsing after
game exit; the old service was later normally stopped.

A later, separate CUA smoke used an isolated candidate runtime and a new local
profile while the game remained closed. Its Workbench instance
(`0cc9fd87fd43c66a5e1afc1b9a789b16`) on port `61159` showed the local home with
no login prompt; selecting “本机资料” loaded entries 1–25 of 326. It did not
modify the old profile or data. Candidate home fix [PR #52](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/52)
at `7c37e15800204756424bc21ac7321b1313fe1949` has 95 Node and 17 Python tests
passing; CI run
[36302306868](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36302306868)
was superseded and cancelled after the required normal base alignment. The
current source combination is `2ed18832bd3bb58c6a751dd73c8663428b35e32b`; its
[Python-scope run 36302839895](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36302839895)
is pending. The candidate remains unmerged. These were automated CUA observations, not Human
actions or Human evidence. The game was closed throughout this browser-only smoke, before the later RC16 launch below.

## Subsequent mouse continuation source candidate

Source `11938165425833ad4f7364edeba2d77adf995f6a` adds bounded single-enemy
mouse target and explicit-cancel input observation. Independent source review
checked the exact game IL and the same-input native callback path; component
identity anchor `ed40f0e` identifies Connector rc.4, Annotator rc.12, Evidence
rc.17 and Game Mod rc.16. Existing SDK/consumer pins and historical evidence
remain unchanged. Target selection is accepted input, not a claim of card Commit
or successor. Untargeted mouse release and ally-targeting stages stay settling.

At clean candidate `1f5cd47958ee3ade3dfa1d3d0e5bda38d911425a`, the supervisor ran
`npm run check:exact-game` successfully (297 Annotator Core tests, source/tool
checks, exact native builds with zero warnings/errors); the Connector witness
filter passed 8 tests and `npm --prefix components/evidence run check` passed
145 tests. `check:identity`, `check:bom`, closeout and diff checks passed. The
planner selects full. Hosted CI and the new exact loaded/Human canary are not
claimed by these local checks. This new candidate does not relabel the RC14
session above.

Subsequent candidate installation occurred at `2026-09-27T07:30:12.491Z`;
`verify-loaded` returned pass with no errors after cold launch. RC16 artifact
SHA-256 is `a1e514bac79d45a523d445f090631bc6f461da68a698c2f922cef0f700da0548`,
MVID `2c23259c-64ed-47c9-af2f-4c32fe5816fd`, runtime
`9d5e71fe94ad4a22901344c7887f4d14`. Platform compiled-source digest is
`320f2e40222e8c240e55ff60dde3b4310168e99137b48328694d9c1a45a484a4`;
Connector and Annotator source anchor is `ed40f0ebbfd59587eca0d5688f9c271062d85753`.
Rollback retains the prior RC15 bytes and configuration. The Agent resumed the
existing run before recording, then opened a distinct session
`session-20260927T073248Z-02329ab4821b48d7acc8f0d2f9672eac` for owner-operated
mouse target/cancel checks. No Agent gameplay occurred after recording start.
The session is awaiting Human actions and attestation; no new Human result is
claimed. Hosted CI remains separate from this candidate load observation.

## Next work and limits

- Complete PR #52 review and hosted CI, then record its integration state. The
  candidate fixes the signed-out home prompt; the local workspace inventory is
  still only a bounded metadata view.
- Capture explicit mouse target-selection and cancellation labels before making
  claims about either interaction.

This receipt does not establish complete-game or Full-Run Human qualification,
complete cancellation/target coverage, research admission, model quality,
training, or cloud independence for features outside the observed local browse.
