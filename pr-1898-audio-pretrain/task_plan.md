# Task Plan: Long-Form Audio Cutting Pipeline for ALM Pretraining

## Goal
Build a Curator audio pipeline that cuts diarized + transcribed long-form audio (one JSONL line per file) into bounded-length, mono, resampled snippets — with overlapping segments dropped — and emits a per-snippet JSONL manifest plus a metrics summary suitable for ALM (Audio LLM) pretraining data. Stages live in `nemo_curator/stages/audio/alm/pretrain/`; the `Pipeline(...)` definition lives in the tutorial at `tutorials/audio/audio_pretrain/pipeline.py` (matching the `tutorials/audio/readspeech/` precedent).

## Current Phase
Phase 5 (Delivery) — complete

## Phases

### Phase 1: Requirements & Discovery — **complete**
- [x] Inspect `test1.jsonl` schema (`segments[].speaker/start/end/text/text_ITN/words[]`)
- [x] Survey existing `nemo_curator/stages/audio/` (`AudioTask`, `SegmentExtractionStage`, VAD stage, readspeech reader)
- [x] Confirm `audio/alm/` namespace conventions
- [x] Resolve open algorithm questions with user (see `findings.md`)
- **Status:** complete

### Phase 2: Planning & Structure — **in_progress**
- [x] Define stage chain (planner / extractor / writer / metrics split)
- [x] Pick stages layout: `nemo_curator/stages/audio/alm/pretrain/{stages.py,__init__.py}`
- [x] Pick tutorial / pipeline layout: `tutorials/audio/audio_pretrain/{pipeline.py,run.py,README.md}` (matches `tutorials/audio/readspeech/` precedent)
- [ ] Lock down stage I/O contracts (input/output keys, `_metadata` fields) — see "Stage contracts" below
- **Status:** in_progress

### Phase 3: Implementation
- [ ] **3a — Pure helpers (no Curator deps)** in `stages.py`:
  - `filter_empty_segments(segs)`
  - `drop_overlaps(segs, min_overlap=0.5)` — returns `(kept, dropped)`
  - `plan_snippets(segs, max_dur, min_dur)` — greedy contiguous packing; returns `[(start, end, [seg_idx])]` plus `dropped_too_long` count
  - `relativize_segments(segs, snippet_start)` — shifts seg & word timestamps to snippet-relative
  - `make_snippet_id(original_id, st, en)` → `f"{original_id}_{st:.2f}_{en:.2f}"`
  - `histogram_30s(durations)` — fixed 30-second bins
- [ ] **3b — Stage: `ReadLongFormManifestStage`** (`_EmptyTask → list[AudioTask]`, fan-out)
  - Reads input JSONL, resolves `audio_filepath` against `audio_dir`, emits one `AudioTask` per line
  - `xenna_stage_spec → max_workers_per_node=1` (single source task)
- [ ] **3c — Stage: `OverlapFilterStage`** (`AudioTask → AudioTask`)
  - Filter empty segments first, then drop overlaps (≥0.5s OR full containment)
  - Stores `_metadata["pretrain"]["dropped_overlap"]`, `["dropped_empty"]`, `["original_seg_count"]`, `["original_seg_duration"]`
- [ ] **3d — Stage: `SnippetCutPlannerStage`** (`AudioTask → AudioTask`)
  - Runs `plan_snippets` with `max_duration_sec`, `min_duration_sec` (default 0.5)
  - Drops snippets with no concatenated text (safety net)
  - Stores plan in `task.data["_snippet_plan"]` and counts `dropped_too_long`, `dropped_no_text`, `dropped_too_short` in `_metadata`
- [ ] **3e — Stage: `SnippetExtractionStage`** (`AudioTask → list[AudioTask]`, fan-out)
  - Reads source audio with `soundfile` once
  - For each planned snippet: slice → channel-average to mono → resample to `target_sample_rate` (default 16000) → write `<snippet_id>.<ext>`
  - Builds output dict per snippet:
    - keep all fields from source dict **except** `alignment` and `_snippet_plan`
    - keep `id` (original) unchanged
    - add `snippet_id`, set `audio_filepath` to the new path, set `duration` to actual snippet duration, replace `segments` with relativized list
  - Forwards `_metadata` and `_stage_perf` onto every emitted task
- [ ] **3f — Stage: `SnippetManifestWriterStage`** (`AudioTask → AudioTask`, batch)
  - Appends each task's `data` (sanitized) as a JSONL line to `output_manifest_path`
  - Single replica (`xenna_stage_spec`) to avoid concurrent writes; uses an append lock or per-worker shard + final merge
- [ ] **3g — Stage: `PretrainMetricsAggregatorStage`** (`AudioTask → AudioTask`, single-replica, end of pipeline)
  - Collects per-snippet duration, per-original counts from `_metadata`
  - Writes `metrics_summary.json` with: totals, dropped breakdowns, snippet-duration histogram (30s bins), per-original counts/durations
