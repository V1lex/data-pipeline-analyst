# MVP integration implementation plan

**Goal:** Combine the fetched team branches into the structure proposed by Ivan and deliver a working local MVP.
**Authority:** User explicitly requested review, integration, near MVP and subsequent Sfera updates. Continue implementation without additional permission checkpoints.
**Architecture:** FastAPI and analyst/compiler/validator/generator in backend/src; isolated TLC worker; React interface; PostgreSQL and Alembic as the canonical persistence schema.
**Constraints:** Preserve team git history, local .env and unrelated untracked images. Python 3.12. Real model unavailable is reported but does not block plumbing. Never generate without an approved version and a completed successful verification. Never execute generated Python on the server.

## Tasks
- [x] Fetch all branches; inspect commits and review analyst, checker, database, generator.
- [x] Merge all unique histories into integration/mvp-dev; preserve original first Alembic revision.
- [x] Consolidate backend packages and dependencies; fix imports; baseline all existing tests.
- [x] Add compatibility Alembic revision, immutable version guarantees, session persistence and repository integration. Verify with real temporary PostgreSQL, fresh and upgrade paths.
- [x] Fix formal completion/violation detection, per-job compilation, reachability and worker polling. Test real Java/TLC positive and negative runs.
- [x] Build API for projects, persistent chat, draft/build/approve versions, verification history, generation/download. Test complete HTTP flow against PostgreSQL with injected model responses and real worker.
- [x] Add React/React Flow frontend and Compose/Dockerfiles; verify frontend build and browser scenario.
- [x] Independent final review, regression suite, live Gemini attempt, documentation and coherent commits.
- [ ] Sfera: deferred by user after integration. Only MNTL-34/T28 was created in sprint 3; existing statuses unchanged. Remaining draft: docs/sprint-3-backlog-draft.md.

## Decisions
Keep analyst as an importable package inside backend/src/agent and formal_check inside backend/src/compiler, preserving public team APIs while removing global src imports. Keep pipeline_spec as canonical shared contract. Evolve original DB migration rather than overwrite history. A new migration adopts uppercase generator statuses as text with explicit checks to avoid competing native enum vocabularies. Preserve legacy rows with missing context as inaccessible to generation. Session snapshots are persisted and concurrent requests serialized without holding a transaction during LLM calls.

## Verification / review ledger
- Team reviews: analyst, formal checker, DB/storage reviewed independently; concrete blockers recorded in docs/review-integration.md and fixed.
- Final independent review: one Important clarification-display finding, reproduced in frontend test then fixed; no other Critical/Important findings.
- Backend suite: 216 passed, 7 skipped with temporary PostgreSQL and real Java/TLC; repeated through locked uv installation.
- Frontend: API error regression passes, production build passes, browser project/graph/TLC/mobile checks; Compose config validates.
- Wheel: installed outside checkout, imports and Alembic upgrade in isolated schema pass.
- Gemini: one real request returned HTTP400 region restriction; user explicitly authorized assuming provider correctness for integration.
- Ruling: use integration branch for review and move completed integration to dev, preserving main and all source branch histories.
- Ruling: worker hard-crash recovery, authentication, CI/container startup and real LLM-generated Spark execution remain explicit sprint 3 work.

- Sfera UI: verified MNTL-34 in sprint 3. User explicitly said «давай потом насчет сферы»; stopped all further Sfera actions.
