"""测试 BaseAdapter 类。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, MagicMock, patch

import pytest

from src.core.components import BaseAdapter
from src.core.components import BasePlugin


class TestAdapter(BaseAdapter):
    """测试用的适配器实现。"""

    adapter_name = "test_adapter"
    adapter_version = "1.0.0"
    adapter_description = "Test adapter"
    platform = "test_platform"

    async def from_platform_message(self, raw: Any):
        """解析平台消息。"""
        from mofox_wire import MessageEnvelope

        return MessageEnvelope(
            direction="incoming",
            message_info={
                "platform": self.platform,
                "message_id": raw.get("message_id", "test_msg_id"),
                "time": 0.0,
            },
            message_segment=[{"type": "text", "data": raw.get("content", "test content")}],
            raw_message=raw,
        )

    async def _send_platform_message(self, envelope) -> None:
        """发送消息到平台。"""
        # 测试实现
        pass

    # 重写父类方法以避免实际调用
    async def _parent_start(self) -> None:
        """Mock 父类 start。"""
        pass

    async def _parent_stop(self) -> None:
        """Mock 父类 stop。"""
        pass

    def is_connected(self) -> bool:
        """Mock 连接状态。"""
        return True

    async def get_bot_info(self) -> dict:
        """Mock Bot 信息。"""
        return {
            "bot_id": "test_bot",
            "bot_name": "Test Bot",
            "platform": self.platform,
        }


class TestBaseAdapter:
    """测试 BaseAdapter 基类。"""

    def test_adapter_class_attributes(self):
        """测试适配器类属性。"""
        assert TestAdapter.adapter_name == "test_adapter"
        assert TestAdapter.adapter_version == "1.0.0"
        assert TestAdapter.adapter_description == "Test adapter"
        assert TestAdapter.platform == "test_platform"
        assert TestAdapter.dependencies == []

    def test_get_signature_without_plugin_name(self):
        """测试未设置插件名称时获取签名。"""
        signature = TestAdapter.get_signature()
        assert signature is None

    def test_get_signature_with_plugin_name(self):
        """测试设置插件名称后获取签名。"""
        TestAdapter._plugin_ = "test_plugin"
        signature = TestAdapter.get_signature()
        assert signature == "test_plugin:adapter:test_adapter"
        # 重置
        TestAdapter._plugin_ = "unknown_plugin"

    def test_adapter_initialization(self):
        """测试适配器初始化。"""
        mock_sink = MagicMock()
        mock_plugin = MagicMock(spec=BasePlugin)

        adapter = TestAdapter(core_sink=mock_sink, plugin=mock_plugin)

        assert adapter.plugin == mock_plugin
        assert adapter._health_check_task_info is None
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_adapter_start(self):
        """测试适配器启动。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock 父类 start 和 get_task_manager
        with patch("src.kernel.concurrency.task_manager.get_task_manager") as mock_tm:
            mock_task_info = MagicMock()
            mock_task_info.task_id = "test_task_id"
            mock_tm_instance = MagicMock()
            mock_tm_instance.create_task.return_value = mock_task_info
            mock_tm.return_value = mock_tm_instance

            # Mock 父类 start
            with patch("mofox_wire.AdapterBase.start", new_callable=AsyncMock):
                await adapter.start()

                assert adapter._running is True
                assert adapter._health_check_task_info is not None

    @pytest.mark.asyncio
    async def test_adapter_stop(self):
        """测试适配器停止。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True
        adapter._health_check_task_info = MagicMock()

        # Mock get_task_manager
        with patch("src.kernel.concurrency.task_manager.get_task_manager") as mock_tm:
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance

            # Mock 父类 stop
            with patch("mofox_wire.AdapterBase.stop", new_callable=AsyncMock):
                await adapter.stop()

                assert adapter._running is False
                assert adapter._health_check_task_info is None

    @pytest.mark.asyncio
    async def test_on_adapter_loaded_hook(self):
        """测试适配器加载钩子。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 默认实现应该不抛出异常
        await adapter.on_adapter_loaded()

    @pytest.mark.asyncio
    async def test_on_adapter_unloaded_hook(self):
        """测试适配器卸载钩子。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 默认实现应该不抛出异常
        await adapter.on_adapter_unloaded()

    @pytest.mark.asyncio
    async def test_health_check_default(self):
        """测试默认健康检查。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock is_connected 方法
        adapter.is_connected = Mock(return_value=True)

        result = await adapter.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_loop(self):
        """测试健康检查循环。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True

        call_count = [0]

        async def mock_sleep(interval):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", mock_sleep):
            # 由于 TestAdapter 重写了 is_connected 返回 True
            # health_check 应该返回 True，不会触发 reconnect
            await adapter._health_check_loop()

        # 测试通过，没有异常

    @pytest.mark.asyncio
    async def test_health_check_loop_triggers_reconnect(self):
        """测试健康检查失败时触发重连。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True

        call_count = [0]

        async def mock_sleep(interval):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        # Mock health_check 返回 False
        with patch("asyncio.sleep", mock_sleep):
            with patch.object(adapter, "health_check", new_callable=AsyncMock, return_value=False):
                with patch.object(adapter, "reconnect", new_callable=AsyncMock) as mock_reconnect:
                    await adapter._health_check_loop()

                    # 确保重连被调用至少一次
                    assert mock_reconnect.call_count >= 1

    @pytest.mark.asyncio
    async def test_reconnect_default(self):
        """测试默认重连逻辑。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock stop 和 start 方法
        adapter.stop = AsyncMock()
        adapter.start = AsyncMock()

        await adapter.reconnect()

        adapter.stop.assert_called_once()
        adapter.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_platform_message_not_implemented(self):
        """测试未实现发送消息方法时抛出异常。"""
        mock_sink = MagicMock()

        # 创建一个没有实现 _send_platform_message 的适配器
        class IncompleteAdapter(BaseAdapter):
            adapter_name = "incomplete"
            platform = "test"

            async def from_platform_message(self, raw):
                pass

        adapter = IncompleteAdapter(core_sink=mock_sink)
        mock_envelope = MagicMock()

        with pytest.raises(NotImplementedError):
            await adapter._send_platform_message(mock_envelope)

    @pytest.mark.asyncio
    async def test_send_platform_message_with_transport_config(self):
        """测试有传输配置时发送消息。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 设置传输配置（使用 type: ignore 绕过严格类型检查）
        adapter._transport_config = {"test": "config"}  # type: ignore[assignment]

        # 由于 TestAdapter 实现了 _send_platform_message，这里不会抛出异常
        mock_envelope = MagicMock()
        await adapter._send_platform_message(mock_envelope)
        # 测试通过，没有抛出异常


