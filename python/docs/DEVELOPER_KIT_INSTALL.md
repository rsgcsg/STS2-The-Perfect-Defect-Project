# Initial installation of a developer kit

This is an operator-assisted macOS installation using the existing Platform lifecycle.
The kit is not a standalone installer. One clean, exact project checkout supplies the installer and Python workbench.
Use the repository root for native commands and python/ for collector commands. Do not build native source
as part of installing a published binary kit. Windows/Linux portable CI is not proof that
this native installation procedure works there. Consult the release's supported game/OS tuple.

## 隔离源码候选：工程配对窗口

此段供已授权的准确候选工程窗口；日常已安装发布版继续走下文原kit流程。
源码候选不冒充release kit，也不覆盖日常Application、固定launcher或项目配置。
仍由本installer、原Application/native-access与原Game Mod/Host owner负责。

先具备：clean的准确源码、该源码集对应的统一Mod build/provenance、同Mod且
Annotator tree一致的完整固定Collection Tool；所选Python 3.11的`-I`实际ROOT须
是该源码的`python/`，Evidence须为真实已安装的配置pin，不能靠PYTHONPATH遮盖。
先按原`project setup --skip-install`及`project collection-tool`入口准备独立私有
config/state并登记Tool；这些步骤不启动游戏或录制。保留日常配置/运行身份。
Game Mod安装/装载、录制root临时绑定及Host关闭/回滚仍按各原owner执行。

`CANDIDATE_HOME`必须取所选隔离Host实际启动的游戏进程HOME（原Host profile
与ready/lifecycle回读），不是操作者终端HOME或随手建的另一个目录；每个窗口
重新核实际路径，不能复用旧driver/PID。它与日常HOME分开，目标launcher槽为空。
私有config/Tool及HOME用绝对、规范、非symlink路径，所选Python可为已验证解释器。
用该解释器执行源码的原installer，替换以下具名输入：

```sh
/ABS/python3.11 /ABS/source/python/tools/install_developer_kit.py prepare-source-candidate \
  --directory /ABS/source --config /ABS/candidate-project.json \
  --python-executable /ABS/python3.11 --collection-tool-directory /ABS/fixed-tool \
  --candidate-home /ABS/host-profile/home
"/ABS/host-profile/home/Library/Application Support/spireagent/workbench/open"
```

prepare返回`source_candidate_prepared`、`launcher_sha256`、`candidate_home`，
`gameplay_started=false`、`release_kit_verified=false`。保存此回执，后续回滚使用
同一hash。它在该HOME的`Library/Application Support/spireagent/workbench/`
写原`launcher.json`、`open`和私密`native-access.json`；不打印/复制其中secret。
生成的`open`自行设置此HOME并清理Python覆盖变量，用准确解释器`-I`执行
`launch-source-candidate`。不要从日常HOME直接调用这个launch子命令；它会找错槽。

launch重新核全部binding后，经原`open_project(..., browser=False)`启动/复用独立
Application；成功返回`status=running`及`instance_id`，仍标source_candidate与
`release_kit_verified=false`。随后游戏内Platform“录制”→项目内“录制”页须实际配对
该Application/游戏实例，并按[原P1操作](../../apps/ingame-ui/README.md)核同session、
Pause/Resume、双closed、下一session与原件读回。launch成功不证明录制/Human成功。

失败保留原code和回执；`doctor_blocked`到原project doctor/依赖owner定位，
`source_candidate_changed`核源码/lock/build/Tool/解释器/launcher差异，不能手改
binding或bootstrap求过。请求unknown不重投。按原控制先Close/封存、Host stop/close，
用`python -m spireagent.workbench project stop --config /ABS/candidate-project.json`
停止这个独立App并核已不运行；不要停止日常App或仅凭`stopping` ACK宣布结束。

退役配对通过同installer已有库API（当前没有另一个rollback CLI）：

