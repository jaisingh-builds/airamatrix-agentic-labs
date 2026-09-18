# Network allow-list

For the IT / network team. Outbound HTTPS (443) from participant laptops.

| Host | Why | Needed |
|---|---|---|
| the training gateway host (ask the trainer — it is on every access card) | The model gateway. Everything depends on it. | **All four days** |
| `registry.npmjs.org` | Node packages | Days 1–4 |
| `repo.maven.apache.org` | Java dependencies | Days 1–4 |
| `pypi.org`, `files.pythonhosted.org` | Python packages (optional labs) | Days 1–4 |
| `github.com`, `codeload.github.com` | The lab repository | All four days |
| `api.anthropic.com` | Claude Code makes a few direct calls here even when using a gateway (WebFetch domain safety check, version check) | Days 2–4 |
| `*.amazonaws.com` (ap-south-1) | AWS console/CLI for the AgentCore labs | **Day 4 only** |

Long-lived streaming connections are used (model responses stream for minutes at
a time). Please ensure idle timeouts on any proxy are **at least 15 minutes**,
and that server-sent-event responses are not buffered.
