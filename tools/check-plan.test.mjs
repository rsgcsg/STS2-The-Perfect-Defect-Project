import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { classifyChanges, parseDiff, makePlan, aggregatePassed, scopeCommands, parseRawDiff } from "./check-plan.mjs";

test("only modified editorial surfaces select docs; additions, deletion and rename stay full", () => {
  assert.equal(classifyChanges([{ status: "M", file: "README.md", oldMode: "100644", newMode: "100644" }]).scope, "docs");
  for (const file of ["AGENTS.md", "docs/TESTING.md", "docs/adr/example.md", "components/annotator/docs/DATA_CONTRACT.md", "python/uv.lock", ".github/workflows/ci.yml", "tools/check-plan.mjs", "new.md"]) {
    assert.equal(classifyChanges([{ status: "M", file }]).scope, "full", file);
  }
  for (const status of ["A", "D", "T", "R100"]) assert.equal(classifyChanges([{ status, file: "README.md" }]).scope, "full");
  assert.equal(classifyChanges([]).scope, "full");
});

test("NUL diff parsing preserves unusual names and refuses ambiguous records", () => {
  assert.deepEqual(parseDiff("M\0docs/name\nwith spaces.md\0"), [{ status: "M", file: "docs/name\nwith spaces.md" }]);
  for (const raw of ["M\0README.md", "M\0", "R100\0a\0b\0"]) assert.throws(() => parseDiff(raw));
});

test("exact Git comparison works, dirty or missing provenance fails closed", () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "check-plan-"));
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: "pipe" }).trim();
  try {
    git("init"); git("config", "user.email", "test@example.invalid"); git("config", "user.name", "Test");
    fs.writeFileSync(path.join(cwd, "README.md"), "old\n"); git("add", "."); git("commit", "-m", "base");
    const base = git("rev-parse", "HEAD");
    fs.writeFileSync(path.join(cwd, "README.md"), "new\n"); git("commit", "-am", "docs");
    assert.equal(makePlan({ base, cwd }).scope, "docs");
    assert.equal(makePlan({ base, cwd, forceFull: true }).scope, "full");
    assert.equal(makePlan({ base: "missing", cwd }).reason, "diff_unavailable");
    fs.writeFileSync(path.join(cwd, "untracked.js"), "change");
    assert.equal(makePlan({ base, cwd }).reason, "dirty_worktree");
  } finally { fs.rmSync(cwd, { recursive: true, force: true }); }
});

test("required aggregate rejects missing, failed, cancelled or unexpectedly skipped work", () => {
  const full = { plan: "success", docs: "skipped", linux: "success", windows: "success" };
  const docs = { plan: "success", docs: "success", linux: "skipped", windows: "skipped" };
  assert.ok(aggregatePassed("full", full)); assert.ok(aggregatePassed("docs", docs));
  for (const key of ["plan", "linux", "windows"]) for (const value of ["skipped", "cancelled", "failure", ""]) assert.equal(aggregatePassed("full", { ...full, [key]: value }), false);
  for (const key of ["plan", "docs"]) for (const value of ["skipped", "cancelled", "failure", ""]) assert.equal(aggregatePassed("docs", { ...docs, [key]: value }), false);
  assert.equal(aggregatePassed("unknown", full), false);
});

test("Python owner changes retain repository guards and both OS Python consumers", () => {
  for (const file of ["python/spireagent/hub/exports.py", "python/stpd/models/new.py", "python/tests/new.py"])
    for (const status of ["M", "A", "D"]) assert.equal(classifyChanges([{file,status}]).scope, "python");
  for (const file of ["python/pyproject.toml", "python/uv.lock", "python/tools/project.py", "components/evidence/src/a.py", "python/deploy/hub/Dockerfile", "contracts/new.json"])
    assert.equal(classifyChanges([{file,status:"M"}, {file:"python/stpd/a.py",status:"M"}]).scope, "full");
  assert.deepEqual(scopeCommands("python"), ["check:python-scope"]);
  assert.deepEqual(scopeCommands("full"), ["check"]);
  assert.deepEqual(scopeCommands("reuse"), ["check:repository"]);
  assert.throws(() => scopeCommands("typo"));
});
test("reuse requires fresh repository checks; Python scope still requires Windows", () => {
  const reused = {plan:"success",docs:"success",linux:"skipped",windows:"skipped"};
  assert.ok(aggregatePassed("reuse",reused));
  for (const key of ["plan","docs"]) assert.equal(aggregatePassed("reuse",{...reused,[key]:"failure"}),false);
  assert.equal(aggregatePassed("python",reused),false);
  assert.ok(aggregatePassed("python",{...reused,docs:"skipped",linux:"success",windows:"success"}));
});

test("real repair-shaped reports do not force unrelated Platform suites", () => {
  const source = {file:"python/spireagent/hub/exports.py",status:"M"};
  const report = [{file:"docs/STATUS.md",status:"M"}, {file:"docs/memory/CURRENT.md",status:"M"},
    {file:"docs/evidence/EXPORT.md",status:"A"}, {file:"python/docs/PROJECT_CONSOLE.md",status:"M"}];
  assert.equal(classifyChanges([source,...report]).scope,"python");
  assert.equal(classifyChanges(report).scope,"full");
  for(const entry of [{file:"docs/evidence/EXPORT.md",status:"D"},
    {file:"docs/adr/new.md",status:"A"}, {file:"python/spireagent/AGENTS.md",status:"M"},
    {file:"python/docs/adr/new.md",status:"A"}, {file:"docs/evidence/subdir/code.js",status:"A"}])
    assert.equal(classifyChanges([source,entry]).scope,"full");
});

