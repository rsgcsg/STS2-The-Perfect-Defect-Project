import { execFile } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { promisify } from "node:util";
import { startJsonLineProcess } from "./json-line-process.mjs";
import { ProcessResourceSampler } from "./process-resource-sampler.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { readSystemIdentity } from "./system-identity.mjs";

const execFileAsync = promisify(execFile);

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

function sha256Buffer(value) {
  return createHash("sha256").update(value).digest("hex");
}

function sha256File(file) {
  return sha256Buffer(readFileSync(file));
}

function plainObject(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function requirePositiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${name} must be a positive integer.`);
  }
  return value;
}

function normalizeText(value) {
  return String(value).replaceAll("\r\n", "\n").replace(/\n*$/u, "\n");
}

function canonicalPatch(value) {
  return normalizeText(value).split("\n")
    .filter((line) => !line.startsWith("index "))
    .join("\n");
}

export function addedPatchPaths(value) {
  const normalized = normalizeText(value);
  const paths = [];
  const pattern = /^diff --git a\/(\S+) b\/(\S+)\nnew file mode [0-7]{6}$/gmu;
  for (const match of normalized.matchAll(pattern)) {
    if (match[1] !== match[2]
        || path.posix.isAbsolute(match[2])
        || match[2].split("/").includes("..")) {
      throw new Error(`Managed candidate patch has an unsafe added path: ${match[2]}`);
    }
    paths.push(match[2]);
  }
  return paths;
}

export function toBashPath(value, platform = process.platform) {
  if (platform !== "win32") return value;
  const resolved = path.win32.resolve(value);
  const drive = path.win32.parse(resolved).root.match(/^([A-Za-z]):\\$/u)?.[1];
  if (drive == null) {
    throw new Error(`Managed candidate requires a drive-qualified Windows game path: ${value}`);
  }
  return `/${drive.toLowerCase()}${resolved.slice(2).replaceAll("\\", "/")}`;
}

async function run(command, args, options = {}) {
  return execFileAsync(command, args, {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
    timeout: options.timeout ?? 300_000,
    cwd: options.cwd,
    env: options.env
  });
}

async function git(args, cwd) {
  return (await run("git", args, { cwd })).stdout.trim();
}

export function loadManagedCandidateManifest(root) {
  const file = path.join(root, "experiments", "managed-exact", "manifest.json");
  const manifest = JSON.parse(readFileSync(file, "utf8"));
  const errors = [];
  if (manifest.schema !== "sts2.headless/managed-candidate-manifest-1") {
    errors.push("schema_invalid");
  }
  if (typeof manifest.candidate_id !== "string" || manifest.candidate_id.length === 0) {
    errors.push("candidate_id_missing");
  }
  if (!plainObject(manifest.upstream)
      || typeof manifest.upstream.url !== "string"
      || !/^[0-9a-f]{40}$/u.test(manifest.upstream.revision ?? "")) {
    errors.push("upstream_identity_invalid");
  }
  if (!plainObject(manifest.exact_game)
      || typeof manifest.exact_game.sts2_dll_sha256 !== "string") {
    errors.push("exact_game_identity_invalid");
  }
  if (!Array.isArray(manifest.required_game_files) || manifest.required_game_files.length === 0) {
    errors.push("required_game_files_missing");
  }
  if (manifest.platform_baselines != null
      && (!Array.isArray(manifest.platform_baselines)
        || manifest.platform_baselines.some((baseline) =>
          !plainObject(baseline)
          || typeof baseline.baseline_id !== "string"
          || !plainObject(baseline.exact_game)
          || !plainObject(baseline.expected_build)))) {
    errors.push("platform_baselines_invalid");
  }
  if (errors.length > 0) throw new Error(`Managed candidate manifest invalid: ${errors.join(", ")}`);
  return { manifest, file };
}

function diskGameIdentity(diskIdentity) {
  return {
    platform: diskIdentity.platform,
    architecture: diskIdentity.architecture,
    version: diskIdentity.release?.version,
    commit: diskIdentity.release?.commit,
    runtime_main_assembly_hash: diskIdentity.runtime_main_assembly_hash,
    sts2_dll_sha256: diskIdentity.sts2_assembly?.sha256,
    godotsharp_dll_sha256: diskIdentity.godotsharp_assembly?.sha256
  };
}

function identityMismatches(exactGame, actual) {
  return Object.entries(exactGame)
    .filter(([key, expected]) => actual[key] !== expected)
    .map(([key, expected]) => ({ key, expected, actual: actual[key] ?? null }));
}

export function selectManagedCandidateManifest(manifest, diskIdentity) {
  const actual = diskGameIdentity(diskIdentity);
  const baselines = manifest.selected_baseline == null
    ? [
        {
          baseline_id: "primary-darwin-arm64",
          status: manifest.status,
          exact_game: manifest.exact_game,
          expected_build: manifest.expected_build,
          non_claims: []
        },
        ...(manifest.platform_baselines ?? [])
      ]
    : [{ ...manifest.selected_baseline, exact_game: manifest.exact_game, expected_build: manifest.expected_build }];
  const matches = baselines.filter((baseline) =>
    identityMismatches(baseline.exact_game, actual).length === 0);
  if (matches.length !== 1) {
    const mismatchReport = baselines.map((baseline) => ({
      baseline_id: baseline.baseline_id,
      mismatches: identityMismatches(baseline.exact_game, actual)
    }));
    throw new Error(
      `Managed candidate refuses this game identity: ${JSON.stringify(mismatchReport)}`
    );
  }
  const selected = matches[0];
  return {
    ...manifest,
    selected_baseline: {
      baseline_id: selected.baseline_id,
      status: selected.status,
      non_claims: selected.non_claims ?? []
    },
    exact_game: selected.exact_game,
    expected_build: selected.expected_build
  };
}

export function assertManagedCandidateGame(manifest, diskIdentity) {
  selectManagedCandidateManifest(manifest, diskIdentity);
  return diskGameIdentity(diskIdentity);
}

export async function resolveDotnet() {
  const candidates = [
    process.env.DOTNET,
    path.join(os.homedir(), ".dotnet-arm64", "dotnet"),
    path.join(os.homedir(), ".dotnet", "dotnet"),
    "dotnet"
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const { stdout } = await run(candidate, ["--version"], { timeout: 5_000 });
      return { command: candidate, version: stdout.trim() };
    } catch {
      // Continue to the next explicit location.
    }
  }
  throw new Error("A .NET 9+ SDK is required for the managed candidate.");
}

async function fingerprintManagedAssembly(root, assembly) {
  const dotnet = await resolveDotnet();
  const project = path.join(root, "tools", "dotnet", "AssemblyFingerprint");
  const { stdout } = await run(dotnet.command, [
    "run", "--project", project, "--", "--assembly", assembly
  ]);
  const fingerprint = JSON.parse(stdout);
  const moduleMvid = fingerprint?.assembly?.module_mvid;
  if (typeof moduleMvid !== "string" || moduleMvid.length === 0) {
    throw new Error("Managed candidate assembly fingerprint did not report an MVID.");
  }
  return { moduleMvid };
}

export async function auditManagedCandidateSource({ root, candidateDirectory, manifest }) {
  const resolvedCandidateDirectory = path.resolve(candidateDirectory);
  const revision = await git(["rev-parse", "HEAD"], resolvedCandidateDirectory);
  if (revision !== manifest.upstream.revision) {
    throw new Error(`Managed candidate revision ${revision} does not match ${manifest.upstream.revision}.`);
  }
  const patchFile = path.join(root, "experiments", "managed-exact", manifest.source_patch);
  const expectedPatch = normalizeText(readFileSync(patchFile, "utf8"));
  const actualPatch = normalizeText(await git(
    ["diff", "--binary", "--no-ext-diff"],
    resolvedCandidateDirectory
  ));
  if (canonicalPatch(actualPatch) !== canonicalPatch(expectedPatch)) {
    throw new Error("Managed candidate source diff does not exactly match the admitted patch ledger.");
  }
  return {
    upstream_revision: revision,
    source_patch_sha256: sha256Buffer(expectedPatch),
    source_patch_bytes: Buffer.byteLength(expectedPatch)
  };
}

function candidateArtifact(candidateDirectory, manifest) {
  return path.resolve(candidateDirectory, manifest.setup_contract.managed_artifact);
}

export async function inspectManagedCandidateBuild({ root, candidateDirectory, manifest }) {
  const resolvedCandidateDirectory = path.resolve(candidateDirectory);
  const source = await auditManagedCandidateSource({
    root,
    candidateDirectory: resolvedCandidateDirectory,
    manifest
  });
  const originalAssembly = path.join(resolvedCandidateDirectory, "lib", "sts2.dll.original");
  const runtimeAssembly = path.join(resolvedCandidateDirectory, "lib", "sts2.dll");
  const artifact = candidateArtifact(resolvedCandidateDirectory, manifest);
  for (const file of [originalAssembly, runtimeAssembly, artifact]) {
    if (!existsSync(file)) throw new Error(`Managed candidate build output missing: ${file}`);
  }
  const originalSha = sha256File(originalAssembly);
  if (originalSha !== manifest.exact_game.sts2_dll_sha256) {
    throw new Error(`Managed candidate original sts2.dll SHA ${originalSha} is not admitted.`);
  }
  const runtimeSha = sha256File(runtimeAssembly);
  if (manifest.setup_contract.requires_unmodified_game_assembly && runtimeSha !== originalSha) {
    throw new Error(`Managed candidate runtime sts2.dll SHA ${runtimeSha} differs from the exact game assembly.`);
  }
  const artifactSha = sha256File(artifact);
  if (source.source_patch_sha256 !== manifest.expected_build?.source_patch_sha256) {
    throw new Error(
      `Managed candidate patch SHA ${source.source_patch_sha256} does not match the frozen baseline.`
    );
  }
  if (artifactSha !== manifest.expected_build?.artifact_sha256) {
    throw new Error(
      `Managed candidate artifact SHA ${artifactSha} does not match the frozen baseline.`
    );
  }
  const { moduleMvid } = await fingerprintManagedAssembly(root, artifact);
  if (moduleMvid !== manifest.expected_build?.artifact_mvid) {
    throw new Error(
      `Managed candidate artifact MVID ${moduleMvid} does not match the frozen baseline.`
    );
  }
  return {
    ...source,
    candidate_directory: resolvedCandidateDirectory,
    artifact,
    artifact_size: statSync(artifact).size,
    artifact_sha256: artifactSha,
    artifact_mvid: moduleMvid,
    original_sts2_sha256: originalSha,
    runtime_sts2_sha256: runtimeSha
  };
}

export async function prepareManagedCandidate({
  root,
  localRoot,
  diskIdentity,
  candidateDirectory = null
}) {
  const { manifest: loadedManifest, file: manifestFile } = loadManagedCandidateManifest(root);
  const manifest = selectManagedCandidateManifest(loadedManifest, diskIdentity);
  const exactGame = assertManagedCandidateGame(manifest, diskIdentity);
  const dotnet = await resolveDotnet();
  const major = Number(dotnet.version.split(".")[0]);
  if (!Number.isSafeInteger(major) || major < manifest.setup_contract.dotnet_major_minimum) {
    throw new Error(`Managed candidate requires .NET ${manifest.setup_contract.dotnet_major_minimum}+.`);
  }
  const destination = path.resolve(candidateDirectory ?? path.join(
    localRoot,
    "candidates",
    `${manifest.candidate_id}-${safeTimestamp()}`
  ));
  if (existsSync(destination)) throw new Error(`Refusing to overwrite candidate directory: ${destination}`);
  mkdirSync(path.dirname(destination), { recursive: true });
  // The pinned upstream contains Bash entry points. A Windows global
  // core.autocrlf setting must not rewrite those files before setup.
  await run("git", [
    "clone",
    "--config",
    "core.autocrlf=false",
    manifest.upstream.url,
    destination
  ]);
  await run("git", ["checkout", "--detach", manifest.upstream.revision], { cwd: destination });
  const patchFile = path.join(root, "experiments", "managed-exact", manifest.source_patch);
  const normalizedPatchFile = path.join(destination, ".git", "stpd-managed-candidate.patch");
  const normalizedPatch = normalizeText(readFileSync(patchFile, "utf8"));
  writeFileSync(normalizedPatchFile, normalizedPatch);
  // Preserve newly added files in the same `git diff` view used by source
  // admission. Applying with git's broad --intent-to-add mode can rewrite the
  // index on Windows, so add only paths declared `new file mode` by the patch.
  await run("git", ["apply", "--whitespace=error-all", normalizedPatchFile], {
    cwd: destination
  });
  const addedPaths = addedPatchPaths(normalizedPatch);
  if (addedPaths.length > 0) {
    await run("git", ["add", "--intent-to-add", "--", ...addedPaths], { cwd: destination });
  }

  const gameDataDirectory = path.dirname(diskIdentity.sts2_assembly.path);
  mkdirSync(path.join(destination, "lib"), { recursive: true });
  for (const name of manifest.required_game_files) {
    const source = path.join(gameDataDirectory, name);
    if (!existsSync(source)) throw new Error(`Exact game dependency missing: ${name}`);
    copyFileSync(source, path.join(destination, "lib", name));
  }

  if (process.platform === "win32") {
    // The admitted patch keeps sts2.dll byte-for-byte exact. All setup.sh does
    // before its build is copy the already enumerated DLLs and create this
    // audit backup, so perform those filesystem steps natively when `bash` is
    // WSL and cannot consume Windows process paths.
    copyFileSync(
      path.join(destination, "lib", "sts2.dll"),
      path.join(destination, "lib", "sts2.dll.original")
    );
  } else {
    try {
      await run("bash", ["setup.sh", toBashPath(gameDataDirectory)], {
        cwd: destination,
        timeout: 600_000,
        env: { ...process.env, DOTNET: dotnet.command }
      });
    } catch (error) {
      throw new Error(`Managed upstream setup failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  const project = path.join(destination, manifest.setup_contract.managed_project);
  await run(dotnet.command, ["build", project, "-c", "Release", "--nologo"], {
    cwd: destination,
    timeout: 600_000,
    env: { ...process.env, STS2_LIB: path.join(destination, "lib") }
  });
  const build = await inspectManagedCandidateBuild({ root, candidateDirectory: destination, manifest });
  const evidenceDirectory = path.join(localRoot, "evidence", `managed-prepare-${safeTimestamp()}`);
  mkdirSync(evidenceDirectory, { recursive: true });
  const reportFile = path.join(evidenceDirectory, "report.json");
  const report = {
    schema: "sts2.headless/managed-candidate-prepare-1",
    generated_at: new Date().toISOString(),
    status: "candidate_built_unqualified",
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    manifest_file: path.relative(root, manifestFile),
    manifest,
    exact_game: exactGame,
    dotnet,
    game_assembly_unmodified: build.runtime_sts2_sha256 === build.original_sts2_sha256,
    build,
    non_claims: manifest.admission.forbidden_claims
  };
  writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  return { report, reportFile, candidateDirectory: destination };
}

