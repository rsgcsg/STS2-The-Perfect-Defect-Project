function sameArtifactIdentity(left, right) {
  return left?.sha256 === right?.sha256
    && left?.module_version_id === right?.module_version_id;
}

function sameHostIdentity(left, right) {
  return left?.artifact_sha256 === right?.sha256
    && left?.module_version_id === right?.module_version_id;
}

function isAtOrAfter(value, lowerBound) {
  const instant = Date.parse(value);
  const boundary = Date.parse(lowerBound);
  return Number.isFinite(instant) && Number.isFinite(boundary) && instant >= boundary;
}

export function extractGameProcessIds(processRecords, platform) {
  return processRecords.flatMap((record) => {
    const text = String(record).trim();
    const match = platform === "win32"
      ? /^"[^"]+","(\d+)"(?:,|$)/u.exec(text)
      : /^(\d+)(?:\s|$)/u.exec(text);
    return match ? [match[1]] : [];
  });
}

function evaluateIdentityEvidence({
  status,
  capabilities,
  platformIdentity,
  liveUiIdentity,
  installed,
  uiPanelReady,
  gameProcessIds
}, requireExecution) {
  const errors = [];
  const expected = installed.artifact;
  const source = installed.source;
  const activeProcessIds = new Set(gameProcessIds.map(String));

  if (!platformIdentity) errors.push("platform_loaded_identity_absent");
  const expectedVersion = installed.schema === "sts2.platform/game-mod-installed-provenance-1"
    ? installed.manifest?.version
    : installed.schema === "sts2.platform/game-mod-build-provenance-1"
      ? installed.package?.manifest?.version : null;
  if (typeof expectedVersion !== "string" || !expectedVersion
      || platformIdentity?.version !== expectedVersion)
    errors.push("platform_loaded_version_mismatch");
  if (platformIdentity?.artifact_sha256 !== expected.sha256) errors.push("platform_loaded_sha_mismatch");
  if (platformIdentity?.module_version_id !== expected.module_version_id) errors.push("platform_loaded_mvid_mismatch");
  if (platformIdentity?.platform_source_revision !== source.platform.source_revision) errors.push("platform_loaded_source_revision_mismatch");
  if (platformIdentity?.platform_source_digest_sha256 !== source.platform.source_digest_sha256) errors.push("platform_loaded_source_digest_mismatch");
  if (platformIdentity?.connector_source_revision !== source.components.connector.source_revision) errors.push("platform_loaded_connector_source_revision_mismatch");
  if (platformIdentity?.annotator_source_revision !== source.components.annotator.source_revision) errors.push("platform_loaded_annotator_source_revision_mismatch");
  if (platformIdentity?.live_ui_source_revision !== source.components.live_ui.source_revision) errors.push("platform_loaded_live_ui_source_revision_mismatch");

  if (!liveUiIdentity) errors.push("live_ui_loaded_identity_absent");
  if (liveUiIdentity?.artifact_sha256 !== expected.sha256) errors.push("live_ui_loaded_sha_mismatch");
  if (liveUiIdentity?.module_version_id !== expected.module_version_id) errors.push("live_ui_loaded_mvid_mismatch");
  if (liveUiIdentity?.source_revision !== source.components.live_ui.source_revision) errors.push("live_ui_source_revision_mismatch");
  if (liveUiIdentity?.source_digest_sha256 !== source.components.live_ui.source_digest_sha256) errors.push("live_ui_source_digest_mismatch");
  if (!uiPanelReady) errors.push("live_ui_panel_ready_absent");

  if (!sameHostIdentity(capabilities.host?.implementation, expected)) errors.push("connector_capabilities_artifact_mismatch");
  if (capabilities.host?.implementation?.source_revision !== source.components.connector.source_revision) errors.push("connector_capabilities_source_revision_mismatch");
  if (requireExecution && capabilities.execution_available !== true) errors.push("connector_execution_not_available");
  if (capabilities.game?.modset?.status !== "exact_platform_modset") errors.push("unified_modset_not_exact");
  if (capabilities.game?.modset?.loaded_mod_ids?.length !== 1
      || capabilities.game.modset.loaded_mod_ids[0] !== "STS2_PLATFORM") {
    errors.push("unified_modset_membership_mismatch");
  }

  if (!activeProcessIds.has(String(status.process_id))) errors.push("annotator_runtime_process_mismatch");
  if (!isAtOrAfter(status.observed_at, platformIdentity?.loaded_at)) errors.push("annotator_runtime_generation_mismatch");

  // A recording environment is session-bound. It is absent in the legal Ready/no-session state,
  // but when present it must agree with the process-global loaded evidence above.
  if (status.environment !== undefined) {
    if (!sameArtifactIdentity(status.environment.connector, expected)) errors.push("recording_connector_artifact_mismatch");
    if (!sameArtifactIdentity(status.environment.annotator, expected)) errors.push("recording_annotator_artifact_mismatch");
    if (status.environment.connector?.source_revision !== source.components.connector.source_revision) errors.push("recording_connector_source_revision_mismatch");
    if (status.environment.annotator?.source_revision !== source.components.annotator.source_revision) errors.push("recording_annotator_source_revision_mismatch");
    if (status.environment.modset_status !== "exact_platform_modset") errors.push("recording_modset_not_exact");
  }

  return { ready: errors.length === 0, errors };
}

// Passive collection needs exact current identity, not Connector gameplay admission.
// Mutation verification below retains its existing execution availability gate.
export function evaluateCollectionLoadedEvidence(evidence) {
  return evaluateIdentityEvidence(evidence, false);
}

export function evaluateLoadedEvidence(evidence) {
  return evaluateIdentityEvidence(evidence, true);
}
