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
