// A throwaway aira-ops with fresh seed data on a free port, for evals and tests - the same commands as the
// Lab 5.2 harness (aira_ops.py --issue-token, then --reset --db --callers). Each account gets its own READ-ONLY
// caller token scoped to that account; optional write tokens are for tests of apply. The admin token is random,
// in memory only, and nobody uses it.
import { execFileSync, spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { SetupError, opsScript, python } from "./repo.mjs";
import { SECRET_NAMES } from "./spans.mjs";

export const hex = (n) => crypto.randomBytes(n).toString("hex");

/** The child gets the parent's environment minus every secret, plus its own admin token. */
function childEnv(admin) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !SECRET_NAMES.test(k)));
  env.AIRA_OPS_TOKEN = admin;
  return env;
}

/** Add a caller to the callers file and return its token (shown once; the file keeps only its SHA-256). */
export function issue(callers, admin, actor, account, write) {
  const args = [opsScript(), "--callers", callers, "--issue-token", actor, "--accounts", account];
  if (write) args.push("--write");
  let tok;
  try {
    tok = execFileSync(python(), args, { env: childEnv(admin), stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    throw new SetupError("aira-ops --issue-token failed");
  }
  if (!tok) throw new SetupError("aira-ops --issue-token failed");
  return tok;
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

async function healthy(url) {
  try { return (await fetch(`${url}/health`, { signal: AbortSignal.timeout(300) })).status === 200; } catch { return false; }
}

export class PrivateOps {
  static async start(accounts, withWriteTokens = false) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "capstone-ops-"));
    const admin = `admin-${hex(8)}`;
    const callers = path.join(tmp, "callers.json");
    const readTokens = {}, writeTokens = {};
    let child;
    try {
      for (const acc of accounts) {
        readTokens[acc] = issue(callers, admin, `sla-responder-${acc}`, acc, false);
        if (withWriteTokens) writeTokens[acc] = issue(callers, admin, `capstone-apply-${acc}`, acc, true);
      }
      const port = await freePort();
      child = spawn(python(), [opsScript(), "--port", String(port), "--quiet", "--reset", "--db", path.join(tmp, "ops.sqlite"),
        "--callers", callers], { env: childEnv(admin), stdio: "ignore" });
      const url = `http://127.0.0.1:${port}`;
      for (let i = 0; i < 80; i++) {
        if (await healthy(url)) return new PrivateOps(tmp, child, url, readTokens, writeTokens);
        await new Promise((r) => setTimeout(r, 100));
      }
      throw new SetupError(`aira-ops did not start (is ${python()} on PATH?)`);
    } catch (e) {
      if (child) child.kill();
      fs.rmSync(tmp, { recursive: true, force: true });
      throw e;
    }
  }

  constructor(tmp, child, url, readTokens, writeTokens) {
    Object.assign(this, { tmp, child, url, readTokens, writeTokens });
    this.onExit = () => child.kill();
    process.on("exit", this.onExit);
  }

  async close() {
    process.off("exit", this.onExit);
    if (this.child.exitCode === null) {
      const gone = new Promise((r) => this.child.once("exit", r));
      this.child.kill();
      await Promise.race([gone, new Promise((r) => setTimeout(r, 5000))]);
    }
    fs.rmSync(this.tmp, { recursive: true, force: true });
  }
}
