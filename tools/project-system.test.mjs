import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  AGENT_CHAIN_BUDGET_BYTES,
  agentBudgetFindings,
  agentReferenceFindings,
  documentedCommandFindings,
  formatContext,
  formatCloseout,
  markdownLinkFindings,
  projectIntegrityFindings,
  skillFindings
} from "./project-system.mjs";

function fixture() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "sts2-project-system-"));
}

function write(root, relative, contents) {
  const file = path.join(root, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, contents);
}

function git(root, ...args) {
  return execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
}

function initializeGit(root) {
  git(root, "init", "-b", "develop");
  git(root, "config", "user.email", "project-system@example.invalid");
  git(root, "config", "user.name", "Project System Test");
  write(root, "README.md", "baseline\n");
  write(root, "AGENTS.md", "Root instructions.\n");
  git(root, "add", ".");
  git(root, "commit", "-m", "fixture");
  git(root, "update-ref", "refs/remotes/origin/develop", "HEAD");
}

test("application and research context includes only its ordered root, Python ancestor and leaf", () => {
  for (const [component, leaf, sibling] of [
    ["project-apps", "python/spireagent/AGENTS.md", "python/stpd/AGENTS.md"],
    ["research", "python/stpd/AGENTS.md", "python/spireagent/AGENTS.md"]
  ]) {
    const root = fixture();
    try {
      const instructions = [
        ["AGENTS.md", "Root 雪.\n"],
        ["python/AGENTS.md", "Python ancestor.\n"],
        [leaf, "Owning leaf.\n"]
      ];
      for (const [file, contents] of instructions) write(root, file, contents);
      write(root, sibling, "Unrelated sibling.\n");
      const output = formatContext(root, { component });
      const bytes = instructions.reduce((total, [, contents]) => total + Buffer.byteLength(contents), 0) + 4;
      assert.match(output, new RegExp(`Instruction chain: ${bytes} / ${AGENT_CHAIN_BUDGET_BYTES} bytes`, "u"));
      assert.ok(output.includes(`${instructions.map(([file]) => `- ${file}`).join("\n")}\n`));
      assert.ok(!output.includes(`- ${sibling}\n`));
      assert.match(output, /^- README\.md$/mu);
      assert.match(output, /^- docs\/TESTING\.md$/mu);
      assert.match(output, /^- python\/docs\/DOCUMENT_MAP\.md$/mu);
      assert.doesNotMatch(output, /MONOREPO_MIGRATION|FULLRUN_RESEARCH/u);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
});

test("context and budget findings agree when ancestor separators cross the byte limit", () => {
  const root = fixture();
  try {
    write(root, "AGENTS.md", "x".repeat(AGENT_CHAIN_BUDGET_BYTES - 8));
    write(root, "python/AGENTS.md", "1234");
    write(root, "python/spireagent/AGENTS.md", "5678");
    const output = formatContext(root, { component: "project-apps" });
    const bytes = AGENT_CHAIN_BUDGET_BYTES + 4;
    assert.match(output, new RegExp(`Instruction chain: ${bytes} / ${AGENT_CHAIN_BUDGET_BYTES} bytes`, "u"));
    assert.deepEqual(agentBudgetFindings(root), [{
      code: "agents-instruction-budget-exceeded",
      file: "python/spireagent/AGENTS.md",
      message: `${bytes} > ${AGENT_CHAIN_BUDGET_BYTES} bytes`
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("context and closeout use the actual committed planner rather than unconditional full checks", () => {
  const root = fixture();
  try {
    initializeGit(root);
    write(root, "README.md", "edited regular prose\n");
    git(root, "add", "README.md");
    git(root, "commit", "-m", "editorial fixture");
    for (const output of [formatContext(root), formatCloseout(root)]) {
      assert.match(output, /docs \(regular_prose_surfaces_only\)/u);
      assert.match(output, /^- npm run check:plan -- --base origin\/develop --run$/mu);
      assert.match(output, /Planner will run: npm run check:docs; do not repeat these commands separately\./u);
      assert.doesNotMatch(output, /^- npm run check(?::docs)?$/mu);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("dirty edits retain the planner's conservative full selection", () => {
  const root = fixture();
  try {
    initializeGit(root);
    write(root, "README.md", "uncommitted prose\n");
    const output = formatCloseout(root);
    assert.match(output, /full \(dirty_worktree\)/u);
    assert.match(output, /Planner will run: npm run check; do not repeat these commands separately\./u);
    assert.doesNotMatch(output, /^- npm run check$/mu);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("final execution appears once while focused development checks are explicitly optional", () => {
  const root = fixture();
  try {
    initializeGit(root);
    write(root, "python/spireagent/workbench/server.py", "# source fixture\n");
    for (const output of [formatContext(root, { component: "project-apps" }), formatCloseout(root)]) {
      const section = output.split("## Final candidate checks\n\n")[1]?.split("\n## ")[0];
      assert.ok(section);
      assert.deepEqual(section.split("\n").filter(line => line.startsWith("- ")), [
        "- npm run check:plan -- --base origin/develop --run"
      ]);
      assert.equal(output.match(/^- npm run check:plan -- --base origin\/develop --run$/gmu)?.length, 1);
      assert.match(output, /## Optional focused development checks/u);
      assert.match(output, /Choose only as useful while editing; these are not additional final candidate steps\./u);
      assert.match(output, /^- npm run check:python$/mu);
      assert.match(output, /^- npm run project:check$/mu);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Workbench and Live UI source changes prompt evidence review without asserting runtime proof", () => {
  for (const file of ["apps/workbench/src/workbench-service.mjs", "apps/ingame-ui/PlatformLiveStatusClient.cs"]) {
    const root = fixture();
    try {
      initializeGit(root);
      write(root, file, "// source fixture\n");
      const output = formatCloseout(root);
      assert.match(output, /Evidence\/non-claim impact: review exact evidence level and non-claims/u);
      assert.match(output, /Contract\/BOM\/version impact: review exact machine-readable owners/u);
      assert.match(output, /STATUS\/CURRENT impact: review required by changed paths/u);
      assert.match(output, /Semantic freshness: human review required/u);
      assert.doesNotMatch(output, /runtime qualified|installed verified/u);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
});

test("Python production sources prompt status, identity and evidence review without a package edit", () => {
  for (const file of ["python/spireagent/workbench/server.py", "python/stpd/policy/native_agent.py"]) {
    const root = fixture();
    try {
      initializeGit(root);
      write(root, file, "# source fixture\n");
      const output = formatCloseout(root);
      assert.match(output, /STATUS\/CURRENT impact: review required by changed paths/u);
      assert.match(output, /Contract\/BOM\/version impact: review exact machine-readable owners/u);
      assert.match(output, /Evidence\/non-claim impact: review exact evidence level and non-claims/u);
      assert.match(output, /Semantic freshness: human review required/u);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
});

test("component source edits prompt source identity review without a package edit", () => {
  const root = fixture();
  try {
    initializeGit(root);
    write(root, "components/policy-runtime/src/runtime.ts", "// source fixture\n");
    const output = formatCloseout(root);
    assert.match(output, /Contract\/BOM\/version impact: review exact machine-readable owners/u);
    assert.match(output, /STATUS\/CURRENT impact: review required by changed paths/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("unrelated prose does not acquire production or runtime evidence implications", () => {
  const root = fixture();
  try {
    initializeGit(root);
    write(root, "README.md", "unrelated editorial change\n");
    write(root, "python/docs/notes.md", "ordinary documentation\n");
    const output = formatCloseout(root);
    assert.match(output, /STATUS\/CURRENT impact: not indicated by paths; confirm semantic truth/u);
    assert.match(output, /Contract\/BOM\/version impact: not indicated by paths/u);
    assert.match(output, /Evidence\/non-claim impact: portable source\/test only unless separately proved/u);
    assert.doesNotMatch(output, /STATUS\/CURRENT impact: review required/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("broken internal Markdown links fail deterministically", () => {
  const root = fixture();
  try {
    write(root, "docs/guide.md", "Read [missing](missing.md).\n");
    assert.deepEqual(markdownLinkFindings(root), [{
      code: "markdown-target-missing",
      file: "docs/guide.md",
      message: "missing.md"
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("missing DOCUMENT_MAP routes receive the routing-specific failure", () => {
  const root = fixture();
  try {
    write(root, "docs/DOCUMENT_MAP.md", "[Missing](missing.md)\n");
    assert.equal(markdownLinkFindings(root)[0]?.code, "document-map-route-missing");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("missing AGENTS path references fail", () => {
  const root = fixture();
  try {
    write(root, "AGENTS.md", "Read `docs/missing.md` before work.\n");
    assert.deepEqual(agentReferenceFindings(root), [{
      code: "agents-reference-missing",
      file: "AGENTS.md",
      message: "docs/missing.md"
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("missing files referenced by a Skill fail", () => {
  const root = fixture();
  try {
    write(root, ".agents/skills/example/SKILL.md", [
      "---",
      "name: example",
      "description: Run a bounded example workflow.",
      "---",
      "",
      "Read [the contract](references/contract.md).",
      ""
    ].join("\n"));
    assert.equal(skillFindings(root).length, 0);
    assert.deepEqual(markdownLinkFindings(root), [{
      code: "markdown-target-missing",
      file: ".agents/skills/example/SKILL.md",
      message: "references/contract.md"
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Skill frontmatter is stable across CRLF Windows checkout", () => {
  const root = fixture();
  try {
    write(root, ".agents/skills/example/SKILL.md", [
      "---",
      "name: example",
      "description: Run a bounded example workflow.",
      "---",
      "",
      "Instructions.",
      ""
    ].join("\r\n"));
    assert.deepEqual(skillFindings(root), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a repository Skill directory requires a SKILL.md entrypoint", () => {
  const root = fixture();
  try {
    write(root, ".agents/skills/missing-entrypoint/agents/openai.yaml", "interface: {}\n");
    assert.deepEqual(skillFindings(root), [{
      code: "skill-entrypoint-missing",
      file: ".agents/skills/missing-entrypoint",
      message: "SKILL.md"
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("documented npm run commands must exist in the owning package", () => {
  const root = fixture();
  try {
    write(root, "package.json", '{"scripts":{"check":"node ok.mjs"}}\n');
    write(root, "README.md", "```bash\nnpm run absent\n```\n");
    assert.deepEqual(documentedCommandFindings(root), [{
      code: "documented-command-missing",
      file: "README.md",
      message: "npm run absent"
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("AGENTS chains above the project budget fail", () => {
  const root = fixture();
  try {
    write(root, "AGENTS.md", "x".repeat(AGENT_CHAIN_BUDGET_BYTES + 1));
    const findings = agentBudgetFindings(root);
    assert.equal(findings.length, 1);
    assert.equal(findings[0].code, "agents-instruction-budget-exceeded");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("invalid Skill metadata and explicit-only policy fail", () => {
  const root = fixture();
  try {
    write(root, ".agents/skills/invalid-name/SKILL.md", [
      "---",
      "name: wrong-name",
      "description: Maintain a repository Skill.",
      "---",
      "",
      "Instructions.",
      ""
    ].join("\n"));
    write(root, ".agents/skills/repo-skill-maintenance/SKILL.md", [
      "---",
      "name: repo-skill-maintenance",
      "description: Maintain a repository Skill when explicitly invoked.",
      "---",
      "",
      "Instructions.",
      ""
    ].join("\n"));
    write(root, ".agents/skills/repo-skill-maintenance/agents/openai.yaml", [
      "policy:",
      "  allow_implicit_invocation: true",
      ""
    ].join("\n"));
    const codes = skillFindings(root).map((item) => item.code);
    assert.ok(codes.includes("skill-name-invalid"));
    assert.ok(codes.includes("skill-invocation-policy-invalid"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("project-system command and portable-check corruption fail", () => {
  const root = fixture();
  try {
    write(root, "package.json", JSON.stringify({ scripts: { check: "node other.mjs" } }));
    const codes = projectIntegrityFindings(root).map((item) => item.code);
    assert.ok(codes.includes("project-system-file-missing"));
    assert.ok(codes.includes("project-system-command-invalid"));
    assert.ok(codes.includes("project-system-check-not-portable"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("closeout reports semantic review instead of rewriting truth", () => {
  const root = fixture();
  try {
    git(root, "init", "-b", "develop");
    git(root, "config", "user.email", "project-system@example.invalid");
    git(root, "config", "user.name", "Project System Test");
    write(root, "README.md", "baseline\n");
    git(root, "add", ".");
    git(root, "commit", "-m", "fixture");
    write(root, "README.md", "changed\n");
    const output = formatCloseout(root);
    assert.match(output, /Semantic freshness: human review required/u);
    assert.match(output, /README\.md/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
