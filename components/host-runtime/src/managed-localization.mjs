import { createHash } from "node:crypto";
import { closeSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const LANGUAGES = ["eng", "zhs"];
const digest = value => createHash("sha256").update(value).digest("hex");
const directory = candidate => path.join(candidate, "lib", "native-localization");
const closureHash = files => digest(JSON.stringify(files.map(({ language, table, sha256 }) => ({ language, table, sha256 }))));

function packHash(fd) {
  const hash = createHash("sha256");
  const chunk = Buffer.alloc(1024 * 1024);
  let offset = 0;
  for (;;) {
    const size = readSync(fd, chunk, 0, chunk.length, offset);
    if (size === 0) break;
    hash.update(chunk.subarray(0, size));
    offset += size;
  }
  return hash.digest("hex");
}

// The exact shipped Godot v3 PCK index. Extract only public localization JSON,
// never executable assets, and preserve each official table's original bytes.
export function extractManagedLocalization({ candidateDirectory, packFile, expectedPackSha256 }) {
  const fd = openSync(packFile, "r");
  try {
    const read = (offset, size) => {
      const value = Buffer.alloc(size);
      if (readSync(fd, value, 0, size, offset) !== size) throw new Error("Truncated native resource pack.");
      return value;
    };
    const header = read(0, 112);
    if (header.toString("ascii", 0, 4) !== "GDPC" || header.readUInt32LE(4) !== 3 || header.readUInt32LE(20) !== 2)
      throw new Error("Managed localization requires the admitted unencrypted Godot v3 resource pack.");
    const sourcePackSha256 = packHash(fd);
    if (sourcePackSha256 !== expectedPackSha256) throw new Error("Native localization resource pack SHA does not match the admitted game.");
    const fileBase = Number(header.readBigUInt64LE(24));
    let cursor = Number(header.readBigUInt64LE(32));
    const count = read(cursor, 4).readUInt32LE(); cursor += 4;
    const entries = [];
    for (let index = 0; index < count; index++) {
      const size = read(cursor, 4).readUInt32LE(); cursor += 4;
      const name = read(cursor, size).toString("utf8").replace(/\0+$/u, ""); cursor += size;
      const entry = read(cursor, 36); cursor += 36;
      const match = /^localization\/(eng|zhs)\/([a-z0-9_.-]+\.json)$/u.exec(name);
      if (match) entries.push({ language: match[1], table: match[2],
        offset: fileBase + Number(entry.readBigUInt64LE(0)), size: Number(entry.readBigUInt64LE(8)),
        md5: entry.subarray(16, 32).toString("hex"), flags: entry.readUInt32LE(32) });
    }
    entries.sort((a, b) => `${a.language}/${a.table}`.localeCompare(`${b.language}/${b.table}`, "en"));
    const files = entries.map(entry => {
      if (entry.flags !== 0) throw new Error("Encrypted native localization table is not supported by this exact candidate.");
      const bytes = read(entry.offset, entry.size);
      if (createHash("md5").update(bytes).digest("hex") !== entry.md5) throw new Error("Native localization table does not match its PCK index checksum.");
      const table = JSON.parse(bytes.toString("utf8"));
      if (table === null || Array.isArray(table) || typeof table !== "object" || Object.values(table).some(value => typeof value !== "string"))
        throw new Error("Native localization table is not a string dictionary.");
      const target = path.join(directory(candidateDirectory), entry.language, entry.table);
      mkdirSync(path.dirname(target), { recursive: true });
      writeFileSync(target, bytes);
      return { language: entry.language, table: entry.table, sha256: digest(bytes) };
    });
    for (const language of LANGUAGES)
      if (!files.some(file => file.language === language && file.table === "cards.json")) throw new Error(`Native localization is missing ${language}/cards.json.`);
    const result = { schema: "sts2.headless/native-localization-1", source_pack_sha256: sourcePackSha256,
      localization_sha256: closureHash(files), files };
    writeFileSync(path.join(directory(candidateDirectory), "manifest.json"), `${JSON.stringify(result, null, 2)}\n`);
    return result;
  } finally { closeSync(fd); }
}

export function inspectManagedLocalization({ candidateDirectory, manifest }) {
  const root = directory(candidateDirectory);
  const receipt = JSON.parse(readFileSync(path.join(root, "manifest.json"), "utf8"));
  if (receipt.schema !== "sts2.headless/native-localization-1" || receipt.source_pack_sha256 !== manifest.native_localization.resource_pack_sha256)
    throw new Error("Managed localization has no admitted native resource pack provenance.");
  const files = LANGUAGES.flatMap(language => readdirSync(path.join(root, language))
    .filter(table => table.endsWith(".json")).map(table => ({ language, table,
      sha256: digest(readFileSync(path.join(root, language, table))) })))
    .sort((a, b) => `${a.language}/${a.table}`.localeCompare(`${b.language}/${b.table}`, "en"));
  const actual = closureHash(files);
  if (actual !== manifest.expected_build.localization_sha256 || actual !== receipt.localization_sha256
      || JSON.stringify(files) !== JSON.stringify(receipt.files))
    throw new Error("Managed localization table closure differs from the admitted native resources.");
  return { resource_pack_sha256: receipt.source_pack_sha256, localization_sha256: actual, localization_table_count: files.length };
}
