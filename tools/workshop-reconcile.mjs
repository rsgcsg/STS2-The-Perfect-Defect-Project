import path from "node:path";
import { parseArgs } from "node:util";
import { reconcilePreMutationFailure } from "./workshop-publish.mjs";

try {
  const { values: v } = parseArgs({ options: Object.fromEntries([
    "prepared-root", "prepared-receipt-sha256", "provenance-sha256", "uploader-receipt",
    "uploader-receipt-sha256", "attempt-id"
  ].map((key) => [key, { type: "string" }])), allowPositionals: false });
  console.log(JSON.stringify(reconcilePreMutationFailure({
    repositoryRoot: path.resolve(import.meta.dirname, ".."), preparedRoot: v["prepared-root"],
    preparedReceiptSha256: v["prepared-receipt-sha256"], approvedProvenanceSha256: v["provenance-sha256"],
    uploaderReceipt: v["uploader-receipt"], uploaderReceiptSha256: v["uploader-receipt-sha256"],
    attemptId: v["attempt-id"]
  }), null, 2));
} catch (error) { console.error(error.message); process.exitCode = 1; }
