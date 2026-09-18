"""The three tools for Lab 1.1, and their schemas.

The schema IS the prompt. The model never sees your implementation — only the
name, the description and the JSON Schema. Lab 1.2 makes that point by breaking
these deliberately.
"""
import ast
import pathlib
import urllib.error
import urllib.request

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent / "workspace"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_BYTES = 20_000          # bound at the source, not after the download


class ToolError(RuntimeError):
    """Raised for input the agent can recover from by trying again differently."""


# --------------------------------------------------------------------- tools
def read_file(path: str) -> str:
    target = (WORKSPACE / path).resolve()
    # is_relative_to, not startswith: "/workspace-evil" starts with "/workspace".
    if not target.is_relative_to(WORKSPACE.resolve()):
        raise ToolError(f"path escapes the workspace: {path}")
    if not target.is_file():
        available = ", ".join(p.name for p in WORKSPACE.iterdir())
        raise ToolError(f"no such file: {path}. Available files: {available}")
    return target.read_text(errors="replace")[:MAX_BYTES]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is a second request to a host you never checked. Re-check it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_host(url: str) -> None:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ToolError(f"host not allowed: {host}. Allowed: {sorted(ALLOWED_HOSTS)}")


def http_get(url: str) -> str:
    _check_host(url)
    opener = urllib.request.build_opener(_NoRedirects)
    with opener.open(url, timeout=10) as response:
        return response.read(MAX_BYTES).decode()


# Arithmetic AST nodes only. Parsing and walking beats a character filter: the
# filter is a denylist you have to keep right forever, this is an allowlist of
# operations. Slide 32: an agent's calculator is a classic path to code execution.
# No ast.Pow: "9**9**9" is a one-line denial of service, and the tool only
# promises + - * / %. No complex/str constants either - a Constant is not
# automatically a number.
_ALLOWED_AST = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add,
                ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                ast.USub, ast.UAdd)


def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ToolError(f"not a valid arithmetic expression: {expression!r}") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ToolError(f"only numbers are supported, not {type(node.value).__name__}")
        if not isinstance(node, _ALLOWED_AST):
            raise ToolError(
                f"only arithmetic is supported; {type(node).__name__} is not allowed"
            )
    try:
        return str(eval(compile(tree, "<calc>", "eval")))   # noqa: S307 - AST-verified above
    except ZeroDivisionError:
        raise ToolError("division by zero") from None
    except Exception as exc:                                # noqa: BLE001
        raise ToolError(f"could not evaluate {expression!r}: {exc}") from None


REGISTRY = {"read_file": read_file, "http_get": http_get, "calculator": calculator}

# ------------------------------------------------------------------- schemas
SCHEMAS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from the lab workspace directory. "
            "Use this to inspect configuration and threshold files. "
            "Returns the full file contents as text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File name relative to the workspace, e.g. 'limits.txt'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "http_get",
        "description": (
            "Perform an HTTP GET and return the response body as text. "
            "Only the local fixture host is reachable: http://127.0.0.1:8137/. "
            "Use this to fetch live service status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute URL, e.g. 'http://127.0.0.1:8137/status.json'.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "calculator",
        "description": (
            "Evaluate a arithmetic expression and return the numeric result. "
            "Supports + - * / and parentheses only. "
            "Use this instead of doing arithmetic yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic only, e.g. '(812 - 500) / 500'.",
                }
            },
            "required": ["expression"],
        },
    },
]


def dispatch(name: str, args: dict) -> tuple[str, bool]:
    """Run a tool. Returns (result_text, ok). Never raises: an agent must be able
    to read the error and recover."""
    fn = REGISTRY.get(name)
    if fn is None:
        return f"unknown tool: {name}. Available: {list(REGISTRY)}", False
    try:
        return str(fn(**args)), True
    except ToolError as exc:
        return f"ERROR: {exc}", False
    except TypeError as exc:
        return f"ERROR: wrong arguments for {name}: {exc}", False
    except Exception as exc:                                   # noqa: BLE001
        return f"ERROR: {name} failed: {exc}", False
