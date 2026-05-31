from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.voice_chatter.config import VoiceChatterConfig
from plugins.voice_chatter.plugin import VoiceChatter
from plugins.voice_chatter.streaming_args import PartialJsonStringFieldExtractor
from plugins.voice_chatter.streaming_assembler import SpeechAssemblerConfig, StreamingSpeechAssembler
from plugins.voice_chatter.streaming_observer import VoiceSayStreamObserver
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
        continuation_grace_ms=0,
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
async def test_voice_say_stream_observer_passes_emotion_and_provider() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_emo", tool_name="action-say"))
    await observer(
        StreamEvent(
            tool_call_id="call_emo",
            tool_args_delta='{"emotion":"happy","provider":"voxcpm","content":"你好。"}',
        )
    )
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.requests
    assert backend.requests[0].emotion == "happy"
    assert backend.requests[0].provider == "voxcpm"


@pytest.mark.asyncio
async def test_voice_say_stream_observer_flushes_tail_without_punctuation() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
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
async def test_voice_say_stream_observer_filters_special_symbols_but_keeps_tags() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_3", tool_name="action-say"))
    await observer(
        StreamEvent(
            tool_call_id="call_3",
            tool_args_delta=r'{"content":"Hello_ \\world![laughing]"}',
        )
    )
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.requests
    assert "".join(request.text for request in backend.requests) == "Hello world![laughing]"
    assert all("_" not in request.text and "\\" not in request.text for request in backend.requests)
    assert "".join(backend.emitted) == "Hello world![laughing]"


@pytest.mark.asyncio
async def test_voice_say_stream_observer_merges_continuation_prefixed_sentence() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_4", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_4", tool_args_delta='{"content":"Hello?'))
    await observer(StreamEvent(tool_call_id="call_4", tool_args_delta='!Again!"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.requests
    assert backend.requests[0].text == "Hello?!Again!"


@pytest.mark.asyncio
async def test_voice_say_stream_observer_flushes_safe_sentence_before_next_sentence_completes() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_5", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_5", tool_args_delta='{"content":"Hello!First'))
    await asyncio.sleep(0)

    assert backend.requests
    assert backend.requests[0].text == "Hello!"

    await observer(StreamEvent(tool_call_id="call_5", tool_args_delta=' sentence!"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert [request.text for request in backend.requests] == ["Hello!", "First sentence!"]


@pytest.mark.asyncio
async def test_voice_say_stream_observer_delays_then_flushes_without_continuation() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=30,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_6", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_6", tool_args_delta='{"content":"Hello!"}'))

    assert backend.requests == []

    await asyncio.sleep(0.05)
    await asyncio.sleep(0)

    assert [request.text for request in backend.requests] == ["Hello!"]


