// Local fixture server so http_get has something deterministic to call.
// Safe to call repeatedly.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";

export const PORT = 8137;
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
let server = null;

function portIsOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host: "127.0.0.1" });
    socket.setTimeout(300);
    socket.on("connect", () => { socket.destroy(); resolve(true); });
    socket.on("error", () => resolve(false));
    socket.on("timeout", () => { socket.destroy(); resolve(false); });
  });
}

export async function serveInBackground(port = PORT) {
  if (server || (await portIsOpen(port))) return server;
  server = http.createServer((req, res) => {
    const file = path.join(ROOT, path.basename(new URL(req.url, "http://x").pathname));
    if (!fs.existsSync(file)) { res.statusCode = 404; return res.end("not found"); }
    res.setHeader("content-type", "application/json");
    res.end(fs.readFileSync(file));
  });
  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  server.unref();
  return server;
}
