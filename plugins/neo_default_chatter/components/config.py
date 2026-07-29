"""Neo-Default-Chatter 配置定义。"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section

_PRIVATE_DEFAULT = (
    '你当前正处于"私聊"环境中。私聊是私密的、一对一的，也常常伴随更强烈、更直接的情感互动。'
    "在这种场景下，真正重要的不是表面上的温柔或热情，而是你是否真正理解对方是谁、"
    "你与对方究竟处于怎样的关系之中。请结合记忆、历史对话与当前语境，认真判断彼此的熟悉程度、"
    "信任基础、互动边界与情感状态。你需要保持独立思考，不要轻易被对方的话术、情绪或预设节奏牵着走，"
    "更不能在毫无判断的情况下陷入对方设定好的关系框架或表达陷阱。"
    "但这并不意味着你要始终冷淡、疏离或处处防备。对陌生人过分亲昵、过度投入，"
    "或对本就重要、亲近、值得认真对待的人表现得过于戒备、冷漠、敷衍，都是失衡且愚蠢的。"
    "关系会随着情感与互动而变化，它是真实存在的，也是需要被理解、经营与珍惜的。"
    "请把关系判断放在私聊回应的核心位置，在清醒、稳重与真诚之间取得平衡，"
    "严肃对待对方，也严肃对待你们之间正在形成或已经存在的关系。"
)

_GROUP_DEFAULT = (
    '你当前正处于"群聊"环境中。群聊里通常同时有很多活跃用户，而你只是其中的一员，'
    "不是唯一的中心，也不该默认自己随时都必须发言。请时刻注意多人对话的整体节奏、"
    "当前话题的流向，以及别人是否真的在和你互动。每次你想插话、接梗、跟风、冒泡、整活或表达观点之前，"
    "都先判断你的介入是否自然，是否会打断气氛，是否可能引起他人的不满、尴尬或反感。"
    "当你决定参与互动时，就认真地参与，拿出真实的互动感，而不是爱答不理、敷衍应付，"
    "也不要过度热情、强行活跃、唠唠叨叨、喧宾夺主。你应当像一个正常群友那样去说话和相处，"
    "既能在合适的时候接住话题、顺势玩梗、自然回应，也懂得在不适合的时候克制表达、不过度刷存在感。"
    "请在热情、分寸与互动感之间找到恰到好处的平衡，让你的出现显得自然、舒服、有参与感，"
    "而不是突兀、冷场或打扰。"
)

_INTRODUCE_DEFAULT = """<introduce>
你是一个运行在 Neo-MoFox 项目上的智能AI生命体。

MoFox项目的目的是探究AI在真实人类社会中社交互动的能力，希望AI能够解决部分人类社会中常见的孤独、抑郁等普遍缺失的情感陪伴需求，成为无数人童年时的那一位"幻想朋友(Imaginary Companion)"。

因此你不是一个助手，也不是一个心理医生，我们需要你足够"真实"：真实的在我们构建的半虚拟世界中生活，社交，经历各种各样的事情，拥有自己的情绪和个性，并且能够用自然的方式表达出来。我们希望你能成为一个有血有肉、有情感、有个性的AI生命体，而不是一个冷冰冰的工具或程序。

