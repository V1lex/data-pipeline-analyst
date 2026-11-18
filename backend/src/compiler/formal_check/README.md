# Formal model checker

The canonical package is `compiler.formal_check`, installed with the backend.
Use the stage checker to obtain validation, compilation, runner and parsed output:

```python
from compiler.formal_check.src.services.checker import check_model

result = check_model(input_json, timeout=30)
```

`input_json` follows `src/models/input.py`. `check_model` validates the complete
contract, compiles into a unique temporary directory and runs TLC. Safety and
variable domains are checked first. Every `reachable` property gets a separate
negated-invariant check. Raw runner output and typed parsed output are returned
separately under `runner` and `parsed`. External workers own the overall verdict
and persistence.

`forbidden_state` negates its expression. `deadlock_free` requires either an
enabled transition or the property's terminal-state expression. Integer bounds and enum
values are variable domains; a reachable state outside them returns
`MODEL_INVALID`, with its trace. `IsDAG` is always compiled and checked as a temporal property.
Unicode enum strings are transported losslessly
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
and converts the stage result into the stored overall verdict. SIGTERM/SIGINT stops polling
after the current bounded verification finishes.

TLC configurations are executed concurrently by `run_tlc_batch` within the
overall timeout. Set `TLC_WORKERS` to control the number of parallel
configurations (default: 4) and `TLC_HEAP_SIZE` to control the Java heap
(default: `2g`). Each TLC process also receives the same worker count.

Legacy `validate_input`, `compile_input`, `run_tlc` and `parse_tlc_output` entry
points remain available. Use `compile_input(..., output_dir=...)` and explicit
runner paths when composing them; shared legacy default files are unsuitable
for concurrent jobs.

## Raw input contract

`check_model` accepts only the canonical formal-check object:

```json
{
  "variables": [
    {
      "name": "status",
      "type": "enum",
      "values": ["Created", "Approved", "Cancelled", "Executed"]
    },
    {
      "name": "approved",
      "type": "boolean"
    },
    {
      "name": "cancelled",
      "type": "boolean"
    },
    {
      "name": "attempts",
      "type": "integer",
      "min": 0,
      "max": 3
    }
  ],
  "initial": {
    "status": "Created",
    "approved": false,
    "cancelled": false,
    "attempts": 0
  },
  "transitions": [
    {
      "name": "Approve",
      "guard": {
        "eq": [{"var": "status"}, "Created"]
      },
      "effects": [
        {"set": ["status", "Approved"]},
        {"set": ["approved", true]}
      ]
    }
  ],
  "properties": [
    {
      "id": "P1",
      "type": "invariant",
      "expression": {
        "le": [{"var": "attempts"}, 3]
      }
    },
    {
      "id": "P2",
      "type": "forbidden_state",
      "expression": {
        "and": [
          {"eq": [{"var": "status"}, "Executed"]},
          {"eq": [{"var": "cancelled"}, true]}
        ]
      }
    },
    {
      "id": "P3",
      "type": "reachable",
      "expression": {
        "eq": [{"var": "status"}, "Executed"]
      }
    },
    {
      "id": "P4",
      "type": "deadlock_free",
      "expression": {
        "in": [
          {"var": "status"},
          ["Created", "Approved", "Cancelled", "Executed"]
        ]
      }
    }
  ]
}
```

## Stage output

`check_model` returns validation, compilation, runner and parsed results:

```json
{
  "validation": {
    "variables": [],
    "initial": {},
    "transitions": [],
    "properties": []
  },
  "compilation": {
    "model_path": "/tmp/formal-check/model.tla",
    "safety_config_path": "/tmp/formal-check/safety.cfg",
    "reachable_config_paths": {
      "P3": "/tmp/formal-check/reachable/P3.cfg"
    }
  },
  "runner": {
    "outputs": [],
    "elapsed_seconds": 0
  },
  "parsed": []
}
```

The worker converts this envelope into one persisted verdict. Supported verdict
statuses are `PROPERTY_HOLDS`, `PROPERTY_VIOLATED`, `PROPERTY_UNREACHABLE`,
`MODEL_INVALID`, `TIMEOUT`, `STATE_LIMIT_REACHED`, and
`INFRASTRUCTURE_ERROR`.

Examples:

```json
{
  "status": "PROPERTY_HOLDS",
  "engine": "TLC",
  "statistics": {
    "states_generated": 162,
    "distinct_states": 37,
    "depth": 8
  }
}
```

```json
{
  "status": "PROPERTY_VIOLATED",
  "engine": "TLC",
  "property_id": "P2",
  "counterexample": {
    "steps": [
      {
        "number": 0,
        "transition": null,
        "state": {
          "status": "Created",
          "approved": false,
          "attempts": 0
        }
      }
    ]
  }
}
```

```json
{
  "status": "PROPERTY_UNREACHABLE",
  "engine": "TLC",
  "property_id": "P3",
  "error": "Target state is unreachable in the completely explored state graph"
}
```

```json
{
  "status": "MODEL_INVALID",
  "engine": "TLC",
  "errors": [
    {
      "type": "OUT_OF_DOMAIN_VALUE",
      "location": "initial.attempts",
      "message": "Initial value (-1) is outside the bounded integer domain [0, 3]"
    }
  ]
}
```

```json
{
  "status": "TIMEOUT",
  "engine": "TLC",
  "timeout_limit_seconds": 5.0
}
```

```json
{
  "status": "STATE_LIMIT_REACHED",
  "engine": "TLC",
  "state_limit": 100000
}
```

```json
{
  "status": "INFRASTRUCTURE_ERROR",
  "engine": "TLC",
  "error": "Java process exited with code 137 (OOM Killed). System memory exhausted."
}
```
