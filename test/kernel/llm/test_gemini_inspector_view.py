"""Gemini 原生 SDK 参数树与 WebUI 兼容展示模型的离线回归。"""

from __future__ import annotations

from typing import Any

from src.kernel.llm.request_inspector import RequestInspector


def native_params() -> dict[str, Any]:
    """包含系统指令、图片、思考、工具调用和工具结果的完整 SDK 快照。"""
    return {
        "model": "offline-gemini", "stream": True,
        "contents": [
            {"role": "user", "parts": [
                {"text": "问题正文"},
                {"inline_data": {"mime_type": "image/png", "data": "aW1hZ2U="}},
            ]},
            {"role": "model", "parts": [
                {"text": "推理正文", "thought": True},
                {"text": "回答正文"},
                {"function_call": {"id": "call_1", "name": "lookup", "args": {"city": "上海"}}},
            ]},
            {"role": "user", "parts": [{"function_response": {
                "id": "call_1", "name": "lookup", "response": {"output": "查询结果"},
            }}]},
        ],
        "config": {
            "system_instruction": {"parts": [{"text": "系统指令正文"}]},
            "max_output_tokens": 64, "temperature": 0.2,
            "tools": [{"function_declarations": [{
                "name": "lookup", "description": "查询城市",
                "parameters_json_schema": {
                    "type": "object", "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }]}],
        },
    }


def test_gemini_request_counts_and_blocks_match_native_snapshot() -> None:
    """列表、概览、消息及工具页应一致，不把有效 Gemini 请求显示成 0/0。"""
    inspector = RequestInspector()
    record_id = inspector.capture("gemini.generate_content", native_params())
    record = inspector.get_record(record_id)
    assert record is not None
    assert record["msg_count"] == 4
    assert record["tool_count"] == 1
    view = record["rendered"]
    overview = {item["label"]: item["value"] for item in view["overview"]}
    assert overview["消息数"] == "4"
    assert overview["工具数"] == "1"
    assert overview["max_output_tokens"] == "64"
    messages = view["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["blocks"][0]["text"] == "系统指令正文"
    assert messages[1]["blocks"][0]["text"] == "问题正文"
    assert messages[1]["blocks"][1]["media_type"] == "image"
    assert messages[2]["blocks"][0]["label"] == "Reasoning"
    assert messages[2]["blocks"][1]["text"] == "回答正文"
    assert messages[2]["blocks"][2]["type"] == "tool_call"
    assert messages[2]["blocks"][2]["call_id"] == "call_1"
    assert messages[3]["blocks"][0]["type"] == "tool_result"
    assert "查询结果" in messages[3]["blocks"][0]["text"]
    tool = view["tools"][0]
    assert tool["name"] == "lookup"
    assert tool["required"] == ["city"]
    assert tool["properties"] == [{
        "name": "city", "type": "string", "description": "", "required": True,
    }]


def test_gemini_rendering_does_not_replace_raw_parts_or_mutate_snapshot() -> None:
    """格式化视图从快照派生，复制/参数树仍保留原始数据及未知字段。"""
    params = native_params()
    params["contents"][0]["parts"].append({"unknown_native_field": {"value": "原始扩展"}})
    inspector = RequestInspector()
    record_id = inspector.capture("gemini.generate_content", params)
    params["contents"][0]["parts"][0]["text"] = "调用方之后修改"
    record = inspector.get_record(record_id)
    assert record is not None
    assert record["params"]["contents"][0]["parts"][0]["text"] == "问题正文"
    assert record["params"]["contents"][0]["parts"][1]["inline_data"]["data"] == "aW1hZ2U="
    blocks = record["rendered"]["messages"][1]["blocks"]
    assert blocks[-1]["type"] == "unknown"
    assert "原始扩展" in blocks[-1]["text"]
