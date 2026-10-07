import { randomUUID } from "node:crypto";
import { createWriteStream, mkdirSync } from "node:fs";
import path from "node:path";
import { createServer } from "node:net";
import { finished } from "node:stream/promises";
import {
  EnvironmentControllerSession,
  PlayerEnvironmentRestClient
} from "@rsgcsg/sts2-connector-client";
import { evaluateRuntimeCompatibility } from "./compatibility.mjs";
import { canonicalizeEpisodeSeed, evaluateEpisodeProvenance } from "./episode-provenance.mjs";
import { readDiskIdentity } from "./game-installation.mjs";
import { evaluateHeadlessCapabilities } from "./headless-host.mjs";
import { chooseBoundAction } from "./journey-probe.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { resolveLaunchProfile } from "./profile-isolation.mjs";
import { instantiateProfileTemplate } from "./profile-template.mjs";
import {
  listGameProcesses,
  requestHostProvenance,
  resolveExperimentalConnectorCanary,
  shippedRuntimeLaunch,
  stopChild,
  waitForEndpoint,
  waitForInteractiveSnapshot
} from "./runtime-probe.mjs";

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

export async function allocateReferenceEndpoint() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  if (address == null || typeof address === "string") {
    throw new Error("Could not allocate an isolated Reference loopback endpoint.");
  }
  return `http://127.0.0.1:${address.port}`;
}

function childReference(child) {
  return {
    get exitCode() {
      return child.exitCode;
    },
    get signalCode() {
      return child.signalCode;
    }
  };
}

function stableSuccessor(snapshot, expectedSnapshotId) {
  return snapshot != null
    && snapshot.snapshot_id !== expectedSnapshotId
    && snapshot.status !== "settling";
}

