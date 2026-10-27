import assert from "node:assert/strict";
import fs from "node:fs";
import { once } from "node:events";
import { createInterface } from "node:readline";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import test from "node:test";
import { classifyChanges, parseDiff, makePlan, aggregatePassed, scopeCommands, parseRawDiff, checkCommittedPatch } from "./check-plan.mjs";

// These CLI fixtures run marker-writing children, not heavyweight checks. Give
// only their planner process a deterministic filesystem observation; production
// capacity admission and its low-space rejection tests remain unchanged.
const fixtureCapacity = "data:text/javascript," + encodeURIComponent(
  "import fs from 'node:fs'; fs.statfsSync = () => ({bavail: 64n * 1024n ** 3n, bsize: 1n});");

test("only modified editorial surfaces select docs; additions, deletion and rename stay full", () => {
  assert.equal(classifyChanges([{ status: "M", file: "README.md", oldMode: "100644", newMode: "100644" }]).scope, "docs");
  for (const file of ["AGENTS.md", "docs/TESTING.md", "docs/adr/example.md", "components/annotator/docs/DATA_CONTRACT.md", "python/uv.lock", "python/tools/pytest-file-costs.json", ".github/workflows/ci.yml", "tools/check-plan.mjs", "new.md"]) {
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

test("hosted planning rejects committed EOF whitespace before publishing a lane or executing it", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "check-plan-hygiene-"));
  const cwd = path.join(temporary, "repo");
  fs.mkdirSync(cwd);
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: "pipe" }).trim();
  const output = path.join(temporary, "route.outputs");
  const marker = path.join(temporary, "expensive.started");
  const environment = { ...process.env, GITHUB_ACTIONS: "false", GITHUB_OUTPUT: output,
    TEST_EXPENSIVE_MARKER: marker, CHECK_FULL: "false", SPIREAGENT_MIN_FREE_BYTES: "2147483648" };
  delete environment.GH_TOKEN;
  delete environment.GITHUB_STEP_SUMMARY;
  const cli = (...args) => spawnSync(process.execPath, ["--import", fixtureCapacity, "tools/check-plan.mjs", ...args], {
    cwd, env: environment, encoding: "utf8",
  });
  try {
    fs.cpSync(path.resolve(import.meta.dirname), path.join(cwd, "tools"), { recursive: true });
    fs.mkdirSync(path.join(cwd, ".github/workflows"), { recursive: true });
    fs.writeFileSync(path.join(cwd, ".github/workflows/ci.yml"), "name: fixture\n");
    fs.writeFileSync(path.join(cwd, "runner.mjs"),
      "import fs from 'node:fs'; fs.writeFileSync(process.env.TEST_EXPENSIVE_MARKER, 'started');\n");
    fs.writeFileSync(path.join(cwd, "package.json"), JSON.stringify({ scripts: {
      "check:docs": "node runner.mjs", "check:repository": "node runner.mjs", "check:python-scope": "node runner.mjs",
      "check:preflight": "node -e 0",
      "precheck:s0": "node runner.mjs", "check:s0-tests": "node runner.mjs", "check:python": "node runner.mjs",
      check: "node runner.mjs",
    } }));
    fs.writeFileSync(path.join(cwd, ".gitignore"), ".local/\n");
    for (const owner of ["components/connector", "components/host-runtime", "components/annotator",
      "components/evidence", "components/policy-runtime", "apps/workbench", "apps/ingame-ui", "apps/game-mod"]) {
      fs.mkdirSync(path.join(cwd, owner), {recursive: true});
      fs.writeFileSync(path.join(cwd, owner, "package.json"), JSON.stringify({scripts: {
        check: "node ../../runner.mjs", test: "node ../../runner.mjs",
      }}));
    }
    const scopes = [["docs", "README.md"], ["repository", "tools/project-system.test.mjs"], ["python", "python/stpd/fixture.py"],
      ["full", "components/fixture.txt"]];
    for (const [, file] of scopes) {
      fs.mkdirSync(path.dirname(path.join(cwd, file)), { recursive: true });
      fs.writeFileSync(path.join(cwd, file), "base\n");
    }
    git("init"); git("config", "user.email", "test@example.invalid");
    git("config", "user.name", "Test"); git("config", "core.whitespace", "blank-at-eof");
    git("add", "."); git("commit", "-m", "base"); const base = git("rev-parse", "HEAD");
    for (const [scope, file, reuseEligible] of [...scopes, ["full", "components/fixture.txt", true]])
      for (const bad of [true, false]) {
      git("reset", "--hard", base);
      fs.rmSync(output, { force: true }); fs.rmSync(marker, { force: true });
      fs.writeFileSync(path.join(cwd, file), bad ? "changed\n\n" : "changed\n");
      git("commit", "-am", "selected patch");
      environment.GITHUB_ACTIONS = reuseEligible ? "true" : "false";
      environment.GITHUB_EVENT_NAME = "push";
      environment.GITHUB_REF = "refs/heads/develop";
      environment.GITHUB_REPOSITORY = "fixture/repository";
      assert.equal(git("status", "--porcelain"), "");
      assert.equal(git("diff", "--check"), ""); // The old lane check missed committed errors.
      const planned = cli("--check-patch", "--base", base);
      if (planned.status === 0) {
        const selected = fs.readFileSync(output, "utf8").match(/^scope=(.+)$/m)?.[1];
        environment.CHECK_SCOPE = selected;
        const executed = cli("execute");
        assert.equal(executed.status, 0, `${scope}: selected lane failed\n${executed.stdout}\n${executed.stderr}`);
      }
      if (bad) {
        assert.notEqual(planned.status, 0, `${scope}: committed bad patch published a lane`);
        assert.match(planned.stdout + planned.stderr, /new blank line at EOF/);
        assert.equal(fs.existsSync(output), false);
        assert.equal(fs.existsSync(marker), false);
      } else {
        assert.equal(planned.status, 0, planned.stderr);
        assert.match(fs.readFileSync(output, "utf8"), new RegExp(`^scope=${scope}$`, "m"));
        assert.equal(fs.readFileSync(marker, "utf8"), "started");
      }
    }
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});