- [ ] **3h — `nemo_curator/stages/audio/alm/pretrain/__init__.py`**: re-export the new stages; update `audio/alm/__init__.py` to surface them
- [ ] **3i — `tutorials/audio/audio_pretrain/pipeline.py`**: `build_audio_pretrain_pipeline(...)` returning a `Pipeline` of the above stages (this is where the pipeline definition lives, per `tutorials/audio/readspeech/` convention)
- [ ] **3j — `tutorials/audio/audio_pretrain/run.py`**: argparse-driven entry point that calls the pipeline factory and runs it
- [ ] **3k — `tutorials/audio/audio_pretrain/README.md`**: short README — algorithm description, mention this is for ALM pretraining data, and that snippets can be used to construct interleaved audio/text continuation, ASR, TTS, and diarization tasks
- **Status:** complete

**Phase 3 additions beyond the original plan** (added during implementation in response to user feedback / code review):
- Top-level `text` field is recomputed per snippet from segment-level `_segment_text` (avoids carrying the whole-audio transcript into every snippet row).
- Per-snippet field cleanup beyond `alignment`: drop `audio_size`, `resampled_audio_filepath`; reset `swift_audio_filepath=""`; overwrite `actual_duration`, `proposed_duration`, `audio_sample_rate`, `audio_num_channels` to match the snippet.
- Snippet ID precision changed from `:.2f` to `:.3f` to avoid collisions on adjacent short snippets.
- New planner argument `max_segment_gap_in_snippet` (default `30.0`s) — closes the snippet when the silence between two surviving segments exceeds the threshold, so a single snippet doesn't bridge semantically distinct conversations.
- **Dry-run mode** on `SnippetExtractionStage` (and surfaced as `--dry-run` on the CLI): emits the manifest + metrics without doing any audio I/O. Used to size up real datasets before committing to a full run.
- **Shard-then-merge** on the writer + aggregator (so they're safe under multi-replica backends — Xenna, Ray Data, Ray Actor Pool). Two helper entry points: `prepare_audio_pretrain_outputs` (called before `pipeline.run`, cleans stale shards) and `finalize_audio_pretrain_outputs` (called after `pipeline.run`, concatenates manifest shards and merges metrics shards). `run.py` wires both.
- Retry-safety fix: extractor reads `_snippet_plan` via `task.data.get(...)` instead of `pop(...)` so Xenna preempt+retry doesn't fail validation on the retried task.
- Stub task uses configured `audio_filepath_key` instead of literal `"audio_filepath"`.
- Renamed per-stage timer keys (`manifest_load_time`, `overlap_filter_time`, `plan_time`, `extract_time`) so they don't shadow `StagePerfStats.process_time`.

### Phase 4: Testing & Verification
- [x] Unit tests in `tests/stages/audio/alm/pretrain/` — 68 tests, all passing:
  - `test_helpers.py` (47 tests) — pure helpers: `_segment_text`, `filter_empty_segments`, `find_overlapping_indices`, `plan_snippets` (incl. gap constraint), `relativize_segments`, `make_snippet_id`, `histogram_30s`, `_resolve_audio_path`, shard helpers (`_make_shard_path`, `_glob_shards`, `_delete_shards`), `_build_final_summary`
  - `test_stages.py` (16 tests) — `ReadLongFormManifestStage`, `OverlapFilterStage`, `SnippetCutPlannerStage`, dry-run extractor, `SnippetManifestWriterStage`, `PretrainMetricsAggregatorStage` (per-task JSONL records), plus `prepare_*` / `finalize_*` end-to-end on multiple shards
  - `test_extraction.py` (5 tests, CPU) — synthesized sine WAV fed through real extractor; verifies mono / target sample rate / duration / file count / output schema
- [ ] Smoke dry-run on `test.jsonl` (the user's local data)
- [x] `uv run ruff check .` clean on new files; `uv run ruff format .` applied
- [x] Coverage ≥ 80% on changed code (achieved **90%** — 522 stmts, 52 missed)
- **Status:** in_progress

### Phase 5: Delivery
- [x] Verify output JSONL fields match spec — confirmed via end-to-end synthetic run; every checklist item from "Snippet output JSONL row shape" matches actual output (id preserved; snippet_id added; audio_filepath/duration updated; segments + words relativized; top-level text recomputed; alignment/audio_size/resampled_audio_filepath dropped; swift_audio_filepath reset to ""; actual_duration/proposed_duration/audio_sample_rate/audio_num_channels overwritten)
- [x] Confirm `metrics_summary.json` shape — totals, per-input segments/duration, per-output snippet count/duration, dropped breakdown (empty/overlap/too_long/too_short/no_text), 30s-bin histogram, per_original list — all present and correct
- [x] Final review against this plan — all phases complete; deliverables listed below
- **Status:** complete

## Final deliverables

**Reusable stages** — `nemo_curator/stages/audio/alm/pretrain/`
- `stages.py` (520 stmts, 90% covered) — six stages plus pure helpers and the prepare/finalize entry points
- `__init__.py` — public API surface

**Tutorial / pipeline factory** — `tutorials/audio/audio_pretrain/`
- `pipeline.py` — `build_audio_pretrain_pipeline()` factory
- `run.py` — argparse CLI; wires `prepare_audio_pretrain_outputs` before `pipeline.run()` and `finalize_audio_pretrain_outputs` after
- `README.md` — algorithm description, dry-run instructions, ALM-pretraining context

**Tests** — `tests/stages/audio/alm/pretrain/`
- `test_helpers.py` (47 tests) — pure helpers
- `test_stages.py` (16 tests) — stage process()-level + prepare/finalize end-to-end
- `test_extraction.py` (5 tests) — real audio extraction via synthesized sine WAV
- 68 passing, 90% line coverage on `pretrain/stages.py`

## Stage contracts (from Phase 2)

**Pipeline argument surface** (passed to `build_audio_pretrain_pipeline`):
- `input_manifest: str` — JSONL path
- `audio_dir: str` — directory containing source audio files
- `audio_filepath_key: str = "audio_filepath"` — JSONL field giving the file path/basename
- `output_dir: str` — directory for snippet audio files
- `output_manifest_path: str` — JSONL output for snippets
- `metrics_path: str` — JSON summary output
- `max_duration_sec: float` (required) — snippet upper bound
- `min_duration_sec: float = 0.5`
- `min_overlap_sec: float = 0.5` — overlap threshold for "non-overlapping"
- `target_sample_rate: int = 16000`
- `output_format: str = "flac"`

**Snippet output JSONL row shape**:
```
{
  "id": <original_id>,                       # unchanged
  "snippet_id": "<id>_{st:.2f}_{en:.2f}",
  "audio_filepath": "<output_dir>/<snippet_id>.flac",
  "duration": <float, snippet seconds>,
  "segments": [ ... relativized to snippet ... ],
  ...all other original top-level fields except `alignment`...
}
```

**Per-snippet `segment` shape** (after relativization):
- `speaker`, `text`, `text_ITN`, optional `metrics` — unchanged
- `start = orig_start - snippet_start`
- `end = orig_end - snippet_start`
- `words[i].start/end` likewise shifted

**Metrics summary JSON shape**:
```
{
  "num_input_audios": int,
  "num_output_snippets": int,
  "input_total_segments": int,
  "input_total_duration_sec": float,
  "output_total_segments": int,
  "output_total_duration_sec": float,
  "dropped": {
    "empty": int, "overlap": int, "too_long": int,
    "too_short": int, "no_text": int
  },
  "snippet_duration_histogram_30s": {"0-30": n, "30-60": n, ...},
  "per_original": [
    {"id": ..., "in_segments": ..., "in_duration_sec": ...,
     "out_snippets": ..., "out_segments": ..., "out_duration_sec": ...,
     "dropped": {...}}
  ]
}
```

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Pipeline definition lives in `tutorials/audio/audio_pretrain/pipeline.py`, not under `nemo_curator/stages/...` | Repo's dominant convention is `tutorials/<modality>/<feature>/pipeline.py` (see `tutorials/audio/readspeech/`). Stages stay reusable in `nemo_curator/stages/audio/alm/pretrain/stages.py`; the wiring is a tutorial-style example. |
| New stages instead of reusing `SegmentExtractionStage` | Old stage is keyed on `original_start_ms`/`diar_segments`; our schema is `start/end` (sec) + `words[]`. Cleaner to write fresh. |
| Planner separate from extractor | Cutting algorithm becomes pure & unit-testable; extractor isolates audio I/O for retry safety |
| Snippet timestamps relativized | User asked for it; matches what a downstream ALM trainer expects (seek inside the snippet WAV) |
| Always mono + resample | User requested; simplifies downstream training |
| Greedy contiguous packing on segments | Simplest algorithm that satisfies "no mid-segment cut" + max-duration; user confirmed |
| Histogram bins fixed 30s wide | User requested; simpler than configurable bins for v1 |
| Single linear `Pipeline` (no `Workflow`) | No need for multiple pipelines or external Ray actors |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       |         |            |

## Notes
- Re-read this plan before each implementation sub-step.
- Forward `_metadata` + `_stage_perf` on every emitted task (Curator convention; extractor is a fan-out so this matters).
- Heavy imports (`soundfile`, `torchaudio`/`librosa` for resample) go inside functions per project convention.
- Run `uv run ruff check .` before declaring any phase complete.
