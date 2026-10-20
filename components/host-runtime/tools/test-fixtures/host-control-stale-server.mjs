// Independent lifecycle fixture: the caller may block without keeping the old
// server-side socket alive. No game, recording or native shutdown is involved.
import { createServer } from "node:http";
let nextSocket = 0;
const server = createServer(async (req, res) => {
  let text = "";
  for await (const chunk of req) text += chunk;
  const body = JSON.parse(text);
  if (body.expected_runtime_instance_id !== "fixture-runtime" || body.host_control_token !== "f".repeat(64))
    throw new Error("fixture_identity");
  process.send({ request: req.url, socket: req.socket.number });
  const shutdown = req.url === "/api/host-control/shutdown";
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ status: shutdown ? "shutdown_requested" : "seed_observed" }));
  if (shutdown) setTimeout(() => server.close(() => process.exit(0)), 40);
  else setTimeout(() => {
    req.socket.destroy();
    process.send({ closed_idle_socket: req.socket.number });
  }, 300);
});
server.keepAliveTimeout = 60000;
server.on("connection", socket => { socket.number = ++nextSocket; });
server.listen(0, "127.0.0.1", () => process.send({ endpoint: `http://127.0.0.1:${server.address().port}` }));