test("committed-patch fallback uses only actual first-parent/root provenance and rejects invalid revisions", () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "check-patch-base-"));
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: "pipe" }).trim();
  try {
    git("init"); git("config", "user.email", "test@example.invalid"); git("config", "user.name", "Test");
    fs.writeFileSync(path.join(cwd, "README.md"), "initial\n");
    git("add", "."); git("commit", "-m", "initial");
    const initial = git("rev-parse", "HEAD");
    const empty = execFileSync("git", ["hash-object", "-t", "tree", "--stdin"], {
      cwd, input: "", encoding: "utf8",
    }).trim();
    assert.deepEqual(checkCommittedPatch({ cwd }), { base: empty, head: initial });
    fs.writeFileSync(path.join(cwd, "README.md"), "next\n"); git("commit", "-am", "next");
    const head = git("rev-parse", "HEAD");
    for (const base of [undefined, "", "0".repeat(40), initial])
      assert.deepEqual(checkCommittedPatch({ base, cwd }), { base: initial, head });
    for (const base of ["missing", "--output=unexpected"])
      assert.throws(() => checkCommittedPatch({ base, cwd }));
    assert.throws(() => checkCommittedPatch({ base: initial, head: "--help", cwd }));
    assert.equal(fs.existsSync(path.join(cwd, "unexpected")), false);
    // A truncated/shallow history is not an initial commit. Retain its declared
    // parent and fail closed if that parent object cannot be verified.
    fs.writeFileSync(path.join(cwd, ".git/shallow"), head + "\n");
    const object = path.join(cwd, ".git/objects", initial.slice(0, 2), initial.slice(2));
    fs.unlinkSync(object);
    assert.throws(() => checkCommittedPatch({ cwd }));
  } finally { fs.rmSync(cwd, { recursive: true, force: true }); }
});

