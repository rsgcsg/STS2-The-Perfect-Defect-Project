# Public M0 and isolated native Host source candidate, 2026-10-01

This packet records separately reviewed source changes after PR139. It does not
report combined-head gates or an installed candidate; the integration PR owns
those receipts. Root is the integration owner; normal merges preserve original
feature commits and path-scoped component provenance.

## Accepted source slices

- Public M0 core `6360600745e83976d0df3f1873756621304aaaa8`: distinct generic
  Snapshot public_lite/public_compact input; manifest-only preflight, current
  owner admission, then payload verification. Independent tests passed 47;
  nine malformed cases rejected before payload reads. The 18-route renderer,
  scratch/frozen-Qwen/LoRA matrix passed with exact catalog/score checks.
- Workbench `465f8e4a8a4944a112ad26dd6c48d3c4fee5967a` and registration
  `7cf6ed6e7dcdba314cbb17b8f89bc817c54432f9`, combined at `411fd13c`:
  public scratch training, export re-verification and owner-bound registration;
  the advanced PF/PL adapter requires its explicit matching local Qwen snapshot.
  Independent combined checks passed 109 Python tests with one skip, 222 browser
  tests and 220 browser cases using real synthetic-model metadata. A one-line
  profile-narrowing correction at `38cd252c6624b162cfd93b1708f8ee7861061db2`
  subsequently passed independent scoped mypy/Ruff checks and impact review;
  its authenticated HTTP training/export regression passed again.
  Current-owner loss denies registration and package re-verification; permitted
  owner source requalification is distinct from reading model derivatives.
- Declaration `0c691233aa4d86342da71a72b832ab0a9b820caa`: immutable current
  user statement plus owner registration, exact scoped membership and held-out
  guards. Independent focused checks passed 62. Historical authorization/manual
  exposure remain unknown; a user statement is not machine proof of Human origin.
  Reads use mode=ro and close their connection. SQLite WAL/SHM coordination files
  are permitted; logical data, optional schema and owner initialization are not.
- Native M2 projection/kernel `6d629bcbb6085f88850c46791362b561449d657c`:
  native-v1 confirmed-interaction projection and concrete shared training data;
  valid unlabeled interactive rows advance memory, labels supervise loss only,
  settling observations do not advance memory, and declared chunk boundaries
  detach gradients. Independent checks passed 103; 16 old/new canonical numerical
  comparisons matched losses, model parameters and optimizer states exactly.
  Legacy export implementation bytes and canonical projection stay unchanged.
- Native Host `fd83007f0b552d2a4c4b55d5bdc4390e310d0154`: explicit isolated
  native_window launch, exact process generation/command ownership, authenticated
  Host provenance and shared-profile sentinels. Review repairs prevent ready-phase
  stop hangs, classify Windows query uncertainty conservatively and reject final
  profile/lifecycle environment overrides. Independent validation passed 30
  focused tests plus signal/CIM/environment harnesses and platform-branch
  simulations. No actual Windows runtime was exercised.

Host candidate version commit `dfe2b73d71c8594758a8d642056bf1e71afe1fe6`
sets `1.1.0-rc.24`; BOM commit `761cf4ac0bceb61af310603e54c1cda61b6ca16d`
binds source/tree/digest. Independent review verified all eight other component
identities and the Host public contract digest remain unchanged. Historical BOM
artifact receipts are retained and do not describe the new package.

## Post-gate repairs and bounded real-data execution

The predecessor `be5a9c78` passed the full local macOS gate (2290 Python tests,
five skipped). Its hosted checks exposed a Windows fake-stream cleanup race and
a Linux score-permutation test assumption. Accepted repairs `cb3a6bf9` wait for
actual fake-child stream close, and `b693e7d1` require exact same-layout scores
across processes while permitting at most one finite float32 ULP per action
across catalog permutation; action IDs, ranking and winner remain exact.
These repairs do not by themselves establish a passing new combined head.

