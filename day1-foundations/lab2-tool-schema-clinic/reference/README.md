# Lab 1.2 — reference clinic

Measures what a tool contract is worth. Same loop, same tasks, same model — the
only variable is the tool definitions handed to the model.

```bash
source ../../../.env
python3 clinic.py                       # runs bad.json then good.json
python3 clinic.py --schema schemas/good.json
```

## Measured result

```
                    bad.json     good.json
  passed                 3/5           5/5
  steps (avg)            6.6           2.4
  invalid args            25             0
  cost               $0.1284       $0.0515
```

**Twenty-five refused tool calls against zero.** 31 characters of description
versus 922. Nothing else changed.

## How it is a fair test

Both schema files are backed by **the same implementations**. The `impl` field
maps a schema entry to a function in the Lab 1.1 reference agent:

```json
{ "impl": "http_get", "name": "get", "description": "Gets data",
  "input_schema": { "properties": { "q": { "type": "string" } } } }
```

So capability is identical. `bad.json` fails because it exposes a parameter
called `q` while the implementation needs a `url` — the model cannot succeed, no
matter how capable it is.

**That is the lesson: the schema is the interface, not the documentation.**

## This is the severe case. The subtle one is worse.

`../score.py` (the lab you did) keeps the tool names and parameters correct and
only weakens the descriptions. That version **passes 5/5** — and still costs 4.4
steps against 3.2.

Outright failure gets noticed and fixed. A contract that works but costs 40% more
never does.

## Worth trying

1. Take `schemas/bad.json` and fix **only the `name` fields** (`get` → `http_get`
   and so on) but leave `q`. Does it pass?
2. Now fix only the parameter names, leaving the descriptions vague. Compare.
3. Which of the two mattered more? Why?
4. Copy a real tool definition from your own work into this format and run it.
