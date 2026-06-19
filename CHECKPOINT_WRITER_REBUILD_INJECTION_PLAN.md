# Checkpoint Writer + Rebuild Injection Plan

## Summary

Add a new implementation plan document for a MiMo-inspired but Python-local-first **checkpoint writer + rebuild injection** line.

This line should make one architectural shift explicit:

- the current Python runtime is **not** missing prompt reduction entirely
- it already has:
  - `context_summary`
  - provider-view prompt assembly
  - prompt-prefix planning and reduction orchestration
  - tool-result replacement
  - artifact indirection
  - balanced microcompact
- the next depth step is to insert a **checkpoint-first rebuild layer** in front of lossy compaction

The target runtime chain should become:

1. normal provider-view assembly
2. threshold-based checkpoint writing in the background
3. checkpoint-backed rebuild injection when budget pressure or prompt-too-long recovery hits
4. replacement / artifact / microcompact on the rebuilt tail
5. full `context_summary` compaction only as the final fallback

This plan should stay mechanism-oriented and source-aware, grounded in MiMo’s real strata:

- `session/checkpoint.ts`
- `session/prune.ts`
- `session/prompt.ts`
- `session/checkpoint-templates.ts`

but adapted to the current Python runtime shape instead of copying MiMo’s broader product breadth.

## Key Changes

### 1. Reframe the problem as checkpoint-first rebuild, not “another summary”

The document should start by stating the current Python baseline clearly:

- current local runtime already supports:
  - project memory injection
  - `context_summary`
  - provider-view prompt assembly
  - prompt-prefix planning and reduction orchestration
  - tool-result replacement / artifact indirection / balanced microcompact
  - transcript persistence and resume
- the missing mechanism is **checkpoint-backed context reconstruction**
- therefore this line is not a replacement for prompt-prefix work; it is a new mid-layer between full-history assembly and lossy compaction

It should also state the intended target behavior in plain terms:

- write structured session snapshots before overflow
- rebuild the next provider view from those snapshots plus a preserved recent tail
- only summarize old history into `context_summary` when rebuild cannot recover enough headroom

### 2. Define the local file model and persisted state

The first-phase local storage model should be fixed as decision-complete:

- workspace-local session-scoped files:
  - `.pyclaude/checkpoints/<session_id>/checkpoint.md`
  - `.pyclaude/checkpoints/<session_id>/notes.md`
- do **not** add a second project-memory file in v1
- reuse the current project memory path as the durable memory input

The document should specify additive persisted state on `SessionState` for checkpoint bookkeeping only, not for storing rebuilt messages. At minimum:

- current checkpoint slug or directory path
- last checkpoint boundary identity
- last checkpoint source message id or timestamp
- last checkpoint write status
- whether a rebuild boundary is active
- optional retry / failure counters needed for threshold-driven writing

It should explicitly keep transcript message content unchanged.

### 3. Add a hidden runtime checkpoint writer owner

The document should choose one concrete writer model and reject weaker alternatives.

Chosen model:

- hidden runtime-owned child session / hidden subagent style writer
- read parent transcript and project memory
- write only the current session’s checkpoint files
- not user-invocable
- not part of the normal public agent surface

This should reuse current child-session / read-only-subagent infrastructure rather than inventing a separate worker framework.

The writer contract should stay narrow:

- input:
  - current session transcript snapshot
  - active plan file if one exists
  - current project memory
  - recent runtime summaries if needed
- output:
  - rewritten `checkpoint.md`
  - updated `notes.md`
  - checkpoint metadata returned to the parent runtime

The document should also fix the content shape of `checkpoint.md` in v1:

- active intent
- next concrete action
- session directives
- current work
- files/code sections
- discovered knowledge
- errors/fixes
- live resources
- design decisions
- open notes

It does not need to copy MiMo’s exact section names verbatim, but it should preserve the same structural purpose and keep section budgets explicit.

### 4. Add threshold-based checkpoint triggering before overflow

The document should define a dedicated threshold subsystem rather than piggybacking on manual compaction.

