import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { projectPython } from "../../../tools/project-python.mjs";

describe("GLOBAL exact semantic dependency identity and restored-copy proofs", () => {
  it("runs the real SDK proof once and rechecks an identical restored copy and later tampering", async () => {
    const f = await globalRuntimeFixture();
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      expect((await f.runtime.tick()).type).toBe("closed");
      const script = `import json,shutil,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from sts2_platform_evidence import verify_agent_session_run_evidence
from sts2_platform_evidence import global_agent_session_run_evidence as owner
directory=Path(sys.argv[1]); expected={'global_runtime_directory':sys.argv[2]}
real=owner.subprocess.run
with tempfile.TemporaryDirectory() as tmp, patch.object(owner.subprocess,'run',wraps=real) as calls:
 restored=Path(tmp)/'restored'; shutil.copytree(directory,restored)
 first=verify_agent_session_run_evidence(directory,expected); assert first.passed,first.findings
 second=verify_agent_session_run_evidence(restored,expected); assert second.passed,second.findings
 assert first.require_value().content_id==second.require_value().content_id
 semantic=[call for call in calls.call_args_list if 'replayGlobalAgentRun' in call.args[0][3]]
 assert len(semantic)==1,len(semantic)
 (restored/'events.jsonl').write_bytes(b'changed after the original proof\\n')
 assert not verify_agent_session_run_evidence(restored,expected).passed
 print(json.dumps({'semantic_calls':len(semantic),'restored_copy':'verified','changed_copy':'rejected'}))`;
      const evidenceRoot = fileURLToPath(new URL("../../evidence", import.meta.url));
      const output = execFileSync(projectPython(["STS2_EVIDENCE_PYTHON"]), ["-c", script, f.evidence.directory,
        fileURLToPath(new URL("../dist", import.meta.url))], {
        encoding: "utf8", timeout: 15000,
        env: { ...process.env, PYTHONPATH: evidenceRoot },
      });
      expect(JSON.parse(output)).toEqual({ semantic_calls: 1, restored_copy: "verified", changed_copy: "rejected" });
    } finally { await f.close(); }
  }, 20000);
});
