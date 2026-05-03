"""screen_understanding agent refactor tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import patch
from unittest.mock import AsyncMock

import pytest

from src.kernel.llm import LLMContextManager
from src.kernel.llm import LLMPayload
from src.kernel.llm import ROLE
from src.kernel.llm import Text
from src.kernel.llm import ToolCall
from src.kernel.llm import ToolResult

from plugins.screen_understanding.action_parser import ScreenControlAction
from plugins.screen_understanding.action_parser import parse_control_response
from plugins.screen_understanding.agent import ScreenControlAgent
from plugins.screen_understanding.agent import ScreenClickTool
from plugins.screen_understanding.agent.tools import ScreenCloseWindowTool
from plugins.screen_understanding.agent.tools import ScreenCheckCompletionTool
from plugins.screen_understanding.agent.tools import ScreenLaunchProgramTool
from plugins.screen_understanding.control_backends import build_control_backend_candidates
from plugins.screen_understanding.control_backends.xdotool_backend import XdotoolControlBackend
from plugins.screen_understanding.control_backends.ydotool_backend import YdotoolControlBackend
from plugins.screen_understanding.plugin import ScreenUnderstandingPlugin
from plugins.screen_understanding.config import ScreenUnderstandingConfig


class _AdapterStub:
    """Minimal adapter stub for control-agent tests."""

    def __init__(self) -> None:
        self._model_set = object()
        self.force_describe_current_screen = AsyncMock(
            return_value=(SimpleNamespace(png_base64="ZmFrZQ=="), "屏幕上有设置按钮")
        )


class _FakeResponse:
    """Minimal follow-up response stub for tool-calling loops."""

    def __init__(self, call_batches: list[list[SimpleNamespace]]) -> None:
        self._call_batches = call_batches
        self._index = 0
        self.call_list = call_batches[0] if call_batches else []
        self.payloads: list[object] = []

    def __await__(self) -> object:
        async def _done() -> None:
            return None

        return _done().__await__()

    def add_payload(self, payload: object, position: object | None = None) -> "_FakeResponse":
        del position
        self.payloads.append(payload)
        return self

    async def send(self, stream: bool = False) -> "_FakeResponse":
        del stream
        self._index += 1
        self.call_list = self._call_batches[self._index] if self._index < len(self._call_batches) else []
        return self


class _FakeRequest:
    """Minimal request stub for the control agent's internal loop."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.payloads: list[object] = []

    def add_payload(self, payload: object, position: object | None = None) -> "_FakeRequest":
        del position
        self.payloads.append(payload)
        return self

    async def send(self, stream: bool = False) -> _FakeResponse:
        del stream
        return self._response


@pytest.mark.asyncio
async def test_control_agent_runs_tool_chain_until_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent 应通过私有 tools 执行动作，并在 finish tool 处结束。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    plugin.bind_adapter(_AdapterStub())
    monkeypatch.setattr(plugin, "describe_active_window", AsyncMock(return_value="app_id=code,title=VS Code"))

    agent = ScreenControlAgent(stream_id="stream-1", plugin=plugin)
    response = _FakeResponse(
        [
            [SimpleNamespace(id="call-1", name="tool-screen_click", args={"x": 10, "y": 20})],
            [SimpleNamespace(id="call-2", name="tool-screen_finish", args={"content": "设置页已经打开"})],
        ]
    )
    monkeypatch.setattr(
        agent,
        "create_llm_request",
        lambda **_kwargs: _FakeRequest(response),
    )
    monkeypatch.setattr(
        agent,
        "execute_local_usable",
        AsyncMock(
            return_value=(
                True,
                {
                    "kind": "action",
                    "action_type": "left_single",
                    "action_inputs": {"start_box": [10.0, 20.0, 10.0, 20.0]},
                    "execution_result": "已点击设置按钮",
                    "terminal": False,
                },
            )
        ),
    )

    success, result = await agent.execute(goal="打开设置", max_steps=3)

    assert success is True
    assert isinstance(result, dict)
    assert result["mode"] == "completed"
    assert result["goal"] == "打开设置"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["action"] == "left_single"
    assert result["message"] == "设置页已经打开"


