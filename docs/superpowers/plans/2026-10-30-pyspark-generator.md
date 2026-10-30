# PySpark generation and storage implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. Track each completed block and its evidence in agents/ways_to_solve.md. User requested the tasks as assigned; do not ask again to redistribute them.

**Goal:** Implement Dmitry's T22–T26 with replaceable LLM generation and PostgreSQL persistence.

**Architecture:** Separate trusted repository, generation service and LLM adapter. The service loads an immutable approved snapshot and the latest normalized verification result before generation. Six PostgreSQL tables include two documented columns for version context and raw verification result; generation manifests provide durable idempotency without holding a transaction during LLM calls.

**Tech Stack:** Python >=3.12, Pydantic 2, psycopg 3, httpx, pytest, PostgreSQL. Spark is a development-only optional dependency.

**Spec:** docs/generator_contract.md; user-supplied pyspark_generator_agent_prompt.md and MNTL-28–MNTL-32.

## Global constraints

- Work in requested checkout and dedicated `pyspark_generator` branch, preserving user images.
- No main changes, force push, backend/UI/analyst/TLC reimplementation, SQLite, broker or new ORM.
- Only stored overall PROPERTY_HOLDS for the exact approved version permits generation.
- Generated code is never automatically executed by the product.
- No secrets or real business data in fixtures, logs or commits.
- Unavailable live integrations remain explicitly unverified.

## Review focus

- New messages must not change an older approved snapshot — repository integration test.
- A latest queued or failed run must block a previously successful version — service/repository test.
- Concurrent retries and cross-version reuse of request IDs must not launch duplicate LLM calls — service integration test.
- An interrupted or blocked model response must not persist as a script — generator tests.
- Failed persistence must not leave a successful manifest without its artifact — PostgreSQL rollback test.

## Task 1 — Contracts and LLM generator (T22/T24)

Files: `pyproject.toml`, `src/pipeline_generator/{models,errors,llm,generator}.py`, `src/pipeline_generator/generation_prompt.md`, `tests/test_generator.py`.

Interfaces: `VerifiedContext`, `Completion`, `GeneratedCode`; `LlmClient.complete(system,user,response_schema)`; `PySparkGenerator.generate(context)`.

- [ ] Write tests for successful complete JSON, all denial statuses, missing context, clarification, malformed/empty/truncated output, context fidelity and bounded transient retries.
- [ ] Run and observe failures before implementing.
- [ ] Implement strict models, prompt assembly and injected LLM client.
- [ ] Run generator tests and full current suite; inspect output.
- [ ] Copy only owned files into requested checkout, review and commit coherent result.

## Task 2 — PostgreSQL repository (T23)

Files: `src/pipeline_generator/storage.py`, `src/pipeline_generator/migrations/001_initial.sql`, `tests/conftest.py`, `tests/test_storage.py`.

Interfaces: create/get project; add/list messages; create/list/approve specification; enqueue/claim/finish/list runs; load verified context; get artifact; reserve/finalize/fail generation manifest. Each method owns a short connection/transaction.

- [ ] Write tests against an isolated real PostgreSQL schema for persistence after reconnection, immutable versions, exact snapshot, history, raw error/counterexample storage and queue claim.
- [ ] Run and observe missing-feature failures.
- [ ] Implement schema and repository with parameterized SQL, immutable snapshots and stored-result gate.
- [ ] Run PostgreSQL and generator suite.
- [ ] Review diff and commit.

## Task 3 — Gemini adapter and issuance service (T24/T25)

Files: `src/pipeline_generator/{gemini,service}.py`, `tests/test_gemini.py`, `tests/test_service.py`, `.env.example`, `docs/integration.md`.

Interfaces: `GeminiClient` implements `LlmClient`; `GenerationService.generate(UUID,UUID)` returns persisted `GeneratedArtifact`; authorized backend serves `repository.get_artifact(spec_id,artifact_id)`.

- [ ] Write tests at the real HTTP transport boundary and real repository for STOP/MAX_TOKENS, auth/timeouts/retries, idempotent and concurrent calls, latest-result gate, failure atomicity and independent regeneration.
- [ ] Observe failures, then implement adapter and service.
- [ ] Run complete suite; record any true external blocker.
- [ ] Review diff and commit.

## Task 4 — Demonstration and integration evidence (T26)

Files: `examples/`, `tests/test_examples.py`, `README.md`, `docs/validation.md`, updated `agents/` journals.

- [ ] Prepare two complete agreed synthetic scenarios: revenue by city and sensor aggregation.
- [ ] Mark manually written reference scripts and fake responses explicitly; do not count either as live generation.
- [ ] Verify expected outputs and data-quality failure cases in a compatible local Spark runtime where available.
- [ ] Attempt real Gemini only with an explicitly configured environment key; no paid fallback.
- [ ] Run final suite and lint, independent whole-branch review, fix material findings and record exact evidence.
- [ ] Commit; update Sfera with actual results, keeping blocked acceptance items open.

## Execution decisions

The user pointed to a dedicated existing branch and directory; work stays there. Intermediate authoring/runtime files stay under this chat's work directory and are copied explicitly into the requested repository. No new worktree, branch reassignment or redistribution of tasks is needed.
Two database columns and local status constraints are implementation proposals documented for backend integration; no existing team schema is overwritten.