test("required aggregate rejects missing, failed, cancelled or unexpectedly skipped work", () => {
  const full = { repository: "skipped", plan: "success", docs: "skipped", linux: "success", windows: "success" };
  const docs = { repository: "skipped", plan: "success", docs: "success", linux: "skipped", windows: "skipped" };
  assert.ok(aggregatePassed("full", full)); assert.ok(aggregatePassed("docs", docs));
  for (const key of ["plan", "linux"]) for (const value of ["skipped", "cancelled", "failure", ""]) assert.equal(aggregatePassed("full", { ...full, [key]: value }), false);
  for (const key of ["plan", "docs"]) for (const value of ["skipped", "cancelled", "failure", ""]) assert.equal(aggregatePassed("docs", { ...docs, [key]: value }), false);
  assert.equal(aggregatePassed("unknown", full), false);
});

test("Python owner changes retain repository guards and installed Python consumers", () => {
  for (const file of ["python/spireagent/hub/exports.py", "python/stpd/models/new.py", "python/tests/new.py"])
    for (const status of ["M", "A", "D"]) assert.equal(classifyChanges([{file,status}]).scope, "python");
  for (const file of ["python/pyproject.toml", "python/uv.lock", "python/tools/project.py", "components/evidence/src/a.py", "python/deploy/hub/Dockerfile", "contracts/new.json"])
    assert.equal(classifyChanges([{file,status:"M"}, {file:"python/stpd/a.py",status:"M"}]).scope, "full");
  assert.deepEqual(scopeCommands("python"), ["check:python-scope"]);
  assert.deepEqual(scopeCommands("full"), ["check"]);
  assert.deepEqual(scopeCommands("reuse"), ["check:repository"]);
  assert.throws(() => scopeCommands("typo"));
});
test("reuse requires fresh repository checks; Python scope uses only Linux for PRs", () => {
  const reused = {repository:"skipped",plan:"success",docs:"success",linux:"skipped",windows:"skipped"};
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
  for (const file of ["docs/design/tool.js", "docs/evidence/manifest.json", "docs/design/AGENTS.md", "docs/plans/SKILL.md", "docs/adr/new.md", "python/docs/new.md", "components/connector/docs/new.md", "docs/design/nested/new.md"])
    assert.equal(classifyChanges([proseEntry(file,"A")]).scope,"full",file);
  assert.equal(classifyChanges([{file:"docs/design/new.md",status:"A"}]).scope,"full");
  assert.equal(classifyChanges([{...proseEntry("docs/design/new.md"),status:"D",newMode:"000000"}]).scope,"full");
  for(const file of ["tools/new.py", "contracts/new.json", "python/uv.lock", "tools/check-workspace.mjs"])
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


test("management prose uses fresh repository guards, tools use repository scope", () => {
  for (const file of ["AGENTS.md", "docs/TESTING.md", "docs/WORKING_RULES.zh-CN.md",
    "docs/AI_COLLABORATION.md", ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/engineering_task.yml"])
    assert.equal(classifyChanges([proseEntry(file)]).scope, "docs", file);
  for (const file of ["tools/project-system.mjs", "tools/check-plan.mjs", "tools/check-ci-contract.test.mjs"])
    assert.equal(classifyChanges([proseEntry("AGENTS.md"), proseEntry(file)]).scope, "repository", file);
  assert.deepEqual(scopeCommands("repository"), ["check:repository"]);
  for (const file of ["package.json", "package-lock.json", "python/uv.lock", "tools/check-workspace.mjs",
    "contracts/component-identity.json", "components/policy-runtime/src/index.ts", "apps/game-mod/package.json"])
    assert.equal(classifyChanges([proseEntry("docs/TESTING.md"), proseEntry(file)]).scope, "full", file);
  assert.equal(classifyChanges([proseEntry("python/stpd/model.py"), proseEntry("docs/TESTING.md")]).scope, "python");
  for (const status of ["D", "T"]) assert.equal(classifyChanges([proseEntry("AGENTS.md", status)]).scope, "full");
  assert.equal(classifyChanges([proseEntry(".github/workflows/ci.yml")]).scope, "full");
});

test("repository aggregate requires its entire matrix and cannot fabricate a full pass", () => {
  const good = {plan:"success", repository:"success", docs:"skipped", linux:"skipped", windows:"skipped"};
  assert.ok(aggregatePassed("repository", good));
  for (const key of ["plan", "repository"]) for (const result of ["failure", "cancelled", "skipped", "", undefined])
    assert.equal(aggregatePassed("repository", {...good, [key]:result}), false);
  for (const key of ["docs", "linux"]) for (const result of ["success", "failure", "", undefined])
    assert.equal(aggregatePassed("repository", {...good, [key]:result}), false);
  for (const scope of ["full", "python", "reuse", "docs"]) assert.equal(aggregatePassed(scope, good), false);
});

test("real Git workflow comparison bounds repository-only routing and forced full", () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "repository-route-"));
  const git = (...args) => execFileSync("git", args, {cwd, encoding:"utf8", stdio:"pipe"}).trim();
  const source = fs.readFileSync(new URL("../.github/workflows/ci.yml", import.meta.url), "utf8");
  const file = path.join(cwd, ".github/workflows/ci.yml");
  try {
    fs.mkdirSync(path.dirname(file), {recursive:true});
    fs.writeFileSync(file, source); git("init"); git("config","user.email","test@example.invalid");
    git("config","user.name","Test"); git("add","."); git("commit","-m","base");
    const base = git("rev-parse","HEAD");
    fs.writeFileSync(file, source.replace("  docs:\n", "  docs:\n    # repository dependency setup repair\n"));
    git("commit","-am","repository workflow only");
    assert.equal(makePlan({base,cwd}).scope,"repository");
    assert.equal(makePlan({base,cwd,forceFull:true}).scope,"full");
    fs.writeFileSync(file, source.replace("dotnet-version: 9.0.x", "dotnet-version: 10.0.x"));
    git("commit","-am","product build input changes");
    assert.equal(makePlan({base,cwd}).scope,"full");
  } finally {fs.rmSync(cwd,{recursive:true,force:true});}
});


test("Windows outcomes never hold the required PR aggregate", () => {
  for (const windows of ["failure", "cancelled", "skipped", "success", undefined]) {
    assert.ok(aggregatePassed("full", {plan: "success", repository: "skipped", docs: "skipped", linux: "success", windows}));
    assert.ok(aggregatePassed("python", {plan: "success", repository: "skipped", docs: "skipped", linux: "success", windows}));
  }
});

// kill(pid, 0) also succeeds for a terminated Linux zombie. This witness
// checks whether owned descendants can still execute, not whether init reaped a PID.
function cancellationProcessExecuting(pid) {
  try { process.kill(pid, 0); }
  catch (error) { if (error.code === "ESRCH") return false; throw error; }
  if (process.platform !== "linux") return true;
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2).split(" ")[0] !== "Z";
  } catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