const proseEntry = (file, status = "M") => ({file, status,
  oldMode: status === "A" ? "000000" : "100644", newMode: "100644"});

test("explicit regular prose additions and modifications retain fresh repository guards", () => {
  for (const file of ["docs/design/new.md", "docs/plans/new.md", "docs/evidence/new.md", "docs/memory/CURRENT.md"])
    for (const status of ["A", "M"]) assert.equal(classifyChanges([proseEntry(file,status)]).scope,"docs");
  const scripts = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url))).scripts;
  assert.deepEqual(scopeCommands("docs"), ["check:docs"]);
  assert.equal(scripts["check:docs"], "npm run check:repository && git diff --check");
  for (const guard of ["project:check", "check:governance", "check:ci", "check:identity", "check:bom", "check:boundaries", "check:history"])
    assert.ok(scripts["check:repository"].includes(`npm run ${guard}`), guard);
});

test("extension, missing modes, deletion, protected prose and executable mixtures stay full", () => {
  for (const file of ["docs/design/tool.js", "docs/evidence/manifest.json", "docs/design/AGENTS.md", "docs/plans/SKILL.md", "docs/adr/new.md", "docs/TESTING.md", "python/docs/new.md", "components/connector/docs/new.md", "docs/design/nested/new.md"])
    assert.equal(classifyChanges([proseEntry(file,"A")]).scope,"full",file);
  assert.equal(classifyChanges([{file:"docs/design/new.md",status:"A"}]).scope,"full");
  assert.equal(classifyChanges([{...proseEntry("docs/design/new.md"),status:"D",newMode:"000000"}]).scope,"full");
  for(const file of ["tools/new.py", "contracts/new.json", "python/uv.lock", "tools/check-plan.mjs"])
    assert.equal(classifyChanges([proseEntry("docs/design/new.md"),proseEntry(file)]).scope,"full");
  for(const newMode of ["120000","100755","160000"])
    assert.equal(classifyChanges([{...proseEntry("docs/design/new.md","A"),newMode}]).scope,"full");
});

test("raw committed modes prevent a Markdown symlink, type replacement and cross-scope rename from taking docs", () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "check-plan-mode-"));
  const git = (...args) => execFileSync("git", args, {cwd,encoding:"utf8",stdio:"pipe"}).trim();
  try {
    git("init"); git("config","user.email","test@example.invalid"); git("config","user.name","Test");
    fs.mkdirSync(path.join(cwd,"docs/design"),{recursive:true});
    fs.writeFileSync(path.join(cwd,"docs/design/base.md"),"base\n");
    git("add","."); git("commit","-m","base"); const base=git("rev-parse","HEAD");
    fs.writeFileSync(path.join(cwd,"docs/design/new.md"),"new\n");
    git("add","."); git("commit","-m","added prose");
    assert.equal(makePlan({base,cwd}).scope,"docs");
    fs.appendFileSync(path.join(cwd,"docs/design/new.md"),"edit\n");
    git("commit","-am","modified prose");
    assert.equal(makePlan({base,cwd}).scope,"docs");
    git("reset","--hard",base);
    // Git index construction also works on Windows without symlink privileges.
    fs.writeFileSync(path.join(cwd,"target"),"target\n");
    const blob=git("hash-object","-w","target"); fs.unlinkSync(path.join(cwd,"target"));
    git("update-index","--add","--cacheinfo",`120000,${blob},docs/design/link.md`);
    git("commit","-m","symlink");
    git("checkout-index","-f","-a");
    assert.equal(makePlan({base,cwd}).reason,"non_regular_or_executable_change");
    git("reset","--hard",base);
    fs.renameSync(path.join(cwd,"docs/design/base.md"),path.join(cwd,"renamed.md"));
    git("add","-A"); git("commit","-m","cross-scope rename");
    assert.equal(makePlan({base,cwd}).scope,"full");
    git("reset","--hard",base);
    git("update-index","--cacheinfo",`120000,${blob},docs/design/base.md`);
    git("commit","-m","type replacement"); git("checkout-index","-f","-a");
    assert.equal(makePlan({base,cwd}).reason,"non_regular_or_executable_change");
  } finally {fs.rmSync(cwd,{recursive:true,force:true});}
});

test("raw parser rejects ambiguous modes and preserves unusual file names", () => {
  const sha="a".repeat(40);
  assert.deepEqual(parseRawDiff(`:000000 100644 ${sha} ${sha} A\0docs/design/a\n b.md\0`),[proseEntry("docs/design/a\n b.md","A")]);
  for(const raw of [`:0 100644 ${sha} ${sha} A\0a\0`,`:100644 100644 ${sha} ${sha} R100\0a\0b\0`,":bad\0a\0"])
    assert.throws(()=>parseRawDiff(raw));
});

test("protected instruction names cannot qualify as Python report companions", () => {
  const source = proseEntry("python/stpd/model.py");
  for (const name of ["AGENTS.md", "SKILL.md", "agents.md", "Skill.MD"]) {
    const instruction = proseEntry(`docs/evidence/${name}`);
    assert.equal(classifyChanges([source, instruction]).scope, "full", name);
    assert.equal(classifyChanges([instruction]).scope, "full", name);
  }
});
