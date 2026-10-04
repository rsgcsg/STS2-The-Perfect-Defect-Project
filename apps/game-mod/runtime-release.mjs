import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const CONTRACT = JSON.parse(readFileSync(
  fileURLToPath(new URL("./runtime-seal-contract.json", import.meta.url)),
  "utf8"
));
const REPO = CONTRACT.repository;
const API_ROOT = `https://api.github.com/repos/${REPO}`;
const RELEASE_ROOT = `https://github.com/${REPO}`;
const DIGEST_RE = /^[0-9a-f]{64}$/u;
const REVISION_RE = /^[0-9a-f]{40}$/u;
const MVID_RE = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/u;
const VERSION_RE = /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.(?:0|[1-9][0-9]*))?$/u;
const REDIRECT_CODES = new Set([301, 302, 303, 307, 308]);
const bundleState = new WeakMap();

export class RuntimeReleaseError extends Error {
  constructor(code) {
    super(code);
    this.name = "RuntimeReleaseError";
    this.code = code;
  }
}

function reject(code) {
  throw new RuntimeReleaseError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function byteInput(raw, limit, label) {
  let bytes;
  if (typeof raw === "string") bytes = Buffer.from(raw, "utf8");
  else if (Buffer.isBuffer(raw) || raw instanceof Uint8Array) bytes = Buffer.from(raw);
  else reject(`${label}_bytes_required`);
  if (bytes.length > limit) reject(`${label}_too_large`);
  return bytes;
}

// JSON.parse accepts duplicate object keys and silently keeps the last value.
// This bounded parser rejects duplicates before any identity fields are used.
function parseJsonUnique(raw, limit, label, maximumDepth = CONTRACT.limits.json_depth) {
  const bytes = byteInput(raw, limit, label);
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    reject(`${label}_invalid_utf8`);
  }

  let cursor = 0;
  const whitespace = () => {
    while (cursor < text.length && /[\u0009\u000a\u000d\u0020]/u.test(text[cursor])) cursor += 1;
  };
  const parseString = () => {
    const start = cursor;
    if (text[cursor] !== '"') reject(`${label}_invalid_json`);
    cursor += 1;
    while (cursor < text.length) {
      const character = text[cursor];
      if (character === '"') {
        cursor += 1;
        try {
          return JSON.parse(text.slice(start, cursor));
        } catch {
          reject(`${label}_invalid_json`);
        }
      }
      if (character === "\\") {
        cursor += 2;
      } else {
        if (character.charCodeAt(0) < 0x20) reject(`${label}_invalid_json`);
        cursor += 1;
      }
    }
    reject(`${label}_invalid_json`);
  };
  const parseValue = (depth) => {
    whitespace();
    if (depth > maximumDepth) reject(`${label}_json_too_deep`);
    const character = text[cursor];
    if (character === '"') return parseString();
    if (character === "{") {
      cursor += 1;
      whitespace();
      const result = Object.create(null);
      const keys = new Set();
      if (text[cursor] === "}") {
        cursor += 1;
        return result;
      }
      while (cursor < text.length) {
        whitespace();
        const key = parseString();
        if (keys.has(key)) reject(`${label}_duplicate_key`);
        keys.add(key);
        whitespace();
        if (text[cursor] !== ":") reject(`${label}_invalid_json`);
        cursor += 1;
        result[key] = parseValue(depth + 1);
        whitespace();
        if (text[cursor] === "}") {
          cursor += 1;
          return result;
        }
        if (text[cursor] !== ",") reject(`${label}_invalid_json`);
        cursor += 1;
      }
      reject(`${label}_invalid_json`);
    }
    if (character === "[") {
      cursor += 1;
      whitespace();
      const result = [];
      if (text[cursor] === "]") {
        cursor += 1;
        return result;
      }
      while (cursor < text.length) {
        result.push(parseValue(depth + 1));
        whitespace();
        if (text[cursor] === "]") {
          cursor += 1;
          return result;
        }
        if (text[cursor] !== ",") reject(`${label}_invalid_json`);
        cursor += 1;
      }
      reject(`${label}_invalid_json`);
    }
    for (const [token, value] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(token, cursor)) {
        cursor += token.length;
        return value;
      }
    }
    const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u
      .exec(text.slice(cursor));
    if (number) {
      if (/[.eE]/u.test(number[0])) reject(`${label}_non_integer_number`);
      cursor += number[0].length;
      const value = Number(number[0]);
      if (!Number.isFinite(value)) reject(`${label}_invalid_json`);
      return value;
    }
    reject(`${label}_invalid_json`);
  };

  const result = parseValue(0);
  whitespace();
  if (cursor !== text.length) reject(`${label}_invalid_json`);
  return result;
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function exactFields(value, fields, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) reject(`${label}_object_required`);
  const actual = Object.keys(value);
  if (actual.length !== fields.length
      || fields.some((field) => !Object.hasOwn(value, field))) {
    reject(`${label}_fields_invalid`);
  }
}

