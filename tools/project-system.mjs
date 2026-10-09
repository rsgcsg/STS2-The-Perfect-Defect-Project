#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { makePlan, scopeCommands } from "./check-plan.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const AGENT_CHAIN_BUDGET_BYTES = 16 * 1024;
export const CONTEXT_BUDGET_BYTES = 8 * 1024;

const ignoredDirectories = new Set([
  ".git", ".local", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", "bin", "dist", "node_modules", "obj", "out"
]);

const componentRoutes = [
  { id: "project-apps", prefix: "python/spireagent/", guide: "python/docs/DOCUMENT_MAP.md", check: "npm run check:python" },
  { id: "research", prefix: "python/stpd/", guide: "python/docs/FULLRUN_RESEARCH.md", check: "npm run check:python" },
  {
    id: "native-foundation",
    prefix: "components/native-foundation/",
    guide: "components/native-foundation/README.md",
    check: "npm --prefix components/connector run test && npm --prefix apps/game-mod run check"
  },
  {
    id: "connector",
    prefix: "components/connector/",
    guide: "components/connector/docs/NEW_ENGINEER_GUIDE.md",
    check: "npm --prefix components/connector run check"
  },
  {
    id: "host-runtime",
    prefix: "components/host-runtime/",
    guide: "components/host-runtime/README.md",
    check: "npm --prefix components/host-runtime run check"
  },
  {
    id: "annotator",
    prefix: "components/annotator/",
    guide: "components/annotator/README.md",
    check: "npm --prefix components/annotator run test"
  },
  {
    id: "evidence",
    prefix: "components/evidence/",
    guide: "components/evidence/README.md",
    check: "npm --prefix components/evidence run check"
  },
  {
    id: "policy-runtime",
    prefix: "components/policy-runtime/",
    guide: "components/policy-runtime/README.md",
    check: "npm --prefix components/policy-runtime run check"
  },
  {
    id: "workbench",
    prefix: "apps/workbench/",
    guide: "apps/workbench/README.md",
    check: "npm --prefix apps/workbench run test"
  },
  {
    id: "ingame-ui",
    prefix: "apps/ingame-ui/",
    guide: "apps/ingame-ui/README.md",
    check: "npm --prefix apps/ingame-ui run check"
  },
  {
    id: "game-mod",
    prefix: "apps/game-mod/",
    guide: "apps/game-mod/README.md",
    check: "npm --prefix apps/game-mod run check"
  }
];

const requiredProjectPaths = [
  "README.md",
  "AGENTS.md",
  "docs/DOCUMENT_MAP.md",
  "docs/NEW_ENGINEER_GUIDE.md",
  "docs/PROJECT_SYSTEM.md",
  "docs/memory/CURRENT.md",
  "tools/project-system.mjs",
  "tools/project-system.test.mjs",
  ".agents/skills/repo-skill-maintenance/SKILL.md",
  ".agents/skills/platform-runtime-qualification/SKILL.md",
  ".agents/skills/platform-human-evidence/SKILL.md"
];

function relativePath(workspaceRoot, file) {
  return path.relative(workspaceRoot, file).split(path.sep).join("/");
}

function splitLines(source) {
  return source.split(/\r?\n/u);
}

function walkFiles(directory, output = []) {
  if (!fs.existsSync(directory)) return output;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) walkFiles(file, output);
    else if (entry.isFile()) output.push(file);
  }
  return output;
}

function walkDirectories(directory, output = []) {
  if (!fs.existsSync(directory)) return output;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (!entry.isDirectory() || ignoredDirectories.has(entry.name)) continue;
    const child = path.join(directory, entry.name);
    output.push(child);
    walkDirectories(child, output);
  }
  return output;
}

function stripFencedCode(source) {
  return source.replace(/```[\s\S]*?```/gu, "");
}