class CustomAdapterWithHooks(TestAdapter):
    """带有自定义钩子的测试适配器。"""

    loaded_called = False
    unloaded_called = False

    async def on_adapter_loaded(self) -> None:
        """自定义加载钩子。"""
        CustomAdapterWithHooks.loaded_called = True
        await super().on_adapter_loaded()

    async def on_adapter_unloaded(self) -> None:
        """自定义卸载钩子。"""
        CustomAdapterWithHooks.unloaded_called = True
        await super().on_adapter_unloaded()


class TestAdapterHooks:
    """测试适配器生命周期钩子。"""

    @pytest.mark.asyncio
    async def test_custom_on_adapter_loaded(self):
        """测试自定义加载钩子被调用。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHooks(core_sink=mock_sink)

        CustomAdapterWithHooks.loaded_called = False

        await adapter.on_adapter_loaded()

        assert CustomAdapterWithHooks.loaded_called is True

    @pytest.mark.asyncio
    async def test_custom_on_adapter_unloaded(self):
        """测试自定义卸载钩子被调用。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHooks(core_sink=mock_sink)

        CustomAdapterWithHooks.unloaded_called = False

        await adapter.on_adapter_unloaded()

        assert CustomAdapterWithHooks.unloaded_called is True


class CustomAdapterWithHealthCheck(TestAdapter):
    """带有自定义健康检查的测试适配器。"""

    async def health_check(self) -> bool:
        """自定义健康检查。"""
        # 模拟检查连接状态
        return True