@pytest.mark.asyncio
async def test_control_agent_returns_stalled_when_repeating_same_action_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续重复同一动作且界面无变化时，agent 应在工具链中提前停止。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    adapter = _AdapterStub()
    adapter.force_describe_current_screen = AsyncMock(
        side_effect=[
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
            (SimpleNamespace(png_base64="ZmFrZQ=="), "相同界面"),
        ]
    )
    plugin.bind_adapter(adapter)
    monkeypatch.setattr(plugin, "describe_active_window", AsyncMock(return_value="app_id=code,title=VS Code"))

    agent = ScreenControlAgent(stream_id="stream-1", plugin=plugin)
    response = _FakeResponse(
        [
            [SimpleNamespace(id="call-1", name="tool-screen_click", args={"x": 10, "y": 20})],
            [SimpleNamespace(id="call-2", name="tool-screen_click", args={"x": 10, "y": 20})],
            [SimpleNamespace(id="call-3", name="tool-screen_click", args={"x": 10, "y": 20})],
        ]
    )
    monkeypatch.setattr(
        agent,
        "create_llm_request",
        lambda **_kwargs: _FakeRequest(response),
    )
    monkeypatch.setattr(
        agent,
        "execute_local_usable",
        AsyncMock(
            return_value=(
                True,
                {
                    "kind": "action",
                    "action_type": "left_single",
                    "action_inputs": {"start_box": [10.0, 20.0, 10.0, 20.0]},
                    "execution_result": "已点击按钮。",
                    "terminal": False,
                },
            )
        ),
    )

    success, result = await agent.execute(goal="点击按钮直到出现新窗口", max_steps=10)

    assert success is True
    assert isinstance(result, dict)
    assert result["mode"] == "stalled"
    assert len(result["steps"]) == 3


@pytest.mark.asyncio
async def test_screen_click_tool_converts_coordinates_to_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """私有 click tool 应将坐标转换为统一的 action inputs 后交给插件执行。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    execute_mock = AsyncMock(return_value=(False, "已点击按钮"))
    monkeypatch.setattr(plugin, "_execute_control_action", execute_mock)

    tool = ScreenClickTool(plugin=plugin)
    success, result = await tool.execute(x=12, y=34)

    assert success is True
    assert isinstance(result, dict)
    assert result["action_type"] == "click"
    assert result["action_inputs"]["start_box"] == [12.0, 34.0, 12.0, 34.0]
    assert execute_mock.await_args is not None
    executed_action = execute_mock.await_args.args[0]
    assert executed_action.action_type == "click"
    assert executed_action.action_inputs["start_box"] == [12.0, 34.0, 12.0, 34.0]


@pytest.mark.asyncio
async def test_screen_check_completion_tool_uses_current_adapter_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """completion tool 应使用当前 adapter 的 dispatch_outputs 参数签名。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    adapter = _AdapterStub()
    plugin.bind_adapter(adapter)
    assess_mock = AsyncMock(return_value=("continue", "仍需继续操作。"))
    monkeypatch.setattr(plugin, "_assess_control_completion", assess_mock)

    tool = ScreenCheckCompletionTool(plugin=plugin)
    success, result = await tool.execute(
        goal="关闭窗口",
        latest_progress="已移动到关闭按钮附近",
        action_history="step=1 | action=hover",
    )

    assert success is True
    assert isinstance(result, dict)
    assert result["verdict"] == "continue"
    assert adapter.force_describe_current_screen.await_args is not None
    assert adapter.force_describe_current_screen.await_args.kwargs == {"dispatch_outputs": False}
    assert assess_mock.await_count == 1


