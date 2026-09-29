import { randomUUID } from "node:crypto";
import { canonicalizeEpisodeSeed } from "./episode-provenance.mjs";
import { ManagedTextMenuSessionAdapter, MANAGED_TEXT_MENU_PROFILE } from "./managed-text-menu-map.mjs";
import { ManagedTextMenuV2SessionAdapter, MANAGED_TEXT_MENU_V2_PROFILE,
  MANAGED_TEXT_MENU_V2_CONTEXT_SCHEMA } from "./managed-text-menu-v2.mjs";

const TEXT_CONTEXT_SCHEMA = "sts2.player-environment/text-menu-observation-context-1";

/** One JSONL driver's raw and text commands over one exact Managed session. */
export class ManagedPeDriverSession {
  #started;
  #text;
  #textV2;
  #timeoutMs;
  #mountAttempted = false;
  #available = false;
  #closed = false;
  #requestedSeed = null;
  #gameContinuityId = null;
  // The adapter owns same-episode replay. This fence prevents a request ID from
  // being reintroduced under a new game continuity after a successful reset.
  #textRequestContinuity = new Map();
  #mutationRoutes = new Map();
  #tail = Promise.resolve();

  constructor(started, { requestTimeoutMs = 10_000 } = {}) {
    if (started?.session == null || started?.runtime == null) {
      throw new TypeError("Managed driver requires one started session and runtime.");
    }
    this.#started = started;
    this.#text = new ManagedTextMenuSessionAdapter(started.session);
    this.#textV2 = new ManagedTextMenuV2SessionAdapter(started.session);
    this.#timeoutMs = requestTimeoutMs;
  }

  get closed() { return this.#closed; }

  #requireEpisode() {
    if (this.#closed) throw new Error("driver_closed");
    if (!this.#available || this.#gameContinuityId == null) {
      throw new Error("managed_episode_unavailable_reset_required");
    }
  }

  #claimMutationRoute(requestId, route) {
    if (typeof requestId !== "string" || requestId.length === 0) return;
    const earlier = this.#mutationRoutes.get(requestId);
    if (earlier != null && earlier !== route) {
      throw new Error("mutation_request_id_conflict_between_routes");
    }
    this.#mutationRoutes.set(requestId, route);
  }

  handle(request) {
    const next = this.#tail.then(() => this.#handle(request));
    this.#tail = next.catch(() => undefined);
    return next;
  }

