# Findings

Notes accumulated while studying the existing pipeline and planning the
repetition filter.

## Existing pipeline shape

`build_audio_pretrain_pipeline` (pipeline.py) wires six stages:

1. `ReadLongFormManifestStage` — JSONL manifest → one `AudioTask` per long-form file.
2. `OverlapFilterStage` — drops empty / overlapping segments.
3. `SnippetCutPlannerStage` — pure planner. Writes a list of snippet specs to `task.data["_snippet_plan"]` (key constant `_PLAN_DATA_KEY`). Stamps `dropped_too_long` / `dropped_too_short` / `dropped_no_text` counters into `task._metadata["pretrain_long_form"]` (key constant `_PRETRAIN_META_KEY`).
4. `SnippetExtractionStage` — fan-out. Reads `_snippet_plan`, slices the source audio, resamples, writes one snippet file per plan entry, emits one `AudioTask` per snippet (or one stub task if the plan was empty / extraction failed).
5. `SnippetManifestWriterStage` — appends each non-stub `task.data` as a JSONL line in a per-replica shard.
6. `PretrainMetricsAggregatorStage` — appends one JSONL record per task into a per-replica metrics shard.

Driver-side: `prepare_audio_pretrain_outputs` clears stale shards before
`pipeline.run()`, `finalize_audio_pretrain_outputs` merges shards into the
user-visible manifest + summary JSON afterward.

## Planner output shape (the input to the new filter)

Each entry in `task.data["_snippet_plan"]` is a dict with:
- `start: float` — absolute seconds in source audio
- `end: float` — absolute seconds in source audio
- `segments: list[dict]` — the original segments grouped into this snippet

Each segment has a `text` field (post-commit `8e5f9df` it no longer
falls back to `text_ITN`). Helper `_segment_text(seg)` (stages.py:107)
returns the stripped text or empty string.

## Snippet text construction

`SnippetExtractionStage._make_snippet_task` already builds the joined
text the same way:

```python
" ".join(_segment_text(s) for s in snippet["segments"]).strip()
```

The new filter should use the **same** join formula so the text it
evaluates matches what ends up in the manifest.

## Per-original metrics flow

- Counters live on `task._metadata[_PRETRAIN_META_KEY]` (a dict).
- `_metadata` is `copy.deepcopy`'d into each fan-out snippet task (extraction stage), so every emitted snippet carries the same input-side counters.
- `PretrainMetricsAggregatorStage.process` reads counters from `meta`, writes one record per task to a JSONL shard.
- `_merge_metrics_shards` keeps the **first** record's input-side fields per `id` (they're identical across fan-outs) and sums non-stub records' output-side fields.
- The `dropped` sub-dict is copied wholesale from the first record (line 1112: `"dropped": dict(r.get("dropped") or {})`). Any new key added to the per-task record automatically rolls up into the final `dropped` summary because `_build_final_summary` does `for k, v in (entry.get("dropped") or {}).items(): totals_dropped[k] += int(v)` (lines 1153-1154).

**Implication:** adding `dropped.repetition` only requires (a) writing the counter to `meta["dropped_repetition"]` in the new stage, and (b) reading it into the aggregator's per-task record. No merger changes.

## Logging colors with loguru

Loguru supports inline color tags like `<red>...</red>` when the sink has
`colorize=True` (default for the stderr sink). To use them, call
`logger.opt(colors=True).warning("...<red>...</red>...")`. Tags are
stripped automatically when the sink is non-colorized (e.g. file sinks).

Caveat: literal `<` and `>` in the message body must be escaped as `\<`
and `\>` to avoid being parsed as tags. Plan: `text.replace("<", "\\<")`
before splicing in the highlighted ranges.

## HF tokenizer offsets

`AutoTokenizer.from_pretrained(path, use_fast=True)` returns a fast
tokenizer. Calling
`tok(text, add_special_tokens=False, return_offsets_mapping=True)`
yields:
- `input_ids: list[int]`
- `offset_mapping: list[tuple[int, int]]` — (start_char, end_char) per token in the input string.

Whitespace tokens may have zero-width offsets (start == end). For our
highlighting purpose that's fine — we use `offset_mapping[i][0]` as the
n-gram start and `offset_mapping[i+n-1][1]` as the n-gram end.

Slow (Python) tokenizers don't return offsets — `use_fast=True` is required.

## Tokenizer at `~/tmp/debug_tok/`

Files: `tokenizer.json` (17 MB), `tokenizer_config.json` (174 KB),
`special_tokens_map.json` (512 B). Loadable via
`AutoTokenizer.from_pretrained` directly. **Not** to be checked in.

## Call-site audit (placeholder)

Need to grep `build_audio_pretrain_pipeline` after the plan is approved
to find every caller that must add `tokenizer_path=...`. Findings will
be updated here as call sites are discovered.

## Test placement (placeholder)

Need to look under `tests/` for the existing pretrain pipeline tests to
know where to add the new ones. Will update once located.