export async function startManagedCandidateRuntime({
  root,
  candidateDirectory,
  diskIdentity,
  requestTimeoutMs = 10_000,
  quietDiagnostics = false
}) {
  requirePositiveInteger(requestTimeoutMs, "requestTimeoutMs");
  const { manifest: loadedManifest } = loadManagedCandidateManifest(root);
  const manifest = selectManagedCandidateManifest(loadedManifest, diskIdentity);
  const exactGame = assertManagedCandidateGame(manifest, diskIdentity);
  const resolvedCandidateDirectory = path.resolve(candidateDirectory);
  const build = await inspectManagedCandidateBuild({
    root,
    candidateDirectory: resolvedCandidateDirectory,
    manifest
  });
  const dotnet = await resolveDotnet();
  const gameDataDirectory = path.dirname(diskIdentity.sts2_assembly.path);
  const { process: child, ready } = await startJsonLineProcess({
    command: dotnet.command,
    args: [build.artifact],
    cwd: resolvedCandidateDirectory,
    env: {
      ...process.env,
      STS2_LIB: path.join(resolvedCandidateDirectory, "lib"),
      STS2_GAME_DIR: gameDataDirectory,
      ...(quietDiagnostics ? { STS2_HEADLESS_QUIET: "1" } : {})
    },
    readyTimeoutMs: requestTimeoutMs,
    diagnosticLimit: 500
  });
  try {
    if (ready?.type !== "ready") {
      throw new Error(`Managed candidate returned invalid ready message: ${JSON.stringify(ready)}`);
    }
    const runtimeIdentity = await child.request({ cmd: "runtime_identity" }, requestTimeoutMs);
    if (runtimeIdentity?.type !== "runtime_identity"
        || runtimeIdentity.process_id !== child.pid
        || runtimeIdentity.host_assembly_sha256 !== build.artifact_sha256
        || runtimeIdentity.sts2_assembly_sha256 !== build.runtime_sts2_sha256) {
      throw new Error("Managed candidate runtime identity does not match its exact process/build.");
    }
    return {
      manifest,
      exactGame,
      build,
      dotnet,
      ready,
      runtimeIdentity,
      adapterRuntimeInstanceId: randomUUID().replaceAll("-", ""),
      process: child
    };
  } catch (error) {
    await child.stop();
    throw error;
  }
}

