import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  fetchOfficialRuntimeRelease,
  parseReleaseProvenance,
  parseRuntimeSeal,
  readOfficialRuntimeRelease,
  RuntimeReleaseError
} from "../runtime-release.mjs";
import contract from "../runtime-seal-contract.json" with { type: "json" };
import golden from "./fixtures/runtime-seal-golden.json" with { type: "json" };

const version = "0.2.0-rc.23";
const tag = contract.tag_template.replace("{version}", version);
const downloadPrefix = `https://github.com/${contract.repository}/releases/download/${encodeURIComponent(tag)}/`;
const asset = (kind) => contract.asset_templates[kind].replace("{version}", version);
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const archive = Buffer.from("synthetic unified developer kit ZIP");
const evidence = Buffer.from("synthetic bounded runtime evidence record\n");

function makeSeal(overrides = {}) {
  return {
    schema: contract.seal_schema,
    release: {
      repository: contract.repository,
      tag,
      version,
      archive_asset: asset("archive"),
      archive_sha256: sha(archive)
    },
    host: {
      implementation_id: contract.implementation_id,
      connector_source_revision: "a".repeat(40),
      platform_source_revision: "b".repeat(40),
      compiled_source_digest_sha256: "c".repeat(64),
      artifact_sha256: "d".repeat(64),
      artifact_mvid: "12345678-1234-1234-1234-123456789abc",
      protocol: contract.protocol
    },
    environment: {
      platform: "darwin",
      architecture: "arm64",
      host_kind: contract.host_kind,
      game_version: "v0.111.0",
      game_commit: "41cef1ea",
      main_assembly_hash: 1010476334,
      main_assembly_sha256: "e".repeat(64),
      main_assembly_mvid: "abcdefab-cdef-abcd-efab-cdefabcdefab",
      modset_status: contract.modset_status,
      modset_fingerprint_scope: contract.modset_fingerprint_scope,
      modset_fingerprint: "f".repeat(64)
    },
    evidence: {
      scope: contract.evidence_scope,
      record_asset: asset("evidence"),
      record_sha256: sha(evidence)
    },
    ...overrides
  };
}

