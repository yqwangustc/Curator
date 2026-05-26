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

"""Tests for no-speaker-aware long-form audio cutting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from nemo_curator.stages.audio.alm.pretrain import (
    NoSpeakerCutPlannerStage,
    PretrainMetricsAggregatorStage,
    ReadLongFormManifestStage,
    SnippetExtractionStage,
    SnippetManifestWriterStage,
    build_audio_no_speaker_cut_pipeline,
    finalize_audio_pretrain_outputs,
    prepare_audio_pretrain_outputs,
)
from nemo_curator.stages.audio.alm.pretrain.planning import (
    is_no_speaker_label,
    plan_no_speaker_snippets,
)
from nemo_curator.stages.audio.alm.pretrain.utils import _PLAN_DATA_KEY, _PRETRAIN_META_KEY
from nemo_curator.tasks import AudioTask, _EmptyTask

if TYPE_CHECKING:
    from pathlib import Path


def _seg(start: float, end: float, speaker: str = "speaker_0", text: str = "x") -> dict:
    return {"speaker": speaker, "start": start, "end": end, "text": text, "words": []}


class TestNoSpeakerLabel:
    def test_matches_obvious_variants(self) -> None:
        for label in ("no-speaker", "NO_SPEAKER", "no speaker", "nospeaker", "non-speech", "silence"):
            assert is_no_speaker_label(label)

    def test_does_not_match_regular_speaker(self) -> None:
        assert not is_no_speaker_label("video_SPEAKER_00")


class TestPlanNoSpeakerSnippets:
    def test_splits_on_no_speaker_segments_and_excludes_them(self) -> None:
        segments = [
            _seg(0.0, 1.0, "no-speaker", "boundary text"),
            _seg(1.0, 2.0, "A", "hello"),
            _seg(2.0, 3.0, "B", "world"),
            _seg(3.0, 4.0, "no_speaker", ""),
            _seg(4.0, 6.0, "A", "again"),
        ]
        snippets, drops = plan_no_speaker_snippets(segments, max_duration_sec=10.0, min_duration_sec=0.5)

        assert [(s["start"], s["end"]) for s in snippets] == [(1.0, 3.0), (4.0, 6.0)]
        assert [[seg["speaker"] for seg in s["segments"]] for s in snippets] == [["A", "B"], ["A"]]
        assert drops["no_speaker"] == 2
        assert drops["too_long"] == 0
        assert drops["too_short"] == 0

    def test_max_duration_splits_long_consecutive_speech_run(self) -> None:
        segments = [
            _seg(0.0, 4.0, "A", "a"),
            _seg(4.0, 8.0, "A", "b"),
            _seg(8.0, 12.0, "A", "c"),
        ]
        snippets, drops = plan_no_speaker_snippets(segments, max_duration_sec=8.0, min_duration_sec=0.5)

        assert [(s["start"], s["end"]) for s in snippets] == [(0.0, 8.0), (8.0, 12.0)]
        assert drops == {
            "no_speaker": 0,
            "too_long": 0,
            "too_short": 0,
            "no_text": 0,
            "too_few_speakers": 0,
            "too_many_speakers": 0,
        }

    def test_applies_shared_duration_and_text_drops(self) -> None:
        segments = [
            _seg(0.0, 10.0, "A", "too long"),
            _seg(20.0, 20.1, "A", "too short"),
            _seg(30.0, 31.0, "A", ""),
        ]
        snippets, drops = plan_no_speaker_snippets(segments, max_duration_sec=5.0, min_duration_sec=0.5)

        assert snippets == []
        assert drops == {
            "no_speaker": 0,
            "too_long": 1,
            "too_short": 1,
            "no_text": 1,
            "too_few_speakers": 0,
            "too_many_speakers": 0,
        }

    def test_applies_speaker_count_drops(self) -> None:
        segments = [
            _seg(0.0, 1.0, "A", "one speaker"),
            _seg(1.0, 1.2, "no-speaker", ""),
            _seg(2.0, 3.0, "A", "two"),
            _seg(3.0, 4.0, "B", "speakers"),
            _seg(4.0, 4.2, "no-speaker", ""),
            _seg(5.0, 6.0, "A", "three"),
            _seg(6.0, 7.0, "B", "speakers"),
            _seg(7.0, 8.0, "C", "here"),
        ]

        snippets, drops = plan_no_speaker_snippets(
            segments,
            max_duration_sec=10.0,
            min_duration_sec=0.5,
            min_num_speaker=2,
            max_num_speaker=2,
        )

        assert [(s["start"], s["end"]) for s in snippets] == [(2.0, 4.0)]
        assert [[seg["speaker"] for seg in s["segments"]] for s in snippets] == [["A", "B"]]
        assert drops["too_few_speakers"] == 1
        assert drops["too_many_speakers"] == 1


class TestNoSpeakerCutPlannerStage:
    def test_writes_plan_and_metadata(self) -> None:
        task = AudioTask(
            task_id="t1",
            dataset_name="ds",
            data={
                "id": "A",
                "segments": [
                    _seg(0.0, 1.0, "no-speaker", "boundary"),
                    _seg(1.0, 2.0, "A", "hello"),
                    _seg(2.0, 4.0, "B", "world"),
                ],
            },
        )
        stage = NoSpeakerCutPlannerStage(max_duration_sec=10.0, min_duration_sec=0.5)

        out = stage.process(task)

        assert [(s["start"], s["end"]) for s in out.data[_PLAN_DATA_KEY]] == [(1.0, 4.0)]
        meta = out._metadata[_PRETRAIN_META_KEY]
        assert meta["original_seg_count"] == 3
        assert meta["dropped_no_speaker"] == 1
        assert meta["dropped_too_few_speakers"] == 0
        assert meta["dropped_too_many_speakers"] == 0
        assert meta["planned_snippets"] == 1

    def test_invalid_args_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_duration"):
            NoSpeakerCutPlannerStage(max_duration_sec=0.0)
        with pytest.raises(ValueError, match="min_duration"):
            NoSpeakerCutPlannerStage(min_duration_sec=-1.0)
        with pytest.raises(ValueError, match="min_duration_sec must be <="):
            NoSpeakerCutPlannerStage(max_duration_sec=5.0, min_duration_sec=10.0)
        with pytest.raises(ValueError, match="no_speaker_labels"):
            NoSpeakerCutPlannerStage(no_speaker_labels=())
        with pytest.raises(ValueError, match="min_num_speaker"):
            NoSpeakerCutPlannerStage(min_num_speaker=-1)
        with pytest.raises(ValueError, match="max_num_speaker"):
            NoSpeakerCutPlannerStage(max_num_speaker=-1)
        with pytest.raises(ValueError, match="min_num_speaker must be <="):
            NoSpeakerCutPlannerStage(min_num_speaker=3, max_num_speaker=2)


class TestNoSpeakerCutPipeline:
    def test_dry_run_reuses_shared_io_and_extraction_infra(self, tmp_path: Path) -> None:
        input_manifest = tmp_path / "in.jsonl"
        audio_dir = tmp_path / "audios"
        audio_dir.mkdir()
        output_dir = tmp_path / "snippets"
        output_manifest = tmp_path / "snippets.jsonl"
        output_audio_tar = tmp_path / "snippets.tar"
        metrics_path = tmp_path / "metrics.json"

        row = {
            "id": "source",
            "audio_filepath": "source.wav",
            "audio_sample_rate": 44100,
            "audio_num_channels": 2,
            "segments": [
                _seg(0.0, 1.0, "no-speaker", "boundary text"),
                _seg(1.0, 2.0, "source_SPEAKER_00", "hello"),
                _seg(2.0, 3.0, "source_SPEAKER_01", "world"),
                _seg(3.0, 4.0, "no-speaker", ""),
                _seg(4.0, 5.0, "source_SPEAKER_00", "again"),
            ],
        }
        input_manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

        pipeline = build_audio_no_speaker_cut_pipeline(
            input_manifest=str(input_manifest),
            audio_dir=str(audio_dir),
            output_dir=str(output_dir),
            output_manifest_path=str(output_manifest),
            output_audio_tar_path=str(output_audio_tar),
            metrics_path=str(metrics_path),
            max_duration_sec=30.0,
            dry_run=True,
        )
        assert tuple(type(s) for s in pipeline.stages) == (
            ReadLongFormManifestStage,
            NoSpeakerCutPlannerStage,
            SnippetExtractionStage,
            SnippetManifestWriterStage,
            PretrainMetricsAggregatorStage,
        )
        assert pipeline.stages[1].min_num_speaker == 1
        assert pipeline.stages[1].max_num_speaker is None

        reader, planner, extractor, writer, aggregator = pipeline.stages
        prepare_audio_pretrain_outputs(str(output_manifest), str(metrics_path), str(output_audio_tar))
        extractor.setup_on_node()
        extractor.setup()
        writer.setup_on_node()
        writer.setup()
        aggregator.setup_on_node()
        aggregator.setup()

        for input_task in reader.process(_EmptyTask(task_id="empty", dataset_name="empty", data=None)):
            planned = planner.process(input_task)
            for snippet_task in extractor.process(planned):
                writer.process(snippet_task)
                aggregator.process(snippet_task)
        extractor.teardown()
        finalize_audio_pretrain_outputs(str(output_manifest), str(metrics_path), str(output_audio_tar))

        rows = [json.loads(line) for line in output_manifest.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert not output_audio_tar.exists()
        assert all(not is_no_speaker_label(seg["speaker"]) for out in rows for seg in out["segments"])
        assert [(row["segments"][0]["start"], row["duration"]) for row in rows] == [(0.0, 2.0), (0.0, 1.0)]

        summary = json.loads(metrics_path.read_text(encoding="utf-8"))
        assert summary["num_input_audios"] == 1
        assert summary["num_output_snippets"] == 2
        assert summary["dropped"]["no_speaker"] == 2
        assert summary["dropped"]["too_few_speakers"] == 0
        assert summary["dropped"]["too_many_speakers"] == 0

    def test_builder_passes_speaker_count_bounds(self, tmp_path: Path) -> None:
        pipeline = build_audio_no_speaker_cut_pipeline(
            input_manifest=str(tmp_path / "in.jsonl"),
            audio_dir=str(tmp_path / "audios"),
            output_dir=str(tmp_path / "snippets"),
            output_manifest_path=str(tmp_path / "snippets.jsonl"),
            output_audio_tar_path=str(tmp_path / "snippets.tar"),
            metrics_path=str(tmp_path / "metrics.json"),
            max_duration_sec=30.0,
            min_num_speaker=2,
            max_num_speaker=4,
            dry_run=True,
        )

        planner = pipeline.stages[1]
        assert isinstance(planner, NoSpeakerCutPlannerStage)
        assert planner.min_num_speaker == 2
        assert planner.max_num_speaker == 4
