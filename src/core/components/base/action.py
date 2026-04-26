"""动作组件基类。

本模块提供 BaseAction 类，定义动作组件的基本行为。
动作是"主动的响应"，通过 LLM Tool Calling 调用。
"""

import random
from abc import ABC, abstractmethod
from typing import Annotated, Any, TYPE_CHECKING
from uuid import uuid4

from src.core.components.types import ChatType
from src.core.components.utils import parse_function_signature
from src.kernel.llm import LLMUsable, LLMUsableExecution
from src.core.models.message import Message, MessageType

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream
    from src.kernel.llm import LLMUsable


class BaseAction(ABC, LLMUsable):
    """动作组件基类。

    动作定义了一个"动作"的行为，例如"发送消息"、"发送表情包"等。
    它是决策后的"结果"，LLM 并不会从中获得信息。
    动作是主动的"响应"。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        action_name: 动作名称
        action_description: 动作的功能描述
        primary_action: 是否为主动作
        chatter_allow: 支持的 Chatter 列表
        chat_type: 支持的聊天类型
        associated_platforms: 关联的平台列表
        associated_types: 需要的内容类型列表

    Examples:
        >>> class SendEmoji(BaseAction):
        ...     action_name = "send_emoji"
        ...     action_description = "发送一个表情"
        ...     primary_action = False
        ...
        ...     async def execute(self, emoji_tag: str) -> tuple[bool, str]:
        ...         # 实现逻辑
        ...         return True, "发送成功"
    """
    _plugin_: str
    _signature_: str

    # 动作元数据
    action_name: str = ""
    action_description: str = ""

    primary_action: bool = False
    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    associated_platforms: list[str] = []
    associated_types: list[str] = []

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:tool:calculator"]

    def __init__(self, chat_stream: "ChatStream", plugin: "BasePlugin") -> None:
        """初始化动作组件。

        Args:
            chat_stream: 聊天流实例
            plugin: 所属插件实例
        """
        self.chat_stream = chat_stream
        self.plugin = plugin
        self._last_message: str | None = None
    
    @classmethod
    def get_signature(cls) -> str | None:
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.action_name:
            return f"{cls._plugin_}:action:{cls.action_name}"
        return None
    
    @abstractmethod
    async def execute(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str, "结果详情"]]:
        """执行动作的主要逻辑。

        所有 Action 组件都必须重写此方法。
        必须编写参数文档来告诉 LLM 每个参数的作用。

        Returns:
            tuple[bool, str]: (是否成功, 结果详情)

        Examples:
            >>> async def execute(
            ...     self,
            ...     emoji_tag: Annotated[str, "表情的情感标签"]
            ... ) -> tuple[bool, str]:
            ...     # 实现发送表情逻辑
            ...     return True, "发送成功"

        Note:
            action 管理器会自动识别类型提示和文档字符串并生成对应的 Tool Schema。
        """
        ...

    def _wrap_execute(self, *args: Any, **kwargs: Any) -> LLMUsableExecution:
        """包装 ``execute``，供统一 tool call 调度器使用。

        ``execute`` 可以保持普通 coroutine 写法并返回 ``(success, result)``；
        顺序敏感的 Action 也可以写成异步生成器，在真正执行最终动作前
        ``yield None`` 暂停，最后一次非空 ``yield`` 作为返回结果。

        Args:
            *args: 传给 ``execute`` 的位置参数。
            **kwargs: 传给 ``execute`` 的关键字参数。

        Returns:
            LLMUsableExecution: 已启动的执行包装对象。
        """
        return LLMUsableExecution(self.execute(*args, **kwargs))

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """生成 LLM Tool Schema。

        通过 inspect 解析 execute 方法生成 OpenAI Tool Calling 格式的 schema。

        Returns:
            dict[str, Any]: OpenAI Tool 格式的 schema

        Examples:
            >>> schema = SendEmoji.to_schema()
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "send_emoji",
            ...         "description": "发送一个表情",
            ...         "parameters": {
            ...             "type": "object",
            ...             "properties": {
            ...                 "emoji_tag": {"type": "string", "description": "..."}
            ...             },
            ...             "required": ["emoji_tag"]
            ...         }
            ...     }
            ... }
        """
        # 使用 utils 中的共同方法生成 schema，name 前缀加上组件类型
        return parse_function_signature(cls.execute, f"action-{cls.action_name}", cls.action_description)

    async def go_activate(self) -> bool:
        """动作激活判定函数。

        子类可重写此方法以实现自定义的激活逻辑。

        Returns:
            bool: 是否激活

        Examples:
            >>> async def go_activate(self) -> bool:
            ...     return await self._random_activation(0.5)
        """
        return True

    async def _random_activation(self, probability: float) -> bool:
        """随机激活工具函数。

        Args:
            probability: 激活概率，范围 0.0 到 1.0

        Returns:
            bool: 是否激活

        Examples:
            >>> if await self._random_activation(0.5):
            ...     print("有50%概率激活")
        """
        return random.random() < probability

    async def _keyword_match(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
    ) -> bool:
        """关键词匹配工具函数。

        聊天内容会自动从实例属性中获取。

        Args:
            keywords: 关键词列表
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否匹配到关键词

        Examples:
            >>> if await self._keyword_match(["hello", "hi"]):
            ...     print("匹配到问候语")
        """
        if not self._last_message:
            return False

        message = self._last_message if case_sensitive else self._last_message.lower()
        keywords = keywords if case_sensitive else [k.lower() for k in keywords]

        return any(kw in message for kw in keywords)

    def _get_recent_chat_content(self, max_messages: int = 6) -> str:
        """获取最近聊天消息的文本内容。

        Args:
            max_messages: 获取的最大消息数量，默认为6条

        Returns:
            str: 格式化的聊天内容，每条消息一行，格式为 "发送者: 内容"
        """
        # 获取最新的 max_messages 条消息
        recent_messages = self.chat_stream.context.history_messages[-max_messages:]

        # 格式化消息内容
        content_lines = []
        for msg in recent_messages:
            # 优先使用 processed_plain_text，其次使用 content
            msg_text = msg.processed_plain_text if msg.processed_plain_text else str(msg.content)
            # 格式：发送者名: 内容
            content_lines.append(f"{msg.sender_name}: {msg_text}")

        return "\n".join(content_lines)

    async def _llm_judge_activation(
        self,
        judge_prompt: str = "",
        action_require: list[str] | None = None,
    ) -> bool:
        """LLM 判断激活工具函数。

        使用 LLM 来判断是否应该激活此 Action。
        会自动构建完整的判断提示词，只需要提供核心判断逻辑即可。

        聊天内容会自动从实例属性中获取。

        Args:
            judge_prompt: 自定义判断提示词（核心判断逻辑）
            action_require: Action 使用场景，如果不提供则使用类属性

        Returns:
            bool: 是否应该激活

        Examples:
            >>> # 最简单的用法
            >>> result = await self._llm_judge_activation(
            >>>     "当用户询问天气信息时激活"
            >>> )
            >>>
            >>> # 提供详细信息
            >>> result = await self._llm_judge_activation(
            >>>     judge_prompt="当用户表达情绪或需要情感支持时激活",
            >>>     action_require=["用户情绪低落", "需要情感支持"]
            >>> )
        """
        import asyncio

        from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
        from src.core.config import get_model_config

        try:
            # 自动获取聊天内容：使用当前聊天流的最新6条消息
            chat_content = self._get_recent_chat_content()

            # 获取 utils_small 模型配置
            utils_small_set = get_model_config().get_task("utils_small")

            if action_require is None:
                action_require = action_require or []

            # 构建完整的判断提示词
            prompt = f"""你需要判断在当前聊天情况下，是否应该激活名为"{self.action_name}"的动作。

动作描述：{self.action_description}
"""

            if action_require:
                prompt += "\n动作使用场景：\n"
                for req in action_require:
                    prompt += f"- {req}\n"

            if judge_prompt:
                prompt += f"\n额外判定条件：\n{judge_prompt}\n"

            if chat_content:
                prompt += f"\n当前聊天记录：\n{chat_content}\n"

            prompt += """
请根据以上信息判断是否应该激活这个动作。
只需要回答"是"或"否"，不要有其他内容。
"""

            # 创建 LLM 请求
            llm_request = LLMRequest(utils_small_set, request_name="ActionActivationJudge")
            llm_request.add_payload(LLMPayload(ROLE.USER, Text(prompt)))

            # 调用 LLM 进行判断，设置 7 秒超时避免长时间等待
            try:
                response = await asyncio.wait_for(
                    llm_request.send(stream=False),
                    timeout=7.0,
                )
                # 获取响应文本（await 返回的是 str）
                response_text = str(response).strip().lower()
                should_activate = "是" in response_text or "yes" in response_text or "true" in response_text
            except asyncio.TimeoutError:
                # 超时时默认激活，交给后续决策系统处理
                should_activate = True

            return should_activate

        except Exception:
            # 出错时默认不激活
            return False
        

    async def _send_to_stream(
        self,
        content: Message | str,
        stream_id: str | None = None,
    ) -> bool:
        """发送任意内容到指定聊天流。

        Args:
            content: 要发送的内容（支持 Message 对象、字符串、或其他类型）
            stream_id: 要发送的聊天流 ID，留空则使用当前聊天流

        Returns:
            bool: 发送是否成功

        Examples:
            >>> # 发送文本消息
            >>> await self._send_to_stream("Hello, world!")
            >>> True
            >>>
            >>> # 发送 Message 对象
            >>> from src.core.models.message import Message
            >>> msg = Message(content="Hi", platform="qq", stream_id="xxx")
            >>> await self._send_to_stream(msg)
            >>> True

        Note:
            此方法通过 transport 层的 MessageSender 发送消息
        """
        from src.core.transport.message_send import get_message_sender
        from src.core.managers.adapter_manager import get_adapter_manager
        try:
            # 如果传入的是 Message 对象，直接发送
            if isinstance(content, Message):
                message = content
            else:
                # 否则构建新的 Message 对象
                # 从当前 chat_stream 获取上下文信息
                target_stream_id = stream_id or self.chat_stream.stream_id
                platform = self.chat_stream.platform
                chat_type = self.chat_stream.chat_type
                context = self.chat_stream.context

                bot_info =await get_adapter_manager().get_bot_info_by_platform(platform)

                # 转换 content 为字符串
                content_str = str(content) if not isinstance(content, str) else content

                target_user_id = None
                target_user_name = None
                target_group_id = None
                target_group_name = None

                def _get_last_context_message() -> Message | None:
                    if context.unread_messages:
                        return context.unread_messages[-1]
                    if context.history_messages:
                        return context.history_messages[-1]
                    return context.current_message

                last_msg = _get_last_context_message()

                if chat_type == "group":
                    if last_msg:
                        target_group_id = last_msg.extra.get("group_id")
                        target_group_name = last_msg.extra.get("group_name")
                else:
                    target_user_id = context.triggering_user_id
                    if not target_user_id and last_msg:
                        target_user_id = last_msg.sender_id
                        target_user_name = last_msg.sender_name

                extra: dict[str, Any] = {}
                if target_user_id:
                    extra["target_user_id"] = target_user_id
                if target_user_name:
                    extra["target_user_name"] = target_user_name
                if target_group_id:
                    extra["target_group_id"] = target_group_id
                if target_group_name:
                    extra["target_group_name"] = target_group_name

                message = Message(
                    message_id=f"action_{self.action_name}_{uuid4().hex}",
                    content=content_str,
                    processed_plain_text=content_str,
                    message_type=MessageType.TEXT,
                    sender_id=bot_info.get("bot_id", "") if bot_info else "",
                    sender_name=bot_info.get("bot_name", "Bot") if bot_info else "Bot",
                    platform=platform,
                    chat_type=chat_type,
                    stream_id=target_stream_id,
                    **extra,
                )

            # 获取 MessageSender 并发送消息
            sender = get_message_sender()
            return await sender.send_message(message)

        except Exception as e:
            from src.kernel.logger import get_logger

            logger = get_logger("action")
            logger.error(
                f"Action {self.action_name} 发送消息失败: {e}",
                exc_info=True,
            )
            return False