```python
from pathlib import Path
import runpy

owner = runpy.run_path("/ABS/source/python/tools/install_developer_kit.py")
receipt = owner["rollback_source_candidate_access"](
    Path("/ABS/host-profile/home"), "PREPARED_LAUNCHER_SHA256"
)
```

仍从日常操作者环境调用，HOME参数显式指定该隔离槽；API只在hash/schema/HOME
一致且原App已停止时删除那三个候选launcher文件，返回`source_candidate_retired`
与`config_and_data_retained=true`。它不删除config、录制原件或日志，也不回滚Mod/
Host/原录制root配置；这些原owner回滚及日常安装/配置身份复核完成后，工程窗口
才算关闭。当前实际窗口结果从[CURRENT](../../docs/memory/CURRENT.md)进入；原日志
与失败/原生退出诊断保留，不能把一次工程零操作session写成Human或整局资格。

## Managed preparation (default for new kits)

Use the approved release's exact checkout to run the following entrypoint from
`python/`. Supply the ZIP and the one independently published SHA256; the existing
`combination.json` supplies every component pin. Choose a permanent directory
outside your development checkout. Do not use the currently running release as a
writable development directory.

```bash
uv run --locked --extra cloud python tools/install_developer_kit.py plan --archive /ABS/kit.zip --sha256 APPROVED_ZIP_SHA256 --releases /ABS/releases
uv run --locked --extra cloud python tools/install_developer_kit.py prepare --archive /ABS/kit.zip --sha256 APPROVED_ZIP_SHA256 --releases /ABS/releases
uv run --locked --extra cloud python tools/install_developer_kit.py initialize --directory /ABS/releases/APPROVED_ZIP_SHA256 --config /ABS/project.json --defer-launcher
uv run --locked --extra cloud python tools/install_developer_kit.py preflight --directory /ABS/releases/APPROVED_ZIP_SHA256 --game-directory /ABS/game
```

