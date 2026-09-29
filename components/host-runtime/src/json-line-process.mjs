import { spawn } from "node:child_process";
import readline from "node:readline";

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

function boundedPush(items, value, limit) {
  items.push(value);
  if (items.length > limit) items.splice(0, items.length - limit);
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function withTimeout(promise, timeoutMs, label) {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) {
    throw new TypeError(`${label} timeout must be a positive integer.`);
  }
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms.`)), timeoutMs);
    })
  ]).finally(() => clearTimeout(timer));
}

export class JsonLineProcess {
  #child;
  #stdout;
  #stderr;
  #messages = [];
  #waiters = [];
  #diagnostics = [];
  #closed;
  #requestPending = false;
  #diagnosticLimit;
  #gracefulStop = null;
  #forcedStop = null;
  #forcedStopTimeoutMs = null;

  constructor({ command, args = [], cwd, env = process.env, diagnosticLimit = 200 }) {
    if (typeof command !== "string" || command.length === 0) {
      throw new TypeError("JsonLineProcess requires a command.");
    }
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string")) {
      throw new TypeError("JsonLineProcess args must be strings.");
    }
    if (!Number.isSafeInteger(diagnosticLimit) || diagnosticLimit < 1) {
      throw new TypeError("diagnosticLimit must be a positive integer.");
    }
    this.#diagnosticLimit = diagnosticLimit;
    this.#child = spawn(command, args, {
      cwd,
      env,
      stdio: ["pipe", "pipe", "pipe"]
    });
    this.#closed = deferred();
    this.#stdout = readline.createInterface({ input: this.#child.stdout });
    this.#stderr = readline.createInterface({ input: this.#child.stderr });
    this.#stdout.on("line", (line) => this.#acceptStdout(line));
    this.#stderr.on("line", (line) => this.#recordDiagnostic("stderr", line));
    this.#child.once("error", (error) => this.#finish({
      code: null,
      signal: null,
      error: errorText(error)
    }));
    this.#child.once("exit", (code, signal) => this.#finish({ code, signal, error: null }));
  }

  get pid() {
    return this.#child.pid ?? null;
  }

  get diagnostics() {
    return this.#diagnostics.map((entry) => ({ ...entry }));
  }

  get exit() {
    return this.#closed.promise;
  }

  sendSignal(signal) {
    if (typeof signal !== "string" || signal.length === 0) {
      throw new TypeError("JSON-line process signal must be a non-empty string.");
    }
    if (this.#closed.settled) return false;
    return this.#child.kill(signal);
  }

  #recordDiagnostic(stream, line) {
    boundedPush(this.#diagnostics, {
      at: new Date().toISOString(),
      stream,
      line: String(line)
    }, this.#diagnosticLimit);
  }

  #acceptStdout(line) {
    let parsed;
    try {
      parsed = JSON.parse(line);
    } catch {
      this.#recordDiagnostic("stdout", line);
      return;
    }
    const waiter = this.#waiters.shift();
    if (waiter) waiter.resolve(parsed);
    else this.#messages.push(parsed);
  }

  #finish(exit) {
    if (this.#closed.settled) return;
    this.#closed.settled = true;
    this.#closed.resolve(exit);
    const detail = exit.error ?? `code=${exit.code ?? "null"}, signal=${exit.signal ?? "null"}`;
    for (const waiter of this.#waiters.splice(0)) {
      waiter.reject(new Error(`JSON-line process exited before replying (${detail}).`));
    }
  }

  async nextMessage(timeoutMs = 10_000) {
    if (this.#messages.length > 0) return this.#messages.shift();
    if (this.#closed.settled) {
      const exit = await this.#closed.promise;
      throw new Error(`JSON-line process is closed (code=${exit.code ?? "null"}).`);
    }
    const waiter = deferred();
    this.#waiters.push(waiter);
    try {
      return await withTimeout(waiter.promise, timeoutMs, "JSON-line response");
    } catch (error) {
      const index = this.#waiters.indexOf(waiter);
      if (index >= 0) this.#waiters.splice(index, 1);
      throw error;
    }
  }

  async request(payload, timeoutMs = 10_000) {
    if (this.#requestPending) {
      throw new Error("JsonLineProcess permits one in-flight request so replies cannot be misattributed.");
    }
    if (this.#closed.settled || this.#child.stdin.destroyed) {
      throw new Error("Cannot write to a closed JSON-line process.");
    }
    let serialized;
    try {
      serialized = JSON.stringify(payload);
    } catch {
      throw new TypeError("JSON-line request must be serializable.");
    }
    this.#requestPending = true;
    try {
      this.#child.stdin.write(`${serialized}\n`);
      return await this.nextMessage(timeoutMs);
    } finally {
      this.#requestPending = false;
    }
  }

  async #forceStop(timeoutMs) {
    if (this.#closed.settled) return this.#closed.promise;
    if (this.#forcedStop != null) {
      if (timeoutMs < this.#forcedStopTimeoutMs) {
        // A consumer EOF can demand a shorter bound after a graceful close
        // has already sent SIGTERM. Escalate that same owned child now.
        this.#forcedStopTimeoutMs = timeoutMs;
        if (!this.#closed.settled) this.#child.kill("SIGKILL");
        return withTimeout(this.#closed.promise, timeoutMs, "JSON-line forced stop upgrade");
      }
      return this.#forcedStop;
    }
    this.#forcedStopTimeoutMs = timeoutMs;
    this.#forcedStop = (async () => {
      if (!this.#closed.settled) this.#child.kill("SIGTERM");
      try {
        return await withTimeout(this.#closed.promise, timeoutMs, "JSON-line process stop");
      } catch {
        if (!this.#closed.settled) this.#child.kill("SIGKILL");
        return withTimeout(this.#closed.promise, timeoutMs, "JSON-line process kill");
      }
    })();
    return this.#forcedStop;
  }

  async stop({ request = null, timeoutMs = 5_000, force = false } = {}) {
    if (force) return this.#forceStop(timeoutMs);
    if (this.#closed.settled) return this.#closed.promise;
    if (this.#gracefulStop != null) return this.#gracefulStop;
    this.#gracefulStop = (async () => {
      if (request != null) {
        try {
          await this.request(request, timeoutMs);
          return await withTimeout(this.#closed.promise, timeoutMs, "JSON-line graceful stop");
        } catch (error) {
          this.#recordDiagnostic("driver", `graceful stop failed: ${errorText(error)}`);
        }
      }
      return this.#forceStop(timeoutMs);
    })();
    return this.#gracefulStop;
  }
}

export async function startJsonLineProcess(options) {
  const processHandle = new JsonLineProcess(options);
  try {
    const ready = await processHandle.nextMessage(options.readyTimeoutMs ?? 30_000);
    return { process: processHandle, ready };
  } catch (error) {
    await processHandle.stop();
    throw error;
  }
}