@pytest.mark.asyncio
async def test_control_agent_uses_suspend_bridge_and_active_window_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每轮工具调用后应以 _SUSPEND_ 承接，并在下一轮 user payload 带上活跃窗口信息。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    plugin.bind_adapter(_AdapterStub())
    monkeypatch.setattr(
        plugin,
        "describe_active_window",
        AsyncMock(
            side_effect=[
                "id=5, app_id=code, title=VS Code",
                "id=14, app_id=chrome, title=Chrome",
                "id=14, app_id=chrome, title=Chrome",
            ]
        ),
    )

    agent = ScreenControlAgent(stream_id="stream-1", plugin=plugin)
    response = _FakeResponse(
        [
            [SimpleNamespace(id="call-1", name="tool-screen_click", args={"x": 10, "y": 20})],
            [SimpleNamespace(id="call-2", name="tool-screen_finish", args={"content": "完成"})],
        ]
    )
    request = _FakeRequest(response)
    monkeypatch.setattr(agent, "create_llm_request", lambda **_kwargs: request)
    monkeypatch.setattr(
        agent,
        "execute_local_usable",
        AsyncMock(
            return_value=(
                True,
                {
                    "kind": "action",
                    "action_type": "click",
                    "action_inputs": {"start_box": [10.0, 20.0, 10.0, 20.0]},
                    "execution_result": "已点击设置按钮",
                    "terminal": False,
                },
            )
        ),
    )

    success, result = await agent.execute(goal="打开设置", max_steps=3)

    assert success is True
    assert isinstance(result, dict)
    assert result["mode"] == "completed"
    initial_system_payload = cast(LLMPayload, request.payloads[0])
    initial_system_texts = [
        content.text for content in initial_system_payload.content if isinstance(content, Text)
    ]
    assert any("你是一个负责桌面屏幕操控的 GUI Agent。" in text for text in initial_system_texts)

    initial_user_payload = cast(LLMPayload, request.payloads[1])
    initial_texts = [content.text for content in initial_user_payload.content if isinstance(content, Text)]
    assert any("当前活跃窗口信息" in text and "app_id=code" in text for text in initial_texts)
    assert not any("你是一个负责桌面屏幕操控的 GUI Agent。" in text for text in initial_texts)

    assistant_payloads = [
        cast(LLMPayload, payload) for payload in response.payloads if isinstance(payload, LLMPayload)
    ]
    assert any(
        any(isinstance(content, Text) and content.text == "_SUSPEND_" for content in payload.content)
        for payload in assistant_payloads
        if payload.role == ROLE.ASSISTANT
    )

    chained_user_payloads = [payload for payload in assistant_payloads if payload.role == ROLE.USER]
    assert len(chained_user_payloads) == 1
    chained_user_texts = [
        content.text for content in chained_user_payloads[0].content if isinstance(content, Text)
    ]
    assert any("当前活跃窗口信息" in text and "app_id=chrome" in text for text in chained_user_texts)
    assert any("不要重新开始任务" in text for text in chained_user_texts)
    assert not any("你是一个负责桌面屏幕操控的 GUI Agent。" in text for text in chained_user_texts)


@pytest.mark.asyncio
async def test_screen_close_window_tool_delegates_to_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭窗口工具应委托给插件侧的本地窗口关闭能力。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    close_mock = AsyncMock(return_value="已关闭窗口：id=5, app_id=code, title=VS Code")
    monkeypatch.setattr(plugin, "close_target_window", close_mock)

    tool = ScreenCloseWindowTool(plugin=plugin)
    success, result = await tool.execute("VS Code")

    assert success is True
    assert result["action_type"] == "close_window"
    assert result["action_inputs"] == {"target": "VS Code"}
    assert "已关闭窗口" in result["execution_result"]


@pytest.mark.asyncio
async def test_screen_launch_program_tool_delegates_to_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动程序工具应委托给插件侧的本地程序唤起能力。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))
    launch_mock = AsyncMock(return_value="已启动程序：firefox")
    monkeypatch.setattr(plugin, "launch_program", launch_mock)

    tool = ScreenLaunchProgramTool(plugin=plugin)
    success, result = await tool.execute("firefox")

    assert success is True
    assert result["action_type"] == "launch_program"
    assert result["action_inputs"] == {"command": "firefox"}
    assert result["execution_result"] == "已启动程序：firefox"


def test_context_manager_allows_assistant_bridge_between_tool_result_and_user() -> None:
    """tool_result 与下一轮 user 之间插入 assistant 承接后，上下文应合法。"""

    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.USER, Text("关闭窗口"))]

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("开始执行"),
                ToolCall(id="call_1", name="tool-screen_click", args={"x": 10, "y": 20}),
            ],
        ),
    )
    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value="已点击关闭按钮", call_id="call_1", name="tool-screen_click"),
        ),
    )
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("最新截图显示窗口仍在")))

    assert [payload.role for payload in payloads[-4:]] == [
        ROLE.USER,
        ROLE.ASSISTANT,
        ROLE.TOOL_RESULT,
        ROLE.USER,
    ] or [payload.role for payload in payloads[-5:]] == [
        ROLE.USER,
        ROLE.ASSISTANT,
        ROLE.TOOL_RESULT,
        ROLE.ASSISTANT,
        ROLE.USER,
    ]
    manager.validate_for_send(payloads)


