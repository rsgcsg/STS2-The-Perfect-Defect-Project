import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const contractPath = new URL("../../../contracts/native-logical-publication-profile-v1.json", import.meta.url);
const outputPath = new URL("../src/nativeLogicalPublicationProfile.generated.ts", import.meta.url);
const bytes = readFileSync(contractPath);
const profile = JSON.parse(bytes.toString("utf8"));
const sha256 = createHash("sha256").update(bytes).digest("hex");
const output = `// Generated from contracts/native-logical-publication-profile-v1.json.\n// Refresh with tools/generate-native-logical-profile.mjs; never edit the seam list here.\nimport { freezeNativeLogical } from "./nativeLogicalWire.js";\n\nexport const NATIVE_LOGICAL_PUBLICATION_PROFILE = freezeNativeLogical(${JSON.stringify(profile, null, 2)} as const);\nexport const NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256 = "${sha256}" as const;\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(outputPath, "utf8") !== output)
    throw new Error("Native logical publication profile export is stale; regenerate it from the single JSON contract.");
} else {
  writeFileSync(outputPath, output);
}