function text(value, label, maximum = 256, pattern = null) {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum
      || (pattern && !pattern.test(value))) {
    reject(`${label}_invalid`);
  }
  return value;
}

function digest(value, label) {
  return text(value, label, 64, DIGEST_RE);
}

function version(value) {
  return text(value, "version", 64, VERSION_RE);
}

function expectedTag(value) {
  return CONTRACT.tag_template.replace("{version}", version(value));
}

function expectedAsset(kind, value) {
  return CONTRACT.asset_templates[kind].replace("{version}", version(value));
}

function safeInteger(value, label, minimum, maximum = Number.MAX_SAFE_INTEGER) {
  if (typeof value !== "number" || !Number.isSafeInteger(value)
      || value < minimum || value > maximum) {
    reject(`${label}_invalid`);
  }
  return value;
}

function validateReleaseTuple(release, label) {
  if (release.repository !== REPO) reject(`${label}_repository_mismatch`);
  const v = version(release.version);
  if (release.tag !== expectedTag(v)) reject(`${label}_tag_mismatch`);
  if (release.archive_asset !== expectedAsset("archive", v)) reject(`${label}_archive_asset_mismatch`);
  digest(release.archive_sha256, `${label}_archive_sha256`);
  return v;
}

export function parseRuntimeSeal(raw) {
  const seal = parseJsonUnique(raw, CONTRACT.limits.seal_bytes, "runtime_seal");
  exactFields(seal, CONTRACT.seal_fields.root, "runtime_seal");
  if (seal.schema !== CONTRACT.seal_schema) reject("runtime_seal_schema_unsupported");

  exactFields(seal.release, CONTRACT.seal_fields.release, "runtime_seal_release");
  const releaseVersion = validateReleaseTuple(seal.release, "runtime_seal_release");

  exactFields(seal.host, CONTRACT.seal_fields.host, "runtime_seal_host");
  if (seal.host.implementation_id !== CONTRACT.implementation_id) reject("runtime_seal_owner_mismatch");
  text(seal.host.connector_source_revision, "runtime_seal_connector_revision", 40, REVISION_RE);
  text(seal.host.platform_source_revision, "runtime_seal_platform_revision", 40, REVISION_RE);
  digest(seal.host.compiled_source_digest_sha256, "runtime_seal_compiled_source_digest");
  digest(seal.host.artifact_sha256, "runtime_seal_artifact_sha256");
  text(seal.host.artifact_mvid, "runtime_seal_artifact_mvid", 36, MVID_RE);
  if (seal.host.protocol !== CONTRACT.protocol) reject("runtime_seal_protocol_mismatch");

  exactFields(seal.environment, CONTRACT.seal_fields.environment, "runtime_seal_environment");
  text(seal.environment.platform, "runtime_seal_platform", 16, /^(?:darwin|win32|linux)$/u);
  text(seal.environment.architecture, "runtime_seal_architecture", 16, /^(?:arm64|x64)$/u);
  if (seal.environment.host_kind !== CONTRACT.host_kind) reject("runtime_seal_host_kind_mismatch");
  text(seal.environment.game_version, "runtime_seal_game_version", 64, /^v[0-9]+\.[0-9]+\.[0-9]+$/u);
  text(seal.environment.game_commit, "runtime_seal_game_commit", 40, /^[0-9a-f]{8,40}$/u);
  safeInteger(seal.environment.main_assembly_hash, "runtime_seal_main_assembly_hash", -2147483648, 2147483647);
  digest(seal.environment.main_assembly_sha256, "runtime_seal_main_assembly_sha256");
  text(seal.environment.main_assembly_mvid, "runtime_seal_main_assembly_mvid", 36, MVID_RE);
  if (seal.environment.modset_status !== CONTRACT.modset_status) reject("runtime_seal_modset_status_mismatch");
  if (seal.environment.modset_fingerprint_scope !== CONTRACT.modset_fingerprint_scope) {
    reject("runtime_seal_modset_scope_mismatch");
  }
  digest(seal.environment.modset_fingerprint, "runtime_seal_modset_fingerprint");

  exactFields(seal.evidence, CONTRACT.seal_fields.evidence, "runtime_seal_evidence");
  if (seal.evidence.scope !== CONTRACT.evidence_scope) reject("runtime_seal_evidence_scope_mismatch");
  if (seal.evidence.record_asset !== expectedAsset("evidence", releaseVersion)) {
    reject("runtime_seal_evidence_asset_mismatch");
  }
  digest(seal.evidence.record_sha256, "runtime_seal_evidence_sha256");
  return deepFreeze(seal);
}