  async #handle(request) {
    if (this.#closed) throw new Error("driver_closed");
    const requestId = request?.request_id ?? null;
    switch (request?.command) {
      case "reset": {
        const seed = canonicalizeEpisodeSeed(request.seed);
        // reset_run first destroys the old simulator. A failed/unknown reset
        // cannot leave either public action route bound to that old episode.
        const reset = this.#mountAttempted;
        this.#mountAttempted = true;
        this.#available = false;
        this.#gameContinuityId = null;
        this.#requestedSeed = null;
        this.#textV2.resetSelection();
        const snapshot = await this.#started.session.mount({
          seed, reset, timeoutMs: this.#timeoutMs
        });
        this.#requestedSeed = seed;
        this.#gameContinuityId = `managed_episode_${randomUUID().replaceAll("-", "")}`;
        this.#available = true;
        return { type: "reset_result", request_id: requestId, snapshot };
      }
      case "observe":
        this.#requireEpisode();
        return { type: "observe_result", request_id: requestId,
          snapshot: this.#started.session.observe() };
      case "read":
        this.#requireEpisode();
        return { type: "read_result", request_id: requestId,
          read: this.#started.session.read({
            readId: request.read_id, expectedSnapshotId: request.expected_snapshot_id
          }) };
      case "step":
        this.#requireEpisode();
        if (this.#started.session.tainted === true) {
          throw new Error(this.#started.session.taintReason === "successor_projection_failed"
            ? "managed_session_tainted_after_successor_projection_failure"
            : "managed_session_tainted_after_unknown");
        }
        this.#claimMutationRoute(request.mutation_request_id, "raw");
        this.#textV2.resetSelection();
        return { type: "step_result", request_id: requestId,
          receipt: await this.#started.session.submit({
            requestId: request.mutation_request_id,
            expectedSnapshotId: request.expected_snapshot_id,
            boundActionId: request.bound_action_id,
            timeoutMs: this.#timeoutMs
          }) };
      case "text_observe": {
        this.#requireEpisode();
        const profile = request.input_profile ?? MANAGED_TEXT_MENU_PROFILE;
        if (profile !== MANAGED_TEXT_MENU_PROFILE && profile !== MANAGED_TEXT_MENU_V2_PROFILE) {
          throw new TypeError("Unsupported text-menu input_profile.");
        }
        const context = {
          schema: profile === MANAGED_TEXT_MENU_V2_PROFILE
            ? MANAGED_TEXT_MENU_V2_CONTEXT_SCHEMA : TEXT_CONTEXT_SCHEMA,
          snapshot: profile === MANAGED_TEXT_MENU_V2_PROFILE
            ? this.#textV2.observe() : this.#text.observe(),
          game_continuity_id: this.#gameContinuityId
        };
        return { type: "text_observe_result", request_id: requestId, context };
      }
      case "text_submit": {
        this.#requireEpisode();
        const profile = request.input_profile ?? MANAGED_TEXT_MENU_PROFILE;
        if (profile !== MANAGED_TEXT_MENU_PROFILE && profile !== MANAGED_TEXT_MENU_V2_PROFILE) {
          throw new TypeError("Unsupported text-menu input_profile.");
        }
        if (request.expected_game_continuity_id !== this.#gameContinuityId) {
          throw new Error("stale_game_continuity");
        }
        const mutationId = request.mutation_request_id;
        if (typeof mutationId !== "string" || mutationId.length === 0) {
          throw new TypeError("text_submit requires mutation_request_id.");
        }
        const earlier = this.#textRequestContinuity.get(mutationId);
        if (earlier != null && earlier !== this.#gameContinuityId) {
          throw new Error("text_request_id_conflict_across_episodes");
        }
        this.#claimMutationRoute(mutationId, profile);
        this.#textRequestContinuity.set(mutationId, this.#gameContinuityId);
        return { type: "text_submit_result", request_id: requestId,
          result: await (profile === MANAGED_TEXT_MENU_V2_PROFILE ? this.#textV2 : this.#text).submit({
            request_id: mutationId,
            expected_snapshot_id: request.expected_snapshot_id,
            action_id: request.action_id,
            input_profile: profile,
            timeout_ms: this.#timeoutMs
          }) };
      }
      case "episode_identity": {
        this.#requireEpisode();
        const runIdentity = await this.#started.runtime.process.request(
          { cmd: "run_identity" }, this.#timeoutMs
        );
        return { type: "episode_identity_result", request_id: requestId,
          identity: {
            candidate_build: this.#started.runtime.build,
            runtime_identity: this.#started.runtime.runtimeIdentity,
            adapter_runtime_instance_id: this.#started.runtime.adapterRuntimeInstanceId,
            environment_fingerprint: this.#started.environmentFingerprint,
            episode_provenance: {
              verdict: runIdentity?.type === "run_identity"
                && runIdentity.active === true
                && runIdentity.seed === this.#requestedSeed
                ? "provenance_pass" : "provenance_incomplete",
              requested_seed: this.#requestedSeed,
              actual_seed: runIdentity?.seed ?? null,
              runtime_instance_id: this.#started.runtime.adapterRuntimeInstanceId
            }
          } };
      }
      case "close":
        this.#closed = true;
        this.#available = false;
        this.#gameContinuityId = null;
        return { type: "close_result", request_id: requestId,
          exit: await this.#started.session.close() };
      default:
        throw new Error(`Unsupported driver command: ${String(request?.command)}`);
    }
  }
}