function makeFixture({ corruptChecksum = false, redirect = null, draft = false,
  assetSizeOverride = null } = {}) {
  const sealBytes = Buffer.from(`${JSON.stringify(makeSeal())}\n`);
  const releaseBytes = {
    [asset("archive")]: archive,
    [asset("seal")]: sealBytes,
    [asset("evidence")]: evidence
  };
  const checksums = Buffer.from(Object.entries(releaseBytes)
    .map(([name, bytes]) => `${sha(bytes)}  ${name}`)
    .join("\n") + "\n");
  if (corruptChecksum) checksums[0] = checksums[0] === 48 ? 49 : 48;
  releaseBytes[contract.asset_templates.checksums] = checksums;

  const assetIds = new Map();
  const assets = Object.entries(releaseBytes).map(([name, bytes], index) => {
    const id = index + 11;
    assetIds.set(id, { name, bytes });
    return {
      id,
      name,
      size: name === asset("archive") && assetSizeOverride != null
        ? assetSizeOverride
        : bytes.length,
      state: "uploaded",
      digest: `sha256:${sha(bytes)}`
    };
  });
  const releaseMetadata = {
    id: 7301,
    tag_name: tag,
    draft,
    prerelease: true,
    published_at: "2026-10-01T00:00:00Z",
    assets
  };
  const calls = [];
  const fetchImpl = async (input, options) => {
    const url = String(input);
    calls.push({ url, options });
    if (url === `https://api.github.com/repos/${contract.repository}/releases/tags/${encodeURIComponent(tag)}`) {
      if (redirect === "metadata") return new Response(null, { status: 302, headers: { location: "https://github.com/" } });
      return new Response(JSON.stringify(releaseMetadata), { status: 200 });
    }
    const assetMatch = new RegExp(
      `^https://api\\.github\\.com/repos/${contract.repository.replaceAll("/", "\\/")}/releases/assets/(\\d+)$`, "u"
    ).exec(url);
    if (assetMatch) {
      const found = assetIds.get(Number(assetMatch[1]));
      if (!found) return new Response(null, { status: 404 });
      if (redirect === "hostile") {
        return new Response(null, { status: 302, headers: { location: "https://evil.example/file" } });
      }
      if (redirect === "github-foreign-path") {
        return new Response(null, { status: 302, headers: { location: "https://github.com/other/repo/releases/download/x/y" } });
      }
      if (redirect === "explicit-default-port") {
        return new Response(null, { status: 302, headers: { location: `https://github.com:443/${contract.repository}/releases/download/${encodeURIComponent(tag)}/${found.name}` } });
      }
      if (redirect === "chain") {
        return new Response(null, { status: 302, headers: { location: `${downloadPrefix}${found.name}` } });
      }
      if (redirect === "cycle") {
        return new Response(null, { status: 302, headers: { location: `${downloadPrefix}${found.name}` } });
      }
      if (redirect === "limit") {
        return new Response(null, { status: 302, headers: { location: `${downloadPrefix}${found.name}` } });
      }
      return new Response(found.bytes, { status: 200 });
    }
    if (url.startsWith(downloadPrefix)) {
      const name = url.slice(downloadPrefix.length);
      const found = Object.values(Object.fromEntries(assetIds)).find((item) => item.name === name);
      if (!found) return new Response(null, { status: 404 });
      if (redirect === "chain") {
        return new Response(null, { status: 302, headers: { location: `https://release-assets.githubusercontent.com/synthetic/${name}?signature=fixture` } });
      }
      if (redirect === "cycle") {
        return new Response(null, { status: 302, headers: { location: `https://release-assets.githubusercontent.com/synthetic/${name}?signature=fixture` } });
      }
      if (redirect === "limit") {
        return new Response(null, { status: 302, headers: { location: `https://release-assets.githubusercontent.com/synthetic/${name}?hop=1` } });
      }
      return new Response(found.bytes, { status: 200 });
    }
    if (url.startsWith("https://release-assets.githubusercontent.com/")) {
      const name = new URL(url).pathname.split("/").at(-1);
      const found = Object.values(Object.fromEntries(assetIds)).find((item) => item.name === name);
      if (!found) return new Response(null, { status: 404 });
      if (redirect === "cycle") {
        return new Response(null, { status: 302, headers: { location: `${downloadPrefix}${name}` } });
      }
      if (redirect === "limit") {
        const hop = Number(new URL(url).searchParams.get("hop"));
        return new Response(null, { status: 302, headers: { location: `https://release-assets.githubusercontent.com/synthetic/${name}?hop=${hop + 1}` } });
      }
      return new Response(found.bytes, { status: 200 });
    }
    return new Response(null, { status: 404 });
  };
  return { fetchImpl, calls, releaseMetadata, releaseBytes };
}

function assertCode(code, action) {
  assert.throws(action, (error) => error instanceof RuntimeReleaseError && error.code === code);
}

test("strict seal parser accepts the unified owner and exact fixed contract", () => {
  assert.equal(golden.synthetic_only, true);
  assert.equal(sha(Buffer.from(golden.seal_raw)), JSON.parse(golden.provenance_raw).seal_sha256);
  assert.equal(parseRuntimeSeal(golden.seal_raw).release.version, version);
  const parsed = parseRuntimeSeal(JSON.stringify(makeSeal()));
  assert.equal(parsed.host.implementation_id, "STS2_PLATFORM");
  assert.equal(parsed.environment.modset_fingerprint_scope, contract.modset_fingerprint_scope);
  assert.equal(Object.isFrozen(parsed.environment), true);
});

test("seal parser rejects duplicate, missing and unknown keys", () => {
  const seal = makeSeal();
  const duplicate = JSON.stringify(seal).replace(
    '"schema":"sts2.platform/runtime-seal-1"',
    '"schema":"sts2.platform/runtime-seal-1","schema":"sts2.platform/runtime-seal-1"'
  );
  assertCode("runtime_seal_duplicate_key", () => parseRuntimeSeal(duplicate));
  const escapedDuplicate = golden.seal_raw.replace(
    '"schema":"sts2.platform/runtime-seal-1"',
    '"schema":"sts2.platform/runtime-seal-1","sche\\u006da":"sts2.platform/runtime-seal-1"'
  );
  assertCode("runtime_seal_duplicate_key", () => parseRuntimeSeal(escapedDuplicate));
  const missing = makeSeal();
  delete missing.host.protocol;
  assertCode("runtime_seal_host_fields_invalid", () => parseRuntimeSeal(JSON.stringify(missing)));
  const unknown = makeSeal();
  unknown.host.launchable = true;
  assertCode("runtime_seal_host_fields_invalid", () => parseRuntimeSeal(JSON.stringify(unknown)));
  const goldenDuplicateProtocol = golden.seal_raw.replace(
    '"protocol":"1.0.0"', '"protocol":"1.0.0","protocol":"1.0.0"'
  );
  assertCode("runtime_seal_duplicate_key", () => parseRuntimeSeal(goldenDuplicateProtocol));
});

