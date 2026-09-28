# Native pile-selection connection check, 2026-09-28

This is an execution report, not full-game, Human-training or learned-policy
qualification. The public report contains summaries and hashes; raw observations,
local configuration, model weights and decompiled game source remain private.

## Exact scopes

- Reviewed source anchor: `develop@889aa980015ed68543b1cb63fd34c276b7e30e2b`,
  tree `5474c0791beda092378cb35b9c5733f24bb9cadb`.
  PR #77's synthetic combat/reward/map/terminal regression passed full run
  [36374756869, attempt 1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36374756869).
  Integration run [36376540639](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36376540639)
  succeeded with `reuse / verified_execution_same_tree`, referencing that full
  execution. It is not another fresh dual-OS full execution.
- Active local application: clean `7a7312b504526e0edf5086f1a9f816b84939abdf`;
  model `005af0725595c3c0f1a59aede0916c77034e49e881da650e1742830c6e1489a6`;
  exported manifest SHA-256 `b6130eb1d3d710d3824f9dbb1fa39133d4ab5cf6115f305f43f1a78026037347`;
  adapter code SHA-256 `8b0047aafbd35d2cee83058037a6efdeaecbd1c3e5aa6bd26a2574c3316acfe2`.
- Same loaded game instance `393e355582904af986db96dcf1efde88`, STS2
  `0.111.0 / 41cef1ea`; Game Mod rc.17 / Annotator rc.13. Connector source
  `ed40f0ebbfd59587eca0d5688f9c271062d85753`, artifact SHA-256
  `c1c2dc7d5a673b523943c50a00d35884ef3f64af8bcf4a1bcf7c0c362cb3aa5a`,
  module MVID `204ec969-386e-42d9-ac23-a47f3aa2e10c`.
  The inspected game assembly SHA-256 is
  `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`,
  MVID `57785517-0b16-42b9-8b36-bad6fb28384b`.
- Installed Policy Runtime remains rc.10 from source
  `60d97f7235a4cf8ce4618362c0eca6802eca0e6b`, code SHA-256
  `74334ca24c90194b4c97b9b9ffaacb2fdc93f3ef53d879998f2f9c11b6c05600`.
  The subsequent test-only component source identities do not relabel this
  installation. No Mod or Runtime package was installed for this check.

## Native mechanism and design boundary

The inspected native `CardModel.TryManualPlay` requests a `PlayCardAction`;
receiving the card UI input is not the completion of that action. For
`Hologram.OnPlay`, STS2 first awaits block gain, then awaits
`CardSelectCmd.FromCombatPile` for one discard card, then moves the returned card
to the hand. Its upgraded form gains five block. The choice is a real native
pause for input inside the enclosing card action, not an invented future state.

`NCombatPileCardSelectScreen.OnCardClicked` updates its selected set. Where
native preferences do not require manual confirmation, reaching the selection
limit calls `CompleteSelection`, resolves the choice and removes the overlay.
Other native preferences can require an explicit confirmation. The adapter must
use those current facts rather than hard-code Hologram or always auto-confirm.

Repository owners at the source anchor:

- `components/connector/host/NativeUi/NativeCombatPileSelection.cs`:
  `TryBuild`, `DescribeCommands`, `Start`; exact top overlay, current preferences,
  public cards, complete selection choices and execute-time binding validation.
- `components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuFrameBuilder.cs`:
  `CaptureCore`, `OrderLegacyTextActions`; current card-operation/selector owner,
  current native order and public bindings.
- `components/connector/host/PlayerEnvironment/TextMenu/TextMenuSession.cs`:
  `Observe`; complete choices only for the current ready, complete page/cursor.
- `components/policy-runtime/src/runtime.ts`: `admitWholeDecisionBundle`, execution lifecycle
  and `manifestCompatibilityReason`; complete-menu scoring and pinned environment
  admission remain separate from native legality.
- `python/stpd/policy/token_port.py`: `TokenPolicyAdapter.decide`; the model
  returns one finite score per exact candidate and cannot submit native actions.

The public text profile describes semantic current-page state and choices,
not mouse coordinates, Godot nodes or device-specific callbacks. Those stay in
the desktop native adapter. A fast or simulator Host may implement the same
profile with its own authoritative bindings and identity. Schema compatibility
alone does not qualify its game behavior or allow reuse of the desktop manifest.
No new fast Host or simulator was implemented or tested here.

## Observations, with their limits

1. A preceding engineering begin/cancel check reached `combat_card_operation`
   through actual native input and returned to combat, with both deliveries
   confirmed and the controller released. Two earlier attempts stopped before
   gameplay because begin was unavailable; their failures remain. A later native
   Escape input coincided with restored begin choices. The evidence does not
   isolate focus, panel visibility or another native readiness predicate as the
   sole cause. No native guard was removed.
2. Actual small-B short run `run-2efeade3-a09e-470a-9513-d8618b84aa36` performed
   16 scores and 16 delivered native inputs: eight card begins, one untargeted
   confirmation and seven cancellations. Submission-limit recovery returned
   Human/released; explicit Stop sealed 86 events, with evidence verification
   reported `pass` by the real Workbench. The confirmed card was Hologram+.
   This is intermediate-run connection evidence, not useful-play or full-run
   qualification. Seven begin/cancel pairs do not justify deleting those actions.
   Events file SHA-256:
   `910abab955b23f6bb2895e2d12002d753723b37455dbea71b98acbaf85ef5bf5`.
3. The user reported seeing the discard selection page. Typed observation at
   **04:15:35.632 UTC** also identified `native_combat_pile_selection`.
   Earlier observations were combat; automated screenshots appeared older.
   This does not establish persistent wrong-owner reporting. It also does not
   explain the entire delay between delivery and selector appearance. Native
   focus warnings alone do not prove that cause.
4. A separate, predeclared **engineering** selector check at **04:21:14 UTC**
   used the unchanged typed SDK and Connector client. Recording was closed,
   exact game/artifact identity was checked, a controller lease was acquired,
   and one current bound `select` was submitted. The complete menu contained
   nine selection choices and two potion openers; none was removed. The first
   available selection was chosen by the test driver, not by the learned model.
   Result: `applied / delivered`; the next observation was combat. Hand count
   changed 4→5, discard count 9→8, the selected card identity appeared in the
   hand, energy stayed 2 and block stayed 5, and end turn was available. Controller
   release was confirmed. No retry occurred. Receipt SHA-256:
   `5f3077b4d99c22fce8b577da97a62cba66479656dcd5688d13bf08d687cb9192`.
   These are delivery and subsequent observation facts, not Annotator-certified
   causal successor or Human labels; no training record was manufactured.
5. The saved, complete selector snapshot was separately passed through the
   real installed application's NDJSON `stpd.policy.token_port` using the same
   model export, config and manifest. It returned **11/11 finite scores** with
   matching candidate digest/count and request ID; process exit 0, about 0.98 s
   including process startup. No scores from this offline check were executed.
   No training, model download or data/Gold-purpose change occurred.

## Remaining limits

The real sequence covers one card-selection continuation, not every selector,
card, reward, scene or complete game. The model's quality, earlier delay's root
cause, untargeted mouse continuation coverage and future Host equivalence remain
separate questions. Polls and screenshot timing do not become causal witnesses.
A regression using synthetic pages tests Runtime mechanics only; it does not
replace these exact native observations or establish full-game support.
