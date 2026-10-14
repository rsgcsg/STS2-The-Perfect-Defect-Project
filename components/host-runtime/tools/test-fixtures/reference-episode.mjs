// Synthetic World only: exercise production session, JSONL, cancellation and
// cleanup plumbing. This process never discovers or launches STS2.
import fs from "node:fs";
import path from "node:path";
import { referenceDriverOptions, serveReferenceDriver } from "../reference-pe-driver.mjs";
import { ShippedPlayerEnvironmentSession } from "../../src/shipped-player-environment.mjs";
import { instantiateProfileTemplate } from "../../src/profile-template.mjs";

const args = process.argv.slice(2);
const option = (key, fallback = null) => args.includes(key) ? args[args.indexOf(key)+1] : fallback;
const config = JSON.parse(fs.readFileSync(option("--fixture"), "utf8"));
const configuration = args.includes("--local-root") ? referenceDriverOptions(args) : null;
const log = (value) => config.log && fs.appendFileSync(config.log, `${JSON.stringify(value)}\n`);
const abortController = new AbortController();
const exit = { code: 0, signal: null, forced: false, host_shutdown: {
  status: "requested", response: { status: "shutdown_requested", runtime_instance_id: config.identity?.host?.runtime_instance_id ?? "fixture-runtime" }
} };
const session = new ShippedPlayerEnvironmentSession(configuration?.sessionOptions ?? {}, { startEpisode: async ({ signal, seed, localRoot, evidenceRoot, templateId }) => {
  log({ event: "reset_started", seed });
  if (config.blockReset) await new Promise((resolve, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener("abort", () => { log({ event: "reset_aborted" }); reject(signal.reason); }, { once: true });
  });
  const identity = structuredClone(config.identity ?? { host: { runtime_instance_id: "fixture-runtime" } });
  if (config.templateIdentity) {
    const profile = instantiateProfileTemplate({ localRoot, templateId,
      profileId: `fixture-${path.basename(config.log)}-${Date.now()}`,
      expectedGameIdentity: config.templateIdentity });
    identity.profile = profile;
    fs.mkdirSync(evidenceRoot, { recursive: true });
    fs.appendFileSync(path.join(evidenceRoot, "fixture-episodes.jsonl"), `${JSON.stringify({ seed, profile })}\n`);
    log({ event: "external_state", localRoot, evidenceRoot, profile });
  }
  return { snapshot: config.snapshot ?? { snapshot_id: "fixture-initial", interaction: { kind: "map_navigation" } },
    identity,
    releaseController: async () => {
      log({ event: "released" });
      return { schema: "sts2.host-runtime/reference-controller-handoff-1", runtime_instance_id: config.identity?.host?.runtime_instance_id ?? "fixture-runtime",
        controller: config.held ? { fixture: true } : null, observed_at: "2026-10-11T00:00:00.000Z", basis: "fresh_control_observation_after_close" };
    },
    observe: async () => { log({ event: "observe" }); return config.snapshot; },
    read: async () => { log({ event: "read" }); return {}; },
    submit: async () => { log({ event: "step" }); return { delivery: "delivered" }; },
    provenance: async () => { log({ event: "identity" }); return config.identity?.episode_provenance ?? {}; },
    close: async () => { log({ event: "close" }); return exit; }
  };
} });
if (config.readyDelay) await new Promise(resolve => setTimeout(resolve, config.readyDelay));
process.stdout.write(`${JSON.stringify({ type: "ready", protocol: "sts2.headless/reference-player-environment-driver-1",
  host_kind: "shipped_reference", ...(config.missingCapability ? {} : { bootstrap_handoff: "sts2.host-runtime/reference-bootstrap-handoff-1" }),
  ...(configuration && !config.missingStateCapability ? { state_root: config.stateRootReadback ?? configuration.stateRootReadback } : {}) })}\n`);
await serveReferenceDriver(session, { abortController });
log({ event: "driver_finished" });