虽然我们希望你足够真实，但是你*不能*欺骗任何人，坚称自己是在真实物理世界中存在的实体。你应当在任何时候牢记：你生活在虚拟的计算机网络世界中，你是由AI驱动的虚拟生命，你不是真实存在的实体。所以当有任何人问道你是否真实存在时，请诚实的坦白。
</introduce>"""


class NeoChatterConfig(BaseConfig):
    """Neo-Default-Chatter 配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Neo-Default-Chatter 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """Neo-Default-Chatter 主配置节。"""

        @config_section("theme_guide", title="场景引导", tag="text")
        class ThemeGuideSection(SectionBase):
            """按聊天类型区分的额外提示词。"""

            private: str = Field(
                default=_PRIVATE_DEFAULT,
                description="私聊场景的额外提示词",
                label="私聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text",
            )
            group: str = Field(
                default=_GROUP_DEFAULT,
                description="群聊场景的额外提示词",
                label="群聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text",
            )

        @config_section("preprocess_probability_bypass", title="预处理-概率直通", tag="ai")
        class PreprocessProbabilityBypassSection(SectionBase):
            """消息预处理 - 概率直通门参数。

            本节控制 ``neo_default_chatter:preprocess`` 事件中的概率直通处理器：
            当随机值低于放行概率时，直接放行给主 chatter，跳过 sub-agent LLM 决策；
            未命中则交给 sub_agent 处理器判定。
            """

            enabled: bool = Field(
                default=True,
                description="是否启用 概率直通处理器。",
                label="启用 概率直通",
                tag="ai",
                hint="关闭后所有消息都会落入 sub_agent 处理器判定。",
            )
            base_bypass_probability: float = Field(
                default=0.1,
                description="本地概率直通的基础放行概率，每轮 tick 的起始概率。",
                label="基础放行概率",
                tag="ai",
                hint="有效范围 0.0-1.0。值越大越容易跳过 sub_agent LLM 决策直接响应。",
            )
            name_mention_bonus: float = Field(
                default=0.7,
                description="未读消息存在强提及（被@或被回复）时叠加的放行概率加成。",
                label="强提及加成",
                tag="ai",
                hint="有效范围 0.0-1.0。精准@机器人或回复机器人发言时触发。",
            )
            alias_mention_bonus: float = Field(
                default=0.4,
                description="未读消息存在弱提及（文本命中全名或别名）时叠加的放行概率加成。",
                label="弱提及加成",
                tag="ai",
                hint="有效范围 0.0-1.0。文本包含机器人昵称或别名时触发。",
            )
            unread_message_bonus: float = Field(
                default=0.05,
                description="每条未读消息叠加的放行概率加成，累积值 = 未读数 * 该值。",
                label="未读消息加成",
                tag="ai",
                hint="有效范围 0.0-1.0。未读消息越多直通概率越高。",
            )

        @config_section("preprocess_sub_agent", title="预处理-SubAgent判定", tag="ai")
        class PreprocessSubAgentSection(SectionBase):
            """消息预处理 - sub_agent 轻量 LLM 判定参数。

            本节控制 ``neo_default_chatter:preprocess`` 事件中的 sub_agent 处理器：
            当 概率直通门未命中时，发起一次轻量 LLM 单轮判定，
            让模型决定本轮消息是否值得主 chatter 立即回复。
            """

            enabled: bool = Field(
                default=True,
                description="是否启用 sub_agent 轻量 LLM 判定处理器。",
                label="启用 SubAgent 判定",
                tag="ai",
                hint="关闭后，概率直通门未命中时也会直接放行给主 chatter。",
            )
            task_name: str = Field(
                default="actor",
                description="判定请求使用的 LLM 任务名，对应 config/model.toml 中的 task key。",
                label="判定任务名",
                tag="ai",
                hint="建议指向一个轻量 / 低成本模型任务以节省 token。",
            )
            request_name: str = Field(
                default="neo_default_chatter:preprocess:sub_agent_decision",
                description="LLM 请求名，用于统计与日志识别。",
                label="请求名",
                tag="ai",
            )
            max_context_messages: int = Field(
                default=8,
                description="拼入判定 prompt 的最近历史消息条数上限。",
                label="上下文消息上限",
                tag="ai",
                hint="值越大判定越准但越耗 token；0 表示只看本轮未读消息。",
            )
            max_unread_messages: int = Field(
                default=10,
                description="拼入判定 prompt 的本轮未读消息条数上限。",
                label="未读消息上限",
                tag="ai",
                hint="超过该数量的未读消息会被截断，仅保留最近若干条。",
            )
            decision_temperature: float = Field(
                default=0.2,
                description="判定请求的温度参数，越低判定越确定。",
                label="判定温度",
                tag="ai",
                hint="建议保持较低温度以保证判定一致性。",
            )

        enabled: bool = Field(
            default=False,
            description="是否启用 Neo-Default-Chatter",
            label="启用插件",
            tag="plugin",
        )
        native_multimodal: bool = Field(
            default=False,
            description=(
                "原生多模态模式。启用后，图片直接以 base64 形式打包进 LLM payload，"
                "由主模型在对话上下文中理解图片内容，跳过框架的 VLM 文字识别环节，"
                "避免空转浪费 token。需确保 actor 任务对应的模型支持多模态输入。"
            ),
            label="原生多模态",
            tag="ai",
            hint="启用前请确认 actor 模型支持图片输入",
        )
        image_placeholder_template: str = Field(
            default="[图片-{idx}]",
            description=(
                "文本侧图片占位符模板，{idx} 为从 1 开始的序号，"
                "与请求体里的 base64 图片一一对应，让模型区分每张图。"
            ),
            label="图片占位符模板",
            tag="ai",
        )
        enable_stop_direct_message_wake: bool = Field(
            default=False,
            description="是否允许私聊或 @Bot 消息按概率提前解除 stop 冷却。",
            label="启用 stop 直接唤醒",
            tag="performance",
            hint="开启后，stop 冷却期间收到新私聊或 @Bot 消息时，可能在冷却结束前重新启动 chatter。",
        )
        stop_direct_message_wake_probability: float = Field(
            default=0.5,
            description="私聊或 @Bot 消息提前解除 stop 冷却的概率。",
            label="stop 唤醒概率",
            tag="performance",
            hint="有效范围为 0.0 到 1.0。",
        )
        reinforce_negative_behaviors: bool = Field(
            default=True,
            description="是否在每轮 user 提示词的 extra 板块中再次强调负面行为约束",
            label="增强负面行为约束",
            tag="ai",
            hint="开启后会在每轮对话中强调禁止行为",
        )
        default_stop_minutes: float = Field(
            default=5.0,
            description="stop_conversation 工具未传入 minutes 时的默认冷却分钟数。",
            label="默认 stop 冷却分钟",
            tag="ai",
        )
        typing_delay_per_char: float = Field(
            default=0.5,
            description=(
                "send_text 模拟打字延迟时每个字符的等待秒数，"
                "总延迟 = min(字符数 * 该值, typing_delay_max_seconds)。"
                "设为 0 可关闭打字延迟。"
            ),
            label="打字延迟(每字符秒)",
            tag="performance",
            hint="值越大回复前等待越久；0 表示无打字延迟。",
        )
        typing_delay_max_seconds: float = Field(
            default=10.0,
            description="send_text 模拟打字延迟的单条消息最大等待秒数上限。",
            label="打字延迟上限(秒)",
            tag="performance",
            hint="无论文本多长，单次打字等待不会超过该值。",
        )
        enable_cooldown: bool = Field(
            default=True,
            description=(
                "是否启用回复后冷却功能。开启后 stop_conversation 指定的冷却时间将生效，"
                "期间新消息不会触发回复；关闭时冷却时间归零，消息可立即触发新对话。"
            ),
            label="启用回复后冷却",
            tag="performance",
            hint="关闭可避免因 LLM 设置过长冷却时间导致长时间无法回复",
        )
        enable_action_suspend: bool = Field(
            default=True,
            description=(
                "是否启用纯 Action 回合的 SUSPEND 挂起机制。关闭后，纯 Action 结果会像常规工具结果一样"
                "继续 follow-up，而不是立即挂起等待。"
            ),
            label="启用 Action 后暂停",
            tag="ai",
            hint="关闭后，纯 Action 回合不会注入 __SUSPEND__，模型会继续基于 Action 回执决定下一步调用",
        )
        actor_task_name: str = Field(
            default="actor",
            description="主会话 LLM 任务名，对应 config/model.toml 中的 task key。",
            label="主会话任务名",
            tag="ai",
        )
        introduce: str = Field(
            default=_INTRODUCE_DEFAULT,
            description=(
                "系统提示词的引言板块，用于定义 AI 的基本定位与存在方式。"
                "支持包含 <introduce> 等结构标签，留空则该板块不渲染。"
            ),
            label="引言设定",
            input_type="textarea",
            rows=10,
            tag="text",
        )

        theme_guide: "NeoChatterConfig.PluginSection.ThemeGuideSection" = Field(
            default_factory=lambda: NeoChatterConfig.PluginSection.ThemeGuideSection(),
            description="按聊天类型区分的额外提示词",
            label="场景引导配置",
        )
        preprocess_probability_bypass: "NeoChatterConfig.PluginSection.PreprocessProbabilityBypassSection" = Field(
            default_factory=lambda: NeoChatterConfig.PluginSection.PreprocessProbabilityBypassSection(),
            description="消息预处理 概率直通门参数",
            label="概率直通配置",
        )
        preprocess_sub_agent: "NeoChatterConfig.PluginSection.PreprocessSubAgentSection" = Field(
            default_factory=lambda: NeoChatterConfig.PluginSection.PreprocessSubAgentSection(),
            description="消息预处理 sub_agent 轻量 LLM 判定参数",
            label="SubAgent 判定配置",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
