# Formal checker review and integration

Reviewed upstream `origin/tla_tlc` at `7065373`; integrated under
`backend/src/compiler/formal_check` without replacing the canonical DSL.

## Defects corrected

- Progress alone returned `PROPERTY_HOLDS`; parser now requires successful
  completion and exhausted final statistics, handles actual TLC error records,
  and recognizes initial and subsequent invariant violations.
- Reachable properties were skipped. Each now uses a negated invariant;
  a witnessed violation establishes existential reachability. The safety run
  and all requested reachability runs must finish before overall success.
- Global model/config paths allowed concurrent jobs to execute another model.
  `check_model` compiles and executes using unique temporary workspaces.
- Clean checkout lacked output directories and crashed without an environment
  variable. Compilation creates directories; configuration has a worker-local
  default jar path. CLI accepts an actual input file instead of absent fixtures.
- Nonzero property failure exits were classified as infrastructure errors.
  Runner distinguishes invariant failure exits, killed JVMs, timeouts,
  infrastructure failures and engine failures. Timeout output is retained.
- Semantic validation was a stub. Initial completeness/domains, references,
  expression/effect types, duplicate assignments and identifier collisions are
  checked. A generated domain invariant catches dynamic out-of-domain states.
- Empty membership sets crashed; Boolean members and string literals were
  emitted incorrectly. Compilation handles scalar literals consistently.
  TLC rejects Unicode literals and JSON Unicode escapes; an injective ASCII
  representation preserves enum equality/membership and original trace values.
- The unconditional undeclared `IsDAG` temporal property rejected valid cycles.
  Verification now checks the caller's declared properties plus variable domains.

## Added integration

`compiler.formal_check.src.services.checker.check_model(data, *, jar_path=None,
timeout=30) -> dict` returns one normalized overall result plus engine diagnostics.
`worker.src.runner.process_one(repository, checker=check_model)` claims a queued
PostgreSQL job and persists its result. The polling entry point handles SIGTERM
and SIGINT between jobs; no database connection is held while Java runs.

## Evidence

Tests were written before the corresponding fixes; reproduced failures included
progress approval, nine missing semantic checks, absent checker/worker APIs,
initial-state trace parsing, hardcoded CLI fixtures and killed-JVM acceptance.
Targeted tests use real TLC 2.19 / Java 8 and PostgreSQL. They cover completed
safety, initial/later violations, witness and unreachable target, legitimate
cycles, domain overflow, Unicode traces, timeouts, isolated paths, malformed
output and durable worker result storage. See the final integration validation
record for the latest complete suite count. Java's local RMI port requires an
unsandboxed test invocation in this environment; this is not an engine failure.

## Remaining operational limits

The checker explores explicit state spaces; an unbounded/large model can time
out, which blocks generation. A hard-killed worker can leave its claimed job
RUNNING: automatic lease/recovery is outside this polling implementation.
The jar must be supplied at deployment; it is excluded from Git. Formal model
verification does not establish correctness of generated Python or real data.
