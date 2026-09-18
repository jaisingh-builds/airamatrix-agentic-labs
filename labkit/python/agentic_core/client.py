"""Anthropic Messages client over the training gateway.

Standard library only: no pip install, no proxy negotiation, nothing to break on
22 laptops on the first morning.
"""
import json
import time
import urllib.error
import urllib.request

from .config import Config


class GatewayError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"gateway returned {status}: {body[:400]}")
        self.status = status
        self.body = body


class GatewayClient:
    def __init__(self, config: Config | None = None) -> None:
        self.cfg = (config or Config()).require()

    def messages(self, messages: list, tools: list | None = None,
                 system: str | list | None = None, max_tokens: int = 1024,
                 model: str | None = None, retries: int = 3) -> dict:
        payload = {
            "model": model or self.cfg.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if system:
            payload["system"] = system

        request = urllib.request.Request(
            f"{self.cfg.base_url}/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "authorization": f"Bearer {self.cfg.api_key}",
            },
            method="POST",
        )

        last = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                # 429 = your daily budget or rate limit at the gateway.
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    last = GatewayError(exc.code, body)
                    continue
                raise GatewayError(exc.code, body) from None
            except urllib.error.URLError as exc:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    last = GatewayError(0, str(exc.reason))
                    continue
                raise GatewayError(0, f"cannot reach {self.cfg.base_url}: {exc.reason}") from None
        raise last