export function parseReleaseProvenance(raw) {
  // This validates structure and identities only. It does not assert that a
  // receipt came from GitHub; only a bundle returned by the fixed fetch below
  // carries that in-process provenance marker.
  const provenance = parseJsonUnique(
    raw, CONTRACT.limits.provenance_bytes, "release_provenance"
  );
  exactFields(provenance, CONTRACT.provenance_fields, "release_provenance");
  if (provenance.schema !== CONTRACT.provenance_schema) reject("release_provenance_schema_unsupported");
  const v = version(provenance.version);
  if (provenance.repository !== REPO) reject("release_provenance_repository_mismatch");
  if (provenance.tag !== expectedTag(v)) reject("release_provenance_tag_mismatch");
  safeInteger(provenance.release_id, "release_provenance_release_id", 1);
  if (provenance.archive_asset !== expectedAsset("archive", v)
      || provenance.seal_asset !== expectedAsset("seal", v)
      || provenance.evidence_asset !== expectedAsset("evidence", v)) {
    reject("release_provenance_asset_mismatch");
  }
  digest(provenance.archive_sha256, "release_provenance_archive_sha256");
  digest(provenance.seal_sha256, "release_provenance_seal_sha256");
  digest(provenance.evidence_sha256, "release_provenance_evidence_sha256");
  return deepFreeze(provenance);
}

function apiAssetUrl(id) {
  return `${API_ROOT}/releases/assets/${safeInteger(id, "release_asset_id", 1)}`;
}

function releaseDownloadUrl(tag, name) {
  return `${RELEASE_ROOT}/releases/download/${encodeURIComponent(tag)}/${name}`;
}

