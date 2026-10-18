import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { extractManagedLocalization, inspectManagedLocalization } from "../src/managed-localization.mjs";

function fixture() {
  const root = mkdtempSync(path.join(os.tmpdir(), "sts2-native-localization-test-"));
  const source = [
    ["localization/eng/cards.json", { "FUEL.description": "Gain {Energy:energyIcons()}." }],
    ["localization/zhs/cards.json", { "FUEL.description": "获得{Energy:energyIcons()}。" }],
    ["localization/eng/static_hover_tips.json", { "ENERGY.title": "Energy" }],
    ["localization/deu/cards.json", { "FUEL.description": "Unrequested language" }]
  ].map(([name, table]) => ({ name, bytes: Buffer.from(JSON.stringify(table)) }));
  const header = Buffer.alloc(112); header.write("GDPC"); header.writeUInt32LE(3, 4); header.writeUInt32LE(2, 20);
  header.writeBigUInt64LE(112n, 24);
  const data = Buffer.concat(source.map(entry => entry.bytes)); header.writeBigUInt64LE(BigInt(112 + data.length), 32);
  const count = Buffer.alloc(4); count.writeUInt32LE(source.length);
  let offset = 0;
  const entries = source.map(({ name, bytes }) => {
    const text = Buffer.from(`${name}\0`); const length = Buffer.alloc(4); length.writeUInt32LE(text.length);
    const entry = Buffer.alloc(36); entry.writeBigUInt64LE(BigInt(offset)); entry.writeBigUInt64LE(BigInt(bytes.length), 8);
    createHash("md5").update(bytes).digest().copy(entry, 16); offset += bytes.length;
    return Buffer.concat([length, text, entry]);
  });
  const packFile = path.join(root, "game.pck"); writeFileSync(packFile, Buffer.concat([header, data, count, ...entries]));
  const expectedPackSha256 = createHash("sha256").update(readFileSync(packFile)).digest("hex");
  return { root, packFile, expectedPackSha256, candidateDirectory: path.join(root, "candidate") };
}

test("prepare extracts every eng/zhs official JSON table without using upstream localization", () => {
  const f = fixture();
  try {
    const receipt = extractManagedLocalization(f);
    assert.deepEqual(receipt.files.map(file => `${file.language}/${file.table}`),
      ["eng/cards.json", "eng/static_hover_tips.json", "zhs/cards.json"]);
    const cards = JSON.parse(readFileSync(path.join(f.candidateDirectory, "lib/native-localization/zhs/cards.json")));
    assert.equal(cards["FUEL.description"], "获得{Energy:energyIcons()}。");
    const result = inspectManagedLocalization({ candidateDirectory: f.candidateDirectory,
      manifest: { native_localization: { resource_pack_sha256: f.expectedPackSha256 },
        expected_build: { localization_sha256: receipt.localization_sha256 } } });
    assert.equal(result.localization_table_count, 3);
    assert.equal(result.resource_pack_sha256, f.expectedPackSha256);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test("candidate admission rejects changed, removed, or extra loaded tables", () => {
  for (const change of [file => writeFileSync(file, '{"FUEL.description":"Wrong old table"}'),
    file => unlinkSync(file), file => writeFileSync(path.join(path.dirname(file), "extra.json"), '{}')]) {
    const f = fixture();
    try {
      const receipt = extractManagedLocalization(f);
      change(path.join(f.candidateDirectory, "lib/native-localization/eng/cards.json"));
      assert.throws(() => inspectManagedLocalization({ candidateDirectory: f.candidateDirectory,
        manifest: { native_localization: { resource_pack_sha256: f.expectedPackSha256 },
          expected_build: { localization_sha256: receipt.localization_sha256 } } }), /table closure/u);
    } finally { rmSync(f.root, { recursive: true, force: true }); }
  }
});

test("prepare rejects a resource pack outside the exact admitted game tuple", () => {
  const f = fixture();
  try { assert.throws(() => extractManagedLocalization({ ...f, expectedPackSha256: "not-the-pack-sha" }), /resource pack SHA/u); }
  finally { rmSync(f.root, { recursive: true, force: true }); }
});
