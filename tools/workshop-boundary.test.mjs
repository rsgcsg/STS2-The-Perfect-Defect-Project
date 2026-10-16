import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const allowed = [".gitignore", "README.md", "image.png", "workshop.json"];
const git = (cwd, args, input) => execFileSync("git", args, {
  cwd, input, encoding: "utf8", env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1" }
});

test("Workshop listing uses official template fields without runtime authority", () => {
  const listing = JSON.parse(fs.readFileSync(path.join(root, "workshop/workshop.json"), "utf8"));
  assert.deepEqual(Object.keys(listing).sort(), [
    "title", "description", "visibility", "changeNote", "tags", "dependencies", "contentDescriptors"
  ].sort());
  for (const field of ["title", "description", "changeNote"]) {
    assert.equal(typeof listing[field], "string");
    assert.ok(listing[field].trim().length > 0);
  }
  assert.equal(listing.visibility, "private");
  assert.deepEqual(listing.tags, ["Tools & APIs"]);
  assert.deepEqual(listing.dependencies, []);
  assert.deepEqual(listing.contentDescriptors, []);
  const runtime = JSON.parse(fs.readFileSync(path.join(root, "apps/game-mod/mod_manifest.json"), "utf8"));
  assert.equal(runtime.id, "STS2_PLATFORM");
  const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  assert.ok(!pkg.workspaces.includes("workshop"), "release workspace is not a runtime npm package");
});

test("preview is a real bounded PNG and has no runtime payload", () => {
  const png = fs.readFileSync(path.join(root, "workshop/image.png"));
  assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  assert.ok(png.length < 1_000_000, "official preview limit is less than 1 MB");
  assert.equal(png.toString("ascii", 12, 16), "IHDR");
  assert.ok(png.readUInt32BE(16) >= 256);
  assert.ok(png.readUInt32BE(20) >= 256);
});

test("real Git ignore policy excludes generated/private/state paths but admits metadata", (t) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "workshop-boundary-"));
  t.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  git(fixture, ["init", "--quiet"]);
  fs.mkdirSync(path.join(fixture, "workshop"));
  fs.copyFileSync(path.join(root, "workshop/.gitignore"), path.join(fixture, "workshop/.gitignore"));
  const denied = [
    "content/STS2_PLATFORM.dll", "content/STS2_PLATFORM.json", "content/.gitkeep",
    "mod_id.txt", "mod-uploader.log", "logs/upload.txt", ".env", ".env.production",
    "credentials.json", "secrets/token", "config/login.json", "steam_appid.txt",
    "steam/ssfn123", "config/loginusers.vdf", "game/sts2.dll", "sts2.pck",
    ".local/receipt.json", "ModUploader.exe", "src/Copy.cs", "unexpected.txt"
  ].map((name) => `workshop/${name}`);
  const admitted = allowed.map((name) => `workshop/${name}`);
  const ignored = git(fixture, ["check-ignore", "--no-index", "--stdin", "-z"],
    [...denied, ...admitted].join("\0") + "\0").split("\0").filter(Boolean);
  assert.deepEqual(ignored.sort(), denied.sort());
});

test("index and nonignored Workshop source contain only the release allowlist", () => {
  // --cached catches force-added private/generated paths even when ignored.
  const files = git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "workshop"])
    .split("\0").filter(Boolean);
  assert.deepEqual([...new Set(files)].sort(), allowed.map((name) => `workshop/${name}`).sort());
  for (const name of allowed) {
    assert.ok(fs.lstatSync(path.join(root, "workshop", name)).isFile(), "no symlink release sources");
  }
});