`plan` reads only; `prepare` checks bounded archive paths/inventory, clones the exact
source, verifies lock/combination/manifest, stages the published native bytes and
publishes the directory. Dependencies initialize **after** placement so virtualenv
paths remain stable. Neither step changes the game, profile, queues or cloud.
Initialize before preflight: the prepared release's game doctor imports its installed
Node consumer dependencies. `--defer-launcher` keeps the current launcher selected;
after preflight passes, complete publication through the existing
[reviewed launcher replacement and recovery](#reviewed-launcher-replacement-and-recovery)
owner commands below. A new installation uses that selected release's `install-launcher`;
an existing launcher requires the documented two-hash replacement.
`preflight` is read-only: it binds the prepared package pins to their published BOM
asset hashes, checks the target platform/architecture against the explicit local game
and verifies declared Node, Python and .NET framework compatibility. It never installs,
starts or loads anything. Exact system-runtime executable/version pins are not currently
part of the kit contract, so a compatible observation still reports
`admission: unqualified` until those identities are deliberately pinned.
The Host archive identity is directly anchored by its BOM version/source/archive/content
hash fields. The Connector Client BOM directly anchors the selected asset name and archive
hash; its source revision and package-content digest remain self-asserted combination
claims, tied to the combination bytes in the verified kit archive but not independently
anchored by the BOM. Preflight reports these as unverified claims.

An optional private Host group is separate from those legacy public package claims.
Derive it from the reviewed Host source tar and the exact source checkout before assembling
the kit:

```bash
uv run --locked --project python python tools/package_developer_kit.py derive-private-host \
  --source-root /ABS/source-checkout \
  --host-source-archive /ABS/approved/rsgcsg-sts2-host-runtime-1.1.0-rc.23.tgz \
  --host-source-archive-sha256 96fcf1c906dacb43819688cf75a4ed11c6fb257efbde734afbd5998b37b1fc5a \
  --platform-bom /ABS/source-checkout/platform-bom.json \
  --platform-bom-sha256 APPROVED_BOM_SHA256 \
  --output-directory /ABS/private-host-candidate
```

The output directory contains `profile.json` and the derived `runtime.tgz`; pass those
files and their printed SHA256 values to the existing `--private-host-profile` and
`--private-host-archive` kit-packaging options. The producer checks the original tar
hash and npm file inventory, compares every original packaged file with the clean
BOM-bound Host source tree, and re-reads the Host and Connector component identities
from that checkout. It copies the locked Connector SDK source to a temporary directory,
installs its lockfile into a new private npm cache with scripts disabled, builds its
`dist`, and uses the locked TypeScript and Zod package integrity. It does not use an
author npm cache, pre-existing SDK `dist`, or `node_modules`.

The derived tar records the original archive SHA and source package-content digest,
per-file source inventory, Host source/tree/digest, producer tool git-blob and SHA256
identities, Node/npm/TypeScript tool versions, selected SDK source/tree/digest and bundle
hash, locked Zod integrity and bundle hash, and the derived package-content/archive
hashes in the external profile. Its internal `private-host-derivation.json` repeats the
input and recipe identity. Derivation requires the clean checkout executing the producer
and rechecks that producer identity after completion. The verifier requires the receipt to
match the profile, every original Host file except the transformed `package.json` to be
byte and mode identical, the exact allowed manifest changes (`dependencies`,
`bundleDependencies`, and `files`), and rejects any file outside the source inventory,
SDK/Zod bundles, shrinkwrap, and derivation receipt. Only the two new derivation-format
profiles carry this receipt; ordinary kits without a private Host group and older
private profile groups remain readable.
Kit consumers verify the receipt against their own clean, exact-revision source clone;
it may be a separate clone from the producer.

The original Host `package.json` selects the public SDK rc1 URL. The derived package
changes that dependency to the SDK package version selected by the kit BOM, adds Zod as
an exact dependency, declares SDK and Zod as `bundleDependencies`, and adds
`npm-shrinkwrap.json` plus the derivation receipt to the package file allowlist. It keeps
Host source files unchanged. The checked-in Host `package-lock.json` is not in the
approved source tar and is not used as the derived lock. The current BOM SDK pairing is
1.3.0-rc.5 from Connector source 89ffa45cb6401827e5f64d6f1927a3843ae6ebf7, tree
98643b4de67073dca4d95da2f9d753f5198a33f0, source digest
6aa96c8bbc6d69f2bf5fd39c52816747fc1c608da559fe690c309742a3729f62. Zod is 3.25.76
with the integrity in the current SDK lockfile. Old rc1 tests or canary evidence do not
qualify this changed pairing.

Before the producer returns, and again when the kit packager consumes a v2 profile, the
exact derived tar must install from a new empty npm cache with explicit `--offline`,
empty user/global npm config, and `--ignore-scripts`. It then imports a side-effect-free
Host source module (the Host package has no package-root import entry), the selected SDK,
and Zod. Host setup/start and game launch are never run by these packaging checks.
The producer needs network access only to obtain the SDK's public lockfile dependencies
into its temporary fresh cache; the resulting tarball itself is tested offline. This
proves the Host/SDK/Zod package closure, not full Node/Python kit offline operation or a
loaded/runtime qualification.

The approved Host rc23 source tar declares the public Connector SDK rc1 release URL.
A private kit candidate instead binds to and bundles the SDK version/source selected by
the current BOM (currently 1.3.0-rc.5); this is a dependency change in the private
distribution. Tests or canary evidence for the old rc1 dependency do not qualify that
new SDK pairing. The actual derived bundle must pass fresh offline install/import checks
and separate selected-profile/runtime validation before it can be called usable.
This is a private kit archive format; it does not modify the Host component source,
rewrite BOM `public_packages`, or relabel a public release.

`prepare` stages an included candidate under
`source/python/.local/private-host-runtime/`; `initialize` verifies and extracts the
fixed Host package there. This does not automatically configure a Managed environment.
After a separately reviewed exact Managed candidate is available, use the existing
stopped-Workbench `environment-profile` command to select it explicitly:

```bash
uv run --locked --extra cloud python -m spireagent.workbench project environment-profile \
  --config /ABS/project.json \
  --candidate-directory /ABS/APPROVED_MANAGED_CANDIDATE \
  --host-package-directory /ABS/releases/APPROVED_ZIP_SHA256/source/python/.local/private-host-runtime/package \
  --host-package-pin /ABS/releases/APPROVED_ZIP_SHA256/source/python/.local/private-host-runtime/host-package-pin.json \
  --input-profile text-menu-v1
```

Kit status reports the candidate package as staged/verified separately from the
public `developer-combination` dependency tuple. It reports environment-profile
selection as unobserved; configuration is not a loaded or running Host. The currently
installed CollectionTool remains its own immutable Annotator/Evidence consumer and is
not rebuilt or relabeled by this kit change. The candidate archive closure does not
qualify the full kit's Node/Python setup or complete offline operation.

Preflight resolves the explicit game directory and the doctor's selected directory to the
same canonical root. This permits normal symlinked ancestors and a selected-directory alias,
while rejecting symlinks and escapes below the canonical game root for the four identity
files it reads. It records the doctor's point-in-time `game_running` observation and may
hash these on-disk files while the game is running; it does not inspect process memory or
claim that the running process loaded those same bytes. This read-only snapshot has no lock
against a concurrent process state change or local file replacement. Deploy still requires
the game to be closed and independently rechecks native game identity before writing.
The verified kit inventory selects the locked environment: every kit installs the
`cloud` and `recording-intake` extras (Tokenizers only, which Workbench recording
intake imports); a kit containing any fixed text Runtime pair also installs
`local-models` (Torch, Tokenizers and Safetensors). Right after the locked sync,
`initialize` imports the recording-intake modules in that environment and rejects
with `recording_intake_imports_failed_<module>` before staging any Runtime or
private Host when one does not import. Releases installed before this check keep their environment until
they are reinitialized. It does not install
Qwen/Transformers or download model weights. Opening the local Workbench later
retains these installed optional dependencies. The local home, model setup and
recorded-report reading do not require team login; team account pairing is
optional until a member uses collection sharing or uploads.

### 正常 Steam 冷载与安装身份

日常游戏从 Steam Library 的 STS2 条目启动，由 Steam 提供应用身份；直接运行原生
可执行文件可能在 Steam 初始化处停止。正常无 canary 冷载可验证已装载身份和被动
录制连接；`artifact_unqualified` / `execution_available=false` 仍不能通过原
`verify-loaded` mutation gate。隔离 Host 的显式 canary 工程运行保独立证据，不能转授
正常执行资格。两种启动上下文即使使用同一 DLL，Modset fingerprint 也可能不同；
测试须冻结实际目标上下文取得的 tuple，保留先前失败，不能覆盖旧回执。

Live UI 的编译身份覆盖 C# 输入，BOM 的组件身份覆盖整个组件目录；非编译文件变化
可让其 source revision/digest 不同，同一 component tree 并不要求这两种身份值相等。
安装校验沿各自原 input domain 比较，不用全目录 revision 代替已嵌入的编译身份。
这些说明不改身份算法，也不更新已装 release 的字节或历史资格。

### P1 Model 的正常 CPU 环境

录制专用 kit 默认使用 `cloud` 与 `recording-intake`；需要在同一正常 Application 注册并运行
P1 M-std Model 时，先停止该 Application，在所选永久 release 的 `source/`
运行原锁定依赖入口，再用原固定 launcher 重开：

```sh
uv sync --project python --locked --extra cloud --extra local-models
```

这是显式启用已有可选 backend，不下载权重或启动训练。随后从该 release 的
`source/python/.venv/bin/python -I` 核 `torch`、`tokenizers`、`safetensors`
来自该永久环境，再沿[原 P1 Model 操作](PROJECT_CONSOLE.md#p1-model正常下载注册与使用)
下载/注册实际 Model ArtifactID。普通 setup 使用 `--inexact` 保留已选择的可选
依赖；重新执行 collection-only 的 installer initialize 会按 kit inventory
同步依赖，之后需要重做此显式选择。backend 可导入不代表 Model 已注册、
Runtime 已安装或实局通过。

P1 使用正常模型 registry 的 primary Runtime pin。若该 pin 尚无公开下载 URL，
先保存与它完全匹配、已审查的本地 Runtime tarball，按原 `project model
--action install-runtime --runtime-archive /ABS/runtime.tgz` 离线入口安装；
Application 必须停止、Runtime port 空闲。owner 校验 tarball、完整包内容及
bundled SDK，不接受临时源码目录或旧 hash。正常 kit、Runtime package 和 Model
Artifact 各自保留身份；升级 Runtime 不能改写旧 run 的 producer/typed evidence。

Installer `uv` subprocesses select this release's own environment and synchronize
its lock even if a calling test runner set `UV_NO_SYNC`. Offline and network policy
remain inherited: an unavailable offline dependency fails initialization. A bare
`.venv` directory is not proof that the release environment was initialized.

For kits whose `combination.json` declares
`workbench_launcher_schema: spireagent/workbench-launcher-v1`, successful
initialization without `--defer-launcher` installs a fixed per-user macOS entry at
`~/Library/Application Support/spireagent/workbench/open`. The game Mod can
invoke it only after an explicit Open action reports the Workbench stopped. The
entry accepts no arguments, verifies its exact kit and bound config through the
release's installer owner, then reuses or opens that Workbench without syncing
dependencies. Ready opens the existing URL; stale or identity-mismatched state
requires operator recovery. Older kits without the capability retain their
existing initialization behavior and do not receive this entry. This entry is
currently macOS-only; Windows is not qualified.

The entry reuses the same configuration and exact running service; it does not
start a second recorder when a panel is reopened. The service is detached from
the launcher and owns its profile scratch directory. Closing a panel or the game
does not stop the Workbench's already sealed-recording maintenance. Explicitly
stopping the Workbench does stop that background work; reopening resumes the
persisted maintenance policy. Cloud access still requires the configured SDK
credential provider to be available to the independent service.

For a device that already uses a private cloud SDK environment file, select it
once with `project setup --cloud-environment-file /ABS/private-cloud.env` and the
same config/state/other setup arguments as the existing installation. Stop the
Workbench before changing its configuration; use `--replace-config` only for
the reviewed settings change. Setup validates the file and stores only its
absolute path as `cloud_environment_file`. Subsequent trusted opens and direct
serve use that same reference, so they do not need a Codex shell or a sourced
terminal. Ordinary SDK profile/role/environment credentials remain supported.

The selected file must be a bounded regular private file owned by the current
user on POSIX; missing files, symlinks, public permissions and malformed syntax
fail explicitly. Only the AWS credential/profile/region keys and STPD S3
bucket/endpoint/prefix/region keys are imported. Existing environment values
win; dotenv values are literal, without interpolation or shell execution.
Unrelated provider keys are not imported. No credential value enters the
project configuration, launcher binding, runtime status or reports. A stopped
service reads a rotated file on its next open; a running service retains its
already loaded environment. Configuring this provider does not itself enable
maintenance or select Human origin, upload scope or cooling policy.

### Reviewed launcher replacement and recovery

Launcher publication belongs to this installer. Ordinary same-profile install and
explicit replacement share `install.lock`; replacement requires **both** observed
`launcher.json` and `open` SHA256 values. Both are rechecked under that lock. A
partial pair, changed digest or one-sided argument fails before pair publication.
The private configuration is never rewritten by the launcher owner.

The installer uses one durable byte writer for install and restore. Prepared,
installed and restored binding JSON uses the same canonical bytes, including its
newline; the script and POSIX modes are also exact (`0600` binding, `0700` script).
If a write fails, the owner attempts to restore both prior files. A failed rollback
reports `launcher_recovery_required` and leaves the actual partial result for
inspection. Two replacements are not a crash-atomic transaction: a reader may
briefly reject a mismatched pair, and a hard kill may require explicit recovery.

Prepare the release's dependencies with `initialize --defer-launcher` when the
global launcher must stay selected until review. This flag only defers launcher
publication; normal initialization still uses its existing profile, dependency
and Runtime owners. Then use the bounded owner commands:

- `backup-launcher --snapshot-directory /ABS/history --expected-launcher-binding-sha256 HASH --expected-open-sha256 HASH`
  preserves the exact current pair and modes as **non-launchable history**. Its
  manifest records `config_inventory` as `existing_regular_file` or `missing`.
  An absent, absolute, normalized, nonsymlink config path may be recorded without
  creating or reconstructing it. This history never grants restore eligibility.
- `prepare-launcher-target --directory /ABS/releases/KIT_HASH --config /ABS/private/project.json --snapshot-directory /ABS/target`
  validates the complete immutable release, existing config's archived
  combination, source identity and isolated target Python 3.11 environment. The
  bounded probe verifies the archive-pinned installed Evidence import; it starts
  no Workbench, game, training or installation. The release initialization lock
  remains held through validation. Target snapshots contain only launcher files
  and a manifest, never configuration contents or credentials.
- `restore-launcher --snapshot-directory /ABS/target --snapshot-manifest-sha256 HASH --expected-launcher-binding-sha256 HASH --expected-open-sha256 HASH`
  revalidates that prepared target, including its still-existing config and usable
  interpreter, before replacing the current pair under the same two-hash guard.
  Historical snapshots cannot be restored. Source/config/environment drift is a
  rejection, not permission to reconstruct a missing profile.

Target source, config and exact binding are freshly revalidated after the bounded
probe and immediately before publication under the existing owner locks. A
missing/invalid config or changed source/binding rejects publication without
replacing the current pair. Snapshots bind the config path and archived
combination, not frozen config contents: a valid update retaining that binding is
accepted only after the current config is loaded and validated again. Restore
also repeats the prepared snapshot's exact binding/script comparison.

These are arguments to the selected verified release's installer; they do not
fetch a release or qualify a loaded Mod. Snapshot directories must be new private
absolute paths outside the selected release and fixed launcher directory. Existing
snapshots are immutable, and both file hashes must be observed again before an
explicit subsequent replacement. `install-launcher` also accepts the same two
expected-hash flags for an explicitly reviewed rebind.

The fixed native `/open` entry and browser `project open` retain their current
launch and expected-running-identity checks. Native Workbench access additionally
binds the exact launcher bytes. Even JSON-format-only changes can invalidate an
existing bootstrap. Publication leaves `native-access.json` untouched: no secret
is copied into snapshots, silently rotated or relabelled as valid. Revalidate
through the separate explicit `native-access` owner after selecting a compatible
launcher/config/Mod. A disabled bootstrap remains disabled unless explicitly
enabled; a different-config bootstrap still requires its own recovery.

Explicit `native-access` disable followed by enable creates a fresh credential
generation, including when launcher, configuration path and Mod are unchanged.
Repeated enabled-to-enabled installation of that unchanged selection is idempotent.
Accepted launcher/Mod rebind creates a new grant; same-path configuration selection
updates require the explicit disable/enable sequence. Registration and launcher
publication do not silently rotate credentials. Old native pairs, ACKs, bearers
and pending Load/Auto authorization cannot be adopted by the fresh grant. No
credential is printed or copied into launcher snapshots.

Signed native pairing is independent of the legacy compatibility browser
registration. Its exact signed Close retires its context for that credential/game
generation; reopening the same closed context requires a fresh grant. Old Python
clients only release their legacy registration, so their signed native slot may
remain until expiry (at most 600 seconds). Do not force unsigned unregister or
stop another service to replace it. The global external-window button deliberately
opens the legacy registered browser service; native object links use their own
freshly authenticated paired service.

A kit may additionally contain an independently approved `text-runtime/profile.json`
and `text-runtime/runtime.tgz`. Its inventory and external ZIP SHA256 bind both;
the packager verifies the archive through the ordinary bundled Runtime installer in
a disposable directory. `prepare` stages these bytes in the release source's
ignored `.local/` area. For such a kit, `initialize` creates a local no-login
project profile if absent, using the config path's directory for state, then
installs the text Runtime in its separate `models/text-menu-v1` slot through the
selected release's CLI. It does not register a model, load weights or start a
Runtime. A kit without these files provides no text Runtime preparation; its
original initialization behavior remains. If initialization fails after profile
creation, retain and inspect that profile and retry only after checking the
reported failure and stopped Workbench state.
An independently supplied `m2-runtime/profile.json` and `m2-runtime/runtime.tgz`
pair is optional and separately inventoried. The profile schema is
`stpd/local-text-m2-runtime-v1`; its archive hash and bundled closure are checked
through the same installer. `prepare` stages it under ignored `.local/`, while
`initialize` installs it to `models/text-menu-m2-v1`, distinct from the older
text Runtime. Missing one member of either pair, a different archive hash, or
staged-byte drift blocks that candidate. Neither optional pair is synthesized
from the source checkout or a release URL; without an approved pair its profile
remains unavailable. The M2 pair enables only later explicit model registration
and readiness checks, not automatic model loading or qualification.
An optional `m2-v2-runtime/profile.json` and `m2-v2-runtime/runtime.tgz` pair
has its own inventory entry and `stpd/local-text-m2-runtime-v2` profile schema.
The packager checks the exact archive and all five text-menu-v2 Connector SDK
methods in a temporary installation before publishing the kit. `prepare` stages
the pair under ignored `.local/`; the Workbench's explicit **检查 v2 记忆运行环境**
action reads it only from the selected verified release and installs it into
`models/text-menu-m2-v2`. A missing pair stays unavailable. `initialize` also
installs an included v2 pair through the selected release's CLI and its same
verified profile and archive. Existing v1 pairs keep their prior slots. The v2
pair does not supply model weights or
establish native or policy qualification.
A prepared directory is never overwritten; `status --directory ...` rechecks its
original archive, source, tool and staged bytes. Failed temporary preparation is
removed; initialization can be retried in the same directory while its Workbench
is stopped. Always pass the same private profile path, including before it exists;
initialization holds its existing Workbench lock when present. Keep the original archive and all older in-use release directories.

With STS2 fully closed, the same entrypoint can run `deploy --directory ...
--game-directory /ABS/game`. It checks actual game/dependency bytes against native
provenance and calls the existing lifecycle, which checks source/artifact identity
and retains rollback. It does not rebuild, launch or claim a loaded game. Use the
cold-load commands below from that permanent source directory.

After creating the private Workbench profile, stop it and run
`register --directory ... --config /ABS/project.json` through this entrypoint.
The selected release's registration owner enforces the stopped-profile lock and
refuses replacing a different registration. Daily consent and upload activation
still require the member's team account. For existing tools/queues use the existing explicit
collection-upgrade prepare/activate procedure: pending evidence is never rewritten.
A Workbench-only change does not require Mod deployment when published Mod/tool
bytes and their necessary contracts remain unchanged.

The manual staging instructions below remain the recovery path for old released
kits whose exact source predates this entrypoint. Do not transplant new executable
files into an old pinned checkout to make its source appear current.

## Verify and stage the approved bytes

1. Verify the ZIP SHA256 against its independently approved release receipt before extraction.
   Read `combination.json`, the release's exact refs, game identity, qualification and rollback.
   Obtain the project checkout at the release commit in one durable directory.
2. Install Git, Python 3.11/uv, Node 20+ and the fixed tool's declared .NET runtime.
   The member owns a local game installation; game files are never distributed in the kit.
3. Fully close the game and stop the existing workbench. Preserve its configuration, complete
   old tool, raw recordings and outbox. This installation does not migrate a queue.
4. In the clean project checkout run `npm ci`. Compare the tracked
   `apps/game-mod/mod_manifest.json` to the kit's `mod/STS2_PLATFORM.json`; stop on differences.
   Stage only these ignored outputs, creating their parent directories if necessary:

| Kit file | Destination within the retained project checkout |
|---|---|
| `mod/STS2_PLATFORM.dll` | `apps/game-mod/bin/Release/net9.0/STS2_PLATFORM.dll` |
| `collection-tool/game-mod/build-provenance.json` | `apps/game-mod/bin/Release/net9.0/build-provenance.json` |
| `collection-tool/sts2-human-annotator.dll` | `components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll` |
| `collection-tool/sts2-human-annotator.deps.json` | same destination directory, same name |
| `collection-tool/sts2-human-annotator.runtimeconfig.json` | same destination directory, same name |
| `collection-tool/STS2HumanAnnotator.Core.dll` | same destination directory, same name |

Require `git status --porcelain` to remain empty. Set `STS2_GAME_DIR` to the actual Steam game
directory containing `SlayTheSpire2.app`, not the app itself. Do not set recording-root or
status-path overrides. Use the packaged tool's `identity` command to verify the Mod and the
installed game's `sts2.dll` SHA256/MVID against build provenance. Check the game release and
the pinned GodotSharp/Harmony bytes too. A version label alone is not the qualification tuple.
The owner lifecycle validates source/artifact provenance; this independent game-byte comparison
is also required. A mismatch requires compatibility qualification, not a forced install.

## Deploy and cold-load through the existing owner

From that exact project checkout, run these commands separately and retain their receipts:

```bash
npm run game-mod:doctor
npm run game-mod:deploy
npm run game-mod:doctor
npm run game-mod:launch
npm run game-mod:verify-loaded
```

The first doctor must show the intended directory and no running game. Deploy backs up
managed files/configuration and verifies installed identity. Launch selects no gameplay.
Verify-loaded must confirm the exact current process, loaded Modset, components and execution
admission. Do not run a build command here: it would replace the supplied release artifact.

On first macOS installation, a Human may need to enable Mods and exactly `STS2_PLATFORM` in
the native game interface, then fully close and repeat launch/verify-loaded. The lifecycle
does not edit macOS Mod-enable settings. Do not invent a settings file or remove unrelated Mods.
Initial Recorder status storage may point inside the retained project checkout; do not delete
or relocate that checkout after installation. Later workbench binding preserves this status path.

## Continue collection and retain recovery

Follow [the everyday workflow](B_PIPELINE_HANDOFF.md): register the whole fixed tool while the
workbench is stopped, reopen the same private profile, confirm daily consent, prepare, bind
with the game closed, cold-load, check the current root and explicitly activate uploads.
The member's first real Close-to-receipt check is separate from installation evidence.

For rollback, stop delivery and close the game. From the same retained project checkout and
game directory run `npm run game-mod:rollback`. It uses the recorded installed provenance and
backup; it does not accept an arbitrary backup path. Preserve old/new receipts. Cold-load the
restored compatible pair with its owning checkout and verify it. A first install may roll back
to no Platform installation. Native macOS Mod-enable choices are outside the file rollback.

Do not rewrite old queues, historical failures, consent or loaded identities to force an
upgrade. Use the [maintenance procedure](B_PIPELINE_HANDOFF.md#daily-work-upgrades-and-incidents).
