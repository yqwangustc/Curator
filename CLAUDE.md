# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

NVIDIA NeMo Curator is a Ray-based, GPU-accelerated framework for building distributed data curation pipelines across four modalities: text, image, audio, and video. The same pipeline definition runs on multiple Ray orchestration backends (Xenna, Ray Actor Pool, Ray Data) without changing user code.

## Common commands

Package management is done with `uv` (>=0.7.0). Tests use `pytest`. Linting/formatting is `ruff` (pinned to `0.14.10`).

```bash
# Install / sync dev environment (defaults: dev + test groups)
uv sync

# Sync with optional modality extras (e.g. text on GPU)
uv sync --extra text_cuda12
uv sync --extra all  # everything, GPU

# Run all tests (CPU + GPU)
uv run pytest

# CPU-only (no NVIDIA GPU available)
uv run pytest -m "not gpu"

# GPU-only
uv run pytest -m gpu

# Single test file / single test
uv run pytest tests/stages/text/test_something.py
uv run pytest tests/stages/text/test_something.py::TestClass::test_case

# Lint / format (matches pre-commit)
uv run ruff check .
uv run ruff format .

# Regenerate lockfile after editing pyproject.toml
uv lock

# Pre-commit (one-time setup, then runs automatically on commit)
pre-commit install --install-hooks
pre-commit run --all-files
```

Commits must be **signed and signed-off** (`git commit -sS`); a `commit-msg` hook enforces `Signed-off-by:`. The `uv-lock` pre-commit hook regenerates `uv.lock` if it's stale and blocks the commit — re-stage and recommit. Send PRs to `main`.

## Architecture

The framework is **task-centric, map-style, and backend-agnostic**. Three concept layers, in order of abstraction:

### Tasks (`nemo_curator/tasks/`)

A `Task[T]` is a batch of data flowing through the pipeline. Subclasses fix the data type:

- `DocumentBatch` — `pa.Table | pd.DataFrame` (text)
- `ImageBatch` — `list[ImageObject]`
- `AudioBatch` — `dict | list[dict]`
- `VideoTask` — `Video`
- `InterleavedBatch` — `pa.Table | pd.DataFrame`
- `FileGroupTask` — `list[str]` (file paths)
- `_EmptyTask` — singleton sentinel; the input to generator stages (file discovery, readers, SDG)

All tasks carry `task_id`, `dataset_name`, `data`, `_stage_perf`, `_metadata`, `_uuid`.

### Stages (`nemo_curator/stages/`)

`ProcessingStage[X, Y]` transforms a task of type `X` into `Y | list[Y] | None` (None = filter out). A stage that emits multiple outputs from one input is a **fan-out stage**. Stages must be **fault-tolerant and retry-safe** because Xenna can preempt and reschedule mid-execution (e.g. during autoscaling).

Important: `name`, `resources`, `batch_size` are **class attributes / dataclass fields**, never properties. The base class exposes them as read-only `_name`, `_resources`, `_batch_size`. Override `inputs()` and `outputs()` to declare required/produced task and data attributes — these power runtime validation. Lifecycle hooks: `setup_on_node`, `setup`, `process` (required), `process_batch`, `teardown`.

**Init / lifecycle discipline** — these run in different places, get this wrong and you'll OOM the driver or re-download models per worker:

- `__init__` runs on the **driver** and is serialized to every worker. Keep it light; do runtime validation only. No model loading, no big state.
- `setup_on_node()` runs **once per node** — use it to pre-download model weights, untar archives, etc.
- `setup()` runs **once per replica (worker)** — load models here, including `self.model = model.to("cuda")`. Override `setup()` is also Curator's signal on Ray Data that this stage should be an **Actor** rather than a Task (see Ray Data section below).

**Metadata and perf propagation (critical)** — every output task must forward the input's `_metadata` and `_stage_perf`. Without this, downstream tasks lose history and end-of-pipeline analysis only sees the last stage's data. This applies to fan-out too — copy the fields onto every emitted task. Use `task._metadata["..."]` for cross-stage signals (e.g. a reader stamps the source filename so the writer can route output). Stages can also call `self._log_metrics(...)` to record custom metrics like `num_rows_processed`; per-call timing and `num_items` are auto-logged via `StagePerfStats`.

**`with_()` for variable resources** — stages that may need different resources per instance use `with_()`:

```python
EmbeddingModelStage(model_id="small").with_(resources=Resources(cpus=1, gpus=0.5))
EmbeddingModelStage(model_id="big").with_(resources=Resources(cpus=1, gpus=2))
```