test("Linux cancellation witness distinguishes a terminated zombie from an executing owner",
  {skip: process.platform !== "linux", timeout: 10000}, async t => {
    // Any fork-capable helper works; Perl ships with Git on Linux, so this
    // repository-lane test needs no project Python. The child is observed as a
    // zombie (state Z) before its pid is published; nothing reaps it until "reap".
    const owner = spawn("perl", ["-e", `
my $pid = fork; die "fork: $!" unless defined $pid;
if ($pid == 0) { require POSIX; POSIX::_exit(0); }
while (1) {
  open(my $stat, "<", "/proc/$pid/stat") or die "stat: $!";
  my $line = <$stat>; close $stat;
  last if (split / /, substr($line, rindex($line, ")") + 2))[0] eq "Z";
}
$| = 1; print "$pid\n";
my $reap = <STDIN>;
waitpid($pid, 0);
`], {stdio: ["pipe", "pipe", "inherit"]});
    const ended = new Promise(resolve => owner.on("close", resolve));
    const lines = createInterface({input: owner.stdout});
    try {
      const [line] = await once(lines, "line", {signal: t.signal});
      const zombie = Number(line);
      assert.ok(Number.isSafeInteger(zombie) && zombie > 0);
      assert.equal(cancellationProcessExecuting(owner.pid), true);
      assert.doesNotThrow(() => process.kill(zombie, 0));
      assert.equal(cancellationProcessExecuting(zombie), false);
    } finally {
      lines.close(); owner.stdin.end("reap\n"); await ended;
    }
  });