class TestAdapterHealthCheck:
    """测试适配器健康检查功能。"""

    @pytest.mark.asyncio
    async def test_custom_health_check(self):
        """测试自定义健康检查方法。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHealthCheck(core_sink=mock_sink)

        result = await adapter.health_check()
        assert result is True


class CustomAdapterWithReconnect(TestAdapter):
    """带有自定义重连逻辑的测试适配器。"""

    reconnect_called = False

    async def reconnect(self) -> None:
        """自定义重连逻辑。"""
        CustomAdapterWithReconnect.reconnect_called = True
        await super().reconnect()


class TestAdapterReconnect:
    """测试适配器重连功能。"""

    @pytest.mark.asyncio
    async def test_custom_reconnect(self):
        """测试自定义重连方法。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithReconnect(core_sink=mock_sink)

        # Mock stop 和 start
        adapter.stop = AsyncMock()
        adapter.start = AsyncMock()

        CustomAdapterWithReconnect.reconnect_called = False

        await adapter.reconnect()

        assert CustomAdapterWithReconnect.reconnect_called is True
        adapter.stop.assert_called_once()
        adapter.start.assert_called_once()


# ---------------------------------------------------------------------------
# 适配器命令 (get_bot_info) 相关测试
# ---------------------------------------------------------------------------


class TestAdapterCommand:
    """测试适配器命令功能（get_bot_info）。"""

    @pytest.mark.asyncio
    async def test_get_bot_info_returns_dict(self):
        """测试 get_bot_info 返回包含必要字段的字典。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        result = await adapter.get_bot_info()

        assert isinstance(result, dict)
        assert "bot_id" in result
        assert "bot_name" in result
        assert "platform" in result

    @pytest.mark.asyncio
    async def test_get_bot_info_platform_matches(self):
        """测试 get_bot_info 返回的平台与适配器平台一致。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        result = await adapter.get_bot_info()

        assert result["platform"] == TestAdapter.platform


# ---------------------------------------------------------------------------
# 适配器命令请求-响应机制测试
# ---------------------------------------------------------------------------