`CompositeStage.with_()` takes a dict keyed by inner stage name instead.

**Task sizing** — every stage transition serializes the task into Ray's **Object Store** (≈40% of cluster CPU memory). Tasks that are too large fill the store and cause backpressure; tasks that are too small drown the system in serialization overhead. Aim for the middle.

Stage tree:

```
nemo_curator/stages/
├── text/        # classifiers, deduplication, download, embedders, filters, io, modifiers
├── image/       # CLIP embeddings, aesthetic, NSFW, dedup
├── audio/       # ASR (NeMo Toolkit), WER, duration, quality
├── video/       # scene detection, clipping, encoding, embeddings (Cosmos-Embed1)
├── interleaved/ # WebDataset tar shards, multimodal filtering
├── deduplication/  # shared across modalities
├── synthetic/      # SDG, shared
├── base.py         # ProcessingStage, CompositeStage
├── resources.py    # Resources dataclass
└── function_decorators.py  # build a ProcessingStage from a function
```

`CompositeStage` bundles related stages with potentially different resource needs (e.g. CPU stage → GPU stage) into one user-facing stage. It's decomposed at pipeline build time. Decomposed children **cannot themselves be CompositeStages**, must have unique names, and `inputs()`/`outputs()` mirror the first/last child. Use `composite.with_({"StageName": {"resources": Resources(...)}})` to retune.

### Resources

```python
Resources(cpus=1.0)                       # CPU-only
Resources(cpus=4.0, gpu_memory_gb=16.0)   # fractional / single GPU
Resources(cpus=8.0, gpus=2.0)             # multi-GPU
```

`gpu_memory_gb` and `gpus` are **mutually exclusive**. `gpu_memory_gb` auto-computes the GPU fraction from available memory; use it for sub-1-GPU stages.

### Pipelines (`nemo_curator/pipeline/`)

```python
pipeline = Pipeline(name="...", stages=[s1, s2, ...])
# or pipeline.add_stage(s)
results = pipeline.run(executor=None, initial_tasks=None)
```

`run()` calls `build()` first, which decomposes composite stages and stores info in `pipeline.decomposition_info`. `describe()` prints the resolved plan.

**Pipelines are linear** — each stage's input must be the previous stage's output. No branching or conditional routing.

**Prefer `EmptyTask` over `initial_tasks`.** The recommended shape is to start the pipeline with a generator stage (e.g. `FilePartitioningStage(input_dir=..., file_format=...)`) that consumes an `_EmptyTask` and emits a list of real tasks. Passing `initial_tasks=[...]` to `run()` forces the **driver** to materialize that list, and drivers are typically smaller / less privileged than workers — so file scans, S3 lists, etc. should live in a stage. When `initial_tasks` is omitted, the executor injects `[EmptyTask]` automatically.

### Workflows (`nemo_curator/workflows/`)

A `Workflow` wraps one or more pipelines plus surrounding setup/teardown into a single `Workflow.run()` call. Use it when:

- Two pipelines must chain (`output_a = PipelineA.run(); PipelineB.run(output_a)`).
- The pipeline needs out-of-band Ray setup (e.g. spinning up the **Id Generator Actor** for monotonically-increasing IDs used by removal stages).
- A single feature mixes streaming and batch executors.

Most deduplication code paths are workflows for exactly these reasons — see `TextRemovalWorkflow` as the reference implementation.

### Executors / backends (`nemo_curator/backends/`)

`BaseExecutor.execute(stages, initial_tasks)` is what actually runs the plan. The pipeline-side stages are wrapped by a `BaseStageAdapter` that handles batching, lifecycle calls, and `StagePerfStats` collection.

| Executor | Mode | When to use |
|---|---|---|
| `XennaExecutor` (default, `backends.xenna`) | Streaming | Production. Concurrent stages, autoscales replicas based on observed throughput. |
| `RayDataExecutor` (`backends.experimental`) | Streaming | Experimental. Ray Data's `map_batches`-based streaming with stage fusion. |
| `RayActorPoolExecutor` (`backends.experimental`) | Batch | Stage A finishes fully before Stage B starts. Used today mostly for **dedup** where each task is just a file reference (light to serialize) and the pipeline truly needs full state before the next step. |