function parseMarkdownTargets(source) {
  const targets = [];
  const visible = stripFencedCode(source);
  for (const match of visible.matchAll(/!?\[[^\]]*\]\(([^)]+)\)/gu)) {
    let target = match[1].trim();
    if (target.startsWith("<") && target.endsWith(">")) {
      target = target.slice(1, -1);
    } else {
      target = target.split(/\s+["']/u, 1)[0];
    }
    targets.push(target);
  }
  return targets;
}

function finding(code, file, message) {
  return { code, file, message };
}

export function markdownLinkFindings(workspaceRoot = root) {
  const findings = [];
  const markdownFiles = walkFiles(workspaceRoot).filter((file) => file.endsWith(".md"));
  for (const file of markdownFiles) {
    const relative = relativePath(workspaceRoot, file);
    for (const rawTarget of parseMarkdownTargets(fs.readFileSync(file, "utf8"))) {
      if (/^(?:https?:|mailto:|tel:|data:)/iu.test(rawTarget) || rawTarget.startsWith("#")) {
        continue;
      }
      const withoutAnchor = rawTarget.split("#", 1)[0].split("?", 1)[0];
      if (!withoutAnchor) continue;
      let decoded;
      try {
        decoded = decodeURIComponent(withoutAnchor);
      } catch {
        findings.push(finding("markdown-target-invalid", relative, rawTarget));
        continue;
      }
      const resolved = path.resolve(path.dirname(file), decoded);
      const inside = resolved === workspaceRoot || resolved.startsWith(`${workspaceRoot}${path.sep}`);
      if (!inside) {
        findings.push(finding("markdown-target-escapes-root", relative, rawTarget));
      } else if (!fs.existsSync(resolved)) {
        const code = relative === "docs/DOCUMENT_MAP.md"
          ? "document-map-route-missing"
          : "markdown-target-missing";
        findings.push(finding(code, relative, rawTarget));
      }
    }
  }
  return findings;
}

function isPathReference(value) {
  if (/\s|[<>|*]/u.test(value)) return false;
  return /(?:^|\/)[\w.-]+\.(?:cs|csproj|js|json|md|mjs|py|toml|ts|ya?ml)$/iu.test(value);
}

function resolveAgentReference(workspaceRoot, agentFile, value) {
  if (/^(?:components|apps|tools|contracts|\.agents|\.github)\//u.test(value)) {
    return path.resolve(workspaceRoot, value);
  }
  return path.resolve(path.dirname(agentFile), value);
}

export function agentReferenceFindings(workspaceRoot = root) {
  const findings = [];
  const agentFiles = walkFiles(workspaceRoot).filter((file) => path.basename(file) === "AGENTS.md");
  for (const file of agentFiles) {
    const relative = relativePath(workspaceRoot, file);
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(/`([^`\n]+)`/gu)) {
      const value = match[1].trim().replace(/[.,;:]$/u, "");
      if (!isPathReference(value)) continue;
      const resolved = resolveAgentReference(workspaceRoot, file, value);
      const inside = resolved === workspaceRoot || resolved.startsWith(`${workspaceRoot}${path.sep}`);
      if (!inside || !fs.existsSync(resolved)) {
        findings.push(finding("agents-reference-missing", relative, value));
      }
    }
  }
  return findings;
}

function closestPackage(workspaceRoot, markdownFile) {
  let directory = path.dirname(markdownFile);
  while (directory === workspaceRoot || directory.startsWith(`${workspaceRoot}${path.sep}`)) {
    const candidate = path.join(directory, "package.json");
    if (fs.existsSync(candidate)) return candidate;
    if (directory === workspaceRoot) break;
    directory = path.dirname(directory);
  }
  return path.join(workspaceRoot, "package.json");
}

function commandDocumentationFiles(workspaceRoot) {
  return walkFiles(workspaceRoot).filter((file) => {
    const relative = relativePath(workspaceRoot, file);
    if (!file.endsWith(".md")) return false;
    if (relative.startsWith("docs/evidence/") || relative.startsWith("migration/")) return false;
    return !relative.includes("/")
      || relative.startsWith("docs/")
      || relative.endsWith("/AGENTS.md")
      || relative.endsWith("/SKILL.md");
  });
}

export function documentedCommandFindings(workspaceRoot = root) {
  const findings = [];
  for (const file of commandDocumentationFiles(workspaceRoot)) {
    const relative = relativePath(workspaceRoot, file);
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(/\bnpm(?:\s+--prefix\s+([^\s`]+))?\s+run\s+([A-Za-z0-9:_-]+)/gu)) {
      const prefix = match[1];
      const script = match[2];
      const packageFile = prefix
        ? path.resolve(workspaceRoot, prefix, "package.json")
        : closestPackage(workspaceRoot, file);
      if (!fs.existsSync(packageFile)) {
        findings.push(finding("documented-command-package-missing", relative, match[0]));
        continue;
      }
      let packageJson;
      try {
        packageJson = JSON.parse(fs.readFileSync(packageFile, "utf8"));
      } catch (error) {
        findings.push(finding("documented-command-package-invalid", relative, error.message));
        continue;
      }
      if (typeof packageJson.scripts?.[script] !== "string") {
        findings.push(finding("documented-command-missing", relative, match[0]));
      }
    }
  }
  return findings;
}

