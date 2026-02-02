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
