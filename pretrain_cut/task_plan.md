# Task: Add `SnippetRepetitionFilterStage` to ALM pretrain pipeline

## Goal

Add an n-gram-frequency-based filter that drops snippets whose joined
segment text contains suspicious repetition (a likely Whisper decoding
hallucination). The filter sits between `SnippetCutPlanner` and
`SnippetExtraction` so filtered snippets never incur audio decode /
resample / write cost.

## Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Insertion point | After `SnippetCutPlannerStage`, before `SnippetExtractionStage` | Avoids wasted audio I/O; integrates naturally with per-original metrics. |
| `tokenizer_path` arg | **Required** in `build_audio_pretrain_pipeline` | User-confirmed: filter always runs. |
| Check in `tmp/debug_tok/` | **No** | 17 MB tokenizer.json bloats the repo; user passes a path at call time. |
| Defaults | `ngram_n=4`, `ngram_max_count=3` | Per spec — drop if any n-gram appears > 3 times (i.e. ≥ 4 occurrences). |
| Logging on filter | Log full snippet text with offending n-gram occurrences highlighted in **red** via loguru color tags | User-requested for debugging. |
| Metrics | New `dropped.repetition` counter alongside the existing `empty`, `overlap`, `too_long`, `too_short`, `no_text` keys | Mirrors existing per-original drop bookkeeping. |
| Empty / short text | If tokenized text has fewer than `ngram_n` tokens, keep the snippet | No n-grams ⇒ nothing to filter on; planner already enforces min duration. |

## Phases

### Phase 1 — Pure helpers (no HF / no Ray)
**Status:** complete

Add to `stages.py` near the existing pure helpers (around `histogram_30s`):

- `_count_ngrams(token_ids: list[int], n: int) -> Counter[tuple[int, ...]]`
- `_find_offending_ngrams(counts: Counter, max_count: int) -> set[tuple[int, ...]]` — returns the set of n-grams whose count exceeds `max_count`.
- `_locate_ngram_char_ranges(token_ids, offsets, offending_ngrams, n) -> list[tuple[int, int]]` — for every position where an offending n-gram starts, return the `(start_char, end_char)` of the span covering tokens `i..i+n-1`. Sorted, possibly overlapping.
- `_merge_char_ranges(ranges) -> list[tuple[int, int]]` — merge overlapping / touching ranges.
- `_format_red(text, ranges) -> str` — wrap each merged range with loguru `<red>...</red>` tags. Escape `<` in the surrounding text via `text.replace("<", "\\<")` so loguru's tag parser doesn't choke.

**Verify:** unit tests for each helper pass.

### Phase 2 — `SnippetRepetitionFilterStage`
**Status:** complete

Add a new dataclass stage in `stages.py`. Mirrors the structure of
`OverlapFilterStage` and `SnippetCutPlannerStage`.

- Fields: `tokenizer_path: str`, `ngram_n: int = 4`, `ngram_max_count: int = 3`.
- `__post_init__`: validate `tokenizer_path` non-empty, `ngram_n >= 1`, `ngram_max_count >= 1`.
- `setup(...)`: lazy import `AutoTokenizer` from `transformers`; load once from `self.tokenizer_path` with `use_fast=True`. Store on `self._tokenizer`.
- `inputs() -> ([], [_PLAN_DATA_KEY])`, `outputs() -> ([], [_PLAN_DATA_KEY])`.
- `process(task)`:
  1. Read `task.data[_PLAN_DATA_KEY]`. Skip if empty.
  2. For each planned snippet: build text via `" ".join(_segment_text(s) for s in snippet["segments"]).strip()`.
  3. Tokenize with `add_special_tokens=False, return_offsets_mapping=True`. Take `input_ids` and `offset_mapping`.
  4. If `len(input_ids) < n`: keep the snippet (no n-grams to evaluate).
  5. Else compute `_count_ngrams`, find offending n-grams, decide filter.
  6. If filtered: locate char ranges, format red-highlighted text, emit `logger.opt(colors=True).warning(...)` with snippet id, ngram count summary, and the colorized text.
  7. Replace `task.data[_PLAN_DATA_KEY]` with the kept snippets.
  8. Stamp counters into `task._metadata[_PRETRAIN_META_KEY]`:
     - `dropped_repetition` (count of snippets dropped)
     - `kept_after_repetition_filter` (count of snippets kept)
  9. Emit `_log_metrics({"repetition_filter_time": ..., "snippets_scanned": ..., "snippets_filtered_repetition": ...})`.

