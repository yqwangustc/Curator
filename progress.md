# Progress Log — Audio ALM Pretrain Pipeline

## Session: 2026-04-29

### Phase 1: Requirements & Discovery
- **Status:** complete
- **Started:** 2026-04-29
- Actions taken:
  - Read user spec for the long-form-audio cutting pipeline
  - Inspected `test1.jsonl` first record to confirm schema (incl. `text_ITN` casing, empty-text segments)
  - Surveyed existing stages: `AudioTask`, `SegmentExtractionStage`, `VADSegmentationStage`, `CreateInitialManifestReadSpeechStage`, `AudioToDocumentStage`
  - Surveyed existing `nemo_curator/stages/audio/alm/` (existing modules: `alm_data_builder`, `alm_data_overlap`)
  - Asked the user 16 clarifying questions; all answered
- Files created/modified:
  - (research only — no code yet)

### Phase 2: Planning & Structure
- **Status:** in_progress
- Actions taken:
  - Locked stage chain: read-manifest → overlap-filter → cut-planner → snippet-extractor → manifest-writer → metrics-aggregator
  - Locked stages layout under `nemo_curator/stages/audio/alm/pretrain/` (stages-only)
  - Locked pipeline + tutorial layout under `tutorials/audio/audio_pretrain/{pipeline.py, run.py, README.md}` after asking about repo conventions; follows `tutorials/audio/readspeech/` precedent
  - Recorded all algorithm decisions in `findings.md`
  - Wrote `task_plan.md` and `findings.md`
- Files created/modified:
  - `task_plan.md` (created)
  - `findings.md` (created)
  - `progress.md` (created)

### Phase 3: Implementation
- **Status:** complete
- Actions taken:
  - Wrote six stages in `nemo_curator/stages/audio/alm/pretrain/stages.py` plus pure helpers
  - Wired `nemo_curator/stages/audio/alm/__init__.py` re-exports for new stages
  - Built pipeline factory + CLI runner under `tutorials/audio/audio_pretrain/`
  - Iterated based on user feedback / code review:
    - Recompute top-level `text` per snippet (was carrying whole-audio transcript)
    - Drop / overwrite per-snippet stale fields (`audio_size`, `resampled_audio_filepath`, `actual_duration`, `proposed_duration`, `audio_sample_rate`, `audio_num_channels`, `swift_audio_filepath`)
    - Snippet ID precision `:.2f` → `:.3f` (collision avoidance)
    - Added `max_segment_gap_in_snippet` (default 30s) — avoid bridging semantically distinct conversations
    - Added dry-run mode (`SnippetExtractionStage.dry_run`, `--dry-run` CLI)
    - Added shard-then-merge for writer + aggregator (multi-replica safe under all backends), with `prepare_audio_pretrain_outputs` / `finalize_audio_pretrain_outputs` driver-side helpers
    - Retry-safety: extractor reads plan via `get` not `pop`
    - Stub task uses configured `audio_filepath_key`
    - Renamed per-stage timer keys (no clash with `StagePerfStats.process_time`)
- Files created/modified:
  - `nemo_curator/stages/audio/alm/pretrain/stages.py` (created)
  - `nemo_curator/stages/audio/alm/pretrain/__init__.py` (created)
  - `nemo_curator/stages/audio/alm/__init__.py` (re-exports added)
  - `tutorials/audio/audio_pretrain/{pipeline.py,run.py,README.md}` (created)

### Phase 4: Testing & Verification
- **Status:** complete
- Actions taken:
  - Wrote `tests/stages/audio/alm/pretrain/test_helpers.py` (47 tests, all passing)
  - Wrote `tests/stages/audio/alm/pretrain/test_stages.py` (16 tests: stages + finalize/prepare end-to-end)
  - Wrote `tests/stages/audio/alm/pretrain/test_extraction.py` (5 tests: real audio I/O via synthesized sine)
  - Ran full pretrain test suite: **68 passed, 0 failed in 43s**
  - Ruff clean across all new files (after auto-fixes)
  - Coverage report on pretrain package: **90%** (522 stmts, 52 missed) — exceeds 80% gate
- Files created/modified:
  - `tests/stages/audio/alm/pretrain/{__init__.py,test_helpers.py,test_stages.py,test_extraction.py}` (created)

### Phase 5: Delivery
- **Status:** complete
- Actions taken:
  - End-to-end run on synthetic data (mono sine at 22050 Hz, manifest with 7 segments incl. empty + overlap-pair)
  - Verified actual output files match every spec line item (snippet IDs, audio properties, JSONL row shape, metrics JSON shape, dropped-segment counts, gap-aware splitting)
  - Updated task_plan.md with deliverables list
- Files created/modified:
  - `task_plan.md` (Phases 4 + 5 marked complete; deliverables listed)

### Phase 5: Delivery
- **Status:** pending

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
|      |       |          |        |        |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
|           |       |         |            |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 2 — Planning & Structure (almost done) |
| Where am I going? | Phase 3 (Implementation) → Phase 4 (Tests) → Phase 5 (Delivery) |
| What's the goal? | Cut diarized long-form audio into bounded mono resampled snippets for ALM pretraining, with overlap dropping + metrics |
| What have I learned? | See findings.md |
| What have I done? | Discovery + planning files committed to disk |
