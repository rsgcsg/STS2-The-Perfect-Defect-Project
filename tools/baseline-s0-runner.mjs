#!/usr/bin/env node
/** Bounded native S0 orchestration. No second executor, Model or legality engine. */
import { randomUUID, createHash } from "node:crypto";
import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { PublicMenuTeacher, TEACHER_ID, TEACHER_VERSION, INPUT_SPEC, S0_TEXT_V2_KINDS } from "./baseline-s0-teacher.mjs";
import { S0RawRecords, sha256 } from "./baseline-s0-records.mjs";
import { resolveInstallation } from "../components/host-runtime/src/game-installation.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCHEMA = "sts2.player-environment/text-menu-snapshot-2";

export function makeS0Manifest(capabilities, artifact, adapter, { learned = false } = {}) {
  if (capabilities.input_profile !== "text-menu-v2" || capabilities.snapshot_schema !== SCHEMA)
    throw new Error("s0_text_menu_v2_capabilities_required");
  const host = capabilities.host;
  const game = capabilities.game;
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: `s0-${artifact.sha256.slice(0, 16)}`,
    policy: { id: learned ? "s0-structured-m2" : TEACHER_ID, version: learned ? "1.0.0" : TEACHER_VERSION, provider: "local",
      architecture: learned ? "stpd.structured-observation-only.s-m2-0.v1" : "public-catalog-heuristic" },
    adapter, artifact,
    representation: { id: learned ? "stpd-structured-observation-only-v1" : INPUT_SPEC,
      version: "1.0.0", input_schema: SCHEMA },
    requirements: { connector_protocol_version: capabilities.protocol_version,
      environment: { host_kind: host.host_kind, connector_version: host.version,
        connector_source_revision: host.implementation.source_revision,
        connector_artifact_sha256: host.implementation.artifact_sha256,
        connector_module_version_id: host.implementation.module_version_id,
        modset_status: game.modset.status, modset_fingerprint: game.modset.fingerprint,
        loaded_mod_ids: [...game.modset.loaded_mod_ids] },
      reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-menu-action-id-order",
      score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: [game.version], game_commits: [game.commit],
      // Native text-v2 advertises verbs, not Managed interaction_kinds/action_verbs.
      // This bounded declared consumer scope is checked against each actual observation.
      interaction_kinds: [...S0_TEXT_V2_KINDS], action_verbs: [...capabilities.verbs] },
    adapter_config: {}, claims: { full_run: false, selector: false, catalog_filtered: false,
      creates_action_authority: false, creates_native_operands: false }
  };
}

export async function compiledRuntimeDigest(directory) {
  const names = (await readdir(directory)).filter(name => name.endsWith(".js")).sort();
  if (!names.length) throw new Error("compiled_runtime_absent");
  const hash = createHash("sha256");
  for (const name of names) hash.update(name).update("\0").update(await readFile(path.join(directory, name))).update("\0");
  return hash.digest("hex");
}

export async function confirmControlReleased(endpoint, runtimeInstanceId, fetchImpl = fetch) {
  // The SDK owns the production route and strict wire decoder; no parallel GET schema here.
  const sdk = await import(pathToFileURL(path.join(ROOT, "components/connector/sdk/typescript/dist/index.js")));
  const client = new sdk.PlayerEnvironmentRestClient(endpoint, 10_000, fetchImpl);
  const control = (await client.controlSnapshot()).data;
  if (control.runtime_instance_id !== runtimeInstanceId || control.controller != null)
    throw new Error("stop_control_release_unconfirmed");
  return { confirmed: true, basis: "fresh_control_observation_after_runtime_stop",
    read_route: sdk.PLAYER_ENVIRONMENT_CONTROL_ROUTE, control };
}

async function defaultDependencies() {
  const [host, runtime, sdk] = await Promise.all([
    import(pathToFileURL(path.join(ROOT, "components/host-runtime/src/shipped-player-environment.mjs"))),
    import(pathToFileURL(path.join(ROOT, "components/policy-runtime/dist/index.js"))),
    import(pathToFileURL(path.join(ROOT, "components/connector/sdk/typescript/dist/index.js")))
  ]);
  return { startEpisode: host.startShippedPlayerEnvironmentEpisode, ...runtime,
    PlayerEnvironmentRestClient: sdk.PlayerEnvironmentRestClient };
}

