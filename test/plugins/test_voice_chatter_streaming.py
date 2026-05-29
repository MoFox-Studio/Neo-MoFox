from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.voice_chatter.config import VoiceChatterConfig
from plugins.voice_chatter.plugin import VoiceChatter
from plugins.voice_chatter.streaming_args import PartialJsonStringFieldExtractor
from plugins.voice_chatter.streaming_observer import VoiceSayStreamObserver
from plugins.voice_chatter.streaming_segmenter import StreamingSentenceSegmenter
from plugins.voice_chatter.streaming_tts import StreamingTTSPipeline
from plugins.voice_chatter.tts import TTSArtifact, TTSRequest
from src.core.components.base.chatter import BaseChatter
from src.kernel.llm import LLMPayload, ROLE, StreamEvent


class _FakeBackend:
    def __init__(self, delays: dict[str, float] | None = None) -> None:
        self.delays = delays or {}
        self.emitted: list[str] = []
        self.requests: list[TTSRequest] = []

    async def synthesize(self, request: TTSRequest) -> TTSArtifact:
        self.requests.append(request)
        await asyncio.sleep(self.delays.get(request.text, 0.0))
        return TTSArtifact(text=request.text, audio=request.text.encode("utf-8"))

    async def emit(self, artifact: TTSArtifact, chat_stream: Any) -> bool:
        _ = chat_stream
        self.emitted.append(artifact.text)
        return True


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def error(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def debug(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        self.warnings.append(message)


def test_partial_json_string_field_extractor_supports_fragmented_content() -> None:
    extractor = PartialJsonStringFieldExtractor("content")

    assert extractor.feed('{"cont') == ""
    assert extractor.feed('ent":"你好') == "你好"
    assert extractor.feed('。今天') == "。今天"
    assert extractor.feed('天气不错。"}') == "天气不错。"


def test_partial_json_string_field_extractor_supports_escapes() -> None:
    extractor = PartialJsonStringFieldExtractor("content")

    result = extractor.feed('{"emotion":"happy","content":"他说：\\"你好\\"。\\n第二句。"}')

    assert result == '他说："你好"。\n第二句。'


def test_streaming_sentence_segmenter_flushes_only_complete_sentences() -> None:
    segmenter = StreamingSentenceSegmenter()

    assert segmenter.feed("你好。今") == ["你好。"]
    assert segmenter.feed("天天气不错") == []
    assert segmenter.feed("。") == ["今天天气不错。"]
    assert segmenter.feed("", flush=True) == []


@pytest.mark.asyncio
async def test_streaming_tts_pipeline_emits_in_submit_order() -> None:
    backend = _FakeBackend(delays={"第一句。": 0.03, "第二句。": 0.0})
    pipeline = StreamingTTSPipeline(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel=2,
        logger=None,
    )

    await pipeline.submit_text("第一句。")
    await pipeline.submit_text("第二句。")
    await pipeline.close()

    assert backend.emitted == ["第一句。", "第二句。"]


@pytest.mark.asyncio
async def test_voice_say_stream_observer_preplays_completed_sentences() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_1", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_1", tool_args_delta='{"content":"你好。今'))
    await observer(StreamEvent(tool_call_id="call_1", tool_args_delta='天天气不错。"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.emitted == ["你好。", "今天天气不错。"]
    assert "call_1" in observer.preplayed_say_call_ids


@pytest.mark.asyncio
async def test_voice_say_stream_observer_flushes_tail_without_punctuation() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_2", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_2", tool_args_delta='{"content":"我明白你的意思"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.emitted == ["我明白你的意思"]
    assert "call_2" in observer.preplayed_say_call_ids


@pytest.mark.asyncio
async def test_voice_chatter_run_tool_call_skips_preplayed_action_say(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceChatterConfig()
    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)
    chatter._active_stream_observer = SimpleNamespace(preplayed_say_call_ids={"call_say"})

    captured_calls: list[list[Any]] = []

    async def _fake_super(
        self: Any,
        calls: list[Any],
        response: Any,
        usable_map: Any,
        trigger_msg: Any,
    ) -> list[tuple[bool, bool]]:
        _ = response, usable_map, trigger_msg
        captured_calls.append(calls)
        return [(True, True) for _ in calls]

    monkeypatch.setattr(BaseChatter, "run_tool_call", _fake_super)

    added_payloads: list[LLMPayload] = []
    response = SimpleNamespace(add_payload=added_payloads.append)
    calls = [
        SimpleNamespace(name="action-say", id="call_say"),
        SimpleNamespace(name="action-search_memory", id="call_search"),
    ]

    results = await chatter.run_tool_call(calls, response, SimpleNamespace(), None)

    assert results == [(True, True), (True, True)]
    assert len(captured_calls) == 1
    assert [call.name for call in captured_calls[0]] == ["action-search_memory"]
    assert len(added_payloads) == 1
    assert added_payloads[0].role == ROLE.TOOL_RESULT
