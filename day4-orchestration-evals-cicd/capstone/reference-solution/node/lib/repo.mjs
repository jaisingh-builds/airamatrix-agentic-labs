// Where things are in the labs checkout. LABS_REPO overrides; otherwise found from this file upwards.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const NODE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Refused-to-start problems in the environment: printed as-is, exit 2. */
export class SetupError extends Error {}

export function root() {
  if (process.env.LABS_REPO) return path.resolve(process.env.LABS_REPO);
  for (let d = NODE_DIR; ; d = path.dirname(d)) {
    if (fs.existsSync(path.join(d, "labkit")) && fs.existsSync(path.join(d, "day3-integration-security"))) return d;
    if (path.dirname(d) === d) break;
  }
  throw new SetupError("run from inside the airamatrix-agentic-labs checkout, or set LABS_REPO");
}

export const opsScript = () => path.join(root(), "day3-integration-security", "aira-ops", "aira_ops.py");

/** reference-solution/ - golden/ and fixtures/ are shared by the Java, Python and Node solutions. */
export const solution = () => path.join(root(), "day4-orchestration-evals-cicd", "capstone", "reference-solution");

/** python3, or python on Windows, unless LAB_PYTHON says otherwise. */
export function python() {
  if (process.env.LAB_PYTHON) return process.env.LAB_PYTHON;
  return process.platform === "win32" ? "python" : "python3";
}

/** The traces directory: LAB_TRACE_DIR, else <repo>/traces (the same place common/spans.py writes). */
export function traceDir() {
  if (process.env.LAB_TRACE_DIR) return process.env.LAB_TRACE_DIR;
  try { return path.join(root(), "traces"); } catch { return path.join(NODE_DIR, "traces"); }
}
