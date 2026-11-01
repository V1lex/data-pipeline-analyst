# Chat-first frontend implementation plan

> Required execution: PA Landing V4 section subagents, then root integration and independent review.

**Goal:** Implement approved full-page-v2.png as the functional conversation-first frontend.
**Architecture:** Existing React/Vite app; separate presentational sections in workspace-v4, one observable state controller for API workflow, React Flow retained inside an optional context drawer.
**Spec:** frontend/src/workspace-v4/landing.json (approved user message recorded).
**Global constraints:** Static `user`, no account controls. No MVP, counters, stepper, decorative metrics, or marketing copy. Preserve backend API and version/verification/generation gates. Show actual API data only. Respect reduced motion, keyboard access and narrow screens. Do not touch deferred Sfera work or untracked user images.

## Tasks
- [x] Record actual approval and extract exact reference crops; code-native sections require no raster runtime assets.
- [x] Navigation section: Navigation({projects,projectId,busy,open,onSelect,onNew,onClose}); desktop sidebar/mobile overlay, static user.
- [x] Conversation section: Conversation({project,loading,text,busy,pendingMessage,error,onTextChange,onSend,onDismissError,actions,artifacts,generated}); unboxed assistant text, composer, real clarification questions; no unsupported controls.
- [x] Context drawer section: ContextDrawer({mode,title,onClose,children,openerRef}); responsive panel, Escape, mobile focus trap/return.
- [x] Contextual artifacts section: ContextualArtifacts({spec,generated,onOpenSpecification,onOpenVerification}); compact actual files/status, no fake assistant prose.
- [x] Root integration: state.js controller; App.jsx coordinator, shared icons/tokens, schema/JSON/verification/file panels preserving all existing actions. Root owns routes/state/dependencies/global styles.
- [x] Verification: failing controller tests first for first-message creation/retry, stale reads, selected version context, generation idempotence and provider clarification; then implementation. Run frontend test/build, full backend regression, browser desktop/mobile/error/version/download checks and independent review.
- [x] Update landing.json accepted files/motion, checkpoint commit; integrate verified result into dev as the final step.

## Review focus
Unknown network outcome must preserve generation UUID; explicit retry after recorded failure must create a new one. Failed first message must not create a second project. Out-of-order project/spec reads must not mix versions. Drawer closing and mobile overlays must preserve text and keyboard focus. Historical files must download with their own specification UUID.

## Verification and review

- Frontend unit tests: 11 passed. Review regressions first reproduced RED: acknowledged turn after failed GET, STORAGE_ERROR UUID reuse, delayed project list. They now pass GREEN.
- Browser Chrome desktop/mobile: 3 passed (4.7s), including real PostgreSQL/TLC, saved artifact/reload/download, provider retry, mobile accessible errors and keyboard focus. The focus regression reproduced RED before filtering the removed opener/body in drawer cleanup.
- Full backend: 216 passed, 7 skipped (15.40s); 6 explicit Spark demonstrations and 1 opt-in live Gemini test. No backend production code changed for this redesign.
- Production Vite build, Ruff and Compose configuration passed. Screenshots inspected at desktop 1440×1000 and mobile 390×844.
- Independent review found four Important issues; all fixed and covered. Actual Gemini previously failed HTTP400 due to region; browser QA injects model responses and does not validate live generated Spark. Docker daemon remains off.
- All remote teammate heads remain ancestors after final fetch. Static user only; Sfera remains deferred.