test("seal parser rejects floats, booleans, loose digests and foreign owner/scope", () => {
  const float = JSON.stringify(makeSeal()).replace(
    '"main_assembly_hash":1010476334', '"main_assembly_hash":1010476334.0'
  );
  assertCode("runtime_seal_non_integer_number", () => parseRuntimeSeal(float));
  const booleanInteger = makeSeal();
  booleanInteger.environment.main_assembly_hash = true;
  assertCode("runtime_seal_main_assembly_hash_invalid", () => parseRuntimeSeal(JSON.stringify(booleanInteger)));
  const badDigest = makeSeal();
  badDigest.host.artifact_sha256 = "A".repeat(64);
  assertCode("runtime_seal_artifact_sha256_invalid", () => parseRuntimeSeal(JSON.stringify(badDigest)));
  const wrongOwner = makeSeal();
  wrongOwner.host.implementation_id = "STS2_MCP";
  assertCode("runtime_seal_owner_mismatch", () => parseRuntimeSeal(JSON.stringify(wrongOwner)));
  const wrongScope = makeSeal();
  wrongScope.environment.modset_fingerprint_scope = "caller-derived";
  assertCode("runtime_seal_modset_scope_mismatch", () => parseRuntimeSeal(JSON.stringify(wrongScope)));
  const goldenMutations = [
    [golden.seal_raw.replace('"main_assembly_hash":1010476334', '"main_assembly_hash":1010476334.0'), "runtime_seal_non_integer_number"],
    [golden.seal_raw.replace('"main_assembly_hash":1010476334', '"main_assembly_hash":true'), "runtime_seal_main_assembly_hash_invalid"],
    [golden.seal_raw.replace('"host_kind":"live_ui"', '"host_kind":"headless"'), "runtime_seal_host_kind_mismatch"],
    [golden.seal_raw.replace('"scope":"bootstrap_exact_bounded_lifecycle"', '"scope":"ordinary_accepted"'), "runtime_seal_evidence_scope_mismatch"],
    [golden.seal_raw.replace('"repository":', '"arbitrary_origin":"https://example.test","repository":'), "runtime_seal_release_fields_invalid"],
    [golden.seal_raw.replace('"artifact_mvid":"11111111-2222-3333-4444-555555555555"', '"artifact_mvid":"not-a-guid"'), "runtime_seal_artifact_mvid_invalid"]
  ];
  for (const [raw, code] of goldenMutations) assertCode(code, () => parseRuntimeSeal(raw));
  assertCode("runtime_seal_invalid_json", () => parseRuntimeSeal("{"));
  assertCode("runtime_seal_too_large", () => parseRuntimeSeal(" ".repeat(65537)));
});