@pytest.mark.asyncio
async def test_voice_say_stream_observer_attaches_tag_only_tail_to_previous_sentence() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=30,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_7", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_7", tool_args_delta='{"content":"Hello!'))
    await observer(StreamEvent(tool_call_id="call_7", tool_args_delta='[laughing]"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert [request.text for request in backend.requests] == ["Hello![laughing]"]
    assert backend.emitted == ["Hello![laughing]"]


@pytest.mark.asyncio
async def test_voice_say_stream_observer_drops_tag_only_output_without_text() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_8", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_8", tool_args_delta='{"content":"[laughing]"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.requests == []
    assert backend.emitted == []


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


def test_assembler_chinese_closing_quote_adsorbed() -> None:
    """P0: Chinese RIGHT DOUBLE QUOTATION MARK must be adsorbed into the candidate."""
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("他说：“你好。") == []
    assert asm.feed("”然后继续。") == ["他说：“你好。”"]
    assert asm.flush_all() == ["然后继续。"]


def test_assembler_leading_punctuation_preserved() -> None:
    """P0: Leading ! or ? must not be dropped; they stay as pending_prefix."""
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    # Feed just a leading punctuation
    assert asm.feed("！真的吗。") == []
    result = asm.flush_all()
    assert "！" in result[0]
    assert "真的吗" in result[0]


def test_assembler_cross_delta_double_newline() -> None:
    """P1: \\n\\n split across two deltas should still be a paragraph boundary."""
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("第一段。\n") == []
    assert asm.feed("\n第二段。") == ["第一段。"]
    assert asm.flush_all() == ["第二段。"]


def test_assembler_force_short_flush() -> None:
    """P1: flush_candidate(force_short=True) bypasses the min-length gate."""
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(
        min_sentence_chars=4, merge_short_sentences=True,
    ))
    assert asm.feed("嗯。") == []
    # Without force_short the short sentence stays held
    assert asm.flush_candidate(force_short=False) == []
    # Feed again
    asm2 = StreamingSpeechAssembler(SpeechAssemblerConfig(
        min_sentence_chars=4, merge_short_sentences=True,
    ))
    assert asm2.feed("嗯。") == []
    # With force_short the short sentence is flushed
    assert asm2.flush_candidate(force_short=True) == ["嗯。"]


# ---------------------------------------------------------------------------
# StreamingSpeechAssembler unit tests
# ---------------------------------------------------------------------------


def test_assembler_basic_sentence_split() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("你好。今") == ["你好。"]
    assert asm.feed("天天气不错。") == []
    assert asm.flush_candidate() == ["今天天气不错。"]


def test_assembler_adsorbs_closing_quotes() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("他说：") == []
    assert asm.feed('"你好。') == []
    assert asm.feed('"然后') == ['他说："你好。"']


def test_assembler_adsorbs_tag_suffix() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("你好。") == []
    assert asm.feed("[laugh]今天") == ["你好。[laugh]"]
    assert asm.feed("天气不错。") == []
    assert asm.flush_candidate() == ["今天天气不错。"]


def test_assembler_no_split_inside_tag() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    result = asm.feed("[style:happy!]你好。")
    # Candidate formed but held pending (no follow-up text yet)
    assert result == []
    # flush_candidate submits the held candidate
    assert asm.flush_candidate() == ["[style:happy!]你好。"]


def test_assembler_single_newline_becomes_space() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("你好\n我在听。") == []
    text = asm.flush_all()
    assert len(text) == 1
    assert "你好" in text[0] and "我在听" in text[0]


def test_assembler_double_newline_boundary() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("第一段。\n\n第二段。") == ["第一段。"]
    assert asm.flush_all() == ["第二段。"]


def test_assembler_short_sentence_merge() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(
        min_sentence_chars=4,
        merge_short_sentences=True,
    ))
    assert asm.feed("嗯。这个可以。") == []
    assert asm.flush_all() == ["嗯。这个可以。"]


def test_assembler_short_sentence_disabled() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(
        min_sentence_chars=1,
        merge_short_sentences=False,
    ))
    assert asm.feed("嗯。这个可以。") == ["嗯。"]
    assert asm.flush_all() == ["这个可以。"]


def test_assembler_flush_all_includes_tail() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("没有标点符号的文本") == []
    assert asm.flush_all() == ["没有标点符号的文本"]


def test_assembler_flush_all_includes_short_prefix() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(
        min_sentence_chars=10,
        merge_short_sentences=True,
    ))
    assert asm.feed("短。") == []
    assert asm.flush_all() == ["短。"]


def test_assembler_empty_input() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    assert asm.feed("") == []
    assert asm.flush_candidate() == []
    assert asm.flush_all() == []


def test_assembler_multiple_sentences_in_one_delta() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    result = asm.feed("你好。今天天气不错。明天")
    assert result == ["你好。", "今天天气不错。"]


def test_assembler_consecutive_terminators() -> None:
    asm = StreamingSpeechAssembler(SpeechAssemblerConfig(min_sentence_chars=1))
    result = asm.feed("什么？！真的吗？")
    assert result == ["什么？！"]
    assert asm.flush_all() == ["真的吗？"]


# ---------------------------------------------------------------------------
# Observer-level pipeline & multi-call ordering tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_uses_global_pipeline_for_multiple_calls() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="call_a", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_b", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="call_a", tool_args_delta='{"content":"第一句。"}'))
    await observer(StreamEvent(tool_call_id="call_b", tool_args_delta='{"content":"第二句。"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert backend.emitted == ["第一句。", "第二句。"]


@pytest.mark.asyncio
async def test_observer_multiple_calls_interleaved() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="c1", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='{"content":"A1!'))
    await observer(StreamEvent(tool_call_id="c2", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="c2", tool_args_delta='{"content":"B1!"}'))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='A2!"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    # Order: A1! submitted when A2! text arrives (starts new sentence),
    # then B1! and A2! flushed in finalize by call state order
    assert len(backend.emitted) == 3
    assert "A1!" in backend.emitted
    assert "B1!" in backend.emitted
    assert "A2!" in backend.emitted


@pytest.mark.asyncio
async def test_observer_disabled_still_finalizes_pipeline() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="c1", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='{"content":"Hello!"}'))

    # Force disable by setting flag
    observer._disabled = True

    # Should not raise; pipeline and states are cleaned up
    await observer.finalize()
    await asyncio.sleep(0)

    assert len(observer._states) == 0


