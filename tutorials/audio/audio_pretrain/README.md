# Audio cutting for ALM pretraining

This tutorial contains the long-form audio cutting entrypoints used before building **audio LLM (ALM) pretraining** examples.

Both pipelines read a diarized + transcribed JSONL manifest and emit bounded-duration snippet audio plus a snippet manifest. Each output row keeps a snippet-relative `segments` list with speaker labels, text, ITN text, and word-level timestamps, so the same cut data can feed:

- interleaved audio/text continuation data,
- ASR training pairs,
- TTS training pairs,
- speaker-diarization training data.

## Pipelines

### `pretrain_cut.py`

Use this for the general ALM pretraining cut flow. It drops empty and overlapping segments, greedily packs nearby speech segments up to `--max-duration-sec`, and applies a tokenizer-based repetition filter to remove Whisper-style decoding loops.

This path requires `--tokenizer-path`.

### `diarization_cut.py`

Use this when the input manifest already contains diarization boundary segments such as `speaker: "no-speaker"`. These segments mark regions where no speaker should be included in a training snippet. The diarization cut pipeline treats those labels as hard snippet boundaries, excludes them from output, and only groups consecutive speech segments between no-speaker regions.

This is useful in pretraining data preparation because diarization/tagging systems often insert explicit no-speaker gaps for silence, music, ads, recording boundaries, or non-speech. Crossing those gaps can produce snippets with weak audio/text continuity and misleading speaker timelines. Cutting on diarization boundaries gives downstream ALM pretraining builders cleaner audio spans, cleaner transcripts, and snippet-relative diarization metadata that does not include no-speaker regions.

This path does not require a tokenizer. It intentionally does not run the overlap filter or repetition filter, because the no-speaker boundary markers must remain visible to the planner.

The older `python -m tutorials.audio.audio_no_speaker_cut.run` entrypoint remains available as a compatibility wrapper for this pipeline.

## Inputs

- `--input-manifest`: JSONL file, one row per long-form audio. Required fields per row: an `id`, an audio path field (default `audio_filepath`), and a `segments` list. Each segment should include `speaker`, `start`, `end`, `text`, `text_ITN`, and a `words` list of `{word, start, end}`.
- `--audio-dir`: directory holding the source audio files.
- `--audio-filepath-key`: manifest field naming the source audio path; default is `audio_filepath`.
- `--audio-path-resolution`: how the reader maps a manifest audio path to disk. Choices are `basename` (default; `audio_dir / basename(value)`), `relative` (`audio_dir / value`), and `as_is` (trust the manifest value verbatim).

## Outputs

- `--output-dir`: directory for pipeline outputs; typically the parent directory of the manifest, tar, and metrics paths.
- `--output-audio-tar-path`: tar archive of snippet audio files, one member per snippet, named `<snippet_id>.<ext>`. Each manifest row's `audio_filepath` refers to this tar-internal basename.
- `--output-manifest`: JSONL where each row is the original source metadata, with source-only alignment removed, plus `snippet_id`, updated `audio_filepath`, updated `duration`, and snippet-relative `segments`.
- `--metrics-path`: JSON summary with input/output counts, duration totals, dropped-snippet/segment breakdowns, a duration histogram, and per-original breakdowns. Diarization cut metrics include `no_speaker`, `too_few_speakers`, and `too_many_speakers` drop counts.

## General Pretrain Cut Usage

```bash
python -m tutorials.audio.audio_pretrain.pretrain_cut \
    --input-manifest /path/to/long_form.jsonl \
    --audio-dir /path/to/audios \
    --output-dir /path/to/output \
    --output-manifest /path/to/output/snippets.jsonl \
    --output-audio-tar-path /path/to/output/snippets.tar \
    --metrics-path /path/to/output/metrics_summary.json \
    --tokenizer-path /path/to/hf_tokenizer_dir \
    --max-duration-sec 30
```

`--tokenizer-path` accepts either a local directory loadable by `AutoTokenizer.from_pretrained` or a HuggingFace Hub repository id, for example `openai/whisper-large-v3`. Use `--tokenizer-cache-dir` and `--hf-token` to override the cache location and provide a token for gated repos.

## Diarization Cut Usage

```bash
python -m tutorials.audio.audio_pretrain.diarization_cut \
    --input-manifest /path/to/diarized_long_form.jsonl \
    --audio-dir /path/to/audios \
    --output-dir /path/to/output \
    --output-manifest /path/to/output/diarization_snippets.jsonl \
    --output-audio-tar-path /path/to/output/diarization_snippets.tar \
    --metrics-path /path/to/output/diarization_metrics.json \
    --max-duration-sec 150 \
    --min-num-speaker 1
```

The diarization cut pipeline scans each recording in time order:

1. Start a snippet at the first non-no-speaker segment.
2. Continue adding consecutive speech segments while the snippet stays within `--max-duration-sec`.
3. Close the snippet when a segment labeled like `no-speaker`, `no_speaker`, or `no speaker` is encountered.
4. Drop snippets whose unique-speaker count is below `--min-num-speaker` or above `--max-num-speaker` when an upper bound is set.
5. Drop snippets shorter than `--min-duration-sec`, longer than `--max-duration-sec`, or with empty joined text.
6. Slice surviving audio, shift segment and word timestamps so each snippet starts at `0.0`, write the snippet audio to the tar, and emit one output manifest row per snippet.

`--min-num-speaker` defaults to `1`, so snippets without a usable speaker label are excluded by default. `--max-num-speaker` is disabled unless provided; set it when pretraining data should avoid high-speaker-count snippets.

By default both pipelines run on the Xenna streaming executor. Pass `--backend ray_data` to switch backends.

## Dry-run Mode

Pass `--dry-run` to skip all audio I/O while still producing the output manifest and metrics summary. In dry-run mode, the manifest's `audio_filepath` still points at the tar-internal basename the snippet would have had, but no tar file is written.

```bash
python -m tutorials.audio.audio_pretrain.diarization_cut \
    --input-manifest /path/to/diarized_long_form.jsonl \
    --audio-dir /path/to/audios \
    --output-dir /tmp/output_unused \
    --output-manifest /tmp/diarization_snippets_dryrun.jsonl \
    --output-audio-tar-path /tmp/diarization_snippets_dryrun.tar \
    --metrics-path /tmp/diarization_metrics_dryrun.json \
    --max-duration-sec 150 \
    --dry-run
```