function stateDigest(state) {
  return sha256Buffer(JSON.stringify(state));
}

function sortedBy(items, keys) {
  return [...items].sort((left, right) => {
    for (const key of keys) {
      const compared = String(left?.[key] ?? "").localeCompare(String(right?.[key] ?? ""), "en", {
        numeric: true
      });
      if (compared !== 0) return compared;
    }
    return 0;
  });
}

export function chooseManagedCandidateAction(state) {
  switch (state?.decision) {
    case "map_select": {
      const choice = sortedBy(state.choices ?? [], ["row", "col"])[0];
      return choice == null ? null : {
        cmd: "action",
        action: "select_map_node",
        args: { col: choice.col, row: choice.row, map_point_ref: choice.native_ref }
      };
    }
    case "combat_play": {
      const enemies = (state.enemies ?? []).filter((enemy) => (enemy.hp ?? 0) > 0);
      const playable = (state.hand ?? []).filter((card) => card.can_play === true);
      if (playable.length === 0) {
        return { cmd: "action", action: "end_turn", args: { player_ref: state.player?.native_ref } };
      }
      const card = sortedBy(playable, ["id", "index"])[0];
      const args = { card_ref: card.native_ref };
      if (card.target_type === "AnyEnemy" && enemies.length > 0) {
        const validTargets = new Set(card.valid_target_refs ?? []);
        const enemy = sortedBy(enemies.filter((candidate) => validTargets.has(candidate.native_ref)), ["id", "index"])[0];
        if (enemy == null) return null;
        args.target_ref = enemy.native_ref;
      }
      return { cmd: "action", action: "play_card", args };
    }
    case "event_choice": {
      const option = sortedBy((state.options ?? []).filter((entry) => entry.is_locked !== true), ["index"])[0];
      return option == null
        ? { cmd: "action", action: "leave_room" }
        : { cmd: "action", action: "choose_option", args: { option_index: option.index } };
    }
    case "rest_site": {
      const options = (state.options ?? []).filter((entry) => entry.is_enabled !== false);
      const option = options.find((entry) => entry.option_id === "HEAL") ?? sortedBy(options, ["option_id"])[0];
      return option == null || typeof option.native_ref !== "string" || option.native_ref.length === 0
        ? null
        : { cmd: "action", action: "choose_option", args: {
          option_index: option.index, option_ref: option.native_ref
        } };
    }
    case "treasure_chest":
      return typeof state.room_ref === "string"
        ? { cmd: "action", action: "open_treasure", args: { room_ref: state.room_ref } }
        : null;
    case "treasure_relic": {
      const relic = (state.relics ?? [])[0];
      if (relic != null && typeof relic.native_ref === "string") {
        return {
          cmd: "action",
          action: "select_treasure_relic",
          args: { relic_ref: relic.native_ref }
        };
      }
      return state.can_skip === true && typeof state.room_ref === "string"
        ? { cmd: "action", action: "skip_treasure_relic", args: { room_ref: state.room_ref } }
        : null;
    }
    case "treasure_complete":
      return typeof state.room_ref === "string"
        ? { cmd: "action", action: "leave_room", args: { room_ref: state.room_ref } }
        : null;
    case "reward_set": {
      const reward = (state.rewards ?? [])[0];
      if (reward != null && typeof reward.native_ref === "string") {
        return { cmd: "action", action: "select_reward", args: { reward_ref: reward.native_ref } };
      }
      if (state.is_terminal === true && state.can_proceed === true && typeof state.room_ref === "string") {
        return { cmd: "action", action: "proceed", args: { room_ref: state.room_ref } };
      }
      return state.can_skip === true ? { cmd: "action", action: "skip_rewards" } : null;
    }
    case "card_reward":
      return (state.cards ?? []).length === 0
        ? { cmd: "action", action: "skip_card_reward" }
        : { cmd: "action", action: "select_card_reward", args: { card_index: 0 } };
    case "combat_rewards_complete":
      return typeof state.room_ref === "string"
        ? { cmd: "action", action: "proceed", args: { room_ref: state.room_ref } }
        : null;
    case "bundle_select":
      return { cmd: "action", action: "select_bundle", args: { bundle_index: 0 } };
    case "card_select":
      return (state.cards ?? []).length === 0
        ? { cmd: "action", action: "skip_select" }
        : { cmd: "action", action: "select_cards", args: { indices: "0" } };
    case "shop":
      return typeof state.room_ref === "string"
        ? { cmd: "action", action: "leave_shop", args: { room_ref: state.room_ref } }
        : null;
    case "game_over":
      return null;
    default:
      return null;
  }
}

