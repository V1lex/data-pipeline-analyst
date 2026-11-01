# Formal model checker

The canonical package is `compiler.formal_check`, installed with the backend.
Use the overall checker instead of chaining progress output into a verdict:

```python
from compiler.formal_check.src.services.checker import check_model

result = check_model(input_json, timeout=30)
```

`input_json` follows `src/models/input.py`. `check_model` validates the complete
contract, compiles into a unique temporary directory and runs TLC. Safety and
variable domains are checked first. Every `reachable` property gets a separate
negated-invariant check: a counterexample establishes an existential witness;
exhaustion establishes unreachability. Only completion of all requested checks
returns `PROPERTY_HOLDS`. One overall timeout covers all runs. Raw stdout,
stderr and exit codes are kept under `diagnostics`.

`forbidden_state` negates its expression. `deadlock_free` requires either an
enabled transition or the property's terminal-state expression. Cycles are
allowed unless an explicit property prohibits them. Integer bounds and enum
values are variable domains; a reachable state outside them returns
`MODEL_INVALID`, with its trace. Unicode enum strings are transported losslessly
through TLC's ASCII literal syntax and decoded in counterexamples.

Set `TLC_JAR_PATH` to an official `tla2tools.jar`, or put it at
`worker/tla2tools.jar`. Java must be on `PATH`; `JAVA_BIN` can override it.
The jar is not committed. Missing Java/jar, engine failure and timeout are
separate outcomes and never authorize code generation.

From the repository root, with backend dependencies installed:

```sh
PYTHONPATH=backend/src python -m compiler.formal_check.src.main model.json --timeout 30
PYTHONPATH=backend/src:. python -m worker.src.runner
```

The worker reads `DATABASE_URL`, optional `DATABASE_SCHEMA`, `TLC_TIMEOUT` and
`WORKER_POLL_INTERVAL`. It polls the PostgreSQL queue using the repository's
`FOR UPDATE SKIP LOCKED` claim, releases the database connection during TLC,
and persists the exact overall checker result. SIGTERM/SIGINT stops polling
after the current bounded verification finishes.

Legacy `validate_input`, `compile_input`, `run_tlc` and `parse_tlc_output` entry
points remain available. Use `compile_input(..., output_dir=...)` and explicit
runner paths when composing them; shared legacy default files are unsuitable
for concurrent jobs.
