# Host Runtime Engineering Rules

This file adds Host Runtime-specific rules to the Platform root `AGENTS.md`.

This component runs the real Slay the Spire 2 runtime. `headless` remains the
stable default CLI/runtime mode; an explicitly isolated `native_window` mode
uses the same Host lifecycle for visible operator canaries. The component name
is Host Runtime. It is not a simulator, strategy agent, RL framework, or copy
of the game.

## Evidence First

- Test the shipped runtime before introducing stubs, patches, or a managed host.
- Bind every game-facing claim to exact game, executable, assembly, platform,
  Headless source, artifact, boot, and runtime evidence.
- Keep source, unit tests, build, boot, programmable control, journey,
  differential, performance, and support evidence separate.
- A fixture, patched assembly, or simulator never proves shipped-runtime parity.

## Runtime Integrity

- STS2 owns rules, RNG, effects, native legality, and Commit paths.
- Reading an internal value does not make it player-visible.
- Do not add a second legality engine, coordinate mutation, arbitrary reflection,
  or hidden-state gameplay API.
- Any patch or stub must name its exact target build, changed behavior, drift
  risk, rollback, and differential test.
- Unknown builds and unproven runtime states fail closed.

## Public Repository

Never commit game DLLs, PCKs, assets, patched binaries, decompiled source,
saves, logs, credentials, local evidence, or user-specific paths. Public CI
must run without proprietary files. Keep locally generated evidence under
`.local/`.