@pytest.mark.asyncio
async def test_observer_finalize_clears_states() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="c1", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='{"content":"Hello."}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert len(observer._states) == 0


# ---------------------------------------------------------------------------
# StreamingTTSPipeline edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_skip_failed_synthesis_continues_emitting() -> None:
    emitted: list[str] = []

    class _SelectiveFailBackend:
        def __init__(self) -> None:
            self.emitted = emitted
            self.requests: list[TTSRequest] = []

        async def synthesize(self, request: TTSRequest) -> TTSArtifact:
            self.requests.append(request)
            if "fail" in request.text:
                raise RuntimeError("simulated failure")
            return TTSArtifact(text=request.text, audio=request.text.encode("utf-8"))

        async def emit(self, artifact: TTSArtifact, chat_stream: Any) -> bool:
            _ = chat_stream
            self.emitted.append(artifact.text)
            return True

    backend = _SelectiveFailBackend()
    pipeline = StreamingTTSPipeline(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel=2,
        logger=_FakeLogger(),
    )

    await pipeline.submit_text("seq0.")
    await pipeline.submit_text("fail.")
    await pipeline.submit_text("seq2.")
    await pipeline.close()

    assert emitted == ["seq0.", "seq2."]


@pytest.mark.asyncio
async def test_pipeline_close_waits_for_pending_tasks() -> None:
    backend = _FakeBackend(delays={"slow.": 0.05, "fast.": 0.0})
    pipeline = StreamingTTSPipeline(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel=2,
        logger=None,
    )

    await pipeline.submit_text("slow.")
    await pipeline.submit_text("fast.")
    await pipeline.close()

    assert len(backend.emitted) == 2
    assert "slow." in backend.emitted
    assert "fast." in backend.emitted


@pytest.mark.asyncio
async def test_pipeline_empty_text_is_ignored() -> None:
    backend = _FakeBackend()
    pipeline = StreamingTTSPipeline(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel=2,
        logger=None,
    )

    await pipeline.submit_text("   ")
    await pipeline.submit_text("real.")
    await pipeline.close()

    assert len(backend.emitted) == 1
    assert backend.emitted[0] == "real."


@pytest.mark.asyncio
async def test_pipeline_submit_after_close_raises() -> None:
    backend = _FakeBackend()
    pipeline = StreamingTTSPipeline(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel=2,
        logger=None,
    )

    await pipeline.close()
    with pytest.raises(RuntimeError, match="already closed"):
        await pipeline.submit_text("test.")


@pytest.mark.asyncio
async def test_observer_continuation_grace_timer_fires() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=30,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="cg", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="cg", tool_args_delta='{"content":"Hello!"}'))

    # Nothing submitted yet (candidate pending, grace timer waiting)
    assert len(backend.requests) == 0

    # Wait for grace timer
    await asyncio.sleep(0.05)
    await asyncio.sleep(0)

    assert len(backend.requests) == 1
    assert backend.requests[0].text == "Hello!"


@pytest.mark.asyncio
async def test_observer_non_action_say_ignored() -> None:
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="c1", tool_name="action-search_memory"))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='{"query":"test"}'))
    await observer.finalize()
    await asyncio.sleep(0)

    assert len(backend.requests) == 0
    assert len(observer.preplayed_say_call_ids) == 0


@pytest.mark.asyncio
async def test_observer_disabled_does_not_skip_say_action() -> None:
    """P1: When the observer degrades mid-stream, preplayed_say_call_ids
    must NOT include the call_id, so the full SayAction still runs."""
    backend = _FakeBackend()
    observer = VoiceSayStreamObserver(
        backend=backend,
        chat_stream=SimpleNamespace(stream_id="s1"),
        max_parallel_tts=2,
        min_sentence_chars=1,
        continuation_grace_ms=0,
        flush_tail_on_done=True,
        empty_audio_retry_count=0,
        logger=_FakeLogger(),
    )

    await observer(StreamEvent(tool_call_id="c1", tool_name="action-say"))
    await observer(StreamEvent(tool_call_id="c1", tool_args_delta='{"content":"Hello!"}'))

    # Simulate mid-stream degradation
    observer._disabled = True

    await observer.finalize()
    await asyncio.sleep(0)

    # Even though preplayed=True and emitted_count > 0, disabled must block skip
    assert "c1" not in observer.preplayed_say_call_ids
