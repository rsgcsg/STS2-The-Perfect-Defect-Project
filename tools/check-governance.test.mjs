import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  adrIndexFindings,
  currentContextFindings,
  durableTruthFindings,
  governanceFindings,
  packageFindings,
  pullRequestTemplatePathFindings,
  skillIndexFindings
} from "./check-governance.mjs";

function fixture() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "sts2-governance-"));
}

function write(root, relative, contents) {
  const file = path.join(root, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, contents);
}

function validFixture(root) {
  const governance = "# Governance\n";
  const pr = [
    "Change class (`G0`-`G6`)",
    "Owning fact / layer",
    "latest head SHA",
    "Test shape",
    "Evidence level actually proved",
    "Merge method",
    "Remaining non-claims",
    ""
  ].join("\n");
  write(root, "docs/ENGINEERING_GOVERNANCE.md", governance);
  write(root, "docs/adr/0001-example.md", "# ADR\n");
  write(root, "docs/adr/README.md", "[ADR](0001-example.md)\n");
  write(root, ".agents/skills/example/SKILL.md", "---\nname: example\ndescription: Example.\n---\n");
  write(root, ".agents/skills/README.md", "[example](example/SKILL.md)\n");
  write(root, ".github/PULL_REQUEST_TEMPLATE.md", pr);
  write(root, "tools/check-governance.mjs", "export {};\n");
  write(root, "tools/check-governance.test.mjs", "export {};\n");
  write(root, "AGENTS.md", "docs/ENGINEERING_GOVERNANCE.md\n");
  write(root, "README.md", "docs/ENGINEERING_GOVERNANCE.md .agents/skills/README.md\n");
  write(root, "docs/DOCUMENT_MAP.md", "ENGINEERING_GOVERNANCE.md adr/README.md ../.agents/skills/README.md\n");
  write(root, "docs/NEW_ENGINEER_GUIDE.md", "ENGINEERING_GOVERNANCE.md G0 G6\n");
  write(root, "CONTRIBUTING.md", "docs/ENGINEERING_GOVERNANCE.md\n");
  write(root, "docs/memory/CURRENT.md", [
    "Resolve live GitHub refs before work.",
    "Current authority sources override this file.",
    "Remaining Platform non-claims",
    ""
  ].join("\n"));
  write(root, "package.json", JSON.stringify({
    scripts: {
      "check:governance": "node --test tools/check-governance.test.mjs && node tools/check-governance.mjs",
      check: "npm run check:governance"
    }
  }));
}

test("a complete governance fixture passes", () => {
  const root = fixture();
  try {
    validFixture(root);
    assert.deepEqual(governanceFindings(root), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("case-variant pull request template paths cannot coexist", () => {
  const findings = pullRequestTemplatePathFindings([
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md"
  ]);
  assert.equal(findings[0]?.code, "pull-request-template-path-invalid");
  assert.deepEqual(
    pullRequestTemplatePathFindings([".github/PULL_REQUEST_TEMPLATE.md"]),
    []
  );
});

test("ADR and Skill indexes must enumerate exact current entrypoints once", () => {
  const root = fixture();
  try {
    write(root, "docs/adr/0001-example.md", "# ADR\n");
    write(root, "docs/adr/README.md", "No index.\n");
    write(root, ".agents/skills/example/SKILL.md", "---\nname: example\ndescription: Example.\n---\n");
    write(root, ".agents/skills/README.md", "No index.\n");
    assert.equal(adrIndexFindings(root)[0]?.code, "adr-index-invalid");
    assert.equal(skillIndexFindings(root)[0]?.code, "skill-index-invalid");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("durable governance docs reject mutable PR and SHA state", () => {
  const root = fixture();
  try {
    write(root, "docs/ENGINEERING_GOVERNANCE.md", "PR #42 at develop@abcdef and 0123456789012345678901234567890123456789\n");
    const codes = durableTruthFindings(root).map((item) => item.code);
    assert.ok(codes.includes("governance-mutable-identity"));
    assert.ok(codes.includes("governance-mutable-work-item"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the bounded current handoff must keep its safety disclaimer and size budget", () => {
  const root = fixture();
  try {
    write(root, "docs/memory/CURRENT.md", "stale snapshot\n");
    const codes = currentContextFindings(root).map((item) => item.code);
    assert.ok(codes.includes("current-context-safety-missing"));
    write(root, "docs/memory/CURRENT.md", [
      "Resolve live GitHub refs before work.",
      "Current sources override this file.",
      "Remaining Platform non-claims",
      "x".repeat(9 * 1024)
    ].join("\n"));
    assert.ok(currentContextFindings(root).some((item) => item.code === "current-context-oversized"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the root portable gate must compose the exact governance command", () => {
  const root = fixture();
  try {
    write(root, "package.json", JSON.stringify({ scripts: { check: "node other.mjs" } }));
    const codes = packageFindings(root).map((item) => item.code);
    assert.ok(codes.includes("governance-command-invalid"));
    assert.ok(codes.includes("governance-check-not-portable"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("CURRENT capacity measures final physical UTF-8 bytes including CRLF", () => {
  const root = fixture();
  try {
    const header = "Resolve live GitHub refs. Current sources override this file. Remaining Platform non-claims\n";
    const boundary = header + "x".repeat(8192 - Buffer.byteLength(header));
    write(root, "docs/memory/CURRENT.md", boundary);
    assert.deepEqual(currentContextFindings(root), []);
    const crlf = boundary.replace(/\n/gu, "\r\n");
    write(root, "docs/memory/CURRENT.md", crlf);
    const oversized = currentContextFindings(root).find(item => item.code === "current-context-oversized");
    assert.ok(oversized, "normalizing line endings must not hide a physical byte above the limit");
    assert.match(oversized.message, /8193.*8192/u);
    const multibyte = header + "雪".repeat(2702);
    assert.ok(multibyte.length < 8192 && Buffer.byteLength(multibyte) > 8192);
    write(root, "docs/memory/CURRENT.md", multibyte);
    assert.ok(currentContextFindings(root).some(item => item.code === "current-context-oversized"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