/** Exported for faithful lifecycle tests; default dependencies are the production libraries. */
export async function runS0(options, dependencies) {
  if (!["collect", "evaluate"].includes(options.mode)) throw new Error("mode_must_be_collect_or_evaluate");
  const maxCalls = options.maxCalls ?? 60, maxSubmissions = options.maxSubmissions ?? 60;
  const deadlineMs = options.deadlineMs ?? 240_000;
  if (!Number.isInteger(maxCalls) || maxCalls < 1 || maxCalls > 60
    || !Number.isInteger(maxSubmissions) || maxSubmissions < 1 || maxSubmissions > 60
    || !Number.isInteger(deadlineMs) || deadlineMs < 1 || deadlineMs > 240_000)
    throw new Error("s0_finite_budget_required");
  if (options.mode === "evaluate" && !options.package) throw new Error("evaluate_package_required");
  const deps = dependencies ?? await defaultDependencies();
  // CLI provides a directory, while the Host Driver consumes resolved installation paths.
  // Programmatic callers may already hold that Host-owned object.
  const installation = typeof options.installation === "string"
    ? (deps.resolveInstallation ?? resolveInstallation)(options.installation) : options.installation;
  const runId = `s0-${options.mode}-${randomUUID()}`;
  await mkdir(options.evidenceRoot, { recursive: true });
  const directory = path.join(options.evidenceRoot, runId);
  const teacherPath = path.join(ROOT, "tools/baseline-s0-teacher.mjs");
  const teacherSha = sha256(await readFile(teacherPath));
  const actor = options.mode === "collect"
    ? { id: TEACHER_ID, version: TEACHER_VERSION, code_sha256: teacherSha, role: "teacher" }
    : { id: "stpd-s0-structured-adapter", version: "1.0.0", role: "learned" };
  const records = await S0RawRecords.create(directory, runId, actor);
  let episode, runtime, port, evidence, endpoint, stop, runError;
  const errors = [];
  let ticks = 0, nativeDeliveries = 0, externalStop = false, termination = "bounded_loop_end";
  const signalStop = () => { externalStop = true; void runtime?.stop().catch(error => errors.push(String(error))); };
  if (!dependencies) { process.once("SIGINT", signalStop); process.once("SIGTERM", signalStop); }
  try {
    await records.append("run_start", { source_kind: "agent", input_spec: INPUT_SPEC, actor,
      budget: { maxPolicyCalls: maxCalls, maxSubmissions, deadlineMs }, I: false, F: false,
      qualification: "sampled_native_agent_offers_only_no_human_or_causal_successor_claim",
      runner_code_sha256: sha256(await readFile(fileURLToPath(import.meta.url))),
      source_revision: execFileSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).trim(),
      source_diff_sha256: sha256(execFileSync("git", ["diff", "HEAD"], { cwd: ROOT })),
      seed: options.seed, template_id: options.templateId ?? "defect-a0-s0",
      character_id: "DEFECT", ascension: 0, teacher_parameters: { browse: options.browse !== false },
      records_code_sha256: sha256(await readFile(path.join(ROOT, "tools/baseline-s0-records.mjs"))) });
    const hostRoot = path.join(directory, "host");
    await mkdir(hostRoot);
    episode = await deps.startEpisode({ installation, localRoot: options.localRoot,
      evidenceRoot: hostRoot, seed: options.seed, templateId: options.templateId ?? "defect-a0-s0",
      characterId: "DEFECT", ascension: 0,
      experimentalBuildAcknowledged: options.experimentalBuildAcknowledged ?? false,
      experimentalConnectorAcknowledged: options.experimentalConnectorAcknowledged ?? false });
    endpoint = episode.identity.endpoint;
    await records.append("episode_identity", episode.identity);
    if (typeof episode.releaseController !== "function") throw new Error("driver_controller_handoff_api_required");
    await records.append("bootstrap_controller_handoff", await episode.releaseController());
    const client = new deps.PlayerEnvironmentRestClient(endpoint, 15_000);
    if (typeof client.getFullTextMenuV2 !== "function") throw new Error("sealed_sdk_required");
    const acquire = client.getFullTextMenuV2.bind(client);
    client.getFullTextMenuV2 = async (...args) => {
      const full = await acquire(...args);
      await records.capture(full);
      return full;
    };
    const capabilities = (await client.textMenuV2Capabilities()).data;
    await records.append("capabilities", capabilities);
    let artifact = { id: TEACHER_ID, path: teacherPath, sha256: teacherSha };
    let adapter = { id: TEACHER_ID, version: TEACHER_VERSION,
      protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: teacherSha };
    if (options.mode === "evaluate") {
      const modelPath = path.join(path.resolve(options.package), "model.json");
      const bytes = await readFile(modelPath);
      const model = JSON.parse(bytes);
      artifact = { id: model.model_id, path: modelPath, sha256: sha256(bytes) };
      adapter = { id: "stpd-s0-structured-adapter", version: "1.0.0",
        protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: model.adapter_code_sha256 };
      actor.code_sha256 = model.adapter_code_sha256;
    }
    const manifest = makeS0Manifest(capabilities, artifact, adapter, { learned: options.mode === "evaluate" });
    deps.validatePolicyManifest(manifest);
    const manifestPath = path.join(directory, "policy-manifest.json");
    await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
    const runtimeCode = await (deps.runtimeCodeDigest ?? compiledRuntimeDigest)(path.join(ROOT, "components/policy-runtime/dist"));
    await records.append("runtime_identity", { version: deps.POLICY_RUNTIME_VERSION, code_sha256: runtimeCode,
      manifest, manifest_sha256: sha256(await readFile(manifestPath)) });
    const evidenceRoot = path.join(directory, "runtime");
    await mkdir(evidenceRoot);
    evidence = await deps.AgentRunEvidence.create({ root: evidenceRoot, runId, policyManifest: manifest,
      runtimeVersion: deps.POLICY_RUNTIME_VERSION, runtimeCodeSha256: runtimeCode, mode: "auto" });
    let statefulPolicy;
    if (options.mode === "collect") {
      const teacher = new PublicMenuTeacher({ browse: options.browse !== false });
      statefulPolicy = async input => {
        const offerId = await records.offer(input);
        const value = teacher.decide(input);
        await records.policyResult(offerId, input, value);
        return value;
      };
    } else {
      const command = options.pythonCommand ?? "uv";
      const args = options.pythonCommand ? ["-m", "stpd.policy.structured_port"]
        : ["run", "--project", path.join(ROOT, "python"), "--locked", "--extra", "local-models",
          "python", "-m", "stpd.policy.structured_port"];
      port = deps.NdjsonPolicyPort.spawn(command, [...args, "--package", path.resolve(options.package),
        "--manifest", manifestPath], { cwd: path.join(ROOT, "python") });
      await port.attest(manifest.adapter);
      statefulPolicy = async (input, signal, onOffer) => {
        let offer;
        try {
          const value = await port.decideV2(input, signal, () => {
            offer = records.beginOffer(input); // Synchronous join must pass BEFORE stdin.write.
            void offer.committed.catch(() => {}); // Await below; do not leave a write failure unhandled during inference.
            onOffer();
          });
          if (!offer) throw new Error("child_returned_without_offer");
          await offer.committed;
          await records.policyResult(offer.offerId, input, value);
          return value;
        } catch (error) { if (offer) await offer.committed; throw error; }
      };
    }
    await evidence.attestAdapter(manifest.adapter);
    const connector = new deps.ConnectorPolicyClient(client, { observationAcquisition: "sealed-text-menu-v2",
      productId: "baseline-s0", productName: "Baseline S0", productVersion: "1.0.0" });
    runtime = new deps.PolicyRuntime({ manifest, connector, statefulPolicy, evidence, runId, mode: "auto",
      statefulOfferBoundary: options.mode === "evaluate" ? "port_write" : "invocation",
      runtimeIdentity: { version: deps.POLICY_RUNTIME_VERSION, code_sha256: runtimeCode },
      autoBudget: { maxPolicyCalls: maxCalls, maxSubmissions, deadlineMs },
      staleRefresh: { maxAttempts: 1, baseBackoffMs: 0 },
      successorPoll: { maxAttempts: 40, baseBackoffMs: 100 }, policyTimeoutMs: 30_000 });
    const started = performance.now();
    while (!externalStop && ticks < 1200 && performance.now() - started < deadlineMs) {
      const result = await runtime.tick();
      ticks += 1;
      await records.append("tick", result);
      if (result.type === "text_native_delivered") nativeDeliveries += 1;
      if (result.type === "unknown" || result.status?.tainted) {
        termination = result.type === "unknown" ? "unknown_delivery_or_successor" : "runtime_tainted";
        break; // No retry or new mutation.
      }
      const status = runtime.status();
      if (status.mode === "human" || status.lifecycle === "stopped"
        || status.autonomy_budget?.state === "exhausted") { termination = "runtime_handoff_or_budget"; break; }
      if (result.type === "not_admitted") await new Promise(resolve => setTimeout(resolve, 100));
    }
  } catch (error) {
    runError = error;
    termination = "run_error";
    errors.push(String(error));
    await records.append("run_error", { message: String(error) }).catch(() => {});
  } finally {
    // Stop first, confirm release while the process still exists, then close its owning Driver.
    try {
      if (runtime) {
        const stopped = await runtime.stop();
        await records.append("runtime_stop", stopped);
        stop = await (deps.confirmControlReleased ?? confirmControlReleased)(endpoint, episode.identity.host.runtime_instance_id);
        await records.append("control_release_confirmation", stop);
      } else if (evidence) await evidence.finalize({ status: "stopped", tainted: true, mode: "human" });
    } catch (error) { errors.push(String(error)); runError ??= error; }
    try { port?.close(); } catch (error) { errors.push(String(error)); runError ??= error; }
    try { if (episode) await episode.close(); }
    catch (error) { errors.push(String(error)); runError ??= error; }
    await records.append("run_end", { ticks, native_deliveries: nativeDeliveries,
      offers: records.offerCount, captures: records.captureCount, capture_bytes: records.captureBytes,
      stop_confirmed: stop?.confirmed === true, termination, errors }).catch(error => { runError ??= error; });
    await records.close().catch(error => { runError ??= error; });
    if (!dependencies) { process.removeListener("SIGINT", signalStop); process.removeListener("SIGTERM", signalStop); }
  }
  const summary = { schema: "sts2.baseline-s0/run-summary-1", run_id: runId, mode: options.mode,
    directory, ticks, offers: records.offerCount, captures: records.captureCount,
    native_deliveries: nativeDeliveries, stop_confirmed: stop?.confirmed === true, termination, errors,
    evidence_level: "raw_native_agent_collection_requires_owner_verification", source_kind: "agent" };
  await writeFile(path.join(directory, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, { flag: "wx" });
  if (runError) throw Object.assign(new Error(`S0 run failed: ${errors.join("; ")}`), { summary });
  return summary;
}

