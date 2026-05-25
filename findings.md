# Findings & Decisions

## Requirements
- Add a cut algorithm similar to the existing `pretrain_cut`.
- Inputs: `input-manifests`, `audio-dir`, `output-dir`, `output-manifest`, `output-audio-tar-path`, `metrics-path`, `max-duration-sec`, `min-duration-sec`, `target-sample-rate`, `output-format`, `audio-filepath-key`, `audio-path-resolution`, `backend`, `execution-mode`, `dry-run`, and `verbose`.
- Argument meanings should match or closely follow `tutorials/audio/audio_pretrain/run.py`.
- Algorithm scans longform audio segments, starts a snippet at a non-"no-speaker" segment, grows through consecutive non-"no-speaker" segments, and emits when a "no-speaker" or similar label is encountered.
- Dry-run emits only the manifest. Non-dry-run cuts audio and writes all output audio into a tar file.
- Reuse or refactor existing pretrain cut infrastructure so future cut algorithms can share it.

## Research Findings
- Existing long-form audio cut code is concentrated under `nemo_curator/stages/audio/alm/pretrain/`.
- The tutorial entrypoint is `tutorials/audio/audio_pretrain/run.py`.
- Existing tests live under `tests/stages/audio/alm/pretrain/`.
- The repository already has unrelated untracked files (`.agents/`, `a_10.jsonl`); leave them untouched.
- The active pretrain pipeline is split across `pipeline.py`, `planning.py`, `extraction.py`, `io.py`, `finalize.py`, and `utils.py`; `stages.py` appears to be an older combined copy kept in the tree.
- Existing reusable infrastructure already handles manifest reading, audio path resolution, dry-run metadata emission, tar-shard writing/merging, manifest shards, and metrics shards.
- Input segments use a `speaker` field. Real fixture data contains segments with `speaker: "no-speaker"`, and some of those no-speaker segments may still carry `text`/`words`, so the new algorithm must inspect labels, not only text emptiness.
- User-provided `a_10.jsonl` has 10 rows, 769 total segments, and 374 segments with `speaker: "no-speaker"`.
- `a_10.jsonl` top-level rows preserve many metadata fields (`audio_filepath`, `audio_sample_rate`, `audio_num_channels`, `actual_duration`, `segments`, `alignment`, etc.); the existing `SnippetExtractionStage` already carries metadata through and removes source-only fields.
- Existing ALM builder code treats exact `speaker == "no-speaker"` specially; tagging utilities create gap segments with the exact label `"no-speaker"`.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Implement `NoSpeakerCutPlannerStage` in `planning.py`. | It keeps the cut strategy colocated with the existing pretrain planner while preserving shared downstream `_snippet_plan` contract. |
| Build a separate no-speaker cut pipeline without overlap or repetition filters. | The requested algorithm is simpler than pretrain cut and `OverlapFilterStage` would erase empty no-speaker boundary segments. |
| Use label normalization for no-speaker detection. | Real data uses `no-speaker`; normalization also catches obvious variants like `no_speaker` and `no speaker` per the "or similar label" requirement. |
| Add `no_speaker` to metrics only when planner metadata includes `dropped_no_speaker`. | This exposes the new algorithm's boundary/drop count without changing existing pretrain metrics schemas that do not use the new planner. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Targeted pytest cannot collect on this macOS host because `nemo_curator/__init__.py` raises unless `sys.platform == "linux"`. | Recorded as a verification blocker; ran `py_compile` and `ruff` checks successfully. |
| Spoofing `sys.platform` to bypass the guard caused Ray/psutil platform import failures. | Abandoned spoofing to avoid misleading test results. |
| Remaining verification needs a Linux host. | Commit and push branch `diar_cut`, then run targeted pytest from Linux. |

## Resources
- `/Users/yongqiangw/Work/nemo/Curator/tutorials/audio/audio_pretrain/run.py`
- `/Users/yongqiangw/Work/nemo/Curator/nemo_curator/stages/audio/alm/pretrain/`
- `/Users/yongqiangw/Work/nemo/Curator/tests/stages/audio/alm/pretrain/`
- `/Users/yongqiangw/Work/nemo/Curator/tests/fixtures/audio/tagging/reference/tts/test_data_reference.jsonl`
- `/Users/yongqiangw/Work/nemo/Curator/a_10.jsonl`

## Visual/Browser Findings
- Not applicable.
