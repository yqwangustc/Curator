# Findings & Decisions — Audio ALM Pretrain Pipeline

## Requirements (from user)
- Input: (a) JSONL with one long-form-audio metadata row per line, (b) directory of source audio files
- Output: (a) JSONL with one snippet metadata row per line, (b) directory of snippet audio files
- Cutting algorithm:
  - Don't cut mid-segment
  - Drop overlapping segment pairs (overlap ≥ 0.5s OR full containment)
  - Filter empty segments **before** cutting
  - Each snippet ≤ `max_duration_sec` (pipeline arg, required)
  - Each snippet ≥ `min_duration_sec` (default 0.5s)
  - Single segment longer than max → drop & log under `too_long`
  - Snippet with no concatenated text → drop
- Output audio: always mono, always resampled to `target_sample_rate` (default 16000), channel-average if multi-channel
- Snippet ID format: `<original_id>_{st:.2f}_{en:.2f}`
- Snippet record: keep all original top-level fields **except `alignment`**; keep original `id`; add `snippet_id`; replace `audio_filepath`, `duration`, `segments`
- Segment timestamps in output: relative to snippet start (also shift `words[].start/end`)
- Metrics: per-input segment count + duration; per-output snippet count + duration; dropped-overlap count; snippet-duration distribution (30s histogram bins)
- Code locations:
  - Stages: `nemo_curator/stages/audio/alm/pretrain/stages.py` (+ `__init__.py`)
  - Pipeline definition + tutorial: `tutorials/audio/audio_pretrain/{pipeline.py, run.py, README.md}` — follows the repo's `tutorials/<modality>/<feature>/pipeline.py` convention (`tutorials/audio/readspeech/` is the precedent)
- Docstring of pipeline must mention: ALM pretraining data; output supports interleaved audio/text continuation, ASR, TTS, and diarization tasks

## Research Findings — code conventions
- `AudioTask` (`nemo_curator/tasks/audio_task.py`) is a single-row dict task; `data` is `_AttrDict` so `task.data.audio_filepath` works.
- The existing `SegmentExtractionStage` (`stages/audio/io/extract_segments.py`) is the closest reference for the audio-cutting + JSONL-emit pattern but uses a different schema (`original_start_ms`, `diar_segments`); reusing it would be hostile to its existing callers.
- `CreateInitialManifestReadSpeechStage` is the canonical `_EmptyTask → list[AudioTask]` fan-out pattern. Use the same shape for our manifest-reader stage; declare it via `ray_stage_spec → IS_FANOUT_STAGE: True` and `xenna_stage_spec → {"max_workers_per_node": 1}` so Xenna doesn't allocate idle replicas (per CLAUDE.md guidance).
- `AudioToDocumentStage` shows the pattern for batch stages that aggregate without forwarding `_metadata` correctly — *don't* copy that quirk; we must forward `_stage_perf` and `_metadata` per CLAUDE.md.
- Existing stages live in `nemo_curator/stages/audio/alm/` — `alm_data_builder.py`, `alm_data_overlap.py`, both re-exported from `audio/alm/__init__.py`. New `pretrain/` subpackage will not collide.
- Soundfile (`soundfile.read(filepath, start=, stop=)`) is the existing way to slice audio without loading the full file. Use the same approach in `SnippetExtractionStage`.
- For resampling: `torchaudio.functional.resample` is already a project dependency (used by `vad_segmentation.py`). Prefer it over `librosa` to avoid adding deps. Channel-averaging is a `tensor.mean(dim=0)`.
- Lifecycle discipline: model loads / heavy state in `setup()`. Our pipeline doesn't need any model — only file I/O — so `setup()` not required for most stages.
- Project ruff config: `select = ["ALL"]`; line length 119; no docstring rule (`D` ignored); `T20` (`print`) ignored. Tests have additional ignores. Heavy imports inside functions OK (`PLC0415`).

