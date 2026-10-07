# Contributing

Start with the [New Engineer Guide](docs/NEW_ENGINEER_GUIDE.md). Read
`AGENTS.md`, the current architecture, and the owning component guide before
editing. Use [Engineering Governance](docs/ENGINEERING_GOVERNANCE.md) to name
the change class, first causal defect, owning fact/layer, test shape, evidence
level, rollback, and non-claims.

Read the [development workflow](docs/DEVELOPMENT_WORKFLOW.md). Ordinary changes
start from current `origin/develop` on a short-lived topic branch and enter
`develop` through a pull request. `main` is the release landing line; never
direct-push `main` or `develop`. One human or agent owns one writable
branch/worktree.

```bash
npm ci
npm run check
npm run project:closeout
git diff --check
```

Game-bound changes additionally require `npm run check:exact-game` and the
relevant exact-runtime gate. Do not include proprietary game files, generated
artifacts, raw recordings, local evidence, credentials or model weights.

This monorepo includes Platform components, project applications and STPD
research. A cross-layer change uses this repository workflow and preserves
component contracts/identity; it does not require a separate STPD repository PR.
External consumers still pin exact versioned contracts or explicit candidates.
Platform remains model-neutral and does not import research semantics.
Code, naming, document writing and incremental consistency are maintained in
[Project System](docs/PROJECT_SYSTEM.md#code-naming-and-formatting-authority);
check selection remains in [Testing](docs/TESTING.md).