export async function settleReferenceReceipt({
  receipt,
  expectedSnapshotId,
  observe,
  child,
  timeoutMs,
  pollIntervalMs = 100
}) {
  if (receipt?.delivery !== "delivered") return receipt;
  if (stableSuccessor(receipt.successor, expectedSnapshotId)) return receipt;
  const started = Date.now();
  let latest = receipt.successor ?? null;
  while (Date.now() - started < timeoutMs) {
    if (child.exitCode != null || child.signalCode != null) break;
    latest = await observe();
    if (stableSuccessor(latest, expectedSnapshotId)) {
      return {
        ...receipt,
        successor: latest,
        successor_observation: "driver_observed_after_delivery"
      };
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  return {
    ...receipt,
    successor: null,
    successor_observation: "timeout_after_delivered_input",
    last_observed_successor: latest
  };
}

/** Release only the reference bootstrap controller, then observe the exact
 * runtime unheld. SDK close intentionally swallows transport failures, so its
 * return alone is not handoff evidence. No gameplay submission is retried. */
export async function releaseReferenceController({
  controller, endpoint, expectedRuntimeInstanceId, timeoutMs = 5000,
  fetchImpl = fetch
}) {
  if (!controller || !expectedRuntimeInstanceId) throw new Error("reference_controller_missing");
  await controller.close();
  // The distributed Host intentionally supports its released SDK, whose close
  // method predates the status getter. Observe the existing public wire contract
  // directly; there is no alternate route, SDK detection or retry.
  const response = await fetchImpl(`${endpoint}/api/player-environment/controller`, {
    signal: AbortSignal.timeout(timeoutMs)
  });
  if (!response.ok) throw new Error(`reference_controller_status_http_${response.status}`);
  const state = await response.json();
  const fields = new Set(["protocol_version", "schema", "runtime_instance_id", "clients", "controller"]);
  const nonempty = value => typeof value === "string" && value.length > 0;
  if (!state || typeof state !== "object" || Array.isArray(state)
    || Object.keys(state).some(key => !fields.has(key))
    || state.protocol_version !== "1.0.0" || state.schema !== "sts2.player-environment/control-1"
    || !nonempty(state.runtime_instance_id) || state.runtime_instance_id !== expectedRuntimeInstanceId
    || !Array.isArray(state.clients) || state.clients.some(client => !client
      || typeof client !== "object" || Array.isArray(client)
      || !nonempty(client.client_session_id) || !nonempty(client.client_instance_id))
    || state.controller != null) {
    throw new Error("reference_controller_release_unconfirmed");
  }
  return {
    schema: "sts2.host-runtime/reference-controller-handoff-1",
    runtime_instance_id: expectedRuntimeInstanceId,
    observed_at: new Date().toISOString(),
    controller: null,
    basis: "fresh_control_observation_after_close"
  };
}

/** Select only Connector-provided setup inputs; never manufacture operands. */
export function chooseReferenceBootstrapAction(snapshot, { characterId = null, ascension = 0 } = {}) {
  if (characterId == null || snapshot.interaction?.kind !== "character_select")
    return chooseBoundAction(snapshot, { tutorialPreference: "disable" });
  if (snapshot.status !== "interactive" || snapshot.bound_actions?.status !== "complete")
    return null;
  const surface = snapshot.interaction.content?.surface;
  const character = surface?.characters?.find((value) => value.character_id === characterId);
  if (!character || character.is_locked || !character.is_enabled)
    throw new Error("reference_desired_character_unavailable");
  const actions = snapshot.bound_actions.actions;
  if (!character.is_selected)
    return actions.find((action) => action.verb === "select"
      && action.subject_referent_id === character.entity_id) ?? null;
  // An absent ascension panel denotes the native default A0, checked again in-run.
  const currentAscension = surface.ascension ?? 0;
  if (currentAscension !== ascension) {
    const label = currentAscension > ascension ? "Decrease Ascension" : "Increase Ascension";
    return actions.find((action) => action.verb === "activate" && action.label === label) ?? null;
  }
  return actions.find((action) => action.verb === "activate" && action.label === "Embark") ?? null;
}

export function verifyReferenceCharacter(snapshot, { characterId = null, ascension = 0 } = {}) {
  if (characterId == null) return;
  const persistent = snapshot.persistent?.content;
  if (persistent?.player?.character_definition_id !== characterId
    || persistent?.run?.ascension !== ascension)
    throw new Error("reference_actual_character_or_ascension_mismatch");
}

async function closeStreams(streams) {
  for (const stream of streams) stream.end();
  await Promise.allSettled(streams.map((stream) => finished(stream)));
}

export async function startShippedPlayerEnvironmentEpisode({
  installation,
  localRoot,
  evidenceRoot,
  seed,
  templateId = "vanilla-clean",
  characterId = null,
  ascension = 0,
  endpoint = null,
  timeoutMs = 90_000,
  requestTimeoutMs = 30_000,
  experimentalBuildAcknowledged = false,
  experimentalConnectorAcknowledged = false
}) {
  if (characterId !== null && (typeof characterId !== "string" || !characterId))
    throw new Error("reference_character_id_invalid");
  if (!Number.isSafeInteger(ascension) || ascension < 0 || ascension > 10)
    throw new Error("reference_ascension_invalid");
  const canonicalSeed = canonicalizeEpisodeSeed(seed);
  if (canonicalSeed == null) throw new Error("Reference episodes require one explicit canonical seed.");
  const running = listGameProcesses();
  if (running.length > 0) {
    throw new Error(`Reference Player Environment requires a clean process baseline:\n${running.join("\n")}`);
  }

  const diskIdentity = readDiskIdentity(installation);
  const runtimeEndpoint = endpoint ?? await allocateReferenceEndpoint();
  const compatibility = evaluateRuntimeCompatibility(diskIdentity);
  if (compatibility.status !== "supported_exact" && !experimentalBuildAcknowledged) {
    throw new Error(
      `Unsupported STS2 runtime (${compatibility.mismatches.join(", ")}); `
      + "explicit experimental acknowledgement is required."
    );
  }
  const profileId = `reference-driver-${randomUUID().slice(0, 12)}`;
  const profile = instantiateProfileTemplate({
    localRoot,
    templateId,
    profileId,
    expectedGameIdentity: diskIdentity
  });
  const launchProfile = resolveLaunchProfile({ localRoot, isolatedProfileId: profileId });
  const connectorCanary = resolveExperimentalConnectorCanary({
    installation,
    compatibility,
    acknowledged: experimentalBuildAcknowledged || experimentalConnectorAcknowledged
  });
  const evidenceDirectory = path.join(evidenceRoot, `reference-driver-${safeTimestamp()}-${profileId.slice(-12)}`);
  mkdirSync(evidenceDirectory, { recursive: true });
  const stdoutStream = createWriteStream(path.join(evidenceDirectory, "stdout.log"));
  const stderrStream = createWriteStream(path.join(evidenceDirectory, "stderr.log"));
  const launch = shippedRuntimeLaunch(installation, {
    launchProfile,
    connectorEndpoint: runtimeEndpoint,
    runSeed: canonicalSeed,
    connectorCanary
  });
  const { child } = launch;
  child.stdout.pipe(stdoutStream);
  child.stderr.pipe(stderrStream);
  let controller = null;
  let capabilities = null;
  let closed = false;
  let handoffReceipt = null;

  const close = async () => {
    if (closed) return null;
    closed = true;
    await controller?.close().catch(() => null);
    const exit = await stopChild(child, {
      endpoint: runtimeEndpoint,
      hostControlToken: launch.hostControlToken,
      expectedRuntimeInstanceId: capabilities?.host?.runtime_instance_id ?? null
    });
    await closeStreams([stdoutStream, stderrStream]);
    return exit;
  };

  try {
    const endpointResult = await waitForEndpoint(runtimeEndpoint, timeoutMs, childReference(child));
    if (!endpointResult.ok) {
      throw new Error(`Reference Connector endpoint did not become ready: ${endpointResult.error}`);
    }
    capabilities = endpointResult.value;
    const gate = evaluateHeadlessCapabilities(capabilities);
    if (!gate.ok) throw new Error(`Reference capability gate failed: ${gate.errors.join(", ")}`);
    const observations = await waitForInteractiveSnapshot(runtimeEndpoint, timeoutMs, childReference(child));
    let snapshot = observations.at(-1)?.value;
    if (snapshot == null) throw new Error("Reference runtime did not mount an interactive snapshot.");

    const client = new PlayerEnvironmentRestClient(runtimeEndpoint, requestTimeoutMs);
    controller = new EnvironmentControllerSession(client, {
      productId: "sts2-headless-reference-driver",
      productName: "STS2 Headless Reference Driver",
      productVersion: readProjectIdentity().version
    });
    await controller.register(capabilities.host, capabilities.control);
    const provenanceResponse = await requestHostProvenance({
      endpoint: runtimeEndpoint,
      hostControlToken: launch.hostControlToken,
      expectedRuntimeInstanceId: capabilities.host.runtime_instance_id,
      timeoutMs: requestTimeoutMs
    });
    let provenance = evaluateEpisodeProvenance({
      requestedSeed: canonicalSeed,
      expectedRuntimeInstanceId: capabilities.host.runtime_instance_id,
      response: provenanceResponse
    });

    const refreshProvenance = async () => {
      const response = await requestHostProvenance({
        endpoint: runtimeEndpoint,
        hostControlToken: launch.hostControlToken,
        expectedRuntimeInstanceId: capabilities.host.runtime_instance_id,
        timeoutMs: requestTimeoutMs
      });
      provenance = evaluateEpisodeProvenance({
        requestedSeed: canonicalSeed,
        expectedRuntimeInstanceId: capabilities.host.runtime_instance_id,
        response
      });
      return provenance;
    };

    const bootstrapTrace = [];
    const runEntryKinds = new Set([
      "main_menu",
      "singleplayer_menu",
      "character_select",
      "tutorial",
      "tutorial_preference"
    ]);
    for (let index = 0; runEntryKinds.has(snapshot.interaction.kind) && index < 16; index += 1) {
      const action = chooseReferenceBootstrapAction(snapshot, { characterId, ascension });
      if (action == null) {
        throw new Error(`Reference reset cannot safely advance ${snapshot.interaction.kind}.`);
      }
      const credentials = await controller.credentials();
      const receipt = (await client.submit({
        requestId: `reference-reset-${String(index + 1).padStart(2, "0")}-${randomUUID()}`,
        expectedSnapshotId: snapshot.snapshot_id,
        boundActionId: action.bound_action_id,
        clientSessionId: credentials.clientSessionId,
        controllerLeaseId: credentials.controllerLeaseId,
        controllerGeneration: credentials.controllerGeneration
      })).data;
      const settled = await settleReferenceReceipt({
        receipt,
        expectedSnapshotId: snapshot.snapshot_id,
        observe: async () => (await client.observe()).data,
        child,
        timeoutMs: requestTimeoutMs
      });
      bootstrapTrace.push({
        interaction_kind: snapshot.interaction.kind,
        verb: action.verb,
        label: action.label,
        delivery: settled.delivery,
        reason_code: settled.reason_code ?? null,
        successor_kind: settled.successor?.interaction?.kind ?? null
      });
      if (settled.delivery !== "delivered" || settled.successor == null) {
        throw new Error(
          `Reference reset input was not followed by a stable successor: `
          + `${settled.delivery}:${settled.reason_code ?? settled.successor_observation ?? "unspecified"}.`
        );
      }
      snapshot = settled.successor;
      await refreshProvenance();
    }
    if (runEntryKinds.has(snapshot.interaction.kind)) {
      throw new Error("Reference reset exceeded the bounded run-entry action budget.");
    }
    if (snapshot.status !== "interactive" || snapshot.interaction.kind !== "map_navigation") {
      throw new Error(
        `Reference reset expected the first map decision, observed ${snapshot.status}:${snapshot.interaction.kind}.`
      );
    }
    verifyReferenceCharacter(snapshot, { characterId, ascension });
    await refreshProvenance();
    if (provenance.verdict !== "provenance_pass") {
      throw new Error(`Reference run seed was not proven after bootstrap: ${provenance.errors.join(", ")}`);
    }

    return {
      snapshot,
      identity: {
        protocol: capabilities.protocol_version,
        host: capabilities.host,
        game: capabilities.game,
        disk: diskIdentity,
        profile,
        get episode_provenance() {
          return provenance;
        },
        bootstrap_trace: bootstrapTrace,
        requested_character_id: characterId,
        requested_ascension: characterId == null ? null : ascension,
        evidence_directory: evidenceDirectory,
        endpoint: runtimeEndpoint
      },
      observe: async () => (await client.observe()).data,
      read: async ({ readId, expectedSnapshotId }) =>
        (await client.read(readId, expectedSnapshotId)).data,
      provenance: refreshProvenance,
      releaseController: async () => {
        if (handoffReceipt) return handoffReceipt;
        handoffReceipt = await releaseReferenceController({
          controller, endpoint: runtimeEndpoint,
          expectedRuntimeInstanceId: capabilities.host.runtime_instance_id,
          timeoutMs: requestTimeoutMs
        });
        return handoffReceipt;
      },
      submit: async ({ requestId, expectedSnapshotId, boundActionId }) => {
        if (handoffReceipt) throw new Error("reference_controller_handed_off");
        const credentials = await controller.credentials();
        const receipt = (await client.submit({
          requestId,
          expectedSnapshotId,
          boundActionId,
          clientSessionId: credentials.clientSessionId,
          controllerLeaseId: credentials.controllerLeaseId,
          controllerGeneration: credentials.controllerGeneration
        })).data;
        const settled = await settleReferenceReceipt({
          receipt,
          expectedSnapshotId,
          observe: async () => (await client.observe()).data,
          child,
          timeoutMs: requestTimeoutMs
        });
        await refreshProvenance();
        return settled;
      },
      close
    };
  } catch (error) {
    await close();
    throw error;
  }
}

export class ShippedPlayerEnvironmentSession {
  constructor(options, { startEpisode = startShippedPlayerEnvironmentEpisode } = {}) {
    this.options = options;
    this.startEpisode = startEpisode;
    this.episode = null;
    this.lastIdentity = null;
  }

  async reset(seed) {
    await this.closeEpisode();
    this.episode = await this.startEpisode({ ...this.options, seed });
    this.lastIdentity = this.episode.identity;
    return this.episode.snapshot;
  }

  requireEpisode() {
    if (this.episode == null) throw new Error("Reference Player Environment must be reset before use.");
    return this.episode;
  }

  observe() {
    return this.requireEpisode().observe();
  }

  read(input) {
    return this.requireEpisode().read(input);
  }

  submit(input) {
    return this.requireEpisode().submit(input);
  }

  releaseController() {
    return this.requireEpisode().releaseController();
  }

  async provenance() {
    const episode = this.requireEpisode();
    const episodeProvenance = await episode.provenance();
    this.lastIdentity = {
      ...this.lastIdentity,
      episode_provenance: episodeProvenance
    };
    return this.lastIdentity;
  }

  async closeEpisode() {
    if (this.episode == null) return null;
    const current = this.episode;
    this.episode = null;
    return current.close();
  }

  close() {
    return this.closeEpisode();
  }
}
