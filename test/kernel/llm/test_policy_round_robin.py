import pytest

from src.kernel.llm.policy.round_robin import RoundRobinPolicy


def test_round_robin_session_switches_after_max_retry():
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": 0, "retry_interval": 0},
        {"model_identifier": "b", "max_retry": 0, "retry_interval": 0},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    first = s.first()
    assert first.model["model_identifier"] == "a"

    second = s.next_after_error(RuntimeError("x"))
    assert second.model["model_identifier"] == "b"


def test_round_robin_session_retries_same_model():
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": 2, "retry_interval": 0},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    assert s.first().model["model_identifier"] == "a"
    assert s.next_after_error(RuntimeError("x")).model["model_identifier"] == "a"
    assert s.next_after_error(RuntimeError("x")).model["model_identifier"] == "a"


def test_round_robin_invalid_model_set_not_list():
    """测试model_set不是list的情况。"""
    pol = RoundRobinPolicy()
    with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
        pol.new_session(model_set="not_a_list", request_name="req")


def test_round_robin_invalid_model_set_empty():
    """测试model_set为空列表的情况。"""
    pol = RoundRobinPolicy()
    with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
        pol.new_session(model_set=[], request_name="req")


def test_round_robin_invalid_model_set_not_all_dicts():
    """测试model_set包含非dict元素的情况。"""
    pol = RoundRobinPolicy()
    with pytest.raises(ValueError, match="model_set 必须是 list\\[dict\\]"):
        pol.new_session(model_set=["not_a_dict"], request_name="req")


def test_round_robin_negative_max_retry():
    """测试max_retry为负数时被处理为0。"""
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": -1, "retry_interval": 0},
        {"model_identifier": "b", "max_retry": 0, "retry_interval": 0},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    first = s.first()
    assert first.model["model_identifier"] == "a"

    # max_retry为-1时应该被当作0，所以下次应该切换到b
    second = s.next_after_error(RuntimeError("x"))
    assert second.model["model_identifier"] == "b"


def test_round_robin_invalid_max_retry_type():
    """测试max_retry为无效类型时被处理为0。"""
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": "invalid", "retry_interval": 0},
        {"model_identifier": "b", "max_retry": 0, "retry_interval": 0},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    first = s.first()
    assert first.model["model_identifier"] == "a"

    # max_retry为invalid时应该被当作0，所以下次应该切换到b
    second = s.next_after_error(RuntimeError("x"))
    assert second.model["model_identifier"] == "b"


def test_round_robin_negative_retry_interval():
    """测试retry_interval为负数时被处理为0.0。"""
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": 1, "retry_interval": -1},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    first = s.first()
    assert first.model["model_identifier"] == "a"

    step = s.next_after_error(RuntimeError("x"))
    # delay_seconds应该是0.0而不是-1
    assert step.delay_seconds == 0.0
    assert step.model["model_identifier"] == "a"


def test_round_robin_invalid_retry_interval_type():
    """测试retry_interval为无效类型时被处理为0.0。"""
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": 1, "retry_interval": "invalid"},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    first = s.first()
    assert first.model["model_identifier"] == "a"

    step = s.next_after_error(RuntimeError("x"))
    # delay_seconds应该是0.0
    assert step.delay_seconds == 0.0


def test_round_robin_exhausted_all_attempts():
    """测试所有尝试都耗尽的情况。"""
    pol = RoundRobinPolicy()
    ms = [
        {"model_identifier": "a", "max_retry": 0, "retry_interval": 0},
        {"model_identifier": "b", "max_retry": 0, "retry_interval": 0},
    ]

    s = pol.new_session(model_set=ms, request_name="req")
    s.first()  # 尝试a
    s.next_after_error(RuntimeError("x"))  # 尝试b
    step = s.next_after_error(RuntimeError("y"))  # 应该耗尽

    assert step.model is None
    assert step.meta.get("reason") == "exhausted"
