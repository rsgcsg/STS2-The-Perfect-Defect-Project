#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const CURRENT_CONTEXT_BUDGET_BYTES = 8 * 1024;

const requiredPaths = [
  "docs/ENGINEERING_GOVERNANCE.md",
  "docs/adr/README.md",
  ".agents/skills/README.md",
  ".github/PULL_REQUEST_TEMPLATE.md",
  "tools/check-governance.mjs",
  "tools/check-governance.test.mjs"
];

const durableIndexPaths = [
  "docs/ENGINEERING_GOVERNANCE.md",
  "docs/adr/README.md",
  ".agents/skills/README.md"
];

function finding(code, file, message) {
  return { code, file, message };
}

function read(workspaceRoot, relative) {
  return fs.readFileSync(path.join(workspaceRoot, relative), "utf8").replace(/\r\n?/gu, "\n");
}

function has(workspaceRoot, relative) {
  return fs.existsSync(path.join(workspaceRoot, relative));
}

function requireTokens(findings, workspaceRoot, relative, tokens) {
  if (!has(workspaceRoot, relative)) return;
  const source = read(workspaceRoot, relative);
  for (const token of tokens) {
    if (!source.includes(token)) {
      findings.push(finding("governance-route-missing", relative, token));
    }
  }
}

export function requiredSurfaceFindings(workspaceRoot = root) {
  const findings = [];
  for (const relative of requiredPaths) {
    if (!has(workspaceRoot, relative)) {
      findings.push(finding("governance-surface-missing", relative, "required governance surface"));
    }
  }
  return findings;
}

export function routeFindings(workspaceRoot = root) {
  const findings = [];
  requireTokens(findings, workspaceRoot, "AGENTS.md", ["docs/ENGINEERING_GOVERNANCE.md"]);
  requireTokens(findings, workspaceRoot, "README.md", [
    "docs/ENGINEERING_GOVERNANCE.md",
    ".agents/skills/README.md"
  ]);
  requireTokens(findings, workspaceRoot, "docs/DOCUMENT_MAP.md", [
    "ENGINEERING_GOVERNANCE.md",
    "adr/README.md",
    "../.agents/skills/README.md"
  ]);
  requireTokens(findings, workspaceRoot, "docs/NEW_ENGINEER_GUIDE.md", [
    "ENGINEERING_GOVERNANCE.md",
    "G0",
    "G6"
  ]);
  requireTokens(findings, workspaceRoot, "CONTRIBUTING.md", [
    "docs/ENGINEERING_GOVERNANCE.md"
  ]);
  return findings;
}

export function durableTruthFindings(workspaceRoot = root) {
  const findings = [];
  for (const relative of durableIndexPaths) {
    if (!has(workspaceRoot, relative)) continue;
    const source = read(workspaceRoot, relative);
    const sha = source.match(/\b[0-9a-f]{40}\b/iu)?.[0];
    if (sha) findings.push(finding("governance-mutable-identity", relative, sha));
    const developRef = source.match(/\bdevelop@[0-9a-f]+\b/iu)?.[0];
    if (developRef) findings.push(finding("governance-mutable-identity", relative, developRef));
    const pr = source.match(/\bPR\s+#\d+\b/iu)?.[0];
    if (pr) findings.push(finding("governance-mutable-work-item", relative, pr));
  }
  return findings;
}

export function pullRequestTemplatePathFindings(trackedPaths) {
  const canonical = ".github/PULL_REQUEST_TEMPLATE.md";
  const aliases = trackedPaths.filter((entry) => entry.toLowerCase() === canonical.toLowerCase());
  const findings = [];
  if (aliases.length !== 1 || aliases[0] !== canonical) {
    findings.push(finding(
      "pull-request-template-path-invalid",
      ".github",
      `track exactly ${canonical}; found ${aliases.join(", ") || "none"}`
    ));
  }
  return findings;
}

function trackedPaths(workspaceRoot) {
  try {
    return execFileSync("git", ["ls-files"], {
      cwd: workspaceRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    }).replace(/\r\n?/gu, "\n").split("\n").filter(Boolean);
  } catch {
    const github = path.join(workspaceRoot, ".github");
    if (!fs.existsSync(github)) return [];
    return fs.readdirSync(github).map((name) => `.github/${name}`);
  }
}

export function pullRequestTemplateFindings(workspaceRoot = root) {
  const findings = pullRequestTemplatePathFindings(trackedPaths(workspaceRoot));
  const canonical = ".github/PULL_REQUEST_TEMPLATE.md";
  if (!has(workspaceRoot, canonical)) return findings;
  const source = read(workspaceRoot, canonical).trim();
  const requiredTokens = [
    "Change class (`G0`-`G6`)",
    "Owning fact / layer",
    "latest head SHA",
    "Test shape",
    "Evidence level actually proved",
    "Merge method",
    "Remaining non-claims"
  ];
  for (const token of requiredTokens) {
    if (!source.includes(token)) {
      findings.push(finding("pull-request-template-field-missing", canonical, token));
    }
  }
  return findings;
}

function indexedLinks(source, pattern) {
  return [...source.matchAll(pattern)].map((match) => match[1]);
}

export function adrIndexFindings(workspaceRoot = root) {
  const findings = [];
  const directory = path.join(workspaceRoot, "docs", "adr");
  const indexPath = "docs/adr/README.md";
  if (!fs.existsSync(directory) || !has(workspaceRoot, indexPath)) return findings;
  const actual = fs.readdirSync(directory)
    .filter((name) => /^\d{4}-.*\.md$/u.test(name))
    .sort();
  const source = read(workspaceRoot, indexPath);
  const indexed = indexedLinks(source, /\((\d{4}-[^)]+\.md)\)/gu);
  for (const name of actual) {
    const count = indexed.filter((entry) => entry === name).length;
    if (count !== 1) findings.push(finding("adr-index-invalid", indexPath, `${name} indexed ${count} time(s)`));
  }
  for (const name of indexed) {
    if (!actual.includes(name)) findings.push(finding("adr-index-orphan", indexPath, name));
  }
  return findings;
}