test("top-level full and Python routers cancel their owned descendants and block later gates",
  {skip: process.platform === "win32", timeout: 30000}, async () => {
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "check-plan-cancel-"));
    const cwd = path.join(temporary, "repo");
    fs.mkdirSync(cwd);
    const git = (...args) => execFileSync("git", args, {cwd, stdio: "pipe"});
    const alive = cancellationProcessExecuting;
    try {
      fs.cpSync(import.meta.dirname, path.join(cwd, "tools"), {recursive: true});
      fs.writeFileSync(path.join(cwd, ".gitignore"), ".local/\n");
      fs.writeFileSync(path.join(cwd, "package.json"), JSON.stringify({scripts: {
        "check:preflight": "node -e 0", "check:repository": "node runner.mjs", "precheck:s0": "node later.mjs",
      }}));
      fs.writeFileSync(path.join(cwd, "later.mjs"), "import fs from 'node:fs';fs.writeFileSync(process.env.TEST_LATER,'unexpected');");
      fs.writeFileSync(path.join(cwd, "descendant.mjs"), `
        import fs from 'node:fs';
        process.on('SIGINT',()=>{fs.writeFileSync(process.env.TEST_ACK,'INT');process.exit(0)});
        fs.writeFileSync(process.env.TEST_READY, String(process.pid));setInterval(()=>{},1000);
      `);
      fs.writeFileSync(path.join(cwd, "runner.mjs"), `
        import {spawn} from 'node:child_process';
        const child=spawn(process.execPath,['descendant.mjs'],{stdio:'inherit'});
        process.on('SIGINT',()=>{});child.on('exit',()=>process.exit(0));
      `);
      git("init"); git("config", "user.email", "test@example.invalid"); git("config", "user.name", "Test");
      git("add", "."); git("commit", "-m", "fixture");
      for (const [scope, args] of [["full", ["--full", "--run", "--base", "HEAD"]], ["python", ["execute"]]]) {
        const ready = path.join(temporary, `${scope}.ready`), ack = ready + ".ack", later = ready + ".later";
        // Synthetic children write only tiny markers. This is not a real full gate.
        const child = spawn(process.execPath, ["--import", fixtureCapacity, "tools/check-plan.mjs", ...args], {cwd,
          env: {...process.env, CHECK_SCOPE: scope, CHECK_FULL: "false", GITHUB_ACTIONS: "false",
            SPIREAGENT_MIN_FREE_BYTES: "2147483648", TEST_READY: ready, TEST_ACK: ack, TEST_LATER: later},
          stdio: ["ignore", "pipe", "pipe"]});
        let output = ""; child.stdout.on("data", b => output += b); child.stderr.on("data", b => output += b);
        const ended = new Promise(resolve => child.on("exit", (code, signal) => resolve({code, signal})));
        let descendant;
        try {
          for (let n = 0; n < 300 && !fs.existsSync(ready) && child.exitCode === null; n++)
            await new Promise(resolve => setTimeout(resolve, 20));
          assert.ok(fs.existsSync(ready), output);
          descendant = Number(fs.readFileSync(ready, "utf8"));
          child.kill("SIGINT");
          const exit = await ended;
          assert.equal(exit.code, 1, output);
          assert.equal(fs.readFileSync(ack, "utf8"), "INT");
          const executing = alive(descendant);
          let processState = "unavailable";
          if (executing && process.platform === "linux") {
            try { processState = fs.readFileSync(`/proc/${descendant}/stat`, "utf8"); }
            catch (error) { processState = error.code; }
          }
          assert.equal(executing, false, `owned descendant still executing: ${descendant}; ${processState}`);
          assert.equal(fs.existsSync(later), false);
          const report = JSON.parse(fs.readFileSync(path.join(cwd, `.local/checks/workspace-${scope}-${process.platform}.json`)));
          assert.equal(report.verdict, "failed"); assert.equal(report.interrupted_signal, "SIGINT");
          assert.deepEqual(report.results.slice(0, 2).map(row => [row.id, row.status]),
            [["preflight", "passed"], ["repository", "cancelled"]]);
          assert.ok(report.results.slice(2).every(row => row.status === "blocked"));
        } finally {
          if (child.exitCode === null && child.signalCode === null) {child.kill("SIGTERM"); await ended;}
          if (descendant && alive(descendant)) process.kill(descendant, "SIGTERM");
        }
      }
    } finally {fs.rmSync(temporary, {recursive: true, force: true});}
  });
