# Findings

## Existing pipeline & stage architecture

- `SnippetExtractionStage` (in `nemo_curator/stages/audio/alm/pretrain/stages.py:820`) currently writes one audio file per snippet to `output_dir/<snippet_id>.<output_format>` via `soundfile.write` (called from `_extract_one_snippet`).
- Each snippet's manifest row carries `audio_filepath = output_dir/<snippet_id>.<ext>`.
- `_make_snippet_task` (line 1031) is where the manifest row is constructed; only `out_path` and `duration` come from `_extract_one_snippet` — clean injection point.
- `_dry_run_emit` (line 941) reuses the same `_make_snippet_task` and computes `out_path` itself — also a clean injection point.

## Existing shard / merge pattern

`SnippetManifestWriterStage` (line 1098) and `PretrainMetricsAggregatorStage` (line 1156) already shard per-replica:
- In `setup()` each replica computes `self._shard_path = _make_shard_path(self.output_path, EXT)` (line 90 helper).
- In `process()` each replica appends to its shard.
- After `pipeline.run()`, the driver calls `finalize_audio_pretrain_outputs` (line 1275) which calls `_merge_manifest_shards` and `_merge_metrics_shards` — both glob shards, concat into the user-facing path, and remove shards.
- `prepare_audio_pretrain_outputs` (line 1259) deletes any stale shards before run (re-run safety).

This is the pattern to mirror for the tar shards.

## Useful constants and helpers

- `_make_shard_path(output_path, ext)` at line 84 → returns `f"{output_path}.shard-{pid}-{hex8}.{ext}"`.
- `_glob_shards(output_path, ext)` at line 93 → sorted glob of `.shard-*.<ext>`.
- `_delete_shards(output_path, ext)` at line 97 → removes them.
- `_SOUNDFILE_SUBTYPES` at line 60 → maps output_format ("wav"/"flac"/"ogg") to subtype ("PCM_16", "PCM_16", "VORBIS").

## Audio encoding to in-memory bytes

`soundfile.write` accepts a file-like object (e.g. `io.BytesIO`) when given an explicit `format` argument:

```python
import io, soundfile as sf
buf = io.BytesIO()
sf.write(buf, mono, sample_rate, format=output_format.upper(), subtype=_SOUNDFILE_SUBTYPES[output_format])
data = buf.getvalue()
```

Format strings are case-insensitive in libsndfile but documented uppercase: `"WAV"`, `"FLAC"`, `"OGG"`.

## Tar shard merge — pure Python

```python
import tarfile

def _merge_tar_shards(output_path):
    shards = _glob_shards(output_path, _TAR_SHARD_EXT)
    if not shards:
        logger.info(f"no tar shards found for {output_path}; skipping merge")
        return
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with tarfile.open(output_path, "w") as out_tar:
        for s in shards:
            with tarfile.open(s, "r") as in_tar:
                for ti in in_tar:
                    f = in_tar.extractfile(ti) if ti.isreg() else None
                    out_tar.addfile(ti, f)
    for s in shards:
        try:
            os.remove(s)
        except OSError as e:
            logger.warning(f"failed to remove tar shard {s}: {e}")
```

This re-writes through `tarfile`, which is portable and correctly handles header/padding boundaries. Dependency-free.

## Test fixture for end-to-end tar test

The existing `test_pipeline.py::TestPipelineEndToEndOnLocalManifest` runs in dry-run mode (no audio files needed). A new tar-mode e2e test needs *real* audio for the extractor to do real work. Options:
1. Use an existing source-audio synth (`_make_wav` already exists in `test_extraction.py`) and stage it into `audio_dir`.
2. Stage the real test fixtures from `../tmp/debug/inputs/audio` — would tie tests to a non-checked-in directory. Skip.

Going with option 1: synth a few short wavs whose ids match the first N rows of the sample manifest (or rebuild a tiny in-memory manifest pointing at the synthed files).

## User-supplied debug paths (Phase 5 smoke test)

- `--input-manifest ../tmp/debug/inputs/test.jsonl`
- `--audio-dir ../tmp/debug/inputs/audio`
- `--output-dir ../tmp/debug/outputs`
- `--output-manifest ../tmp/debug/outputs/shard_0.jsonl`  *(note: user typed `.josnl` — typo, will use `.jsonl`)*
- `--output-audio-tar-path ../tmp/debug/outputs/shard_0.tar`
- `--metrics-path ../tmp/debug/outputs/shard_0_metrics.json`
- `--max-duration-sec 600`
- `--tokenizer-path ../tmp/debug_tok`
- 10 source audios staged in audio_dir.
