import fs from "node:fs";

// kill(pid, 0) also succeeds for a terminated Linux zombie. This witness
// checks whether owned descendants can still execute, not whether init reaped a PID.
export function processExecuting(pid) {
  try { process.kill(pid, 0); }
  catch (error) { if (error.code === "ESRCH") return false; throw error; }
  if (process.platform !== "linux") return true;
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2).split(" ")[0] !== "Z";
  } catch (error) { if (error.code === "ENOENT") return false; throw error; }
}
