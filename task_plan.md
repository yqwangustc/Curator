# Task Plan: Longform Speaker-Aware Audio Cut

## Goal
Implement a reusable longform audio cut algorithm that emits snippets made only from consecutive non-"no-speaker" segments, sharing as much infrastructure as practical with the existing pretrain cut flow.

## Current Phase
Phase 6

## Phases

### Phase 1: Requirements & Discovery
- [x] Capture user requirements.
- [x] Inspect existing tutorial/audio/audio_pretrain/run.py and pretrain cut implementation.
- [x] Identify reusable infrastructure and extension points.
- **Status:** complete

### Phase 2: Design Shared Structure
- [x] Decide how to factor shared manifest/audio/tar/metrics logic.
- [x] Define the new cut strategy interface or function boundaries.
- [x] Record technical decisions in findings.md.
- **Status:** complete

### Phase 3: Implementation
- [x] Implement the no-speaker-aware cut algorithm.
- [x] Add CLI/tutorial entrypoint and argument plumbing.
- [x] Preserve dry-run vs tar-writing behavior.
- **Status:** complete

### Phase 4: Tests & Verification
- [x] Add or update focused tests.
- [x] Run targeted test suite and formatting/lint checks where available.
- [x] Log test results in progress.md.
- **Status:** complete

### Phase 5: Delivery
- [x] Review diffs for scope and consistency.
- [x] Summarize changed files and verification.
- **Status:** complete

### Phase 6: Linux Remote Verification
- [ ] Clone or checkout branch `diar_cut` on a Linux host.
- [ ] Run targeted pytest for the new no-speaker cut tests.
- [ ] Run broader pretrain audio tests if dependencies permit.
- [ ] Fix any Linux-only test failures.
- **Status:** pending

## Key Questions
1. Where does the current pretrain cut implementation live, and what reusable interfaces already exist?
2. How are speaker/no-speaker segments represented in manifests?
3. What output manifest schema is expected for cut audio snippets?
4. Which backends and execution modes are already supported by pretrain cut?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use file-based planning for this task. | The request requires discovery, refactor judgment, implementation, and tests across several files. |
| Add a new planner stage and pipeline builder while reusing existing reader/extractor/writer/finalizer/metrics stages. | The existing infrastructure already handles manifest fan-out, audio path resolution, dry-run emission, tar shards, output manifest shards, and metrics merging; only the cut-boundary strategy needs to differ. |
| Do not run the existing `OverlapFilterStage` in the no-speaker cut pipeline. | It drops empty no-speaker segments before planning, which would remove boundary markers and allow snippets to cross no-speaker regions. |
| Continue verification on Linux from branch `diar_cut`. | Local macOS test collection is blocked by the project’s Linux-only import guard. |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `pytest` command not found | 1 | Switched to `uv run pytest`. |
| `uv run pytest` fails on macOS because `nemo_curator/__init__.py` raises on non-Linux platforms | 1 | Recorded as environment blocker; used `py_compile` and `ruff` checks for available verification. |
| Spoofing `sys.platform` to run pytest caused platform-specific dependency import failures | 2 | Stopped that approach to avoid unreliable verification; kept the explicit macOS blocker. |

## Notes
- Keep edits scoped to the new cut algorithm and shared infrastructure needed to support it.
- Do not revert unrelated worktree changes.
- On Linux, start with `uv run pytest -q tests/stages/audio/alm/pretrain/test_no_speaker_cut.py`.
- Then consider `uv run pytest -q tests/stages/audio/alm/pretrain` to catch regressions in the shared pretrain infrastructure.