**Streaming vs batch — why it matters.** Streaming lets stages run concurrently, interleaves CPU/GPU work (so GPU stages don't sit idle waiting for tokenization), and interleaves I/O with compute (start processing a file as soon as it downloads instead of waiting for all 100 to land). The cost is autoscaling complexity: a fast stage will flood the Object Store unless replicas are rebalanced, leading to backpressure. Batch is simpler but holds all of Stage A's output in memory before Stage B can start, which is only viable when the next stage genuinely needs full state (group-by, dedup, training).

`XennaExecutor` config knobs: `execution_mode` (`streaming`/`batch`), `cpu_allocation_percentage`, `autoscale_interval_s`, `logging_interval`, `ignore_failures`. It does **not** support `ignore_head_node` (the two experimental executors do).

**Backend-specific gotchas:**

- **Xenna — first stage from `EmptyTask`:** Xenna divides cluster resources across stages, so a generator stage that only ever processes one `EmptyTask` will get N//M idle replicas. Set `max_workers_per_node: 1` in `xenna_stage_spec` on that stage.
- **Ray Data — Task vs Actor:** Curator decides per stage automatically: if `setup()` is overridden, it becomes a Ray **Actor** (persistent state, e.g. loaded model); otherwise a **Task** (lightweight `f(x)`). Force it explicitly with `ray_stage_spec={RayStageSpecKeys.IS_ACTOR_STAGE: True}`.
- **Ray Data — fan-out stages:** Ray Data treats a stage's output as one "block" passed whole to the next stage. For fan-out you usually want the items broken apart, so set `ray_data_stage_spec={RayDataStageSpecKeys.IS_FANOUT_STAGE: True}`.
- **Ray Data — stage fusion:** Ray Data fuses adjacent stages onto the same worker (skipping the Object Store roundtrip and simplifying autoscaling) when they have **identical resource specs**, are **both Tasks or both Actors**, and **neither requires GPU**. If you want fusion, match resources; if you don't, vary them.

Pipelines do not otherwise need to know which executor will run them.

## Conventions

- Line length 119, ruff `select = ["ALL"]` with the ignores in `pyproject.toml` (notably: `D` docstrings, `PTH`, `T20` print, `FBT` boolean args, `SLF001` private access). `examples/`, `tests/`, `tutorials/`, `benchmarking/`, `docs/`, `fern/**/*.py`, `.github/scripts/` have additional per-directory ignores — check `[tool.ruff.lint.extend-per-file-ignores]` before silencing rules.
- Use `from loguru import logger` for logging (no stdlib `logging` setup needed).
- Heavy/optional imports go **inside functions** (`PLC0415` is allowed) so CPU-only installs don't break on missing GPU libs.
- Tests requiring a GPU must be marked `@pytest.mark.gpu`. The `tests/` tree mirrors `nemo_curator/`. Coverage gate is **80%** of changed code.
- All non-empty Python files start with the NVIDIA Apache-2.0 copyright header (see existing files for the canonical block).
- Python 3.11–3.13 (`requires-python = ">=3.11,<3.14"`).

## Dependency / install groups

The project ships granular extras per modality, each split into `_cpu` and `_cuda12`: `text_cpu` / `text_cuda12`, `image_cpu` / `image_cuda12`, `audio_cpu` / `audio_cuda12`, `video_cpu` / `video_cuda12`, plus `math_*`, `interleaved_*`, `sdg_*`, `inference_server`, `deduplication_cuda12`, and `all`. The `cuda12` variants pull RAPIDS (cuDF/cuML/cuGraph) and require CUDA 12.x with a Volta+ GPU. Check `pyproject.toml` `[project.optional-dependencies]` before adding new dependencies — many constraints (`override-dependencies`, `constraint-dependencies`) exist to resolve conflicts between `nemo-toolkit`, `vllm`, `data-designer`, and `transformers`; touch them carefully.

## Benchmarking

New features are expected to ship with a benchmark. Workflow:

1. Add a script to `benchmarking/scripts/` that runs the feature on a defined dataset and records the params it ran with plus the metrics it produced.
2. Add a config entry under `benchmarking/` YAML pointing at the dataset/params, including the expected metric values for that configuration. This lets you sweep configs locally before the PR.
3. The nightly cron runs `nightly-benchmarking.yaml` on 4×A100 and posts results to the `#swrapids-workflows-nightly-tests` Slack channel.

`benchmarking/**` has its own ruff override (`BLE001` — blind exception catches allowed in benchmark runners).

## Docs

Sphinx + MyST under `docs/`, plus a Fern site under `fern/`. Build via the `Makefile`:

```bash
make docs-env                        # one-time: create .venv-docs
make docs-html                       # HTML build
make docs-live                       # autobuild + live reload
make docs-publish DOCS_ENV=ga        # fail-on-warning publish build
```

For Fern doc edits (any change under `fern/`), use the `nemo-curator-docs` skill — it knows the site structure and the navigation/redirect rules.
