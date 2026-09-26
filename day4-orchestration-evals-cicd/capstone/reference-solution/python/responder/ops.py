"""The reads the responder needs from aira-ops - and nothing else.

Two readers, one per mode, same five methods:
  HttpOpsReader     local: your own aira-ops, a READ-ONLY caller token scoped to one account
                    (`capstone.py tokens`). aira-ops enforces the scope: another account's ticket is a 404.
  GatewayOpsReader  AgentCore (agentcore/runtime/main.py): the Gateway's read tools over MCP, Cedar-filtered.

There is no write method on purpose. The only write in this system is made by gate.apply, after a human
approved it, with a credential no agent process holds.

Also here: PrivateOps - a throwaway aira-ops with fresh seed data on a free port, for evals and tests.
"""
import json, os, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

from . import repo

MAX_BYTES = 256 * 1024


class OpsError(Exception):
    """aira-ops said no (404, 401, ...) or could not be reached (status 0). Reported to the model as data."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class HttpOpsReader:
    def __init__(self, base_url, token, timeout=8.0):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def account(self, account_id):
        return self._get("/accounts/" + _enc(account_id))

    def tickets(self, account_id):
        """Tickets of one account, newest first, at most 50: id, title, status, priority."""
        return self._get("/tickets?limit=50&account_id=" + _enc(account_id))

    def ticket(self, ticket_id):
        return self._get("/tickets/" + _enc(ticket_id))

    def jobs(self, account_id):
        return self._get("/jobs?account_id=" + _enc(account_id))

    def config(self, key):
        return self._get("/config/" + _enc(key))

    def _get(self, path):
        req = urllib.request.Request(self.base + path, headers={
            "Authorization": f"Bearer {self.token}", "X-Actor": "sla-responder"})
        try:
            try:
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                status = resp.status
            except urllib.error.HTTPError as e:
                resp, status = e, e.code
            with resp:
                body = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError) as e:
            raise OpsError(0, "unavailable", f"aira-ops unreachable: {type(e).__name__}") from None
        if len(body) > MAX_BYTES:        # bounded at the source: refused, never sliced
            raise OpsError(502, "too_large", f"aira-ops response over {MAX_BYTES} bytes - refused, not truncated")
        try:
            data = json.loads(body) if body else {}
        except ValueError as e:
            raise OpsError(0, "unavailable", f"aira-ops unreachable: {type(e).__name__}") from None
        if status >= 400:
            e = data.get("error", {}) if isinstance(data, dict) else {}
            raise OpsError(status, e.get("code") or f"http_{status}", e.get("message") or f"HTTP {status}")
        return data


def _enc(s):
    return urllib.parse.quote(str(s), safe="")


# ------------------------------------------------------------------------------------ a private aira-ops

def child_env(admin):
    """The child gets the parent's environment minus every secret, plus its own admin token."""
    env = {k: v for k, v in os.environ.items() if not repo.spans._SECRET_NAMES.search(k)}
    env["AIRA_OPS_TOKEN"] = admin
    return env


def issue(callers, admin, actor, account, write):
    """aira_ops.py --issue-token: adds a caller (hash only) to the callers file, returns its token once."""
    cmd = [repo.python_exe(), str(repo.ops_script()), "--callers", str(callers), "--issue-token", actor,
           "--accounts", account] + (["--write"] if write else [])
    p = subprocess.run(cmd, capture_output=True, text=True, env=child_env(admin))
    tok = p.stdout.strip()
    if p.returncode != 0 or not tok:
        raise RuntimeError("aira-ops --issue-token failed")
    return tok


def hex_(n):
    return secrets.token_hex(n)


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=0.3) as r:
            return r.status == 200
    except Exception:
        return False


class PrivateOps:
    """A throwaway aira-ops (fresh seed, free port). Each account gets its own READ-ONLY token scoped to it;
    with_write_tokens adds a write token per account for tests of apply. The admin token is random, in
    memory only, and nobody uses it. Use as a context manager."""

    def __init__(self, accounts, with_write_tokens=False):
        self.tmp = tempfile.mkdtemp(prefix="capstone-ops-")
        admin = "admin-" + hex_(8)
        callers = os.path.join(self.tmp, "callers.json")
        self.read_tokens, self.write_tokens, self.proc = {}, {}, None
        try:
            for acc in accounts:
                self.read_tokens[acc] = issue(callers, admin, "sla-responder-" + acc, acc, False)
                if with_write_tokens:
                    self.write_tokens[acc] = issue(callers, admin, "capstone-apply-" + acc, acc, True)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            self.proc = subprocess.Popen(
                [repo.python_exe(), str(repo.ops_script()), "--port", str(port), "--quiet", "--reset",
                 "--db", os.path.join(self.tmp, "ops.sqlite"), "--callers", callers],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env(admin))
            self.url = f"http://127.0.0.1:{port}"
            for _ in range(80):
                if healthy(self.url):
                    return
                time.sleep(0.1)
            raise RuntimeError(f"aira-ops did not start (is {repo.python_exe()} on PATH?)")
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        shutil.rmtree(self.tmp, ignore_errors=True)