test("provenance parser requires exact fields and official release identity", () => {
  assert.equal(parseReleaseProvenance(golden.provenance_raw).release_id, 1234);
  const provenance = {
    schema: contract.provenance_schema,
    repository: contract.repository,
    tag,
    version,
    release_id: 7301,
    archive_asset: asset("archive"),
    archive_sha256: "a".repeat(64),
    seal_asset: asset("seal"),
    seal_sha256: "b".repeat(64),
    evidence_asset: asset("evidence"),
    evidence_sha256: "c".repeat(64)
  };
  assert.equal(parseReleaseProvenance(JSON.stringify(provenance)).release_id, 7301);
  assertCode("release_provenance_tag_mismatch", () => parseReleaseProvenance(
    JSON.stringify({ ...provenance, tag: "candidate/other" })
  ));
  assertCode("release_provenance_non_integer_number", () => parseReleaseProvenance(
    JSON.stringify(provenance).replace('"release_id":7301', '"release_id":7301e0')
  ));
  const goldenFloatId = golden.provenance_raw.replace('"release_id":1234', '"release_id":1234.0');
  assertCode("release_provenance_non_integer_number", () => parseReleaseProvenance(goldenFloatId));
  const duplicateReceipt = golden.provenance_raw.replace(
    '"schema":"sts2.platform/official-release-provenance-1"',
    '"schema":"sts2.platform/official-release-provenance-1","schema":"sts2.platform/official-release-provenance-1"'
  );
  assertCode("release_provenance_duplicate_key", () => parseReleaseProvenance(duplicateReceipt));
  assertCode("release_provenance_invalid_json", () => parseReleaseProvenance("{"));
  assertCode("version_invalid", () => parseReleaseProvenance(
    golden.provenance_raw.replaceAll(version, "0.2.0-beta.23")
  ));
  assertCode("version_invalid", () => parseReleaseProvenance(
    golden.provenance_raw.replaceAll(version, "1".repeat(65))
  ));
  const maximumStable = `${"1".repeat(21)}.${"2".repeat(21)}.${"3".repeat(20)}`;
  assert.equal(maximumStable.length, 64);
  const maximumStableReceipt = JSON.stringify({
    ...provenance,
    version: maximumStable,
    tag: contract.tag_template.replace("{version}", maximumStable),
    archive_asset: contract.asset_templates.archive.replace("{version}", maximumStable),
    seal_asset: contract.asset_templates.seal.replace("{version}", maximumStable),
    evidence_asset: contract.asset_templates.evidence.replace("{version}", maximumStable)
  });
  assert.equal(parseReleaseProvenance(maximumStableReceipt).version, maximumStable);
});

test("official fetch verifies same-release bytes and mints an opaque immutable receipt", async () => {
  const fixture = makeFixture();
  const bundle = await fetchOfficialRuntimeRelease(version, { fetchImpl: fixture.fetchImpl });
  assert.equal(bundle.status, "official_release_assets_verified");
  const release = readOfficialRuntimeRelease(bundle);
  assert.equal(release.seal.host.implementation_id, "STS2_PLATFORM");
  assert.equal(release.provenance.release_id, 7301);
  assert.equal(sha(release.archive), sha(archive));
  assert.equal(sha(release.evidence), sha(evidence));
  release.archive.fill(0);
  assert.equal(sha(readOfficialRuntimeRelease(bundle).archive), sha(archive));
  assertCode("official_release_bundle_required", () => readOfficialRuntimeRelease({
    ...bundle,
    status: "official_release_assets_verified"
  }));
  assert.ok(fixture.calls.every(({ options }) => !Object.hasOwn(options.headers, "authorization")));
  assert.equal(fixture.calls[0].options.redirect, "error");
});

test("official fetch permits prereleases but rejects drafts, wrong tags, missing assets and bad checksums", async () => {
  const prerelease = makeFixture();
  assert.equal((await fetchOfficialRuntimeRelease(version, { fetchImpl: prerelease.fetchImpl })).status,
    "official_release_assets_verified");
  const draft = makeFixture({ draft: true });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: draft.fetchImpl }),
    (error) => error.code === "release_metadata_invalid");
  const wrongTag = makeFixture();
  wrongTag.releaseMetadata.tag_name = "other/tag";
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: wrongTag.fetchImpl }),
    (error) => error.code === "release_metadata_invalid");
  const missing = makeFixture();
  missing.releaseMetadata.assets = missing.releaseMetadata.assets.filter((item) => item.name !== asset("seal"));
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: missing.fetchImpl }),
    (error) => error.code === "release_asset_missing_or_duplicate");
  const corrupt = makeFixture({ corruptChecksum: true });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: corrupt.fetchImpl }),
    (error) => error.code === "release_checksums_rows_invalid" || error.code === "release_checksums_mismatch");
});