**Verify:** unit test the stage with a tiny in-memory `tokenizers` Tokenizer
(or `pytest.importorskip("transformers")` + the local `debug_tok/`).

### Phase 3 — Wire into `build_audio_pretrain_pipeline`
**Status:** complete

In `nemo_curator/stages/audio/alm/pretrain/pipeline.py`:

- Add params: `tokenizer_path: str` (required, no default), `ngram_n: int = 4`, `ngram_max_count: int = 3`.
- Insert `SnippetRepetitionFilterStage(tokenizer_path=..., ngram_n=..., ngram_max_count=...)` between `SnippetCutPlannerStage` and `SnippetExtractionStage`.
- Extend the docstring with the three new args; mention that the filter logs the offending text in red.

**Breaking change:** any existing `build_audio_pretrain_pipeline(...)` call sites must pass `tokenizer_path`. Audit them in Phase 5.

**Verify:** `python -c "from nemo_curator.stages.audio.alm.pretrain import build_audio_pretrain_pipeline"` still works; the function signature requires `tokenizer_path`.

### Phase 4 — Metrics aggregator
**Status:** complete

In `PretrainMetricsAggregatorStage.process` (stages.py ~L1005), add the new key:

```python
"dropped": {
    "empty": int(meta.get("dropped_empty", 0)),
    "overlap": int(meta.get("dropped_overlap", 0)),
    "too_long": int(meta.get("dropped_too_long", 0)),
    "too_short": int(meta.get("dropped_too_short", 0)),
    "no_text": int(meta.get("dropped_no_text", 0)),
    "repetition": int(meta.get("dropped_repetition", 0)),  # NEW
},
```

The merger in `_merge_metrics_shards` already iterates `entry.get("dropped") or {}` items, so totals roll up automatically. No change needed there.

Also update the class docstring's `dropped` enumeration if it lists keys.

**Verify:** integration test asserts `dropped.repetition` shows up in the final summary JSON.

### Phase 5 — Exports + call-site audit
**Status:** complete

- Export `SnippetRepetitionFilterStage` from:
  - `nemo_curator/stages/audio/alm/pretrain/stages.py` (already there once defined)
  - `nemo_curator/stages/audio/alm/pretrain/pipeline.py` `from .stages import (...)` block + `__all__`
  - `nemo_curator/stages/audio/alm/pretrain/__init__.py` re-exports + `__all__`
- Grep for existing call sites of `build_audio_pretrain_pipeline` (run.py, tests, examples) and add `tokenizer_path=...` to each. See findings.md.

**Verify:** `grep -rn build_audio_pretrain_pipeline` shows every call site updated; imports succeed.

### Phase 6 — Tests
**Status:** complete

- Pure helper tests (no HF dep): `_count_ngrams`, `_find_offending_ngrams`, `_locate_ngram_char_ranges`, `_merge_char_ranges`, `_format_red`.
- Stage test:
  - Build a tiny `tokenizers.Tokenizer` fixture (WordLevel, ~20 token vocab) and save to a tmpdir as a HF-loadable directory; load with `AutoTokenizer.from_pretrained(tmpdir)` in the test. (Fallback: skip if `transformers` unavailable.)
  - Test 1: snippet with no repetition passes through unchanged.
  - Test 2: snippet with high-frequency n-gram is dropped; `dropped_repetition` counter incremented.
  - Test 3: snippet shorter than `n` tokens passes through.
  - Test 4: red-highlighting log fires (capture via `caplog` or a custom loguru sink).
- Update or add pipeline-level integration test that goes end-to-end with the tiny tokenizer.

**Verify:** new tests pass; existing tests still pass.

### Phase 7 — Docs sweep
**Status:** complete

- Update the long-form-cut pipeline overview in `pipeline.py` module docstring to mention the repetition filter step.
- (Optional, if Fern docs describe this pipeline) update `fern/` pages — defer to user if relevant.

**Verify:** `grep -n "repetition" nemo_curator/stages/audio/alm/pretrain/*.py` shows the new stage is described.

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| _(none yet)_ | | |

## Files to be modified / created

- **Modified:** `nemo_curator/stages/audio/alm/pretrain/stages.py` — new helpers, new stage, aggregator key.
- **Modified:** `nemo_curator/stages/audio/alm/pretrain/pipeline.py` — new params, stage wired in.
- **Modified:** `nemo_curator/stages/audio/alm/pretrain/__init__.py` — re-export.
- **Modified:** any `run.py` / example / test that calls `build_audio_pretrain_pipeline`.
- **Created:** new test file(s) under `tests/` (path TBD — see findings.md after grep).
