import readline from "node:readline";

/** One JSONL owner for explicit close, pipe EOF and process exit signals. */
export function serveManagedPeDriver(driver, {
  stdin = process.stdin, stdout = process.stdout,
  stderr = process.stderr,
  signals = process, exit = (code) => process.exit(code)
} = {}) {
  const input = readline.createInterface({ input: stdin });
  let queue = Promise.resolve();
  let closing = false;
  let cleanClose = false;
  let shutdownPromise = null;
  let forcedPromise = null;

  function write(value) {
    if (!closing) stdout.write(`${JSON.stringify(value)}\n`);
  }

  function shutdown(force) {
    closing = true;
    // A later EOF/signal may upgrade a graceful close that is itself waiting
    // for an in-flight native request. The process owner handles idempotency.
    if (force) {
      forcedPromise ??= Promise.resolve(driver.shutdown({ force: true }));
      return forcedPromise;
    }
    shutdownPromise ??= Promise.resolve(driver.shutdown());
    return shutdownPromise;
  }

  input.on("line", (line) => {
    if (closing) return;
    let request;
    let outcome;
    try {
      request = JSON.parse(line);
      // Admit at line ingress. The session captures the current controller
      // generation here, before a queued release can change it.
      outcome = driver.handle(request).then(
        (value) => ({ value }), (error) => ({ error })
      );
    } catch (error) {
      outcome = Promise.resolve({ error });
    }
    queue = queue.then(async () => {
      if (closing) return;
      const { value, error } = await outcome;
      if (error == null) write(value);
      else {
        write({ type: "error", request_id: request?.request_id ?? null,
          code: "driver_request_failed",
          message: error instanceof Error ? error.message : String(error) });
      }
      // An eagerly admitted later close may already have set driver.closed.
      // Only its own response writer may end input, after writing that result.
      if (request?.command === "close" && driver.closed && !closing) {
        cleanClose = true;
        input.close();
      }
    });
  });
  input.on("close", () => {
    void shutdown(!cleanClose).catch((error) => {
      stderr.write(`driver cleanup unconfirmed: ${error instanceof Error ? error.message : String(error)}\n`);
      exit(1);
    });
  });

  for (const signal of ["SIGTERM", "SIGINT"]) {
    signals.on(signal, () => {
      void shutdown(true).then(() => exit(0), () => exit(1));
      input.close();
    });
  }
  return { shutdown };
}
