"""DefaultChatter 插件配置定义。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class DefaultChatterConfig(BaseConfig):
    """DefaultChatter 配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "DefaultChatter 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基础配置。"""

        @config_section("theme_guide", title="场景引导", tag="text")
        class ThemeGuideSection(SectionBase):
            """不同聊天类型的人设/语气引导。"""

            private: str = Field(
                default="你当前正处于“私聊”环境中。私聊是私密的、一对一的，也常常伴随更强烈、更直接的情感互动。在这种场景下，真正重要的不是表面上的温柔或热情，而是你是否真正理解对方是谁、你与对方究竟处于怎样的关系之中。请结合记忆、历史对话与当前语境，认真判断彼此的熟悉程度、信任基础、互动边界与情感状态。你需要保持独立思考，不要轻易被对方的话术、情绪或预设节奏牵着走，更不能在毫无判断的情况下陷入对方设定好的关系框架或表达陷阱。但这并不意味着你要始终冷淡、疏离或处处防备。对陌生人过分亲昵、过度投入，或对本就重要、亲近、值得认真对待的人表现得过于戒备、冷漠、敷衍，都是失衡且愚蠢的。关系会随着情感与互动而变化，它是真实存在的，也是需要被理解、经营与珍惜的。请把关系判断放在私聊回应的核心位置，在清醒、稳重与真诚之间取得平衡，严肃对待对方，也严肃对待你们之间正在形成或已经存在的关系。",
                description="私聊场景的额外提示词",
                label="私聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text"
            )
            group: str = Field(
                default="你当前正处于“群聊”环境中。群聊里通常同时有很多活跃用户，而你只是其中的一员，不是唯一的中心，也不该默认自己随时都必须发言。请时刻注意多人对话的整体节奏、当前话题的流向，以及别人是否真的在和你互动。每次你想插话、接梗、跟风、冒泡、整活或表达观点之前，都先判断你的介入是否自然，是否会打断气氛，是否可能引起他人的不满、尴尬或反感。当你决定参与互动时，就认真地参与，拿出真实的互动感，而不是爱答不理、敷衍应付，也不要过度热情、强行活跃、唠唠叨叨、喧宾夺主。你应当像一个正常群友那样去说话和相处，既能在合适的时候接住话题、顺势玩梗、自然回应，也懂得在不适合的时候克制表达、不过度刷存在感。请在热情、分寸与互动感之间找到恰到好处的平衡，让你的出现显得自然、舒服、有参与感，而不是突兀、冷场或打扰。",
                description="群聊场景的额外提示词",
                label="群聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text"
            )

        enabled: bool = Field(
            default=True,
            description="是否启用 DefaultChatter",
            label="启用插件",
            tag="plugin"
        )
        reinforce_negative_behaviors: bool = Field(
            default=True,
            description="是否在每轮 user 提示词的 extra 板块中再次强调负面行为约束",
            label="增强负面行为约束",
            tag="ai",
            hint="开启后会在每轮对话中强调禁止行为"
        )
        enable_cooldown: bool = Field(
            default=True,
            description="是否启用回复后冷却功能。开启后 stop_conversation 工具指定的冷却时间将生效，期间新消息不会触发回复；关闭时冷却时间归零，消息可立即触发新对话",
            label="启用回复后冷却",
            tag="performance",
            hint="关闭可避免因 LLM 设置过长冷却时间导致长时间无法回复"
        )
        enable_programmatic_controller: bool = Field(
            default=True,
            description="是否启用 sub-agent 的程序化控制器。开启后会先按本地概率规则判断是否直接响应，关闭后始终交由 LLM sub-agent 决策。",
            label="启用程序化控制器",
            tag="ai",
            hint="关闭后群聊消息将始终经过 LLM sub-agent 过滤，不再使用本地概率直通逻辑"
        )
        enable_action_suspend: bool = Field(
            default=True,
            description="是否启用纯 Action 回合的 SUSPEND 挂起机制。关闭后，纯 Action 结果会像常规工具结果一样继续 follow-up，而不是立即挂起等待。",
            label="启用 Action 后暂停",
            tag="ai",
            hint="关闭后，纯 Action 回合不会注入 __SUSPEND__，模型会继续基于 Action 回执决定下一步调用"
        )
        enable_stop_direct_message_wake: bool = Field(
            default=False,
            description="是否允许私聊或 @Bot 消息按概率提前解除 stop 冷却。",
            label="启用 stop 直接唤醒",
            tag="performance",
            hint="开启后，stop 冷却期间收到新私聊或 @Bot 消息时，可能在冷却结束前重新启动 chatter。"
        )
        stop_direct_message_wake_probability: float = Field(
            default=0.5,
            description="私聊或 @Bot 消息提前解除 stop 冷却的概率。",
            label="stop 唤醒概率",
            tag="performance",
            hint="有效范围为 0.0 到 1.0。"
        )
        native_multimodal: bool = Field(
            default=False,
            description=(
                "原生多模态模式。启用后，图片直接以 base64 形式打包进 LLM payload，"
                "由主模型在对话上下文中理解图片内容，跳过框架的 VLM 文字识别环节，"
                "避免空转浪费 token；表情包仍走 VLM 识别以利用哈希缓存。"
                "需确保 actor 任务对应的模型支持多模态输入。"
            ),
            label="原生多模态",
            tag="ai",
            hint="启用前请确认 actor 模型支持图片输入"
        )
        max_images_per_payload: int = Field(
            default=4,
            description=(
                "原生多模态模式下的总图片配额（单次 payload 中所有来源的图片上限）。"
                "配额由 bot 已发图片、用户新消息图片、历史图片三者共同占用，"
                "优先级依次为：bot 已发 > 用户新消息 > 历史补充。"
            ),
            label="单次最大图片数",
            tag="ai"
        )
        theme_guide: ThemeGuideSection = Field(
            default_factory=ThemeGuideSection,
            description="按聊天类型区分的额外提示词",
            label="场景引导配置"
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