export async function runManagedCandidateProbe({
  root,
  candidateDirectory,
  diskIdentity,
  seed,
  character = "Ironclad",
  maxActions = 200,
  episodeCount = 1,
  resetAtDecisions = [],
  requestTimeoutMs = 10_000,
  evidenceRoot = null,
  evidenceLabel = "managed-probe"
}) {
  requirePositiveInteger(maxActions, "maxActions");
  requirePositiveInteger(episodeCount, "episodeCount");
  requirePositiveInteger(requestTimeoutMs, "requestTimeoutMs");
  if (!Array.isArray(resetAtDecisions)
      || resetAtDecisions.some((decision) => typeof decision !== "string" || decision.length === 0)) {
    throw new TypeError("resetAtDecisions must contain non-empty decision names.");
  }
  const resetDecisionSet = new Set(resetAtDecisions);
  if (typeof seed !== "string" || seed.length === 0) {
    throw new TypeError("seed must be a non-empty string.");
  }
  const startedAtMs = performance.now();
  const runtime = await startManagedCandidateRuntime({
    root,
    candidateDirectory,
    diskIdentity,
    requestTimeoutMs
  });
  const { manifest, build, ready, process: child } = runtime;
  const sampler = new ProcessResourceSampler(child.pid, { intervalMs: 250 });
  await sampler.start();
  let decisionStartedMs = null;
  let decisionEndedMs = null;
  let state;
  let terminal = null;
  let stopReason = null;
  let failure = null;
  const events = [];
  const episodes = [];
  const resetWallMs = [];
  const runtimeIdentity = runtime.runtimeIdentity;
  try {
    for (let episodeIndex = 0; episodeIndex < episodeCount && stopReason == null; episodeIndex += 1) {
      const episodeSeed = episodeCount === 1 ? seed : `${seed}E${episodeIndex + 1}`;
      const mountStarted = performance.now();
      state = await child.request({
        cmd: episodeIndex === 0 ? "start_run" : "reset_run",
        character,
        seed: episodeSeed
      }, requestTimeoutMs);
      const runIdentity = await child.request({ cmd: "run_identity" }, requestTimeoutMs);
      const mountWallMs = performance.now() - mountStarted;
      if (episodeIndex > 0) resetWallMs.push(mountWallMs);
      if (state?.type !== "decision") {
        throw new Error(`Managed candidate did not mount episode ${episodeIndex + 1}: ${JSON.stringify(state)}`);
      }
      if (runIdentity?.type !== "run_identity"
          || runIdentity.active !== true
          || runIdentity.seed !== episodeSeed) {
        throw new Error(`Managed candidate did not prove episode seed ${episodeSeed}.`);
      }
      if (decisionStartedMs == null) decisionStartedMs = performance.now();
      const episodeStartEvent = events.length;
      let resetBoundaryDecision = null;
      terminal = null;
      while (events.length - episodeStartEvent < maxActions) {
        if (state.decision === "game_over") {
          terminal = {
            victory: state.victory === true,
            act: state.act ?? null,
            floor: state.floor ?? null
          };
          break;
        }
        if (events.length > episodeStartEvent && resetDecisionSet.has(state.decision)) {
          resetBoundaryDecision = state.decision;
          break;
        }
        const action = chooseManagedCandidateAction(state);
        if (action == null) {
          stopReason = `unsupported_decision:${state.decision ?? "missing"}`;
          break;
        }
        const before = performance.now();
        const successor = await child.request(action, requestTimeoutMs);
        const after = performance.now();
        events.push({
          index: events.length,
          episode_index: episodeIndex,
          episode_decision_index: events.length - episodeStartEvent,
          decision: state.decision,
          state_sha256: stateDigest(state),
          action: { action: action.action, args: action.args ?? null },
          delivery_wall_ms: after - before,
          successor_type: successor?.type ?? null,
          successor_decision: successor?.decision ?? null,
          successor_error: successor?.type === "error"
            ? {
                message: successor.message ?? null,
                stack_trace: successor.stack_trace ?? null
              }
            : null,
          successor_sha256: stateDigest(successor)
        });
        if (successor?.type === "error") {
          stopReason = `candidate_error:${successor.message ?? "unknown"}`;
          break;
        }
        if (successor?.type !== "decision") {
          stopReason = `unexpected_successor:${successor?.type ?? "missing"}`;
          break;
        }
        state = successor;
      }
      episodes.push({
        episode_index: episodeIndex,
        requested_seed: episodeSeed,
        game_reported_seed: runIdentity.seed,
        mount_wall_ms: mountWallMs,
        delivered_raw_decisions: events.length - episodeStartEvent,
        termination: terminal != null
          ? "game_over"
          : resetBoundaryDecision != null
            ? "reset_boundary"
            : "action_limit",
        reset_boundary_decision: resetBoundaryDecision,
        terminal
      });
    }
    decisionEndedMs = performance.now();
  } catch (error) {
    failure = error instanceof Error ? error.message : String(error);
    stopReason ??= `candidate_exception:${failure}`;
  } finally {
    if (decisionEndedMs == null && decisionStartedMs != null) decisionEndedMs = performance.now();
  }
  const resources = await sampler.stop();
  const exit = await child.stop({ request: { cmd: "quit" }, timeoutMs: 5_000 });
  const windowSeconds = decisionStartedMs == null || decisionEndedMs == null
    ? null
    : (decisionEndedMs - decisionStartedMs) / 1000;
  const actionDeliverySeconds = events.reduce((sum, event) => sum + event.delivery_wall_ms, 0) / 1000;
  const firstResourceSample = resources.samples[0] ?? null;
  const lastResourceSample = resources.samples.at(-1) ?? null;
  const peakRssBytes = resources.samples.length === 0
    ? null
    : Math.max(...resources.samples.map((sample) => sample.rss_bytes));
  const report = {
    schema: "sts2.headless/managed-candidate-probe-1",
    generated_at: new Date().toISOString(),
    status: failure != null
      ? "candidate_failure"
      : stopReason == null && episodes.length === episodeCount
        ? episodeCount === 1 && terminal != null ? "terminal_reached" : "episodes_complete"
        : "fail_closed",
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    candidate: {
      manifest,
      build,
      ready,
      adapter_runtime_instance_id: runtime.adapterRuntimeInstanceId,
      runtime_identity: runtimeIdentity
    },
    game_identity: {
      version: diskIdentity.release.version,
      commit: diskIdentity.release.commit,
      runtime_main_assembly_hash: diskIdentity.runtime_main_assembly_hash,
      original_sts2_sha256: diskIdentity.sts2_assembly.sha256
    },
    episode: {
      requested_seed: seed,
      seed_provenance: episodes.length === episodeCount
        && episodes.every((episode) => episode.requested_seed === episode.game_reported_seed)
        ? "game_reported_match"
        : "incomplete",
      character,
      episodes_requested: episodeCount,
      episodes_completed: episodes.length,
      reset_at_decisions: [...resetDecisionSet],
      delivered_raw_decisions: events.length,
      terminal,
      stop_reason: stopReason,
      failure,
      episodes
    },
    performance: {
      unit: "external_candidate_raw_decision_not_player_environment",
      process_startup_seconds: decisionStartedMs == null ? null : (decisionStartedMs - startedAtMs) / 1000,
      decision_window_started_ms: decisionStartedMs,
      decision_window_ended_ms: decisionEndedMs,
      reset_inclusive_decision_window_seconds: windowSeconds,
      reset_inclusive_raw_decisions_per_second: windowSeconds > 0 ? events.length / windowSeconds : null,
      action_delivery_seconds: actionDeliverySeconds,
      action_only_raw_decisions_per_second: events.length > 0
        ? events.length / actionDeliverySeconds
        : null,
      reset_count: resetWallMs.length,
      reset_wall_ms: resetWallMs,
      resource_summary: {
        sample_count: resources.samples.length,
        peak_rss_bytes: peakRssBytes,
        first_rss_bytes: firstResourceSample?.rss_bytes ?? null,
        last_rss_bytes: lastResourceSample?.rss_bytes ?? null,
        observed_rss_growth_bytes: firstResourceSample != null && lastResourceSample != null
          ? lastResourceSample.rss_bytes - firstResourceSample.rss_bytes
          : null
      },
      resource_samples: resources.samples,
      resource_sample_errors: resources.errors
    },
    process: { pid: child.pid, exit, diagnostics: child.diagnostics },
    events,
    non_claims: [
      ...manifest.admission.forbidden_claims,
      "Raw external decisions are not normalized Player Environment decisions.",
      "The current candidate lacks Host-local request idempotency and unknown-delivery recovery.",
      "The deterministic probe policy is not a gameplay or training policy."
    ]
  };
  let reportFile = null;
  if (evidenceRoot != null) {
    const directory = path.join(evidenceRoot, `${evidenceLabel}-${safeTimestamp()}`);
    mkdirSync(directory, { recursive: true });
    reportFile = path.join(directory, "report.json");
    writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  }
  return { report, reportFile };
}