export function parseArguments(args) {
  const mode = args.shift();
  const values = {};
  const allowed = new Set(["installation", "local-root", "evidence-root", "seed", "template-id",
    "package", "python-command", "max-calls", "max-submissions", "deadline-ms",
    "experimental-build-acknowledged", "experimental-connector-acknowledged", "browse"]);
  while (args.length) {
    const key = args.shift();
    const value = args.shift();
    if (!key?.startsWith("--") || !allowed.has(key.slice(2)) || value === undefined
      || Object.hasOwn(values, key.slice(2))) throw new Error("invalid_or_duplicate_argument");
    values[key.slice(2)] = value;
  }
  for (const key of ["installation", "local-root", "evidence-root", "seed"])
    if (!values[key]) throw new Error(`--${key} required`);
  const result = { mode };
  for (const [key, value] of Object.entries(values)) {
    const name = key.replace(/-([a-z])/gu, (_, letter) => letter.toUpperCase());
    if (["max-calls", "max-submissions", "deadline-ms"].includes(key)) result[name] = Number(value);
    else if (["experimental-build-acknowledged", "experimental-connector-acknowledged", "browse"].includes(key)) {
      if (!["true", "false"].includes(value)) throw new Error(`--${key} must be true or false`);
      result[name] = value === "true";
    } else result[name] = ["installation", "local-root", "evidence-root", "package", "python-command"].includes(key)
      ? path.resolve(value) : value;
  }
  return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runS0(parseArguments(process.argv.slice(2))).then(result => process.stdout.write(`${JSON.stringify(result)}\n`))
    .catch(error => { process.stderr.write(`${JSON.stringify({ error: String(error), summary: error.summary ?? null })}\n`);
      process.exitCode = 1; });
}
