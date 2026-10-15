#!/usr/bin/env node
import { readFileSync, statSync } from "node:fs";
import { compareP1CaptureRecords } from "./p1-capture-comparison.mjs";

// This command performs only bounded local file reads. No HTTP, native capture,
// controller, qualification, legacy comparison fallback or output-file mutation.
class InputError extends Error {}
function read(file) {
  if (!file) throw new InputError("required_arguments");
  let info;
  try { info = statSync(file); } catch { throw new InputError("input_unavailable"); }
  if (!info.isFile()) throw new InputError("regular_file_required");
  if (info.size > 64 * 1024 * 1024) throw new InputError("document_byte_bound_exceeded");
  const bytes = readFileSync(file);
  if (bytes.length > 64 * 1024 * 1024) throw new InputError("document_byte_bound_exceeded");
  try { return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new InputError("invalid_utf8"); }
}
try {
  const args = process.argv.slice(2);
  const names = ["--reference", "--candidate", "--reference-report", "--candidate-report", "--declaration"];
  if (args.length !== names.length * 2 || names.some(name => args.filter(value => value === name).length !== 1)
      || args.some((value, index) => index % 2 === 0 && !names.includes(value))) throw new InputError("required_arguments");
  const option = name => read(args[args.indexOf(name) + 1]);
  const result = await compareP1CaptureRecords({ reference: { record: option("--reference"), report: option("--reference-report") },
    candidate: { record: option("--candidate"), report: option("--candidate-report") }, declaration: option("--declaration") });
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.verdict === "c1_scope_match" ? 0 : result.verdict === "c1_scope_mismatch" ? 10 : 20;
} catch (error) {
  console.log(JSON.stringify({ schema: "sts2.project/p1-c1-comparison-1", verdict: "c1_incomparable", diagnostic_only: true,
    agent_admissible: false, creates_mutation_authority: false, p1_status: "unsupported", cross_host_qualification: "incomparable",
    alignment_verified: false, environment_alignment: "externally_asserted", relation_scope: "declared_typed_alias_relations_only",
    extension_semantics: "literal_values_no_undeclared_relation_inference", first_difference: { path: "$", reason: error instanceof InputError ? error.message : "input_unavailable" } }, null, 2));
  process.exitCode = 20;
}