Chosen trigger model:

- threshold checks run from the main query/runtime loop before provider calls
- thresholds are based on the current provider-view token estimate or char estimate already produced by the local runtime
- newly crossed thresholds enqueue or start checkpoint writing
- repeated turns above the same threshold do not refire endlessly
- a currently running writer suppresses duplicate launches
- repeated writer failures eventually stop retrying until the session state changes materially

This logic should live near the current budget/recovery path in `runtime/query_loop.py`, with helper ownership moved into a dedicated runtime module rather than added inline.

This first phase should stay local and heuristic:

- no provider-side dry-run counting
- no global scheduler
- no hosted memory service
- no checkpoint writing from background infrastructure unrelated to the current session

### 5. Add rebuild-first provider-view injection

This is the core of the document.

The plan should define a provider-view rebuild path that activates when:

- the local runtime budget says the current provider-view is over or near limit
- or a prompt-too-long recovery path is entered
- and a usable checkpoint exists

The rebuilt provider view should be assembled from:

- existing system prompt blocks and prompt attachments
- existing project memory block
- a new checkpoint block rendered from `checkpoint.md`
- a new notes block rendered from `notes.md`
- a preserved recent tail of actual transcript messages after a rebuild boundary

This rebuilt tail must stay compatible with current provider-view reduction mechanics:

- frozen replacement reapply still runs
- artifact indirection still runs
- microcompact can still run on the rebuilt tail
- full `context_summary` compaction remains the last fallback

The document should be explicit that:

- rebuild affects only provider-view assembly
- raw transcript history is not deleted or rewritten
- the rebuild layer is not the same thing as `/rewind`
- `context_summary` remains valid but moves to a later fallback tier

### 6. Define a rebuild boundary and preserved-tail model

The document should add one explicit boundary model instead of vaguely saying “recent messages”.

The boundary rules should be fixed:

- each successful checkpoint captures a rebuild boundary
- the boundary identifies the first message that the rebuilt tail must preserve
- the preserved tail is chosen token-budget-first, not by a fixed message count
- boundary adjustment must remain API-safe for tool-use / tool-result pairing
- rebuild-time microcompact only applies to messages strictly newer than the boundary

This section should also say the Python runtime will reuse its current provider-view group and microcompact machinery where possible, rather than introducing a second tail-reduction system.

### 7. Define resume, fork, and hydration semantics

The document should make recovery behavior explicit.

For resume:

- restore checkpoint metadata from transcript state
- rebind the session to the existing `.pyclaude/checkpoints/<session_id>/`
- if checkpoint files are missing, fail soft:
  - mark checkpoint unavailable
  - fall back to current provider-view behavior
  - do not fail session restore

For fork / child session:

- child sessions get their own checkpoint directory
- parent and child never write the same checkpoint files
- child checkpoint writing is opt-in by runtime ownership, not automatic for every child session
- any future hidden checkpoint writer spawned inside a child uses the child’s checkpoint scope, not the parent’s

The document should explicitly avoid MiMo’s broader multi-actor checkpoint inheritance depth in v1.

### 8. Add checkpoint-aware prompt/context surfaces

The document should make inspection a first-class part of the work.

Add additive surfaces to `/context`, `/status workflow`, remote/headless payloads, and TUI for:

- checkpoint available: yes/no
- checkpoint writer running: yes/no
- last checkpoint age
- last checkpoint boundary summary
- rebuild injection active: yes/no
- rebuilt tail chars or tokens
- rebuild fallback reason
- whether the current call used:
  - normal provider view
  - checkpoint rebuild
  - full compaction fallback

The document should define checkpoint-aware vocabulary so this line does not get mislabeled as ordinary compaction.

Suggested wording:

- `checkpoint writer`
- `checkpoint rebuild`
- `rebuild boundary`
- `rebuild tail`
- `rebuild-first recovery`
- `full compaction fallback`

### 9. Keep scope narrow and explicitly exclude broader MiMo breadth

