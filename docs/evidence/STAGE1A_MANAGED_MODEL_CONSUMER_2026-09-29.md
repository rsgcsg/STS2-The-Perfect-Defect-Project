# Managed public text consumer and exported-model smoke, 2026-09-29

This is a bounded local engineering receipt. It records an installed private Host
package and two exported models using its public Python consumer and JSONL driver.
It is not a public release, Godot/native-Human parity, Policy Runtime HTTP run,
continuous full game, strategy evaluation or memory-benefit result.

## Exact identities

- Source/test integration for the successful model runs:
  `d654991cef4d029b8e8d62f58496761969df7a09`, clean.
- Host candidate `1.1.0-rc.19`, path source
  `faf5a8e3d9f68a2ec6f86633529a7dcb90558f00`, component tree
  `37dc7acf3fd8ec6e36b7c6b71a084f26bf76e867`, source digest
  `b278b2c6dfd47c153df2152197d9d99cefe9f48ba338b43c523b1c3d84df8240`.
  Public contract digest
  `7a3799d66059f17fa473a73593eea07f592adabc638bfb5d618445966b40b0ba`.
- Root privately packed and installed those unchanged Host bytes from clean
  integration `cf5046b605218d8e8a8f764e8a83ea3edbe0e15b`. Archive SHA-256
  `780515fcc47057b94157a3d3d791312b2fe34e0c3d89a932143f3f69298e0ab7`;
  complete installed-directory SHA-256
  `961f060d0ca43b7fbf8574203796d88577a174b8ba9653bef4215446a67a45f6`.
  The experimental pin uses the actual component source/tree and actual archive
  and directory hashes. It does not update a production consumer pin.
- Exact Managed upstream `wuhao21/sts2-cli@d11aa883b582dd68bd39b331f3370746b30d447e`;
  admitted patch SHA-256
  `bf3ac3d1aadee5ce556687745d2d64d37d0eea47e2b67904ca7c97b080d8b89e`.
  Private Managed DLL SHA-256
  `dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93`,
  MVID `145c95e9-ace0-42b5-bb46-3fef292ac645`.
