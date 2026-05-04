# Progress Log

## Session 1 — 2026-05-04

### Context gathering
- Read `pipeline.py` and the relevant stages in `stages.py` (Read, OverlapFilter, SnippetCutPlanner, SnippetExtraction, SnippetManifestWriter, PretrainMetricsAggregator) plus the `_merge_metrics_shards` driver-side merger.
- Confirmed planner stores plan in `task.data["_snippet_plan"]` as a list of `{start, end, segments}` dicts.
- Confirmed `_make_snippet_task` joins text as `" ".join(_segment_text(s) for s in snippet["segments"]).strip()` — the new filter will reuse this exact formula.
- Confirmed `_merge_metrics_shards` rolls up arbitrary keys under `dropped`, so adding `dropped.repetition` only requires the per-task record to carry it (no merger change).
- Confirmed `tokenizers.json` at `~/tmp/debug_tok/` is 17 MB — too large for git; will not check in.

### Design questions resolved with user
1. Insertion point: **after planner, before extractor** (option A).
2. `tokenizer_path` is **required** in `build_audio_pretrain_pipeline` — filter always runs.
3. Do **not** check in `tmp/debug_tok/`.
4. On filter, **log the full snippet text with offending n-gram occurrences highlighted in red** via loguru color tags.

### Planning files written
- `task_plan.md` — 7 phases (helpers → stage → wire-in → aggregator → exports/audit → tests → docs).
- `findings.md` — pipeline shape, metrics flow, loguru color usage, HF offset-mapping notes.
- `progress.md` — this file.

### Next action
Phase 1: implement pure helpers (`_count_ngrams`, `_find_offending_ngrams`, `_locate_ngram_char_ranges`, `_merge_char_ranges`, `_format_red`) in `stages.py`. Wait for user to confirm before writing code.

### Implementation pass (all phases)
After user said "go ahead":

- **Phase 1:** Added helpers `_count_ngrams`, `_find_offending_ngrams`, `_locate_ngram_char_ranges`, `_merge_char_ranges`, `_format_red` to `stages.py`. Added `Counter` to the `collections` import. Smoke-tested via inline scripts.
- **Phase 2:** Added `SnippetRepetitionFilterStage` (between Stages 3 and 4 — renumbered Stages 4-6 to 5-7 to keep the section comments accurate). Loads HF fast tokenizer in `setup()`, validates `tokenizer_path` non-empty, `ngram_n>=1`, `ngram_max_count>=1` in `__post_init__`. Filters in-place on `task.data["_snippet_plan"]`; logs offending text in red via `logger.opt(colors=True).warning(...)`.
- **Phase 3:** Added required `tokenizer_path: str` plus `ngram_n: int = 4`, `ngram_max_count: int = 3` to `build_audio_pretrain_pipeline`. Inserted the filter stage between planner and extractor. Updated docstring.
- **Phase 4:** Added `"repetition": int(meta.get("dropped_repetition", 0))` to the per-task metrics record in `PretrainMetricsAggregatorStage.process`. Driver-side merger needs no change (it iterates whatever keys are in `dropped`).
- **Phase 5:** Re-exported `SnippetRepetitionFilterStage` from `pretrain/__init__.py` and `pretrain/pipeline.py`. Audited call sites: only `tutorials/audio/audio_pretrain/run.py:144` calls the builder. Added `--tokenizer-path` (required), `--ngram-n`, `--ngram-max-count` CLI args and threaded them through the call.
- **Phase 6:** Added 16 unit tests for the new helpers in `test_helpers.py`. Updated `test_pipeline.py` to wire the new stage into the inline pipeline (with a tiny WordLevel HF tokenizer fixture built via `tokenizers` lib) and updated the `dropped` keys assertion to include `"repetition"`. Added `TestSnippetRepetitionFilterStage` class to `test_stages.py` covering: drops repetitive, keeps non-repetitive, keeps short text, mixed-plan filtering, post-init validation. **All 95 pretrain tests pass.**
- **Phase 7:** Updated module docstring in `pipeline.py` to describe the repetition filter step.

### Final verification
- Pipeline builds end-to-end with 7 stages in the right order.
- Real tokenizer at `~/tmp/debug_tok/` correctly drops a snippet with `'thank you for watching '*12 + 'please subscribe.'` and emits ANSI red `\x1b[31m...\x1b[0m` on the offending span.
- All 95 tests pass.

### Follow-up: surface filtered snippet texts in metrics
User asked to also save the dropped snippet text to metrics, capped at the first 1000.

- Added constant `_MAX_FILTERED_TEXT_EXAMPLES = 1000` in `stages.py`.
- `SnippetRepetitionFilterStage.process` collects un-colorized dropped texts onto `task._metadata["filtered_repetition_texts"]` (per-source list, capped at the constant).
- `PretrainMetricsAggregatorStage` gained a per-replica `_seen_ids` set; `filtered_texts` is included on the **first** record per id only, keeping shard size bounded under fan-out.
- `_merge_metrics_shards` accumulates a global `filtered_examples` list, applying the same cap. The first record per id contributes its texts; once 1000 is reached, no further texts are appended.
- `_build_final_summary` now takes `filtered_examples` and surfaces it as `dropped_repetition_examples` in the summary JSON (always present, possibly empty).
- Tests:
  - Updated `TestSnippetRepetitionFilterStage` to assert `filtered_repetition_texts` content + a per-source cap test using `monkeypatch` on the constant.
  - Updated `TestPretrainMetricsAggregatorStage.test_writes_one_jsonl_record_per_task` to assert `filtered_texts` is emitted exactly once per id (first occurrence only).
  - Updated `TestPrepareAndFinalize.test_finalize_merges_manifest_and_metrics` to assert the `dropped_repetition_examples` key.
  - Added `TestPrepareAndFinalize.test_finalize_caps_filtered_examples_globally` exercising the global cap with `monkeypatch`.
  - Updated pipeline-level test to assert presence + types of `dropped_repetition_examples`.

**All 97 tests pass** (was 95; +2 new cap-behavior tests). Smoke-tested end-to-end with the real `~/tmp/debug_tok/` tokenizer: a repetitive snippet is dropped, `summary["dropped_repetition_examples"]` carries its text, `summary["dropped"]["repetition"]` matches the count.
