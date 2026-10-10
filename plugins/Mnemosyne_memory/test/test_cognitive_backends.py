"""ACT-R、KDA、LayerSplit、Transformer 四个认知后端的单元测试。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from ..vnext.backends.actr import ACTRActivationEngine
from ..vnext.backends.kda_memory import (
    ChannelKDAMemory,
    ChannelPolicy,
    DeltaMemory,
    hashed_features,
)
from ..vnext.backends.layersplit_store import LayerSplitStore, SensoryItem
from ..vnext.backends.transformer import (
    MemoryToken,
    MemoryTransformerBlock,
    gelu,
    layer_norm,
    softmax,
    temporal_encoding,
)

NOW = datetime(2026, 10, 6, tzinfo=UTC)


# ------------------------------------------------------------------ 基础函数


def test_gelu_saturates_at_extremes() -> None:
    """GELU 在负无穷趋近 0,正无穷趋近恒等。"""
    assert gelu(-10.0) == pytest.approx(0.0, abs=1e-6)
    assert gelu(10.0) == pytest.approx(10.0, abs=1e-3)
    assert 0 < gelu(0.5) < 0.5


def test_layer_norm_zero_mean_unit_variance() -> None:
    """LayerNorm 总输出零均值单位方差。"""
    values = (1.0, 2.0, 3.0, 4.0, 5.0)
    normalized = layer_norm(values)
    mean = sum(normalized) / len(normalized)
    variance = sum((item - mean) ** 2 for item in normalized) / len(normalized)
    assert mean == pytest.approx(0.0, abs=1e-9)
    assert variance == pytest.approx(1.0, abs=1e-5)


def test_softmax_sums_to_one() -> None:
    """softmax 输出和为 1,空输入返回空。"""
    assert softmax(()) == ()
    result = softmax((1.0, 2.0, 3.0))
    assert sum(result) == pytest.approx(1.0)
    assert result[2] > result[1] > result[0]


def test_temporal_encoding_multi_scale() -> None:
    """时间编码在多个尺度上正交,相近时间在小尺度可区分。"""
    zero = temporal_encoding(0.0)
    one_hour = temporal_encoding(1.0)
    one_day = temporal_encoding(24.0)
    assert len(zero) == len(one_hour) == len(one_day)
    assert zero != one_hour
    assert sum((a - b) ** 2 for a, b in zip(zero, one_hour)) > 0


# ------------------------------------------------------------------ ACT-R


def test_base_level_matches_closed_form() -> None:
    """单次访问的基础激活等于 -d·ln(t)。"""
    engine = ACTRActivationEngine(decay=0.5, time_unit_seconds=86400)
    value = engine.base_level_activation([NOW - timedelta(days=4)], NOW)
    assert value == pytest.approx(-0.5 * math.log(4))


def test_recent_and_frequent_access_activates_more() -> None:
    """更近、更频繁的访问得到更高激活。"""
    engine = ACTRActivationEngine()
    old = engine.base_level_activation([NOW - timedelta(days=30)], NOW)
    recent = engine.base_level_activation([NOW - timedelta(days=1)], NOW)
    frequent = engine.base_level_activation(
        [NOW - timedelta(days=1), NOW - timedelta(days=2)], NOW
    )
    assert old < recent < frequent


def test_approximation_close_to_exact_sum() -> None:
    """长历史近似与精确求和误差有限。"""
    times = [NOW - timedelta(hours=h) for h in range(1, 201)]
    exact = ACTRActivationEngine(max_access_times=1000).base_level_activation(times, NOW)
    approx = ACTRActivationEngine(max_access_times=20).base_level_activation(times, NOW)
    assert approx == pytest.approx(exact, abs=0.05)


def test_activation_components_and_probabilities() -> None:
    """扩散、部分匹配、噪声计入总分，概率归一且阈值过滤。"""
    engine = ACTRActivationEngine(mismatch_penalty=1.0, noise_scale=0.0)
    parts = engine.activation(
        access_times=[NOW - timedelta(hours=1)],
        now=NOW,
        associations=[(0.5, 2.0)],
        mismatches=[0.5],
        noise_unit=0.5,
    )
    assert parts.total == pytest.approx(
        parts.base_level + parts.spreading + parts.partial_matching + parts.noise
    )
    assert parts.spreading == pytest.approx(1.0)
    assert parts.partial_matching == pytest.approx(-0.5)
    probabilities = engine.retrieval_probabilities({"a": 1.0, "b": 0.0, "c": -9.0})
    assert set(probabilities) == {"a", "b"}
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["a"] > probabilities["b"]


def test_noise_is_deterministic_and_symmetric() -> None:
    """logistic 噪声在中位为 0 且关于 0.5 对称。"""
    engine = ACTRActivationEngine(noise_scale=0.4)
    assert engine.logistic_noise(0.5) == pytest.approx(0.0)
    assert engine.logistic_noise(0.8) == pytest.approx(-engine.logistic_noise(0.2))


def test_select_orders_by_probability() -> None:
    """select 按概率降序并保留明细。"""
    engine = ACTRActivationEngine()
    components = {
        key: engine.activation(access_times=[NOW - timedelta(hours=h)], now=NOW)
        for key, h in (("near", 1), ("far", 48))
    }
    result = engine.select(components, limit=2)
    assert [item.memory_id for item in result] == ["near", "far"]
    assert result[0].components is components["near"]



def test_hashed_features_are_stable() -> None:
    """特征哈希可复现。"""
    assert hashed_features(("a", "b"), 16) == hashed_features(("a", "b"), 16)


# ------------------------------------------------------------------ LayerSplit


def test_layersplit_sensory_fifo() -> None:
    """L0 感知缓冲 FIFO,超容量时驱逐最老。"""
    store = LayerSplitStore(sensory_capacity=2, sensory_ttl_seconds=3600)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    store.perceive("stream1", SensoryItem("first", None, now - timedelta(minutes=10)))
    store.perceive("stream1", SensoryItem("second", None, now - timedelta(minutes=5)))
    store.perceive("stream1", SensoryItem("third", None, now))
    items = store.sensory("stream1", now)
    assert len(items) == 2
    assert [item.text for item in items] == ["second", "third"]


def test_layersplit_sensory_time_window() -> None:
    """L0 返回时只取时间窗口内的项。"""
    store = LayerSplitStore(sensory_capacity=10, sensory_ttl_seconds=3600)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    store.perceive("stream1", SensoryItem("old", None, now - timedelta(hours=2)))
    store.perceive("stream1", SensoryItem("recent", None, now - timedelta(minutes=30)))
    items = store.sensory("stream1", now)
    assert [item.text for item in items] == ["recent"]


def test_layersplit_working_bonus_decays_by_turn() -> None:
    """L1 工作记忆在复述后按轮次衰减。"""
    store = LayerSplitStore(working_capacity=5, working_half_life_turns=2.0)
    now = datetime.now(UTC)
    store.rehearse("stream1", "mem1", activation=1.0, turn=10, now=now)
    bonus10 = store.working_bonus("stream1", turn=10)
    bonus12 = store.working_bonus("stream1", turn=12)
    assert bonus10["mem1"] == pytest.approx(1.0)
    assert bonus12["mem1"] == pytest.approx(0.5)


def test_layersplit_working_lru_eviction() -> None:
    """L1 超容量时驱逐最久未复述的项。"""
    store = LayerSplitStore(working_capacity=2, working_half_life_turns=2.0)
    now = datetime.now(UTC)
    store.rehearse("stream1", "mem1", activation=1.0, turn=1, now=now)
    store.rehearse("stream1", "mem2", activation=1.0, turn=2, now=now)
    store.rehearse("stream1", "mem3", activation=1.0, turn=3, now=now)
    bonus = store.working_bonus("stream1", turn=3)
    assert "mem1" not in bonus
    assert "mem2" in bonus
    assert "mem3" in bonus


def test_layersplit_hot_ids_time_based() -> None:
    """L2 返回时间窗口内的热记忆 ID。"""
    store = LayerSplitStore(hot_ttl_seconds=86400)
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    store.hot_put("mem1", {"title": "A"}, now - timedelta(hours=2))
    store.hot_put("mem2", {"title": "B"}, now - timedelta(days=2))
    hot = store.hot_ids(now, limit=10)
    assert "mem1" in hot
    assert "mem2" not in hot


def test_layersplit_forget_clears_all_layers() -> None:
    """forget_memory 清除全部层级的缓存。"""
    store = LayerSplitStore()
    now = datetime.now(UTC)
    store.rehearse("stream1", "mem1", activation=1.0, turn=1, now=now)
    store.hot_put("mem1", {"title": "Test"}, now)
    store.forget_memory("mem1")
    assert "mem1" not in store.working_bonus("stream1", turn=1)
    assert store.hot_get("mem1", now) is None


# ------------------------------------------------------------------ Transformer


def test_transformer_empty_tokens_returns_empty() -> None:
    """空候选返回空结果。"""
    block = MemoryTransformerBlock(head_names=("semantic",))
    output = block.forward({"semantic": (1.0, 0.0)}, [])
    assert output.scores == {}
    assert output.heads == ()


def test_transformer_single_head_attention() -> None:
    """单头注意力对齐时得分高。"""
    block = MemoryTransformerBlock(head_names=("semantic",), temperature=1.0)
    query = {"semantic": (1.0, 0.0, 0.0)}
    tokens = [
        MemoryToken("aligned", {"semantic": (1.0, 0.0, 0.0)}, prior=0.0),
        MemoryToken("orthogonal", {"semantic": (0.0, 1.0, 0.0)}, prior=0.0),
    ]
    output = block.forward(query, tokens)
    assert output.scores["aligned"] > output.scores["orthogonal"]


def test_transformer_gate_weights_heads() -> None:
    """门控决定由哪个头主导排序。"""
    block = MemoryTransformerBlock(head_names=("h1", "h2"))
    query = {"h1": (1.0, 0.0), "h2": (1.0, 0.0)}
    tokens = [
        MemoryToken("a", {"h1": (1.0, 0.0), "h2": (0.0, 1.0)}, prior=0.0),
        MemoryToken("b", {"h1": (0.0, 1.0), "h2": (1.0, 0.0)}, prior=0.0),
    ]
    h1_only = block.forward(query, tokens, gates={"h1": 1.0, "h2": 0.0})
    h2_only = block.forward(query, tokens, gates={"h1": 0.0, "h2": 1.0})
    assert h1_only.order[0] == "a"
    assert h2_only.order[0] == "b"


def test_transformer_residual_adds_prior() -> None:
    """先验分数进入残差主干,对最终得分有贡献。"""
    block = MemoryTransformerBlock(head_names=("semantic",), residual_weight=1.0)
    query = {"semantic": (1.0, 0.0)}
    low_prior = MemoryToken("low", {"semantic": (1.0, 0.0)}, prior=-1.0)
    high_prior = MemoryToken("high", {"semantic": (1.0, 0.0)}, prior=1.0)
    output = block.forward(query, [low_prior, high_prior])
    assert output.scores["high"] > output.scores["low"]


def test_transformer_deterministic() -> None:
    """相同输入总得到相同输出。"""
    block = MemoryTransformerBlock(head_names=("semantic",), seed=42)
    query = {"semantic": (1.0, 0.5, 0.2)}
    tokens = [
        MemoryToken("mem1", {"semantic": (0.8, 0.6, 0.0)}, prior=0.1),
        MemoryToken("mem2", {"semantic": (0.2, 0.1, 0.9)}, prior=-0.2),
    ]
    out1 = block.forward(query, tokens)
    out2 = block.forward(query, tokens)
    assert out1.scores == out2.scores


def test_transformer_order_matches_scores() -> None:
    """order 字段按分数降序排列。"""
    block = MemoryTransformerBlock(head_names=("semantic",))
    query = {"semantic": (1.0, 0.0, 0.0)}
    tokens = [
        MemoryToken("low", {"semantic": (0.1, 0.0, 0.0)}, prior=-1.0),
        MemoryToken("high", {"semantic": (1.0, 0.0, 0.0)}, prior=1.0),
        MemoryToken("mid", {"semantic": (0.5, 0.0, 0.0)}, prior=0.0),
    ]
    output = block.forward(query, tokens)
    assert output.order == ("high", "mid", "low")
    assert all(
        output.scores[output.order[i]] >= output.scores[output.order[i + 1]]
        for i in range(len(output.order) - 1)
    )


def test_actr_rejects_naive_time() -> None:
    """无时区时间被拒绝。"""
    with pytest.raises(ValueError):
        ACTRActivationEngine().base_level_activation([datetime(2026, 1, 1)], NOW)


def test_delta_rule_converges_instead_of_accumulating() -> None:
    """同一键重复写入收敛到目标值，不会无限叠加。"""
    memory = DeltaMemory(key_dim=3, value_dim=2)
    for _ in range(30):
        memory.write((1, 0, 0), (2.0, -1.0), beta=0.5)
    assert memory.read((1, 0, 0)) == pytest.approx((2.0, -1.0), abs=1e-6)
    memory.write((1, 0, 0), (0.0, 5.0), beta=1.0)
    assert memory.read((1, 0, 0)) == pytest.approx((0.0, 5.0))


def test_channels_forget_at_their_own_rate() -> None:
    """群聊通道比私聊通道衰减更快，互不污染。"""
    kda = ChannelKDAMemory(
        key_dim=2,
        value_dim=2,
        policies={
            "private": ChannelPolicy(retention_per_day=0.99, learning_rate=1.0),
            "group": ChannelPolicy(retention_per_day=0.5, learning_rate=1.0),
        },
    )
    kda.write("private", (1, 0), (1, 0), at=NOW)
    kda.write("group", (0, 1), (0, 1), at=NOW)
    kda.advance(NOW + timedelta(days=3))
    assert kda.energy("private") == pytest.approx(0.99**3)
    assert kda.energy("group") == pytest.approx(0.5**3)
    assert ChannelPolicy(retention_per_day=0.5).half_life_days() == pytest.approx(1.0)


def test_kda_scores_rank_associated_value() -> None:
    """线索读出与已关联的记忆值最相似。"""
    dim = 64
    kda = ChannelKDAMemory(
        key_dim=dim, value_dim=dim,
        policies={"private": ChannelPolicy(retention_per_day=1.0, learning_rate=1.0)},
    )
    cue = hashed_features(("咖啡", "失眠"), dim, salt="cue")
    coffee = hashed_features(("coffee-memory",), dim, salt="value")
    tea = hashed_features(("tea-memory",), dim, salt="value")
    kda.write("private", cue, coffee, at=NOW)
    scores = kda.scores(cue, {"coffee": coffee, "tea": tea}, at=NOW)
    assert scores["coffee"] == pytest.approx(1.0)
    assert scores["coffee"] > scores["tea"]