class AdapterWithCommandResponse(TestAdapter):
    """支持命令请求-响应的测试适配器。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_commands = []  # 记录收到的命令
        self.command_responses = {}  # 预设的命令响应

    async def from_platform_message(self, raw: Any):
        """解析平台消息，包括adapter_command和adapter_response。"""
        from mofox_wire import MessageEnvelope

        # 记录收到的命令（用于测试验证）
        if isinstance(raw, dict) and raw.get("type") == "adapter_command":
            self.received_commands.append(raw)
            
            # 模拟处理命令并返回响应
            request_id = raw.get("request_id")
            action = raw.get("action")
            
            # 获取预设的响应，或使用默认响应
            response = self.command_responses.get(
                action,
                {"status": "ok", "data": {"action": action}, "message": "success"}
            )
            
            # 构建adapter_response消息信封
            response_envelope: MessageEnvelope = {
                "direction": "incoming",  # type: ignore[typeddict-item]
                "message_info": {
                    "message_id": str(request_id),
                    "platform": self.platform,
                    "time": 0,
                },
                "message_segment": {  # type: ignore[typeddict-item]
                    "type": "adapter_response",
                    "data": {
                        "request_id": request_id,
                        "response": response,
                    }
                },
            }
            
            # 通过core_sink发回核心
            if self.core_sink:
                await self.core_sink.send(response_envelope)
            
        return await super().from_platform_message(raw)

    async def _send_platform_message(self, envelope) -> None:
        """发送消息到平台，处理adapter_command类型。"""
        message_segment = envelope.get("message_segment")
        
        if isinstance(message_segment, dict):
            seg_type = message_segment.get("type")
            
            # 如果是adapter_command，模拟处理
            if seg_type == "adapter_command":
                seg_data = message_segment.get("data", {})
                
                # 构建原始消息格式并通过from_platform_message处理
                raw_command = {
                    "type": "adapter_command",
                    "request_id": seg_data.get("request_id"),
                    "action": seg_data.get("action"),
                    "params": seg_data.get("params", {}),
                }
                
                # 触发消息处理
                await self.from_platform_message(raw_command)
                return
        
        # 其他消息类型的默认处理
        await super()._send_platform_message(envelope)


class TestAdapterCommandResponseMechanism:
    """测试适配器命令请求-响应机制。"""

    @pytest.mark.asyncio
    async def test_adapter_command_request_response_flow(self):
        """测试完整的命令请求-响应流程。"""
        from mofox_wire import MessageEnvelope
        
        # 创建mock core_sink
        mock_sink = MagicMock()
        received_envelopes = []
        
        async def mock_send(envelope: MessageEnvelope):
            received_envelopes.append(envelope)
        
        mock_sink.send = AsyncMock(side_effect=mock_send)
        
        # 创建适配器
        adapter = AdapterWithCommandResponse(core_sink=mock_sink)
        
        # 构建adapter_command消息信封
        request_id = "test-request-123"
        command_envelope: MessageEnvelope = {
            "direction": "outgoing",  # type: ignore[typeddict-item]
            "message_info": {
                "message_id": request_id,
                "platform": adapter.platform,
                "time": 0,
            },
            "message_segment": {  # type: ignore[typeddict-item]
                "type": "adapter_command",
                "data": {
                    "request_id": request_id,
                    "action": "get_group_list",
                    "params": {},
                    "timeout": 20.0,
                }
            },
        }
        
        # 发送命令到适配器
        await adapter._send_platform_message(command_envelope)
        
        # 验证适配器收到命令
        assert len(adapter.received_commands) == 1
        assert adapter.received_commands[0]["action"] == "get_group_list"
        assert adapter.received_commands[0]["request_id"] == request_id
        
        # 验证适配器发送了响应
        assert len(received_envelopes) == 1
        response_envelope = received_envelopes[0]
        
        assert response_envelope["direction"] == "incoming"
        assert isinstance(response_envelope["message_segment"], dict)
        assert response_envelope["message_segment"]["type"] == "adapter_response"
        
        # 验证响应数据
        response_data = response_envelope["message_segment"]["data"]
        assert response_data["request_id"] == request_id
        assert response_data["response"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_adapter_command_with_custom_response(self):
        """测试带有自定义响应的命令处理。"""
        mock_sink = MagicMock()
        received_envelopes = []
        
        async def mock_send(envelope):
            received_envelopes.append(envelope)
        
        mock_sink.send = AsyncMock(side_effect=mock_send)
        
        adapter = AdapterWithCommandResponse(core_sink=mock_sink)
        
        # 预设命令响应
        custom_response = {
            "status": "ok",
            "data": {"groups": [{"id": "123", "name": "Test Group"}]},
            "message": "群列表获取成功"
        }
        adapter.command_responses["get_group_list"] = custom_response
        
        # 发送命令
        request_id = "test-request-456"
        command_envelope = {
            "direction": "outgoing",  # type: ignore[typeddict-item]
            "message_info": {
                "message_id": request_id,
                "platform": adapter.platform,
                "time": 0,
            },
            "message_segment": {  # type: ignore[typeddict-item]
                "type": "adapter_command",
                "data": {
                    "request_id": request_id,
                    "action": "get_group_list",
                    "params": {},
                }
            },
        }
        
        await adapter._send_platform_message(command_envelope)
        
        # 验证收到自定义响应
        assert len(received_envelopes) == 1
        response_envelope = received_envelopes[0]
        response_data = response_envelope["message_segment"]["data"]
        
        assert response_data["response"] == custom_response
        assert response_data["response"]["data"]["groups"][0]["name"] == "Test Group"

    @pytest.mark.asyncio
    async def test_adapter_command_with_params(self):
        """测试带参数的命令处理。"""
        mock_sink = MagicMock()
        received_envelopes = []
        
        async def mock_send(envelope):
            received_envelopes.append(envelope)
        
        mock_sink.send = AsyncMock(side_effect=mock_send)
        
        adapter = AdapterWithCommandResponse(core_sink=mock_sink)
        
        # 发送带参数的命令
        request_id = "test-request-789"
        params = {"group_id": "123456", "user_id": "789"}
        
        command_envelope = {
            "direction": "outgoing",  # type: ignore[typeddict-item]
            "message_info": {
                "message_id": request_id,
                "platform": adapter.platform,
                "time": 0,
            },
            "message_segment": {  # type: ignore[typeddict-item]
                "type": "adapter_command",
                "data": {
                    "request_id": request_id,
                    "action": "kick_member",
                    "params": params,
                }
            },
        }
        
        await adapter._send_platform_message(command_envelope)
        
        # 验证命令携带的参数被正确接收
        assert len(adapter.received_commands) == 1
        assert adapter.received_commands[0]["params"] == params

    @pytest.mark.asyncio
    async def test_adapter_command_error_response(self):
        """测试命令错误响应。"""
        mock_sink = MagicMock()
        received_envelopes = []
        
        async def mock_send(envelope):
            received_envelopes.append(envelope)
        
        mock_sink.send = AsyncMock(side_effect=mock_send)
        
        adapter = AdapterWithCommandResponse(core_sink=mock_sink)
        
        # 预设错误响应
        error_response = {
            "status": "failed",
            "data": None,
            "message": "权限不足"
        }
        adapter.command_responses["ban_member"] = error_response
        
        # 发送命令
        request_id = "test-request-error"
        command_envelope = {
            "direction": "outgoing",  # type: ignore[typeddict-item]
            "message_info": {
                "message_id": request_id,
                "platform": adapter.platform,
                "time": 0,
            },
            "message_segment": {  # type: ignore[typeddict-item]
                "type": "adapter_command",
                "data": {
                    "request_id": request_id,
                    "action": "ban_member",
                    "params": {"user_id": "123"},
                }
            },
        }
        
        await adapter._send_platform_message(command_envelope)
        
        # 验证错误响应
        assert len(received_envelopes) == 1
        response_envelope = received_envelopes[0]
        response_data = response_envelope["message_segment"]["data"]
        
        assert response_data["response"]["status"] == "failed"
        assert response_data["response"]["message"] == "权限不足"

    @pytest.mark.asyncio
    async def test_multiple_commands_sequential(self):
        """测试顺序发送多个命令。"""
        mock_sink = MagicMock()
        received_envelopes = []
        
        async def mock_send(envelope):
            received_envelopes.append(envelope)
        
        mock_sink.send = AsyncMock(side_effect=mock_send)
        
        adapter = AdapterWithCommandResponse(core_sink=mock_sink)
        
        # 发送多个命令
        commands = [
            ("req-1", "get_group_list"),
            ("req-2", "get_friend_list"),
            ("req-3", "get_bot_info"),
        ]
        
        for request_id, action in commands:
            command_envelope = {
                "direction": "outgoing",  # type: ignore[typeddict-item]
                "message_info": {
                    "message_id": request_id,
                    "platform": adapter.platform,
                    "time": 0,
                },
                "message_segment": {  # type: ignore[typeddict-item]
                    "type": "adapter_command",
                    "data": {
                        "request_id": request_id,
                        "action": action,
                        "params": {},
                    }
                },
            }
            await adapter._send_platform_message(command_envelope)
        
        # 验证所有命令都被处理
        assert len(adapter.received_commands) == 3
        assert len(received_envelopes) == 3
        
        # 验证响应顺序
        for i, (request_id, action) in enumerate(commands):
            response_data = received_envelopes[i]["message_segment"]["data"]
            assert response_data["request_id"] == request_id
            assert response_data["response"]["data"]["action"] == action

    @pytest.mark.asyncio
    async def test_adapter_command_without_core_sink(self):
        """测试没有core_sink时的命令处理。"""
        # 创建没有core_sink的适配器
        adapter = AdapterWithCommandResponse(core_sink=None)
        
        request_id = "test-request-no-sink"
        command_envelope = {
            "direction": "outgoing",  # type: ignore[typeddict-item]
            "message_info": {
                "message_id": request_id,
                "platform": adapter.platform,
                "time": 0,
            },
            "message_segment": {  # type: ignore[typeddict-item]
                "type": "adapter_command",
                "data": {
                    "request_id": request_id,
                    "action": "test_action",
                    "params": {},
                }
            },
        }
        
        # 不应该抛出异常
        await adapter._send_platform_message(command_envelope)
        
        # 命令应该被记录
        assert len(adapter.received_commands) == 1
