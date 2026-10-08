import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { NATIVE_LOGICAL_PUBLICATION_PROFILE, NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256 } from "../dist/index.js";

const bytes = readFileSync(new URL("../../../contracts/native-logical-publication-profile-v1.json", import.meta.url));
const fixture = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/native-logical-publication-profile-v1.json", import.meta.url), "utf8"));
assert.deepEqual(NATIVE_LOGICAL_PUBLICATION_PROFILE, JSON.parse(bytes.toString("utf8")));
assert.equal(NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256, createHash("sha256").update(bytes).digest("hex"));
assert.equal(NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256, fixture.contract_sha256);
assert.ok(Object.isFrozen(NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams));