## Schema findings — `test1.jsonl`
- Top-level keys observed in row 1 (representative):
  - `id`, `fulltitle`, `description`, `language`, `language_source`, `region`, `language_pred`, `language_pred_source`,
  - `channel_id`, `channel_name`, `channel_follower_count`, `view_count`, `like_count`,
  - `audio_sample_rate`, `audio_num_channels`, `audio_size`, `categories`, `subtitled`, `license`,
  - `youtube_upload_date`, `crawled_date`, `download_date`, `proposed_duration`, `actual_duration`,
  - `tags`, `metadata_filepath`, `audio_filepath` (`./test1.m4a`), `available_subs`, `subtitle_filepath`,
  - `dataset_name`, `dataset_id`, `dataset_version`, `youtube_id`, `module`, `created_at`,
  - `swift_audio_filepath`, `duration`, `resampled_audio_filepath`, `segments`
- Confirmed: field is **`text_ITN`** (uppercase), not `text_itn`. Empty-text segments exist (e.g. `text=""`, `words=[]`).
- `segments[].words[]` schema: `{word, start, end}` (start/end in seconds).
- The `alignment` field (per spec) must be stripped from output but isn't visible in the head-of-file probe; it may appear in some rows. Just unconditionally drop the key when emitting.

## Algorithm clarifications (resolved)
| Question | Resolution |
|----------|------------|
| Path resolution | `audio_dir` + basename of `audio_filepath` field |
| `text_ITN` casing | Preserve as `text_ITN` |
| Snippet packing | Greedy contiguous packing; snippet span = `[first.start, last.end]` |
| Single segment > max | Drop, count as `too_long` |
| Min snippet duration | `min_duration_sec=0.5`; also drop snippets with no text |
| Overlap rule | Pair overlaps if intersect ≥ 0.5s OR one fully contains the other |
| Empty segments | Filter **before** cutting |
| Snippet id | `f"{original_id}_{st:.2f}_{en:.2f}"` |
| Output format | Always mono, resampled to `target_sample_rate=16000` |
| Snippet timestamps | Relativize to snippet start; shift `words[].start/end` too |
| Carried fields | All original top-level fields except `alignment`; keep original `id`; add `snippet_id` |
| Metrics location | Per-task `_metadata` + per-replica JSONL shards merged into `metrics_summary.json` by `finalize_audio_pretrain_outputs` |
| Metrics writer architecture | Each replica appends one JSONL record per task in `process()` (no `teardown()` reliance — Xenna kills actors via `ray.kill()`); the merger sums `out_*` across non-stub records per id |
| Backend-safe writer | Each writer replica writes its own `<output>.shard-<pid>-<uuid>.jsonl`; `finalize_audio_pretrain_outputs` concatenates after `pipeline.run()` |
| Snippet ID precision | `:.3f` (millisecond precision; avoids collisions on adjacent short snippets) |
| Default `max_segment_gap_in_snippet` | `30.0`s — long silences are treated as semantic boundaries |
| Dry-run mode | `SnippetExtractionStage.dry_run=True` emits manifest + metrics with no audio I/O |
| Histogram bins | Fixed 30s width |
| Planner / extractor split | Keep separate stages |
| Reuse `SegmentExtractionStage` | No — build new stages |

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Pure helpers + thin stage wrappers | Lets the cutting algorithm be unit-tested without Ray / soundfile |
| Use `torchaudio.functional.resample` | Already in dependency tree (VAD stage), no new deps |
| Single-replica writer & metrics aggregator | Avoid concurrent JSONL append races; aggregator naturally runs once |
| Drop `alignment` unconditionally on output | User requested; keeps output schema lean |
| Forward `_metadata` and `_stage_perf` on every fan-out emit | Curator framework requirement (CLAUDE.md) |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
|       |            |

## Resources
- Curator project root: `/scratch/fsw/portfolios/llmservice/projects/llmservice_fm_text/users/yongqiangw/Curator`
- Project CLAUDE.md (architecture + conventions): `Curator/CLAUDE.md`
- `AudioTask`: `nemo_curator/tasks/audio_task.py`
- Reference cutting stage: `nemo_curator/stages/audio/io/extract_segments.py`
- Reference fan-out reader: `nemo_curator/stages/audio/datasets/readspeech/create_initial_manifest.py`
- VAD stage (resample reference): `nemo_curator/stages/audio/segmentation/vad_segmentation.py`
- Existing alm package: `nemo_curator/stages/audio/alm/`
- Test data: `Curator/test1.jsonl` (~352 KB), `Curator/test1.m4a`

## Visual/Browser Findings
- (none — work is purely code-based)