test("GitHub tag redirect path is exact and uses canonical escaped slash", async () => {
  const allowed = makeFixture({ redirect: "chain" });
  assert.equal((await fetchOfficialRuntimeRelease(version, { fetchImpl: allowed.fetchImpl })).status,
    "official_release_assets_verified");
  for (const badPath of [
    `https://github.com/${contract.repository}/releases/download/${tag}/${asset("archive")}`,
    `https://github.com/${contract.repository}/releases/download/${encodeURIComponent(tag).replaceAll("%2F", "%2f")}/${asset("archive")}`,
    `${downloadPrefix}${asset("archive")}/extra`
  ]) {
    const fixture = makeFixture();
    const originalFetch = fixture.fetchImpl;
    fixture.fetchImpl = async (input, options) => {
      if (String(input).endsWith("/releases/assets/11")) {
        return new Response(null, { status: 302, headers: { location: badPath } });
      }
      return originalFetch(input, options);
    };
    await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: fixture.fetchImpl }),
      (error) => error.code === "release_redirect_forbidden");
  }
});

test("request timeout covers a stalled response body", async () => {
  const fixture = makeFixture();
  const originalFetch = fixture.fetchImpl;
  fixture.fetchImpl = async (input, options) => {
    if (String(input).endsWith("/releases/assets/11")) {
      return {
        status: 200,
        ok: true,
        headers: new Headers(),
        body: { getReader: () => ({
          read: () => new Promise(() => {}),
          cancel: async () => {}
        }) }
      };
    }
    return originalFetch(input, options);
  };
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, delay, ...args) => originalSetTimeout(callback, Math.min(delay, 5), ...args);
  try {
    await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: fixture.fetchImpl }),
      (error) => error.code === "release_asset_timeout");
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});

test("official fetch rejects metadata redirects, hostile redirects, wrong path and explicit port", async () => {
  const metadataRedirect = makeFixture({ redirect: "metadata" });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: metadataRedirect.fetchImpl }),
    (error) => error.code === "release_metadata_http_status");
  for (const redirect of ["hostile", "github-foreign-path", "explicit-default-port"]) {
    const fixture = makeFixture({ redirect });
    await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: fixture.fetchImpl }),
      (error) => error.code === "release_redirect_forbidden");
  }
});

test("manual asset redirects allow bounded official GitHub/CDN hops and reject cycles", async () => {
  const chain = makeFixture({ redirect: "chain" });
  const bundle = await fetchOfficialRuntimeRelease(version, { fetchImpl: chain.fetchImpl });
  assert.equal(readOfficialRuntimeRelease(bundle).version, version);
  assert.ok(chain.calls.some((call) => call.url.startsWith("https://release-assets.githubusercontent.com/")));
  const cycle = makeFixture({ redirect: "cycle" });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: cycle.fetchImpl }),
    (error) => error.code === "release_redirect_cycle");
  const limit = makeFixture({ redirect: "limit" });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: limit.fetchImpl }),
    (error) => error.code === "release_redirect_limit");
});

test("asset bounds, API digests, versions and invalid encodings fail closed", async () => {
  const oversized = makeFixture({ assetSizeOverride: contract.limits.archive_bytes + 1 });
  await assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: oversized.fetchImpl }),
    (error) => error.code === "release_asset_size_invalid");
  await assert.rejects(fetchOfficialRuntimeRelease("../v0", { fetchImpl: async () => assert.fail("must not fetch") }),
    (error) => error.code === "version_invalid");
  assertCode("runtime_seal_invalid_utf8", () => parseRuntimeSeal(Buffer.from([0xff, 0xfe])));
});

test("oversize body rejects even if stream cancellation never settles", async () => {
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(contract.limits.metadata_bytes + 1)); },
    cancel() { return new Promise(() => {}); }
  });
  let timer;
  try {
    await Promise.race([
      assert.rejects(fetchOfficialRuntimeRelease(version, { fetchImpl: async () => new Response(stream) }),
        (error) => error.code === "release_metadata_too_large"),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("cancellation escaped bound")), 200); })
    ]);
  } finally { clearTimeout(timer); }
});

test("completed and redirected requests abort unconsumed bodies at cleanup", async () => {
  const fixture = makeFixture({ redirect: "chain" });
  await fetchOfficialRuntimeRelease(version, { fetchImpl: fixture.fetchImpl });
  assert.ok(fixture.calls.length > 5);
  assert.ok(fixture.calls.every(({ options }) => options.signal.aborted));
});