The document should close with sharp boundaries.

Out of scope for this phase:

- SQLite FTS memory search
- dream / distill
- full MiMo project-memory promotion logic
- broader actor registry / telemetry depth around checkpoint writer
- hosted checkpoint storage
- background autonomous checkpoint pipelines outside the current main-session runtime
- transcript schema redesign for reconstructed message slices
- replacing current project memory with a MiMo-style separate memory subsystem

This keeps the plan aligned to the current Python architecture rather than ballooning into a second product.

## Important Interfaces

The document should propose additive runtime interfaces along these lines:

- `runtime/session_checkpoint.py`
  - checkpoint file helpers
  - checkpoint state helpers
  - boundary metadata helpers
- `runtime/checkpoint_writer.py`
  - start/check/status helpers for hidden writer runs
- `runtime/rebuild_injection.py`
  - build rebuilt provider-view messages and prompt blocks
- `runtime/checkpoint_thresholds.py`
  - threshold parsing / crossed-threshold state / retry guard

Likely session/runtime additions:

- `Session.checkpoint_surface_payload()`
- `Session.rebuild_surface_payload()`
- `Session.current_checkpoint_dir()`
- `Session.current_checkpoint_file_path()`
- `Session.current_notes_file_path()`
- `Session.has_usable_checkpoint()`
- `Session.build_checkpoint_rebuild_view(...)`

Likely new persisted metadata on `SessionState`:

- checkpoint directory or slug
- last checkpoint boundary marker
- last checkpoint status / timestamp
- rebuild-active metadata
- checkpoint failure count or equivalent retry guard state

The document should keep provider interfaces unchanged.

## Test Plan

The document should include a phase-aware test plan with at least these groups.

### Checkpoint file lifecycle

- new session creates a stable checkpoint directory on first writer use
- checkpoint and notes files are created lazily
- resume restores file association
- missing files after resume fail soft and fall back cleanly

### Threshold triggering

- newly crossed thresholds start checkpoint writing once
- repeated turns above the same threshold do not refire endlessly
- active writer suppresses duplicate launches
- repeated writer failures stop retrying until reset conditions are met

### Hidden writer behavior

- writer can read session transcript and project memory
- writer can only write checkpoint-owned files
- writer output updates checkpoint metadata in the parent session
- writer does not appear in normal public tool or agent surfaces

### Rebuild injection

- budget pressure with usable checkpoint chooses rebuild before full compaction
- rebuilt provider view contains project memory, checkpoint, notes, and preserved tail
- replacement/artifact/microcompact still apply on rebuilt tail
- if rebuild still exceeds budget, current `context_summary` fallback path remains available

### Boundary and tail safety

- rebuilt tail preserves tool-use / tool-result coherence
- microcompact touches only post-boundary rebuild-tail content
- boundary drift is deterministic for the same checkpoint and recent tail input

### Surfaces and diagnostics

- `/context` and `/status workflow` expose checkpoint writer and rebuild state
- remote/headless payloads mirror the same checkpoint fields
- TUI shows checkpoint availability, rebuild mode, and fallback reason
- prompt-prefix / provider-view summaries distinguish checkpoint rebuild from normal compaction

### Regression

- current prompt-prefix planner, replacement/artifact/microcompact, and full compaction behavior remain unchanged when checkpoint rebuild is inactive
- provider API shape does not change
- transcript messages are not rewritten
- project memory loading remains unchanged

## Assumptions

- The new document should be named `CHECKPOINT_WRITER_REBUILD_INJECTION_PLAN.md`.
- The plan should be MiMo-mechanism-inspired, but adapted to the current Python runtime rather than copying MiMo’s product breadth.
- V1 reuses existing project memory and adds only session-scoped `checkpoint.md` and `notes.md`.
- V1 uses hidden runtime-owned child-session / subagent infrastructure for checkpoint writing.
- Rebuild injection is provider-view-only and leaves transcript history intact.
- Existing `context_summary` remains in the system as the final fallback instead of being removed.