function parseSkillFrontmatter(source) {
  const normalized = source.replace(/\r\n?/gu, "\n");
  const match = normalized.match(/^---\n([\s\S]*?)\n---(?:\n|$)/u);
  if (!match) return null;
  const fields = {};
  for (const line of splitLines(match[1])) {
    const field = line.match(/^([a-z_]+):\s*(.*)$/u);
    if (!field) continue;
    fields[field[1]] = field[2].trim().replace(/^(["'])(.*)\1$/u, "$2");
  }
  return fields;
}

export function discoverSkills(workspaceRoot = root) {
  return walkFiles(workspaceRoot)
    .filter((file) => path.basename(file) === "SKILL.md"
      && relativePath(workspaceRoot, file).includes(".agents/skills/"))
    .map((file) => {
      const source = fs.readFileSync(file, "utf8");
      const fields = parseSkillFrontmatter(source);
      return {
        file,
        relative: relativePath(workspaceRoot, file),
        directoryName: path.basename(path.dirname(file)),
        name: fields?.name ?? null,
        description: fields?.description ?? null,
        source
      };
    })
    .sort((left, right) => left.relative.localeCompare(right.relative));
}

export function skillFindings(workspaceRoot = root) {
  const findings = [];
  const seen = new Set();
  const skillRoots = walkDirectories(workspaceRoot).filter((directory) =>
    path.basename(directory) === "skills" && path.basename(path.dirname(directory)) === ".agents");
  for (const skillRoot of skillRoots) {
    for (const entry of fs.readdirSync(skillRoot, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const entrypoint = path.join(skillRoot, entry.name, "SKILL.md");
      if (!fs.existsSync(entrypoint)) {
        findings.push(finding(
          "skill-entrypoint-missing",
          relativePath(workspaceRoot, path.join(skillRoot, entry.name)),
          "SKILL.md"
        ));
      }
    }
  }
  for (const skill of discoverSkills(workspaceRoot)) {
    if (!skill.name || !skill.description) {
      findings.push(finding("skill-frontmatter-invalid", skill.relative, "name and description are required"));
      continue;
    }
    if (!/^[a-z0-9-]{1,63}$/u.test(skill.name) || skill.name !== skill.directoryName) {
      findings.push(finding("skill-name-invalid", skill.relative, skill.name));
    }
    if (seen.has(skill.name)) findings.push(finding("skill-name-duplicate", skill.relative, skill.name));
    seen.add(skill.name);
    if (/\bTODO\b/u.test(skill.source)) {
      findings.push(finding("skill-placeholder-present", skill.relative, "TODO"));
    }
    const yaml = path.join(path.dirname(skill.file), "agents", "openai.yaml");
    if (fs.existsSync(yaml)) {
      const contents = fs.readFileSync(yaml, "utf8");
      const policy = contents.match(/allow_implicit_invocation:\s*(\S+)/u)?.[1];
      if (policy && !["true", "false"].includes(policy)) {
        findings.push(finding("skill-invocation-policy-invalid", relativePath(workspaceRoot, yaml), policy));
      }
      if (skill.name === "repo-skill-maintenance" && policy !== "false") {
        findings.push(finding("skill-invocation-policy-invalid", relativePath(workspaceRoot, yaml), "repo-skill-maintenance must be explicit-only"));
      }
      if (["platform-runtime-qualification", "platform-human-evidence"].includes(skill.name)
          && policy !== "true") {
        findings.push(finding("skill-invocation-policy-invalid", relativePath(workspaceRoot, yaml), `${skill.name} must remain implicit-enabled`));
      }
    } else if ([
      "repo-skill-maintenance",
      "platform-runtime-qualification",
      "platform-human-evidence"
    ].includes(skill.name)) {
      findings.push(finding("skill-metadata-missing", skill.relative, "invocation policy metadata"));
    }
  }
  return findings;
}

function ancestorsFromRoot(workspaceRoot, directory) {
  const directories = [];
  let current = directory;
  while (current === workspaceRoot || current.startsWith(`${workspaceRoot}${path.sep}`)) {
    directories.push(current);
    if (current === workspaceRoot) break;
    current = path.dirname(current);
  }
  return directories.reverse();
}

function agentsForDirectory(workspaceRoot, directory) {
  return ancestorsFromRoot(workspaceRoot, directory)
    .map((ancestor) => path.join(ancestor, "AGENTS.md"))
    .filter((file) => fs.existsSync(file))
    .map((file) => relativePath(workspaceRoot, file));
}

function instructionChainBytes(workspaceRoot, agents) {
  return agents.reduce((total, relative) => total + fs.statSync(path.join(workspaceRoot, relative)).size, 0)
    + Math.max(0, agents.length - 1) * 2;
}

export function agentBudgetFindings(workspaceRoot = root) {
  const findings = [];
  const agentFiles = walkFiles(workspaceRoot).filter((file) => path.basename(file) === "AGENTS.md");
  for (const leaf of agentFiles) {
    const chain = agentsForDirectory(workspaceRoot, path.dirname(leaf));
    const bytes = instructionChainBytes(workspaceRoot, chain);
    if (bytes > AGENT_CHAIN_BUDGET_BYTES) {
      findings.push(finding(
        "agents-instruction-budget-exceeded",
        relativePath(workspaceRoot, leaf),
        `${bytes} > ${AGENT_CHAIN_BUDGET_BYTES} bytes`
      ));
    }
  }
  return findings;
}

export function projectIntegrityFindings(workspaceRoot = root) {
  const findings = [];
  for (const required of requiredProjectPaths) {
    if (!fs.existsSync(path.join(workspaceRoot, required))) {
      findings.push(finding("project-system-file-missing", required, "required V1 surface"));
    }
  }
  const packageFile = path.join(workspaceRoot, "package.json");
  if (!fs.existsSync(packageFile)) return findings;
  let packageJson;
  try {
    packageJson = JSON.parse(fs.readFileSync(packageFile, "utf8"));
  } catch (error) {
    findings.push(finding("project-system-package-invalid", "package.json", error.message));
    return findings;
  }
  const expected = {
    "project:context": "node tools/project-system.mjs context",
    "project:check": "node --test tools/project-system.test.mjs && node tools/project-system.mjs check",
    "project:closeout": "node tools/project-system.mjs closeout"
  };
  for (const [name, command] of Object.entries(expected)) {
    if (packageJson.scripts?.[name] !== command) {
      findings.push(finding("project-system-command-invalid", "package.json", `${name} must equal ${command}`));
    }
  }
  if (!packageJson.scripts?.check?.includes("npm run project:check")) {
    findings.push(finding("project-system-check-not-portable", "package.json", "check must compose project:check"));
  }
  for (const duplicate of [
    ".agents/skills/registry.json",
    "docs/memory/DECISIONS.md",
    "docs/COMPONENT_VERSIONS.json"
  ]) {
    if (fs.existsSync(path.join(workspaceRoot, duplicate))) {
      findings.push(finding("project-system-duplicate-authority", duplicate, "use native discovery or canonical identity owners"));
    }
  }
  return findings;
}

export function freshnessWarnings(workspaceRoot = root) {
  const warnings = [];
  const current = path.join(workspaceRoot, "docs", "memory", "CURRENT.md");
  if (fs.existsSync(current)) {
    const source = fs.readFileSync(current, "utf8");
    if (Buffer.byteLength(source) > 4096 || splitLines(source).length > 80) {
      warnings.push(finding("current-context-growth", "docs/memory/CURRENT.md", "review bounded handoff scope"));
    }
  }
  const readme = path.join(workspaceRoot, "README.md");
  if (fs.existsSync(readme) && /session-20|\b[0-9a-f]{12,64}\.\.\./iu.test(fs.readFileSync(readme, "utf8"))) {
    warnings.push(finding("readme-mutable-evidence", "README.md", "move exact current evidence to STATUS/CURRENT"));
  }
  const workflow = path.join(workspaceRoot, "docs", "DEVELOPMENT_WORKFLOW.md");
  if (fs.existsSync(workflow) && /current independent .*workstreams/iu.test(fs.readFileSync(workflow, "utf8"))) {
    warnings.push(finding("workflow-mutable-workstreams", "docs/DEVELOPMENT_WORKFLOW.md", "move active work to CURRENT/PRs"));
  }
  return warnings;
}

export function collectProjectSystemFindings(workspaceRoot = root) {
  return [
    ...markdownLinkFindings(workspaceRoot),
    ...agentReferenceFindings(workspaceRoot),
    ...documentedCommandFindings(workspaceRoot),
    ...skillFindings(workspaceRoot),
    ...agentBudgetFindings(workspaceRoot),
    ...projectIntegrityFindings(workspaceRoot)
  ];
}

function git(workspaceRoot, args, fallback = "unknown") {
  try {
    return execFileSync("git", ["-C", workspaceRoot, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"]
    }).trim() || fallback;
  } catch {
    return fallback;
  }
}

function routeForId(id) {
  return componentRoutes.find((route) => route.id === id) ?? null;
}

function routeForPath(relative) {
  const normalized = relative.replace(/^\.\//u, "").replace(/\\/gu, "/");
  return componentRoutes.find((route) => normalized === route.prefix.slice(0, -1)
    || normalized.startsWith(route.prefix)) ?? null;
}

function localAgentsForRoute(workspaceRoot, route) {
  return agentsForDirectory(workspaceRoot, route ? path.join(workspaceRoot, route.prefix) : workspaceRoot);
}

function parseContextArgs(args) {
  let component = null;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--component") component = args[index + 1] ?? null;
    else throw new Error(`unknown context argument: ${args[index]}`);
    index += 1;
  }
  return { component };
}

export function formatContext(workspaceRoot = root, options = {}) {
  const explicit = options.component ?? null;
  const cwdRelative = relativePath(workspaceRoot, process.cwd());
  const route = explicit ? routeForId(explicit) : routeForPath(cwdRelative);
  if (explicit && !route) {
    throw new Error(`unknown component: ${explicit}; expected ${componentRoutes.map((item) => item.id).join(", ")}`);
  }
  const owner = route?.id ?? "platform";
  const skills = discoverSkills(workspaceRoot).filter((skill) => {
    if (!route) return true;
    if (skill.name === "repo-skill-maintenance") return true;
    if (skill.name === "platform-runtime-qualification") {
      return ["native-foundation", "connector", "host-runtime", "game-mod"].includes(owner);
    }
    if (skill.name === "platform-human-evidence") {
      return ["native-foundation", "annotator", "evidence", "game-mod"].includes(owner);
    }
    return false;
  });
  const agents = localAgentsForRoute(workspaceRoot, route);
  const plan = makePlan({ base: "origin/develop", cwd: workspaceRoot });
  const branch = git(workspaceRoot, ["branch", "--show-current"], "detached");
  const sha = git(workspaceRoot, ["rev-parse", "HEAD"]);
  const base = git(workspaceRoot, ["rev-parse", "origin/develop"]);
  const status = git(workspaceRoot, ["status", "--short"], "");
  const lines = [
    "# Platform task context",
    "",
    `- Repository: rsgcsg/STS2-The-Perfect-Defect-Project`,
    `- Branch: ${branch}`,
    `- HEAD: ${sha}`,
    `- Known integration base: origin/develop at ${base}`,
    `- Worktree: ${status ? "dirty; inspect before editing" : "clean"}`,
    `- Owning component: ${owner}`,
    `- Instruction chain: ${instructionChainBytes(workspaceRoot, agents)} / ${AGENT_CHAIN_BUDGET_BYTES} bytes`,
    "",
    "## Read",
    "",
    ...agents.map((file) => `- ${file}`),
    "- README.md",
    "- docs/memory/CURRENT.md",
    "- docs/ARCHITECTURE.md",
    "- docs/COMPONENTS.md",
    "- docs/DEVELOPMENT_WORKFLOW.md",
    "- docs/TESTING.md",
    ...(route ? [`- ${route.guide}`] : ["- docs/DOCUMENT_MAP.md"]),
    "",
    "Load docs/STATUS.md and dated evidence only when current claims or exact proof matter.",
    "",
    "## Applicable Skills",
    "",
    ...(skills.length > 0
      ? skills.map((skill) => `- ${skill.name}: ${skill.description}`)
      : ["- None; ordinary work normally needs no Skill."]),
    "",
    "## Recommended checks",
    "",
    `Selected check plan: ${plan.scope} (${plan.reason}); docs/TESTING.md owns selection and evidence limits.`,
    "",
    ...(route ? [`- ${route.check}`] : []),
    "- npm run project:check",
    "- npm run check:plan -- --base origin/develop --run",
    ...scopeCommands(plan.scope).map((command) => `- npm run ${command}`),
    "- npm run project:closeout",
    "- git diff --check",
    "",
    "Portable checks are source/test evidence only. Use exact-game/runtime/Human gates only when the change owns that behavior."
  ];
  const output = `${lines.join("\n")}\n`;
  if (Buffer.byteLength(output) > CONTEXT_BUDGET_BYTES) {
    throw new Error(`project context exceeds ${CONTEXT_BUDGET_BYTES} bytes`);
  }
  return output;
}

function changedFiles(workspaceRoot) {
  const files = new Set();
  const commands = [
    ["diff", "--name-only", "origin/develop...HEAD"],
    ["diff", "--name-only"],
    ["diff", "--name-only", "--cached"],
    ["ls-files", "--others", "--exclude-standard"]
  ];
  for (const args of commands) {
    const output = git(workspaceRoot, args, "");
    for (const file of splitLines(output).filter(Boolean)) files.add(file);
  }
  return [...files].sort();
}

function ownersForFiles(files) {
  const owners = new Set();
  for (const file of files) owners.add(routeForPath(file)?.id ?? "platform");
  return [...owners].sort();
}

function anyMatch(files, patterns) {
  return files.some((file) => patterns.some((pattern) => pattern.test(file)));
}

export function formatCloseout(workspaceRoot = root) {
  const files = changedFiles(workspaceRoot);
  const owners = ownersForFiles(files);
  const plan = makePlan({ base: "origin/develop", cwd: workspaceRoot });
  const checks = new Set(["npm run project:check", "npm run check:plan -- --base origin/develop --run",
    ...scopeCommands(plan.scope).map((command) => `npm run ${command}`), "git diff --check"]);
  for (const owner of owners) {
    const route = routeForId(owner);
    if (route) checks.add(route.check);
  }
  const docsChanged = files.filter((file) => file.endsWith(".md"));
  const pythonSourceImpact = files.some((file) => /^python\/(?:spireagent|stpd)\//u.test(file)
    && !/\.md$/iu.test(file));
  const componentSourceImpact = files.some((file) => {
    const route = routeForPath(file);
    return route?.prefix.startsWith("components/") || route?.prefix.startsWith("apps/");
  });
  const statusImpact = pythonSourceImpact || anyMatch(files, [
    /^components\//u, /^apps\//u, /^contracts\//u, /^platform-bom\.json$/u, /^docs\/evidence\//u
  ]);
  const adrImpact = anyMatch(files, [
    /AGENTS\.md$/u, /^docs\/ARCHITECTURE\.md$/u, /^docs\/COMPONENTS\.md$/u, /^contracts\//u
  ]);
  const identityImpact = pythonSourceImpact || componentSourceImpact || anyMatch(files, [
    /^contracts\//u, /package\.json$/u, /pyproject\.toml$/u, /\.csproj$/u, /^platform-bom\.json$/u
  ]);
  const evidenceImpact = pythonSourceImpact || anyMatch(files, [
    /^components\/(?:native-foundation|connector|host-runtime|annotator|evidence|policy-runtime)\//u,
    /^apps\/game-mod\//u,
    /^docs\/(?:STATUS|TESTING)\.md$/u,
    /^docs\/evidence\//u
  ]);
  const governanceImpact = anyMatch(files, [
    /AGENTS\.md$/u,
    /^\.agents\//u,
    /^docs\/(?:NEW_ENGINEER_GUIDE|PROJECT_SYSTEM|DOCUMENT_MAP|DEVELOPMENT_WORKFLOW)\.md$/u,
    /^tools\/project-system/u,
    /^\.github\//u,
    /^package(?:-lock)?\.json$/u
  ]);
  const lines = [
    "# Project closeout review",
    "",
    `- Changed files: ${files.length}`,
    `- Owning components/layers: ${owners.length ? owners.join(", ") : "none detected"}`,
    "",
    "## Likely checks",
    "",
    `Selected check plan: ${plan.scope} (${plan.reason}); docs/TESTING.md owns selection and evidence limits.`,
    "",
    ...[...checks].map((command) => `- ${command}`),
    "",
    "## Review signals",
    "",
    `- Documentation impact: ${docsChanged.length ? `changed (${docsChanged.join(", ")})` : "review whether behavior changes need canonical docs"}`,
    `- STATUS/CURRENT impact: ${statusImpact ? "review required by changed paths" : "not indicated by paths; confirm semantic truth"}`,
    `- ADR impact: ${adrImpact ? "review authority/architecture decision" : "not indicated by paths"}`,
    `- Contract/BOM/version impact: ${identityImpact ? "review exact machine-readable owners" : "not indicated by paths"}`,
    `- Evidence/non-claim impact: ${evidenceImpact ? "review exact evidence level and non-claims" : "portable source/test only unless separately proved"}`,
    `- Agent/project-system governance impact: ${governanceImpact ? "yes; review routing, budgets, and rollback" : "none indicated"}`,
    "- Semantic freshness: human review required; this command never invents or rewrites truth.",
    "- Potential Skill candidate: do not create automatically; record recurrence and use the admission policy in docs/PROJECT_SYSTEM.md.",
    "",
    "## Changed paths",
    "",
    ...(files.length ? files.map((file) => `- ${file}`) : ["- None"]),
    ""
  ];
  return lines.join("\n");
}

function printFindings(label, findings) {
  if (findings.length === 0) return;
  console.error(`${label} (${findings.length}):`);
  for (const item of findings) console.error(`- [${item.code}] ${item.file}: ${item.message}`);
}

function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === "context") {
    process.stdout.write(formatContext(root, parseContextArgs(args)));
    return;
  }
  if (command === "check") {
    if (args.length > 0) throw new Error(`check accepts no arguments: ${args.join(" ")}`);
    const errors = collectProjectSystemFindings(root);
    const warnings = freshnessWarnings(root);
    printFindings("Project-system warnings", warnings);
    if (errors.length > 0) {
      printFindings("Project-system errors", errors);
      process.exitCode = 1;
      return;
    }
    console.log(`project system passed with ${warnings.length} warning(s)`);
    return;
  }
  if (command === "closeout") {
    if (args.length > 0) throw new Error(`closeout accepts no arguments: ${args.join(" ")}`);
    process.stdout.write(formatCloseout(root));
    return;
  }
  throw new Error("usage: project-system.mjs <context|check|closeout> [--component <name>]");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