- Game v0.111.0/41cef1ea; original and runtime game DLL SHA-256 both
  `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.
  The installed package's owning `managed-exact.mjs audit` verified this tuple
  before model startup. No production game files or old candidate were replaced.
- Python 3.11.15 in an exclusive development environment, Torch 2.13.0,
  tokenizers 0.22.2; unchanged lock SHA-256
  `ac0c9af1e21ed72059ebba8c7a0366ebc68e26755ef22af38f02787fb84d3654`.
  Imports resolved to the isolated source and exact installed public package.
  Locked dependencies were prepared from the existing uv cache; no model download
  or cloud call occurred.

## Owning changes and faithfully retained failures

The public consumer now supports atomic text Snapshot plus game continuity and
bound text submission. One driver owns one Managed session, serializes commands,
invalidates failed reset state, separates request IDs across episodes and raw/text
routes, and refuses stale continuity. The Host retains its complete legal catalog
and execute-time validation. The model supplies only scores over that catalog.

Independent review found that an already-delivered native action followed by a
successor projection failure could leave the old catalog usable. The repaired
Host returns the known delivered receipt without a successor and taints the
session. It never downgrades known delivery to `unknown` or `not_delivered`.
Malformed transport/results and legal `unknown` prevent further Python mutation;
valid navigation and not-applied/reobserve results retain their own meanings.

Actual package use revealed two additional lifecycle defects. The .NET assembly
fingerprint helper compiled inside the pinned installation, and Python imports
could create `__pycache__` there. Fingerprint sources now build in a unique temporary
directory; Python activation validates the full pin, eagerly imports the public
package with bytecode writes briefly suppressed, and restores the prior setting.
Repeat activation and failure recovery are tested. No extra-file hash exclusion,
replacement pin for dirty bytes or global permanent interpreter flag was added.
Other Host commands that write `.local` outputs are not covered by this narrow
immutability guarantee; `source-audit` needs an explicit external `--output`.

The failed receipts remain:

1. Selecting an older rc.15 native directory for rc.19 audit was correctly rejected
   with `Managed candidate source diff does not exactly match the admitted patch ledger`.
   The correct already-existing candidate was selected after exact comparison;
   the old directory was not overwritten.
2. Before the package fix, audit added 24 .NET build files to an otherwise unchanged
   package. The first model command exited 2 before child startup with package
   identity mismatch. Original member bytes were identical; no dirty package was
   accepted or re-pinned.
3. After the package fix, a run with input seed `M2HOST20260929A` exited 2 with
   `episode_provenance_unverified`: the Host correctly normalizes O to zero, but
   the experiment compared against the original input. It had zero observations,
   policy calls and submissions. Source `d654991c` now reuses the existing
   `require_canonical_game_seed` owner to reject ambiguous aliases before reset
   or child creation. Two new regression cases failed on the old runner
   (16 passed, 2 failed); the repaired combined suite passed all 22 tests.

## Actual successful model runs

Both use the public installed `ManagedPlayerEnvironment`, `observe_text_menu` and
`submit_text_menu` methods; no private Host imports, scripted action substitution,
raw-command fallback, model training or live registration manifest was used.
Each fresh process requested seed `M2H0ST20260929A`, character Defect and A0.
Native episode provenance matched the seed. The first public Snapshot reported
`DEFECT` and `ascension=0`, checked before scoring. Character is an experiment
setting, not an admission or generalization claim in the model export.

| Run | Exact model | UTC window | Observations / scores / submissions / delivered | End |
| --- | --- | --- | --- | --- |
| Reset-K1 | `14a4f5df0a8ef5d6205e4bb71201b3e4f16224468838ef13be75bd7e5a8b7dec` | 11:43:57.084–11:44:02.655 | 2 / 2 / 2 / 2 | exit 0, submission budget exhausted |
| M2-K1 | `ebe9dd8387adeb0f7ef9b903106a9ef9c80fcbd90de16a28c5dadfa6b2bd64a9` | 11:44:46.898–11:44:51.939 | 8 / 8 / 8 / 8 | exit 0, submission budget exhausted |

The Reset command allowed 4 policy calls, 2 submissions, 8 observations and
30 seconds. The M2 command allowed 8 policy calls, 8 submissions, 12 observations
and 40 seconds. These are cooperative experiment limits with bounded transport
calls, not a hard inference-kill SLA. A returned native receipt is accounted
before checking elapsed deadline. Neither run reached a terminal page.

Both returned `engineering_smoke_complete`, `policy_runtime_http=false`,
`qualification=engineering_only`. The runner closed its owned child in `finally`;
a separate process read at 11:45:36 UTC found no matching driver or native child.
Installed directory SHA-256 remained exactly unchanged after each run. The wall
windows include startup and fingerprint work; they do not measure policy or
Connector throughput separately. Different submission budgets are not a fair
model-quality comparison.

## Source and package checks

On Host source `faf5a8e3`, the component `npm run check` passed: 226 Node tests,
4 explicitly skipped proprietary gates, 11 Python consumer tests, repository/docs,
syntax and package checks. Independent review reran the staging test and four
Python activation tests. A temporary installed package passed two consecutive
actual audits and two independent Python imports, retaining its whole-directory
hash. The later `b15964a3` commit only formats two Python files; root scoped Ruff
and mypy passed after preserving the earlier lint failure.

On clean combined `d654991c`, root ran the 22-test runner/activation suite, scoped
Ruff and mypy, component identity, BOM, `git diff --check`, project closeout and
check planner against `develop@54cdcedc88e35c19e6c1533548dd51ba98fcbf0c`.
Every command exited 0; planner selected full. Hosted current-head CI must be
recorded separately. Prior PR103 full is not this new consumer/runner gate.

The actual model receipt establishes a bounded macOS Managed connection, not
coverage of every new scene. It neither establishes Live/Managed input equality
nor proves cross-episode model memory reset in a real terminal-to-new-game run.
Current exports and this experiment remain text-menu-v1 and observation-only M2.
The separately proposed text-menu-v2 profile does not relabel them.

## Legacy S1 decision import boundary

The first PR #105 full run `36564851362` (attempt 1, head
`4120e6401df711cf52686df76aabb6f07becf565`) exposed three Linux failures
in the local-model readiness and S1 manifest tests. The reported policy source
digest was `8d22bde95d5b2b8bf05d356a8048fd4040e103c8c8ba000ac9d0d51f5fd8166d`,
while historical v6 pinned
`f5c00cfea077b7ac52289f84d2f055e5db1bb1206ced086d41fc22229eb721ee`.
The three failures also reproduced locally before the repair; they were not
model scoring or native delivery failures.

The first incorrect dependency was the eager `stpd.environment` package export:
importing its decision projector also imported runtime collection, training smoke
and installed Host activation. Commit `a85936ef45e1c81cb1832963b166891a1b779eea`
makes only the three runtime-collection exports lazy, preserving their public
API. S1's explicit source closure still equals its fresh-process loaded source
set; no actually imported module is exempted from identity verification. A new
subprocess regression fails on the original import chain, checks the absent Host
modules, then resolves the real legacy collection exports. Existing collection
behavior regressions also remain.

The historical v6 manifest is byte-for-byte unchanged. New v7 changes only the
adapter identity (`1.0.2`) and implementation digest; trained policy, weights,
config hashes, support restrictions and Runtime consumer pin remain unchanged.
The final implementation digest is
`48acd27bcdd8e70834f27661e46cf24a3a418ca9cd931b53ff98b5835f9cec83`.
Default S1 entrypoints now reference v7; this does not install/reload a policy.

On final working bytes preceding that commit, the private Python 3.11 interpreter
ran `pytest -q -ra tests/test_policy_adapter.py tests/test_local_models.py
tests/test_environment_collector.py tests/test_environment_identity.py
tests/test_runtime_collection.py tests/test_managed_memory_smoke.py
tests/test_host_runtime_client.py`: exit 0, **178 passed, 1 skipped**, 25.70 s.
The skip requires the optional exact Policy Runtime installation; no substitute
live test is claimed. Scoped Ruff check/format and mypy returned exit 0. The
initial Ruff import-format failure was corrected and retained in the earlier
local log. Independent review confirmed the owning import boundary, preserved
legacy exports, exact closure check and immutable v6. A new hosted gate is still
required for the corrected PR head; the original failed run remains evidence.
