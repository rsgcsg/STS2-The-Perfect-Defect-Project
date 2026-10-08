import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { NATIVE_LOGICAL_PUBLICATION_PROFILE, NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256,
  type NativeLogicalAttachInput } from "../src/index.js";

describe("fixed native publication profile export", () => {
  it("matches the single contract bytes and the source golden definition pin", () => {
    const bytes = readFileSync(new URL("../../../contracts/native-logical-publication-profile-v1.json", import.meta.url));
    const fixture = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/native-logical-publication-profile-v1.json", import.meta.url), "utf8"));
    expect(NATIVE_LOGICAL_PUBLICATION_PROFILE).toEqual(JSON.parse(bytes.toString("utf8")));
    expect(NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256).toBe(createHash("sha256").update(bytes).digest("hex"));
    expect(NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256).toBe(fixture.contract_sha256);
    expect(NATIVE_LOGICAL_PUBLICATION_PROFILE.profile_id).toBe(fixture.profile_id);
  });

  it("is immutable and directly usable by the existing generic Attach API", () => {
    const profile = NATIVE_LOGICAL_PUBLICATION_PROFILE;
    const attach: NativeLogicalAttachInput = { eagerScope: profile.eager_scope,
      requiredSeams: profile.required_seams, deliveryMode: profile.delivery_mode };
    expect(attach.requiredSeams).toBe(profile.required_seams);
    expect(Object.isFrozen(profile)).toBe(true);
    expect(Object.isFrozen(profile.required_seams)).toBe(true);
    expect(profile.required_seams.every(Object.isFrozen)).toBe(true);
    expect(() => (profile.required_seams as any).pop()).toThrow();
  });
});