export function skillIndexFindings(workspaceRoot = root) {
  const findings = [];
  const directory = path.join(workspaceRoot, ".agents", "skills");
  const indexPath = ".agents/skills/README.md";
  if (!fs.existsSync(directory) || !has(workspaceRoot, indexPath)) return findings;
  const actual = fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && fs.existsSync(path.join(directory, entry.name, "SKILL.md")))
    .map((entry) => entry.name)
    .sort();
  const source = read(workspaceRoot, indexPath);
  const indexed = indexedLinks(source, /\(([^/)]+)\/SKILL\.md\)/gu);
  for (const name of actual) {
    const count = indexed.filter((entry) => entry === name).length;
    if (count !== 1) findings.push(finding("skill-index-invalid", indexPath, `${name} indexed ${count} time(s)`));
  }
  for (const name of indexed) {
    if (!actual.includes(name)) findings.push(finding("skill-index-orphan", indexPath, name));
  }
  return findings;
}

export function currentContextFindings(workspaceRoot = root) {
  const findings = [];
  const relative = "docs/memory/CURRENT.md";
  if (!has(workspaceRoot, relative)) return findings;
  const bytes = fs.readFileSync(path.join(workspaceRoot, relative));
  const source = bytes.toString("utf8").replace(/\r\n?/gu, "\n");
  if (bytes.byteLength > CURRENT_CONTEXT_BUDGET_BYTES) {
    findings.push(finding("current-context-oversized", relative,
      `final UTF-8 file is ${bytes.byteLength} > ${CURRENT_CONTEXT_BUDGET_BYTES} bytes; move detail to linked evidence`));
  }
  for (const token of ["Resolve live GitHub refs", "override this file", "Remaining Platform non-claims"]) {
    if (!source.includes(token)) {
      findings.push(finding("current-context-safety-missing", relative, token));
    }
  }
  return findings;
}

export function packageFindings(workspaceRoot = root) {
  const findings = [];
  const relative = "package.json";
  if (!has(workspaceRoot, relative)) return findings;
  let packageJson;
  try {
    packageJson = JSON.parse(read(workspaceRoot, relative));
  } catch (error) {
    return [finding("governance-package-invalid", relative, error.message)];
  }
  const expected = "node --test tools/check-governance.test.mjs && node tools/check-governance.mjs";
  if (packageJson.scripts?.["check:governance"] !== expected) {
    findings.push(finding("governance-command-invalid", relative, `check:governance must equal ${expected}`));
  }
  if (!packageJson.scripts?.check?.includes("npm run check:governance")) {
    findings.push(finding("governance-check-not-portable", relative, "check must compose check:governance"));
  }
  return findings;
}

export function governanceFindings(workspaceRoot = root) {
  return [
    ...requiredSurfaceFindings(workspaceRoot),
    ...routeFindings(workspaceRoot),
    ...durableTruthFindings(workspaceRoot),
    ...pullRequestTemplateFindings(workspaceRoot),
    ...adrIndexFindings(workspaceRoot),
    ...skillIndexFindings(workspaceRoot),
    ...currentContextFindings(workspaceRoot),
    ...packageFindings(workspaceRoot)
  ];
}

function main() {
  const findings = governanceFindings(root);
  if (findings.length > 0) {
    console.error("Governance contract failed:");
    for (const item of findings) {
      console.error(`- [${item.code}] ${item.file}: ${item.message}`);
    }
    process.exitCode = 1;
    return;
  }
  console.log("governance contract passed");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
