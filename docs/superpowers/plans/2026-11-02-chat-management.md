# Chat management and focus implementation plan

**Goal:** Rename and delete saved conversations from the existing sidebar and remove overlapping input focus rectangles.
**Design:** User-requested extension of the approved PA V4 interface. A row action menu opens inline rename or delete confirmation. Rename persists a trimmed 1–200 character name. Delete removes that project's messages, versions, verification history and artifacts using existing FK cascades. No schema migration. Cancellation leaves data intact. Static user and existing workflow remain.
**Architecture:** PATCH/DELETE project API -> PostgresRepository; WorkspaceController handles persisted history/selection; Navigation owns transient menu/form state. Worker discards a result when its project was deleted. Composer uses one rounded container focus indicator; keyboard navigation remains visible.

- [x] Write failing API cascade/rename/validation/not-found and worker deletion tests; run RED.
- [x] Write failing controller stale-list, active/inactive delete and rename-failure tests; run RED.
- [x] Write failing browser management/focus scenario; run RED before section edits.
- [x] Implement storage/API/worker, controller and delegated navigation section; fix composer focus specificity.
- [x] Verify real PostgreSQL, Java/TLC full suite; frontend unit/build; desktop/mobile browser including error/cancel/reload. Update API and landing records.
- [x] Review diff, commit coherent completed block; integrate into dev without rewriting teammate branches.

Commands: `TEST_DATABASE_URL=postgresql://dpa@127.0.0.1:55439/dpa_test uv run --locked pytest -q`; `npm test --prefix frontend`; `npm run build --prefix frontend`; isolated browser QA per README. Compose stop from root: `docker compose --env-file .env -f infra/docker-compose.yml down` (preserves volume).

## Verification

- API management tests first failed with 405; after PATCH/DELETE implementation the worker-deletion race failed NOT_FOUND, then passed after safely discarding removed runs.
- Controller regressions first failed for absent methods; selected/deleted/stale history and failure handling now pass. Independent review found one minor late-read title race; reproduced RED and fixed GREEN.
- Full backend: 220 passed, 7 skipped (16.36s); existing 6 opt-in Spark demonstrations and 1 opt-in live Gemini test skipped.
- Frontend: 16 unit tests passed, production build and Ruff passed.
- Browser: 6 Chrome scenarios passed, including real TLC/download, rename/reload, delete/cancel/cascade, mobile error/Escape/focus and composer focus; desktop and mobile screenshots inspected.
- No schema change, no real user chats deleted, no changes to Sfera or user images. QA uses a disposable random schema and injected model responses.
