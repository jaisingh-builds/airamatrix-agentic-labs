# Lab 1.1 — extensions

Finished early? These are ordered by how much they teach.

### 1. Break the schema on purpose
Change `calculator`'s description to just `"does maths"`. Re-run. Watch the agent
do the arithmetic itself, wrong. Change it back. This is Lab 1.2 in miniature:
**the schema is the prompt.**

### 2. Make a tool fail
Make `http_get` return `ERROR: connection refused` every time. Does your agent
recover, retry forever, or give up and invent an answer? All three are real
production behaviours.

### 3. Count the cost of not caching
Run the same goal five times. Look at `budget.summary()` each time. Now add a
long `system` prompt and run five more. The difference is what prompt caching
pays for.

### 4. Add a fourth tool
`list_files` over the workspace. Write the description first, the implementation
second — and notice which one took longer to get right.

### 5. Parallel tool calls
The model often asks for `read_file` and `http_get` in the same step. Print how
many tools each step requested. Then deliberately return the results in two
separate user messages instead of one, and watch parallel calling stop.
