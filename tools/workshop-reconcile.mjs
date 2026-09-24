import path from "node:path";
import { parseArgs } from "node:util";
import { reconcilePreMutationFailure, reconcileUploaderSuccess } from "./workshop-publish.mjs";

try {
  const { values: v } = parseArgs({ options: Object.fromEntries([
    "prepared-root", "prepared-receipt-sha256", "provenance-sha256", "uploader-receipt",
    "uploader-receipt-sha256", "attempt-id", "kind", "evidence-sha256", "human-observed-item-id"
  ].map((key) => [key, { type: "string" }])), allowPositionals: false });
  if (v.kind && !["pre-mutation", "uploader-success"].includes(v.kind)) throw new Error("unknown_reconciliation_kind");
  const reconcile = v.kind === "uploader-success" ? reconcileUploaderSuccess : reconcilePreMutationFailure;
  console.log(JSON.stringify(reconcile({
    repositoryRoot: path.resolve(import.meta.dirname, ".."), preparedRoot: v["prepared-root"],
    preparedReceiptSha256: v["prepared-receipt-sha256"], approvedProvenanceSha256: v["provenance-sha256"],
    uploaderReceipt: v["uploader-receipt"], uploaderReceiptSha256: v["uploader-receipt-sha256"],
    attemptId: v["attempt-id"], evidenceSha256: v["evidence-sha256"],
    humanObservation: v["human-observed-item-id"] ? {
      evidence_level: "human_reported_steam_page_not_api_readback", item_id: v["human-observed-item-id"],
      visibility: "private", title: "SpireAgent Platform", preview_matches_candidate: true
    } : undefined
  }), null, 2));
} catch (error) { console.error(error.message); process.exitCode = 1; }
