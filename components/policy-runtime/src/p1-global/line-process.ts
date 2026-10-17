import { spawn, type ChildProcess } from "node:child_process";
import readline from "node:readline";

/** One NDJSON child process: ordered request/response with explicit timeouts. */
export class LineProcess {
  readonly child: ChildProcess;
  private readonly queue: string[] = [];
  private readonly waiters: { resolve: (line: string) => void; reject: (error: Error) => void }[] = [];
  private exited: { code: number | null; signal: NodeJS.Signals | null } | null = null;
  readonly stderrTail: string[] = [];

  constructor(command: string, args: readonly string[], options: { cwd?: string; env?: NodeJS.ProcessEnv; stderr?: "pipe" | "inherit" } = {}) {
    this.child = spawn(command, [...args], { cwd: options.cwd, env: options.env ?? process.env,
      stdio: ["pipe", "pipe", options.stderr ?? "pipe"] });
    readline.createInterface({ input: this.child.stdout! }).on("line", (line) => {
      const waiter = this.waiters.shift();
      if (waiter) waiter.resolve(line); else this.queue.push(line);
    });
    if (this.child.stderr) readline.createInterface({ input: this.child.stderr }).on("line", (line) => {
      this.stderrTail.push(line); if (this.stderrTail.length > 200) this.stderrTail.shift();
    });
    this.child.on("exit", (code, signal) => {
      this.exited = { code, signal };
      for (const waiter of this.waiters.splice(0)) waiter.reject(new Error(`process_exited:${code ?? signal}`));
    });
    this.child.on("error", (error) => { for (const waiter of this.waiters.splice(0)) waiter.reject(error); });
  }

  get exit() { return this.exited; }

  send(value: unknown): void {
    if (this.exited || !this.child.stdin?.writable) throw new Error("process_not_writable");
    this.child.stdin.write(JSON.stringify(value) + "\n");
  }

  /** Next line within timeoutMs. A timeout is reported, never silently retried. */
  next(timeoutMs: number): Promise<string> {
    const queued = this.queue.shift();
    if (queued !== undefined) return Promise.resolve(queued);
    if (this.exited) return Promise.reject(new Error(`process_exited:${this.exited.code ?? this.exited.signal}`));
    return new Promise((resolve, reject) => {
      const waiter = {
        resolve: (line: string) => { clearTimeout(timer); resolve(line); },
        reject: (error: Error) => { clearTimeout(timer); reject(error); }
      };
      const timer = setTimeout(() => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        reject(new Error("line_timeout"));
      }, timeoutMs);
      this.waiters.push(waiter);
    });
  }

  async terminate(graceMs = 5000): Promise<void> {
    if (this.exited) return;
    this.child.stdin?.end();
    const done = new Promise<void>((resolve) => this.child.once("exit", () => resolve()));
    const timeout = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), graceMs));
    if (await Promise.race([done.then(() => "done" as const), timeout]) === "timeout") {
      this.child.kill("SIGTERM");
      await Promise.race([done, new Promise((resolve) => setTimeout(resolve, 3000))]);
      if (!this.exited) this.child.kill("SIGKILL");
    }
  }
}
