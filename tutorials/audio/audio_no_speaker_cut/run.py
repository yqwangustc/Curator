# Copyright (c) 2026, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI runner for no-speaker-aware long-form audio cutting."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time

from loguru import logger

from nemo_curator.stages.audio.alm.pretrain import (
    build_audio_no_speaker_cut_pipeline,
    finalize_audio_pretrain_outputs,
    prepare_audio_pretrain_outputs,
)

_EXECUTOR_FACTORIES = {
    "xenna": "nemo_curator.backends.xenna.executor:XennaExecutor",
    "ray_data": "nemo_curator.backends.ray_data.executor:RayDataExecutor",
}


def _create_executor(backend: str, **kwargs: object) -> object:
    if backend not in _EXECUTOR_FACTORIES:
        msg = f"Unknown backend {backend!r}; choose from {list(_EXECUTOR_FACTORIES)}"
        raise ValueError(msg)
    module_path, class_name = _EXECUTOR_FACTORIES[backend].rsplit(":", 1)
    return getattr(importlib.import_module(module_path), class_name)(**kwargs)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cut long-form audio into snippets that exclude no-speaker segments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-manifest",
        "--input-manifests",
        "--input-manifes",
        dest="input_manifest",
        required=True,
        help="Path to input JSONL manifest",
    )
    parser.add_argument("--audio-dir", required=True, help="Directory containing source audio files")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for pipeline outputs",
    )
    parser.add_argument("--output-manifest", required=True, help="Path to output JSONL manifest")
    parser.add_argument(
        "--output-audio-tar-path",
        required=True,
        help="Path to output tar archive containing one audio member per snippet",
    )
    parser.add_argument("--metrics-path", required=True, help="Path to metrics summary JSON")
    parser.add_argument("--max-duration-sec", type=float, required=True, help="Maximum snippet duration in seconds")
    parser.add_argument(
        "--min-duration-sec", type=float, default=0.5, help="Minimum snippet duration in seconds (default 0.5)"
    )
    parser.add_argument(
        "--target-sample-rate", type=int, default=16000, help="Output snippet sample rate (default 16000)"
    )
    parser.add_argument("--output-format", choices=["wav", "flac", "ogg"], default="flac", help="Output audio format")
    parser.add_argument(
        "--audio-filepath-key",
        default="audio_filepath",
        help="JSONL field naming the source audio path (default 'audio_filepath')",
    )
    parser.add_argument(
        "--audio-path-resolution",
        choices=["basename", "relative", "as_is"],
        default="basename",
        help=(
            "How the reader maps each row's audio path to disk: "
            "'basename' (audio_dir/basename(value)), 'relative' "
            "(audio_dir/value), or 'as_is' (trust manifest value)."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=sorted(_EXECUTOR_FACTORIES),
        default="xenna",
        help="Execution backend (default 'xenna')",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["streaming", "batch"],
        default="streaming",
        help="Xenna execution mode (default 'streaming')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip audio I/O and produce only the manifest and metrics summary",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "INFO")

    for path in (
        args.output_dir,
        os.path.dirname(args.output_manifest),
        os.path.dirname(args.output_audio_tar_path),
        os.path.dirname(args.metrics_path),
    ):
        if path:
            os.makedirs(path, exist_ok=True)

    pipeline = build_audio_no_speaker_cut_pipeline(
        input_manifest=args.input_manifest,
        audio_dir=args.audio_dir,
        output_dir=args.output_dir,
        output_manifest_path=args.output_manifest,
        output_audio_tar_path=args.output_audio_tar_path,
        metrics_path=args.metrics_path,
        max_duration_sec=args.max_duration_sec,
        min_duration_sec=args.min_duration_sec,
        target_sample_rate=args.target_sample_rate,
        output_format=args.output_format,
        audio_filepath_key=args.audio_filepath_key,
        audio_path_resolution=args.audio_path_resolution,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        logger.info("DRY RUN: snippet audio files will NOT be written; manifest + metrics only.")
    logger.info(pipeline.describe())

    executor_kwargs: dict[str, object] = {}
    if args.backend == "xenna":
        executor_kwargs["config"] = {"execution_mode": args.execution_mode}
    executor = _create_executor(args.backend, **executor_kwargs)

    logger.info(f"Running on backend={args.backend}")
    prepare_audio_pretrain_outputs(args.output_manifest, args.metrics_path, args.output_audio_tar_path)
    t0 = time.monotonic()
    try:
        pipeline.run(executor)
    finally:
        elapsed = time.monotonic() - t0
        finalize_audio_pretrain_outputs(args.output_manifest, args.metrics_path, args.output_audio_tar_path)
    logger.info(
        f"Pipeline finished in {elapsed:.2f}s ({elapsed / 60:.2f} min). "
        f"Snippet tar at {args.output_audio_tar_path}, manifest at {args.output_manifest}, "
        f"metrics at {args.metrics_path}"
    )


if __name__ == "__main__":
    main()