export async function runManagedCandidateCapacity({
  root,
  candidateDirectory,
  diskIdentity,
  workerCounts = [1, 2, 4],
  maxActions = 200,
  episodesPerWorker = 5,
  seedPrefix = "H1MANAGED",
  evidenceRoot
}) {
  requirePositiveInteger(maxActions, "maxActions");
  requirePositiveInteger(episodesPerWorker, "episodesPerWorker");
  if (!Array.isArray(workerCounts) || workerCounts.length === 0) {
    throw new TypeError("workerCounts must contain at least one positive integer.");
  }
  workerCounts.forEach((count) => requirePositiveInteger(count, "workerCount"));
  const outputDirectory = path.join(evidenceRoot, `managed-capacity-${safeTimestamp()}`);
  mkdirSync(outputDirectory, { recursive: true });
  const groups = [];
  for (const workerCount of workerCounts) {
    const groupStart = performance.now();
    const workers = await Promise.all(Array.from({ length: workerCount }, (_, index) =>
      runManagedCandidateProbe({
        root,
        candidateDirectory,
        diskIdentity,
        seed: `${seedPrefix}${workerCount}W${index + 1}`,
        maxActions,
        episodeCount: episodesPerWorker,
        evidenceRoot: null
      })));
    const groupSeconds = (performance.now() - groupStart) / 1000;
    const decisions = workers.reduce((sum, worker) =>
      sum + worker.report.episode.delivered_raw_decisions, 0);
    const peakRss = workers.reduce((sum, worker) => {
      const samples = worker.report.performance.resource_samples;
      return sum + (samples.length === 0 ? 0 : Math.max(...samples.map((sample) => sample.rss_bytes)));
    }, 0);
    const starts = workers.map((worker) => worker.report.performance.decision_window_started_ms);
    const ends = workers.map((worker) => worker.report.performance.decision_window_ended_ms);
    const commonWindowSeconds = [...starts, ...ends].every(Number.isFinite)
      ? (Math.max(...ends) - Math.min(...starts)) / 1000
      : null;
    groups.push({
      worker_count: workerCount,
      status: workers.every((worker) => ["episodes_complete", "terminal_reached"].includes(worker.report.status))
        ? "measured_raw_candidate"
        : "candidate_failure",
      group_wall_seconds: groupSeconds,
      delivered_raw_decisions: decisions,
      process_lifecycle_inclusive_raw_decisions_per_second: groupSeconds > 0 ? decisions / groupSeconds : null,
      common_reset_inclusive_decision_window_seconds: commonWindowSeconds,
      aggregate_reset_inclusive_raw_decisions_per_second:
        commonWindowSeconds > 0 ? decisions / commonWindowSeconds : null,
      summed_worker_peak_rss_bytes: peakRss,
      workers: workers.map((worker) => ({
        status: worker.report.status,
        runtime_identity: worker.report.candidate.runtime_identity,
        episode: worker.report.episode,
        performance: {
          process_startup_seconds: worker.report.performance.process_startup_seconds,
          reset_inclusive_decision_window_seconds:
            worker.report.performance.reset_inclusive_decision_window_seconds,
          reset_inclusive_raw_decisions_per_second:
            worker.report.performance.reset_inclusive_raw_decisions_per_second,
          action_only_raw_decisions_per_second:
            worker.report.performance.action_only_raw_decisions_per_second,
          reset_count: worker.report.performance.reset_count,
          resource_summary: worker.report.performance.resource_summary
        },
        exit: worker.report.process.exit,
        diagnostic_count: worker.report.process.diagnostics.length
      }))
    });
  }
  const report = {
    schema: "sts2.headless/managed-candidate-capacity-1",
    generated_at: new Date().toISOString(),
    status: groups.every((group) => group.status === "measured_raw_candidate")
      ? "measured_raw_candidate"
      : "candidate_failure",
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    worker_counts: workerCounts,
    max_actions_per_worker: maxActions,
    episodes_per_worker: episodesPerWorker,
    groups,
    non_claims: [
      "This report measures the external candidate protocol, not canonical Player Environment throughput.",
      "In-process reset exercise does not establish long-run reset reliability or semantic equivalence.",
      "No semantic differential, idempotency, recovery, or H1.0 claim follows from capacity."
    ]
  };
  const reportFile = path.join(outputDirectory, "report.json");
  writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  return { report, reportFile };
}