@pytest.mark.asyncio
async def test_execute_control_action_uses_backend_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plugin 控制动作执行应委托给解析出的 backend。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))

    class _BackendStub:
        backend_name = "stub"

        async def execute_action(self, action: ScreenControlAction) -> str:
            return f"stub:{action.action_type}"

    async def _fake_backend(*_args: object, **_kwargs: object) -> _BackendStub:
        return _BackendStub()

    monkeypatch.setattr(
        "plugins.screen_understanding.plugin.get_first_available_control_backend",
        _fake_backend,
    )

    terminal, result = await plugin._execute_control_action(
        ScreenControlAction(
            thought="点击一下",
            action_type="click",
            action_inputs={"start_box": [1, 2, 1, 2]},
            raw_text="Action: click(start_box='(1,2)')",
        )
    )

    assert terminal is False
    assert result == "stub:click"


def test_get_components_exposes_agent_instead_of_service_tool() -> None:
    """插件导出应包含 Adapter 和 Agent，而非旧 service/tool 组合。"""

    plugin = cast(Any, ScreenUnderstandingPlugin(config=ScreenUnderstandingConfig()))

    component_names = {component.__name__ for component in plugin.get_components()}

    assert component_names == {"ScreenUnderstandingAdapter", "ScreenControlAgent"}


def test_build_control_backend_candidates_respects_platform_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """backend 列表应把 auto/local_desktop 展开成平台相关候选。"""

    monkeypatch.setattr("plugins.screen_understanding.control_backends.sys.platform", "linux")

    candidates = build_control_backend_candidates(
        ["auto", "windows_uia", "xdotool", "xdotool"],
        command_timeout_seconds=1.0,
    )

    assert [candidate.backend_name for candidate in candidates] == ["ydotool", "xdotool", "windows_uia"]


def test_parse_control_response_supports_ui_tars_box_tokens() -> None:
    """parser 应兼容 UI-TARS 常见的 box token 坐标格式。"""

    action = parse_control_response(
        "Thought: 点击按钮\nAction: click(start_box='<|box_start|>(200,300)<|box_end|>')"
    )

    assert action.action_type == "click"
    assert action.action_inputs["start_box"] == [200.0, 300.0, 200.0, 300.0]


def test_xdotool_backend_extract_action_point_accepts_two_value_points() -> None:
    """xdotool backend 应兼容两元素 point 坐标，而不只接受四元素 box。"""

    backend = XdotoolControlBackend(command_timeout_seconds=1.0)

    point = backend._extract_action_point({"start_box": [200, 300]}, "start_box")

    assert point == (200, 300)


@pytest.mark.asyncio
async def test_xdotool_backend_is_available_requires_runtime_probe() -> None:
    """xdotool backend 应在真实探测失败时视为不可用。"""

    backend = XdotoolControlBackend(command_timeout_seconds=1.0)

    success_proc = AsyncMock()
    success_proc.returncode = 0
    success_proc.communicate = AsyncMock(return_value=(b"x:10 y:20 screen:0 window:1\n", b""))

    with patch("shutil.which", return_value="/usr/sbin/xdotool"), patch(
        "asyncio.create_subprocess_exec",
        return_value=success_proc,
    ):
        assert await backend.is_available() is True

    failing_proc = AsyncMock()
    failing_proc.returncode = 1
    failing_proc.communicate = AsyncMock(return_value=(b"", b"Error: Can't open display\n"))

    with patch("shutil.which", return_value="/usr/sbin/xdotool"), patch(
        "asyncio.create_subprocess_exec",
        return_value=failing_proc,
    ):
        assert await backend.is_available() is False


@pytest.mark.asyncio
async def test_ydotool_backend_is_available_requires_daemon_connection() -> None:
    """ydotool backend 需要 client 可连接到 daemon socket 才视为可用。"""

    backend = YdotoolControlBackend(command_timeout_seconds=1.0)

    success_proc = AsyncMock()
    success_proc.returncode = 0
    success_proc.communicate = AsyncMock(return_value=(b"fd_daemon_socket: 3\n", b""))

    with patch("shutil.which", return_value="/usr/sbin/ydotool"), patch(
        "asyncio.create_subprocess_exec",
        return_value=success_proc,
    ):
        assert await backend.is_available() is True

    failing_proc = AsyncMock()
    failing_proc.returncode = 2
    failing_proc.communicate = AsyncMock(
        return_value=(
            b"failed to connect socket '/run/user/1000/.ydotool_socket': No such file or directory\n",
            b"",
        )
    )

    with patch("shutil.which", return_value="/usr/sbin/ydotool"), patch(
        "asyncio.create_subprocess_exec",
        return_value=failing_proc,
    ):
        assert await backend.is_available() is False