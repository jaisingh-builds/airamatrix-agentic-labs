"""The three tools for Lab 1.1, and their schemas.

The schema IS the prompt. The model never sees your implementation — only the
name, the description and the JSON Schema. Lab 1.2 makes that point by breaking
these deliberately.
"""
import json
import pathlib
import urllib.request

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent / "workspace"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class ToolError(RuntimeError):
    """Raised for input the agent can recover from by trying again differently."""


# --------------------------------------------------------------------- tools
def read_file(path: str) -> str:
    target = (WORKSPACE / path).resolve()
    if not str(target).startswith(str(WORKSPACE.resolve())):
        raise ToolError(f"path escapes the workspace: {path}")
    if not target.exists():
        available = ", ".join(p.name for p in WORKSPACE.iterdir())
        raise ToolError(f"no such file: {path}. Available files: {available}")
    return target.read_text()


def http_get(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ToolError(f"host not allowed: {host}. Allowed: {sorted(ALLOWED_HOSTS)}")
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode()


def calculator(expression: str) -> str:
    allowed = set("0123456789.+-*/() ")
    if not set(expression) <= allowed:
        raise ToolError(
            f"expression contains unsupported characters: {expression!r}. "
            "Only numbers and + - * / ( ) are supported."
        )
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))   # noqa: S307 - charset-restricted
    except Exception as exc:
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
