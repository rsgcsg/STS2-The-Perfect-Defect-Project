# Component Versioning

The monorepo does not impose lockstep component versions.

- Connector, Host Runtime, Annotator, evidence tools and Workbench have
  independent semantic versions.
- Component tags use `<component>/v<version>`.
- `platform-bom/v<version>` names a tested compatible composition.
- Player Environment protocol, SDK version, component version and exact artifact
  identity are distinct.
- Consumer SDK and Host Runtime package assets are immutable release inputs;
  external consumers do not install a branch or sibling source checkout.

The component identity report records the workspace commit, the most recent
commit that changed each component path, the component Git tree, and source and
contract digests. A change outside a component cannot change its path-scoped
identities. Runtime evidence binds artifact bytes and does not transfer across
a rebuild.

## Compatibility is not equal Git revisions

Connection compatibility belongs to the declared protocol, capabilities and auth contract;
game-native compatibility belongs to actual loaded native seams and operands. Dataset
compatibility belongs to schema/semantic interpretation and explicit selection policy; model
compatibility belongs to representation, adapter, Reads/action catalog and environment support.
Record exact game, Mod, collector, application and research provenance without requiring all
components or machines to share a Git SHA. An unrelated edit is not a required client update.

Exact binding remains necessary for artifact integrity, the executing native tuple, immutable
queue/tool association, frozen dataset/splits, checkpoints and reproducible evaluations. Unknown
compatibility stays unknown; this rule does not waive an existing native or schema check.
Keep historical data unchanged and publish a new derived dataset/model when interpretation
changes. Upgrade/release triggers and rollout ownership are defined once in
[Development Workflow](DEVELOPMENT_WORKFLOW.md#compatibility-and-update-policy).

## Small-team update decisions

The immutable release combination is the delivery recommendation; main is its
formal source history, develop is daily integration, and the deployment receipt
identifies actual running bytes. None is an alias for the others. Main may advance
for documentation while a previously accepted executable stays in use.

Only changed deliverables need a new candidate. A Hub-only fix leaves member Mod,
tool, model and dataset bytes alone. Shared console changes are tested in both
shells; a shared lock/build/schema change expands the affected consumers. A local
Workbench update does not itself migrate a pinned outbox or replace a native Mod.
Use the existing combination/BOM and release/integration receipts; never create a
parallel version registry or rewrite an old producer to today's SHA.

Current interface support is finite: test the current writer/reader and an actual
supported released consumer for changed public surfaces. The release compatibility
suite uses the archived published Hub client against current authenticated APIs.
This bounded wire test does not qualify its entire old installation or arbitrary
future schemas. Unknown schema stays unsupported with diagnostics; different
producer source SHAs alone do not reject supported data. Preserve exact integrity,
lineage, candidate semantics, model representation, native operands and queue/tool
binding. Training dataset selection stays permissive about coverage and versions,
while scientific grouping/splits and sequence gaps remain explicit.

Users update for a needed feature, supported-interface change, correctness or
security fix, not because a branch moved. Removal of a supported interface needs a
reason, affected consumers and migration window. Stopping old executable support
never authorizes deleting archival data readers. Ordinary dependency maintenance
is batched; urgent security or data-integrity issues are handled promptly.

For the current P1 baseline, the protected consumer is the actual
`native-segments-1` / `p1-global-r1` / `r1.4` reader and the fixed
`stpd-p1-mstd-input@1` model adapter. A converter implementation repair creates
a new derived artifact, preserving the original accepted inputs and any already
frozen training snapshot. Unchanged observations, catalogs, InputSpec and weights
remain usable; environment changes use a new isolated registration with the
accurate producer/game/core envelope. This is finite consumer compatibility, not
permission to relax exact runtime binding, silently reassign choice IDs or admit
all historical revisions. New projection or action semantics need their own
explicit decision and affected-consumer checks.

## Repository spelling correction (2026-09-16)

The same GitHub repository was renamed from `rsgcsg/STS2-The-Pefect-Defect-Project`
to `rsgcsg/STS2-The-Perfect-Defect-Project` (repository ID 1371044812 unchanged).
Current clone/download links and newly built producer identities use the corrected name.
Readers accept only these two exact names where the project repository is constrained;
they preserve the original value, source SHA, lock and content hashes. No arbitrary GitHub
repository becomes trusted. The archived predecessor remains separately identified.

Historical import/retained-release records, dated evidence, approved combinations and
locked package URLs keep their original values. Their GitHub redirects and exact asset
hashes must remain usable. Do not regenerate locks, native BOMs, old releases, datasets or
model provenance just to remove the old spelling. Never reuse the old GitHub name.

Existing installations, queues, runtime directories, OCI digests, cloud domain and accounts
remain unchanged. Only Git remote configuration is updated on managed clones; new clones
may use the corrected directory name. Do not move a live checkout to match the remote name.
A repository rename alone does not require a new executable release, game restart or Human
canary. Future source builds use the corrected origin and the normal affected release gates.