Actual native startup rejected the installed unsealed Connector source before
any gameplay delivery. Accepted Host repair `1302cf74` adds the explicit
`--experimental-connector-source` exact revision option only for isolated
native-window launches. It reuses the existing source/artifact resolver and
preserves loaded identity, authenticated provenance, readiness and profile
sentinel gates. Default launch authority remains sealed-only. The current rc.24
BOM now binds this Host source and its path-scoped tree/digest; historical
artifact receipts remain unchanged. Independent review passed 32 focused tests,
eight negative gate probes and four CLI denial cases. Live execution of this
repaired candidate remains a separate gate.

A separate private run on `be5a9c78` registered the user's current training
statement for the previously identified 814-row corpus, preserving historical
unknowns and previous manifests. Owner-admitted public_compact projection used
49 training decisions and 16 diagnostic decisions. Scratch M0 paused at update 2,
resumed through update 10 and exported a verified package. Diagnostic NLL
1.72122475 is effectively uniform (uniform baseline 1.72121967); this is pipeline
evidence, not a usable-policy claim. The diagnostic data's prior exposure is
unknown and physical-game independence unresolved. No new corpus authorization,
clean held-out assessment or model-quality qualification follows from this run.

## Final repair and safe-stop addendum

The next hosted run still exposed a second fake-stream cleanup omission and an
unsupported cross-platform one-ULP assumption. Accepted fixes `78ee6d1c` and
`d6b30e31` close the omitted stream and use finite float32 tolerances for
catalog permutation; exact same-layout fresh-process equality, action IDs,
ranking and winner remain required. A root follow-up also rejects overflow
after float32 conversion. The rc.24 BOM now binds Host source `78ee6d1c`;
this is source identity, not a newly installed artifact.

Reviewed checkpoint cadence `aaa4df58` adds an operational interval to all four
token CLI routes, retaining default every-update saves and every loss event.
Pause and final updates force a checkpoint. Failed resumed saves retain the
last fully verified checkpoint; no automatic retry is introduced. Independent
review passed 12 focused checks and serialized predecessor-checkpoint resume.
The change has no measured actual-data speedup claim yet.

A further private 100-update scratch run used the same owner-admitted 49/16
projection. It completed in 222.584 seconds, with 47.675 seconds in updates.
Tie-aware training top-1 increased from 0.440476 to 0.510204; diagnostic top-1
remained 0.375. Diagnostic NLL 1.72117428 remains effectively at the uniform
baseline 1.72121967. This small, exposure-unknown diagnostic is not independent
held-out evidence. No new 1000-update run or corpus expansion was started.

The user requested completion of this round followed by a safe stop. Preserve
reviewed but unintegrated M2 engine/epoch/worker/consumer branches and declaration
display work. The prepared native public-M0 runner was not executed; its runtime
pin remains unresolved. The ordinary Steam game was restored to the main menu
with profile 2 and its Continue entry visible; no saved-game action was taken.
Final combined local and hosted gate outcomes belong to the integration PR.

## Next gates and limits

Run the exact combined source gates after final repairs, derive a fresh private
Host package from clean BOM-bound source, and verify package/install/load before
native exercise. Keep existing saved native profile, data, models and production
installation as rollback anchors. The new native route does not authorize a
second legality engine or a policy-created native operand. Unknown delivery
stops without retry.

The earlier Managed legacy-M2 three-decision canary belongs to `08656cf5` and
cannot qualify public M0, native-v1 M2 or the new native-window lifecycle. New
public M0 needs its exact owner-admitted model and native generic Connector;
new native-v1 M2/Reset needs its separate consumer pipeline and explicitly
admitted text source. The canonical-814 user statement does not extend to an
additional corpus by assumption. No approximately-10k corpus, clean independent
dev, memory benefit, full-game model success, natural Human recording, public
release, production upgrade or completed Stage1a is claimed here.
