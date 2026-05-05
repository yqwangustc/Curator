# Task: Tar snippet audio output in SnippetExtractionStage

## Goal

Replace per-file snippet audio output with a single tar archive (per pipeline output) to avoid generating many small files. Add a new **required** `output_audio_tar_path` argument to `build_audio_pretrain_pipeline` that causes `SnippetExtractionStage` to write all extracted snippet audio bytes into one tar file (under `output_dir`) instead of individual files.

## Scope

- File: `nemo_curator/stages/audio/alm/pretrain/stages.py`
  - `SnippetExtractionStage`: add **required** `output_audio_tar_path` field; write each snippet's audio bytes into a per-replica tar shard (using `tarfile`+`io.BytesIO`). The legacy per-file write path is **removed** (no backward compatibility).
  - The stage emits manifest entries whose `audio_filepath` is the tar-internal basename (`<snippet_id>.<ext>`) — webdataset/Energon convention.
  - Add `_TAR_SHARD_EXT = "tar"`; add helper `_merge_tar_shards`; update `prepare_audio_pretrain_outputs` and `finalize_audio_pretrain_outputs` to clean up / merge tar shards.
- File: `nemo_curator/stages/audio/alm/pretrain/pipeline.py`
  - `build_audio_pretrain_pipeline`: accept required `output_audio_tar_path: str`; pass through to `SnippetExtractionStage`. Update `prepare_*` and `finalize_*` call sites in `run.py`.
- File: `tutorials/audio/audio_pretrain/run.py`
  - Add **required** `--output-audio-tar-path` CLI flag; thread into builder, `prepare_*`, and `finalize_*`.
- Tests: `tests/stages/audio/alm/pretrain/test_extraction.py` (unit) and `test_pipeline.py` (e2e).

## Design decisions

1. **Tar mode is the only mode**: `output_audio_tar_path` is required. Per-file write path is removed.
2. **Per-replica shards**: same pattern as `SnippetManifestWriterStage` -- each replica writes its own `<output_audio_tar_path>.shard-<pid>-<rand>.tar`; merger re-writes via Python `tarfile` (portable, no GNU-tar dep). Shards cleaned up in `prepare_*`.
3. **Manifest `audio_filepath` is the tar-internal basename** (`<snippet_id>.<ext>`). Matches webdataset/Energon convention; sample key (`<snippet_id>`) is everything before the first `.`, which is safe because `make_snippet_id` already replaces decimal `.` with `_`.
4. **Tar members live at the tar root** (no subdirectories) and **are sorted lexicographically during merge** — Energon-friendly.
5. **Dry run + tar**: in `dry_run=True`, **no tar file is created and no tar shard is opened**. The emitted manifest entries still use the tar-internal basename for `audio_filepath` (consistent with real runs).
6. **Stub tasks**: unchanged; no audio written for stubs.
7. **In-memory encoding**: write to `io.BytesIO` via `sf.write(buf, mono, sr, format=output_format.upper(), subtype=...)`, then `tarfile.TarFile.addfile(tarinfo, BytesIO)`.

## Phases

### Phase 1: Plumb `output_audio_tar_path` through SnippetExtractionStage  → status: complete
- Add required `output_audio_tar_path: str = ""` field; raise in `__post_init__` if empty.
- In `setup()`: compute `self._tar_shard_path = _make_shard_path(self.output_audio_tar_path, _TAR_SHARD_EXT)` (skip when `dry_run`).
- Refactor `_extract_one_snippet` to: encode mono into `BytesIO` with soundfile, build a `TarInfo`, append to the per-replica tar shard (open shard for `"a"` per snippet — see findings; or hold an open `TarFile` on the instance and close in teardown — picking the latter for fewer fopen()/fclose() calls).
- `_make_snippet_task`: `out_path = f"{snippet_id}.{ext}"` (basename only).
- `_dry_run_emit`: same basename behavior, but no tar.
- Verify: `tarfile.is_tarfile()` on the shard, `audio_filepath` is the basename only (no slashes).