function validateRedirect(location, expectedDownloadUrl) {
  let target;
  try {
    target = new URL(location);
  } catch {
    reject("release_redirect_invalid");
  }
  const authority = /^https:\/\/([^/?#]+)/iu.exec(location)?.[1];
  if (target.protocol !== "https:" || target.username || target.password || target.port
      || !authority || authority.toLowerCase() !== target.hostname.toLowerCase()
      || target.hash || !CONTRACT.download_hosts.includes(target.hostname.toLowerCase())) {
    reject("release_redirect_forbidden");
  }
  if (target.hostname.toLowerCase() === "api.github.com") reject("release_redirect_forbidden");
  if (target.hostname.toLowerCase() === "github.com") {
    const expectedPath = new URL(expectedDownloadUrl).pathname;
    // Compare the raw encoded path as well as URL's parsed path. This accepts
    // the single canonical escaped slash in game-mod/v... and rejects URL
    // normalization, alternate escaping, query strings, and extra segments.
    const rawPath = /^https:\/\/[^/?#]+([^?#]*)/iu.exec(location)?.[1];
    if (target.pathname !== expectedPath || rawPath !== expectedPath || target.search) {
      reject("release_redirect_forbidden");
    }
  }
  return target.href;
}

function requestTimeout() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONTRACT.limits.request_timeout_ms);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function awaitRequest(promise, signal, label, cancel = null) {
  if (signal.aborted) {
    try { cancel?.(); } catch { /* best-effort stream cancellation */ }
    reject(`${label}_timeout`);
  }
  let onAbort;
  const aborted = new Promise((_, rejectPromise) => {
    onAbort = () => {
      try { cancel?.(); } catch { /* best-effort stream cancellation */ }
      rejectPromise(new RuntimeReleaseError(`${label}_timeout`));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([promise, aborted]);
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}

async function readBody(response, maximumBytes, label, signal) {
  const declared = response.headers?.get?.("content-length");
  if (declared != null && (!/^\d+$/u.test(declared) || Number(declared) > maximumBytes)) {
    reject(`${label}_too_large`);
  }
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await awaitRequest(
        reader.read(), signal, label, () => { void reader.cancel().catch(() => {}); }
      );
      if (done) break;
      length += value.byteLength;
      if (length > maximumBytes) {
        await reader.cancel().catch(() => {});
        reject(`${label}_too_large`);
      }
      chunks.push(Buffer.from(value));
    }
  } catch (error) {
    if (error instanceof RuntimeReleaseError) throw error;
    reject(`${label}_read_failed`);
  }
  return Buffer.concat(chunks, length);
}

function fetchOptions(redirect) {
  return {
    redirect,
    headers: {
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28"
    }
  };
}

async function request(fetchImpl, url, options, label, maximumBytes) {
  const timeout = requestTimeout();
  try {
    let response;
    try {
      response = await awaitRequest(fetchImpl(url, { ...options, signal: timeout.signal }), timeout.signal, label);
    } catch (error) {
      if (error instanceof RuntimeReleaseError) throw error;
      reject(`${label}_request_failed`);
    }
    if (!response || response.status !== 200 || !response.ok) reject(`${label}_http_status`);
    return await readBody(response, maximumBytes, label, timeout.signal);
  } finally {
    timeout.clear();
  }
}

async function getReleaseMetadata(fetchImpl, versionValue) {
  const tag = expectedTag(versionValue);
  const url = `${API_ROOT}/releases/tags/${encodeURIComponent(tag)}`;
  const raw = await request(
    fetchImpl, url, fetchOptions("error"), "release_metadata", CONTRACT.limits.metadata_bytes
  );
  const metadata = parseJsonUnique(raw, CONTRACT.limits.metadata_bytes, "release_metadata");
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)
      || metadata.tag_name !== tag || metadata.draft !== false
      || typeof metadata.prerelease !== "boolean"
      || typeof metadata.published_at !== "string" || !Number.isFinite(Date.parse(metadata.published_at))) {
    reject("release_metadata_invalid");
  }
  const releaseId = safeInteger(metadata.id, "release_id", 1);
  if (!Array.isArray(metadata.assets)) reject("release_assets_invalid");
  return { metadata, tag, releaseId };
}

function assetMetadata(release, name, maximumBytes) {
  const matches = release.metadata.assets.filter((asset) => asset?.name === name);
  if (matches.length !== 1) reject("release_asset_missing_or_duplicate");
  const asset = matches[0];
  const size = safeInteger(asset.size, "release_asset_size", 1, maximumBytes);
  if (asset.state !== "uploaded") reject("release_asset_unpublished");
  const digestMatch = /^sha256:([0-9a-f]{64})$/u.exec(asset.digest ?? "");
  if (!digestMatch) reject("release_asset_digest_missing");
  safeInteger(asset.id, "release_asset_id", 1);
  return { id: asset.id, size, digest: digestMatch[1] };
}

async function downloadAsset(fetchImpl, asset, name, versionValue, maximumBytes) {
  const expectedUrl = releaseDownloadUrl(expectedTag(versionValue), name);
  let url = apiAssetUrl(asset.id);
  const visited = new Set();
  let redirects = 0;
  while (true) {
    if (visited.has(url)) reject("release_redirect_cycle");
    visited.add(url);
    const timeout = requestTimeout();
    try {
      let response;
      try {
        response = await awaitRequest(fetchImpl(url, {
          ...fetchOptions("manual"),
          signal: timeout.signal,
          headers: { accept: "application/octet-stream" }
        }), timeout.signal, "release_asset");
      } catch (error) {
        if (error instanceof RuntimeReleaseError) throw error;
        reject("release_asset_request_failed");
      }
      if (!response || typeof response.status !== "number") reject("release_asset_http_status");
      if (REDIRECT_CODES.has(response.status)) {
        if (redirects >= CONTRACT.limits.redirects) reject("release_redirect_limit");
        const location = response.headers?.get?.("location");
        if (!location) reject("release_redirect_invalid");
        url = validateRedirect(location, expectedUrl);
        redirects += 1;
        continue;
      }
      if (response.status !== 200 || !response.ok) reject("release_asset_http_status");
      const bytes = await readBody(response, maximumBytes, "release_asset", timeout.signal);
      if (bytes.length !== asset.size || sha256(bytes) !== asset.digest) {
        reject("release_asset_digest_mismatch");
      }
      return bytes;
    } finally {
      timeout.clear();
    }
  }
}

function parseChecksums(raw, versionValue, expectedDigests) {
  const bytes = byteInput(raw, CONTRACT.limits.checksums_bytes, "release_checksums");
  let textValue;
  try {
    textValue = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    reject("release_checksums_invalid_utf8");
  }
  const rows = textValue.replace(/\r\n/gu, "\n").split("\n");
  if (rows.at(-1) === "") rows.pop();
  const expectedNames = [
    expectedAsset("archive", versionValue),
    expectedAsset("seal", versionValue),
    expectedAsset("evidence", versionValue)
  ];
  if (rows.length !== expectedNames.length) reject("release_checksums_rows_invalid");
  const actual = new Map();
  for (const row of rows) {
    const match = /^([0-9a-f]{64})  ([A-Za-z0-9._-]+)$/u.exec(row);
    if (!match || actual.has(match[2])) reject("release_checksums_rows_invalid");
    actual.set(match[2], match[1]);
  }
  if (expectedNames.some((name) => actual.get(name) !== expectedDigests[name])) {
    reject("release_checksums_mismatch");
  }
  return true;
}

function makeProvenance(release, versionValue, digests) {
  return parseReleaseProvenance(Buffer.from(`${JSON.stringify({
    schema: CONTRACT.provenance_schema,
    repository: REPO,
    tag: release.tag,
    version: versionValue,
    release_id: release.releaseId,
    archive_asset: expectedAsset("archive", versionValue),
    archive_sha256: digests.archive,
    seal_asset: expectedAsset("seal", versionValue),
    seal_sha256: digests.seal,
    evidence_asset: expectedAsset("evidence", versionValue),
    evidence_sha256: digests.evidence
  }, null, 2)}\n`));
}

function verifyState(state) {
  const { version: versionValue, releaseId, bytes, digests } = state;
  const actual = {
    archive: sha256(bytes.archive),
    seal: sha256(bytes.seal),
    evidence: sha256(bytes.evidence),
    checksums: sha256(bytes.checksums),
    provenance: sha256(bytes.provenance)
  };
  if (actual.archive !== digests.archive || actual.seal !== digests.seal
      || actual.evidence !== digests.evidence || actual.checksums !== digests.checksums
      || actual.provenance !== digests.provenance) {
    reject("verified_release_bytes_changed");
  }
  const seal = parseRuntimeSeal(bytes.seal);
  const provenance = parseReleaseProvenance(bytes.provenance);
  const archiveName = expectedAsset("archive", versionValue);
  const sealName = expectedAsset("seal", versionValue);
  const evidenceName = expectedAsset("evidence", versionValue);
  if (seal.release.version !== versionValue || seal.release.tag !== state.tag
      || seal.release.archive_asset !== archiveName || seal.release.archive_sha256 !== actual.archive
      || seal.evidence.record_asset !== evidenceName || seal.evidence.record_sha256 !== actual.evidence) {
    reject("verified_release_seal_binding_mismatch");
  }
  if (provenance.repository !== REPO || provenance.tag !== state.tag
      || provenance.version !== versionValue || provenance.release_id !== releaseId
      || provenance.archive_asset !== archiveName || provenance.archive_sha256 !== actual.archive
      || provenance.seal_asset !== sealName || provenance.seal_sha256 !== actual.seal
      || provenance.evidence_asset !== evidenceName || provenance.evidence_sha256 !== actual.evidence) {
    reject("verified_release_provenance_binding_mismatch");
  }
  parseChecksums(bytes.checksums, versionValue, {
    [archiveName]: actual.archive,
    [sealName]: actual.seal,
    [evidenceName]: actual.evidence
  });
  return { seal, provenance };
}

export async function fetchOfficialRuntimeRelease(versionInput, { fetchImpl = fetch } = {}) {
  const v = version(versionInput);
  if (typeof fetchImpl !== "function") reject("fetch_implementation_required");
  const release = await getReleaseMetadata(fetchImpl, v);
  const names = {
    archive: expectedAsset("archive", v),
    seal: expectedAsset("seal", v),
    evidence: expectedAsset("evidence", v),
    checksums: CONTRACT.asset_templates.checksums
  };
  const metadata = {
    archive: assetMetadata(release, names.archive, CONTRACT.limits.archive_bytes),
    seal: assetMetadata(release, names.seal, CONTRACT.limits.seal_bytes),
    evidence: assetMetadata(release, names.evidence, CONTRACT.limits.evidence_bytes),
    checksums: assetMetadata(release, names.checksums, CONTRACT.limits.checksums_bytes)
  };
  if (new Set(Object.values(metadata).map((entry) => entry.id)).size !== Object.keys(metadata).length) {
    reject("release_asset_id_duplicate");
  }
  const bytes = Object.create(null);
  for (const kind of ["archive", "seal", "evidence", "checksums"]) {
    bytes[kind] = await downloadAsset(
      fetchImpl, metadata[kind], names[kind], v,
      CONTRACT.limits[`${kind}_bytes`]
    );
  }
  const seal = parseRuntimeSeal(bytes.seal);
  if (seal.release.version !== v || seal.release.tag !== release.tag
      || seal.release.repository !== REPO || seal.release.archive_asset !== names.archive
      || seal.evidence.record_asset !== names.evidence) {
    reject("release_seal_metadata_mismatch");
  }
  const digests = {
    archive: sha256(bytes.archive),
    seal: sha256(bytes.seal),
    evidence: sha256(bytes.evidence),
    checksums: sha256(bytes.checksums)
  };
  parseChecksums(bytes.checksums, v, {
    [names.archive]: digests.archive,
    [names.seal]: digests.seal,
    [names.evidence]: digests.evidence
  });
  if (seal.release.archive_sha256 !== digests.archive
      || seal.evidence.record_sha256 !== digests.evidence) {
    reject("release_seal_digest_mismatch");
  }
  const provenance = makeProvenance(release, v, digests);
  const provenanceBytes = Buffer.from(`${JSON.stringify(provenance, null, 2)}\n`);
  const bundle = Object.freeze({
    status: "official_release_assets_verified",
    repository: REPO,
    tag: release.tag,
    version: v,
    release_id: release.releaseId
  });
  bundleState.set(bundle, {
    version: v,
    tag: release.tag,
    releaseId: release.releaseId,
    bytes: Object.freeze({ ...bytes, provenance: provenanceBytes }),
    digests: Object.freeze({ ...digests, provenance: sha256(provenanceBytes) })
  });
  verifyState(bundleState.get(bundle));
  return bundle;
}

export function readOfficialRuntimeRelease(bundle) {
  const state = bundleState.get(bundle);
  if (!state) reject("official_release_bundle_required");
  const { seal, provenance } = verifyState(state);
  return Object.freeze({
    status: "official_release_assets_verified",
    repository: REPO,
    tag: state.tag,
    version: state.version,
    release_id: state.releaseId,
    digests: Object.freeze({ ...state.digests }),
    seal,
    provenance,
    archive: Buffer.from(state.bytes.archive),
    seal_bytes: Buffer.from(state.bytes.seal),
    evidence: Buffer.from(state.bytes.evidence),
    checksums: Buffer.from(state.bytes.checksums),
    provenance_bytes: Buffer.from(state.bytes.provenance)
  });
}