### Phase 2: Tar-shard merge / cleanup helpers  → status: complete
- Add `_TAR_SHARD_EXT = "tar"`.
- Add `_merge_tar_shards(output_path)` using Python `tarfile`: open final tar in `"w"`, gather all `(tarinfo, body_bytes)` from every shard, **sort by member name**, then `addfile` in sorted order. Skip when no shards (re-run safety mirrors manifest/metrics).
- `prepare_audio_pretrain_outputs(output_manifest_path, metrics_path, output_audio_tar_path)` and `finalize_audio_pretrain_outputs(output_manifest_path, metrics_path, output_audio_tar_path)` gain a required `output_audio_tar_path` arg.
- Verify: after multi-shard run, single tar file exists with sorted, expected entries; tar shards removed; safe to re-run.

### Phase 3: Plumb through pipeline + CLI  → status: complete
- `build_audio_pretrain_pipeline`: required `output_audio_tar_path: str`, forwarded to `SnippetExtractionStage`.
- `run.py`: add `--output-audio-tar-path` (required) flag; pass into builder + prepare/finalize.
- Verify: `python -m tutorials.audio.audio_pretrain.run --help` shows new flag.

### Phase 4: Tests  → status: complete
- `test_extraction.py`: tar-mode test that writes 2 snippets, asserts (a) tar file exists, (b) tar contains exactly the 2 expected member names sorted, (c) each member is a readable audio with correct sr/duration, (d) `audio_filepath` is the tar-internal basename.
- `test_extraction.py`: dry-run + tar-mode test → manifest entries point at basename; **no tar shard or tar file created**.
- `test_extraction.py`: missing `output_audio_tar_path` → `__post_init__` raises.
- Update existing tests in `test_extraction.py` and `test_pipeline.py` (`SnippetExtractionStage(...)` constructor calls now need `output_audio_tar_path`).
- `test_pipeline.py`: end-to-end happy path with tar mode (real synth audio). Assert tar shards merged, single final tar, members match manifest's `audio_filepath`s, members sorted.
- Verify all phase-4 tests pass with `pytest tests/stages/audio/alm/pretrain/`.

### Phase 5: Smoke test on user's debug data  → status: complete
- Run the pipeline against the user-provided test args (`../tmp/debug/inputs/test.jsonl`, `../tmp/debug/inputs/audio`, etc.).
- Verify `../tmp/debug/outputs/shard_0.tar` is produced, manifest's `audio_filepath` values match the tar member set exactly.
- **First run uncovered**: `_merge_tar_shards` crashed on truncated shards (Xenna `ray.kill` mid-write). Fixed by walking shards member-by-member with `next()` instead of `for ti in in_tar:` and stopping at the first malformed header / unreadable member, keeping all members up to that point.
- **Second run uncovered**: 27 of 28 manifest rows had matching tar members; one snippet was lost because the manifest writer flushed its JSONL line before the tar member's bytes reached disk. Added `_reconcile_manifest_with_tar` to drop manifest rows whose `audio_filepath` isn't a valid tar member; called from `finalize_audio_pretrain_outputs` after all merges. Added a unit test covering the drop behavior.

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| `tarfile.ReadError: unexpected end of data` during merge | 1 | Walk shards manually with `in_tar.next()`; stop at first malformed header; log how many members were recovered. |
| 1 manifest row referenced a tar member that wasn't intact in the tar | 2 | Added `_reconcile_manifest_with_tar` post-merge step + unit test. |

## Files Modified

| File | Change |
|------|--------|
| nemo_curator/stages/audio/alm/pretrain/stages.py | tar-shard write path in SnippetExtractionStage; `_TAR_SHARD_EXT`, `_merge_tar_shards`; `prepare_*`/`finalize_*` extended |
| nemo_curator/stages/audio/alm/pretrain/pipeline.py | new required `output_audio_tar_path` arg threaded into stage |
| tutorials/audio/audio_pretrain/run.py | new required `--output-audio-tar-path` flag, threaded into builder + prepare/finalize |
| tests/stages/audio/alm/pretrain/test_extraction.py | tar-mode unit tests; existing tests updated |
| tests/stages/audio/alm/pretrain/test_pipeline.py | e2e tar-mode test; existing tests updated |
