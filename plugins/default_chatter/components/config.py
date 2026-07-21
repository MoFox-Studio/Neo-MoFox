"""DefaultChatter 插件配置定义。"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DefaultChatterConfig(BaseConfig):
    """DefaultChatter 配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "DefaultChatter 配置"

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
            hint="关闭后群聊消息始终经过 LLM sub-agent 过滤"
        )
        enable_action_suspend: bool = Field(
            default=True,
            description="是否启用纯 Action 回合的 SUSPEND 挂起机制。关闭后，纯 Action 结果会像常规工具结果一样继续 follow-up，而不是立即挂起等待。",
            label="启用 Action 后暂停",
            tag="ai",
            hint="关闭后，纯 Action 回合不会注入 __SUSPEND__，模型会继续基于 Action 回执决定下一步调用"
        )
        enable_sub_agent_collaboration: bool = Field(
            default=False,
            description="是否启用 default chatter 的子代理协作模式。开启后主工具列表将隐藏 MCP 工具，并改为通过 create_agent/get_agent/kill_agent 把指定工具和 MCP 能力委托给子代理。",
            label="启用子代理协作",
            tag="ai",
            hint="开启后主代理不能直接使用 MCP 工具，只能把它们分配给子代理"
        )
        sub_agent_task_name: str = Field(
            default="actor",
            description="子代理创建 LLM request 时使用的模型任务名，对应 config/model.toml 中的 task key。",
            label="子代理任务名",
            tag="ai",
            hint="留空时回退为 actor；可配置为单独的子代理模型任务，例如 sub_agent_actor"
        )

        filter_mode: str = Field(
            default="sub_only",
            description="消息过滤模式：sub_only=仅 sub-agent，interest_only=仅兴趣值，interest_then_sub=兴趣值初筛后执行 sub-agent",
            label="过滤模式",
            tag="ai",
            hint="选择群聊消息的响应决策流程",
            input_type="select",
            choices=["sub_only", "interest_only", "interest_then_sub"],
        )
        enable_sub_agent_context: bool = Field(
            default=True,
            description="是否为 sub-agent 提供历史上下文和决策记录",
            label="Sub-Agent 上下文",
            tag="ai",
            hint="开启后 sub-agent 能感知话题连续性，判断更准确"
        )
        sub_agent_context_history_limit: int = Field(
            default=25,
            description="sub-agent 上下文中包含的历史消息条数",
            label="Sub-Agent 历史消息数",
            tag="ai",
            hint="传给 sub-agent 的历史消息条数上限"
        )
        sub_agent_decision_history_limit: int = Field(
            default=3,
            description="sub-agent 决策历史保留条数",
            label="Sub-Agent 决策历史",
            tag="ai",
            hint="保留最近 N 次决策记录传给 sub-agent"
        )

        @config_section("interest", title="兴趣值配置", tag="ai")
        class InterestSection(SectionBase):
            """兴趣值计算参数。

            控制二维加权兴趣值计算（语义 + 提及）的权重、阈值和动态调整。
            """

            reply_threshold: float = Field(
                default=0.72,
                description="回复动作兴趣阈值，兴趣值达到此值则触发回复",
                label="回复阈值",
                tag="ai",
                hint="有效范围 0.0-1.0"
            )
            action_threshold: float = Field(
                default=0.55,
                description="非回复动作兴趣阈值，达到此值则执行非回复动作",
                label="动作阈值",
                tag="ai",
                hint="有效范围 0.0-1.0"
            )
            semantic_weight: float = Field(
                default=0.6,
                description="语义兴趣度权重",
                label="语义权重",
                tag="ai",
                hint="语义兴趣度在总分中的权重"
            )
            mentioned_weight: float = Field(
                default=0.4,
                description="提及分权重",
                label="提及权重",
                tag="ai",
                hint="提及分在总分中的权重"
            )
            strong_mention_score: float = Field(
                default=2.0,
                description="强提及的兴趣分（被@、被回复、私聊）",
                label="强提及分",
                tag="ai",
                hint="强提及时的提及分"
            )
            weak_mention_score: float = Field(
                default=0.8,
                description="弱提及的兴趣分（文本匹配名字/别名）",
                label="弱提及分",
                tag="ai",
                hint="弱提及时的提及分"
            )
            no_reply_threshold_adjustment: float = Field(
                default=0.02,
                description="每次不回复降低的阈值",
                label="不回复阈值降低",
                tag="ai",
                hint="连续不回复时每次降低的回复阈值"
            )
            max_no_reply_count: int = Field(
                default=5,
                description="最大不回复计数",
                label="最大不回复计数",
                tag="ai",
                hint="不回复计数上限"
            )
            reply_cooldown_reduction: int = Field(
                default=2,
                description="回复后减少的不回复计数",
                label="回复冷却减少",
                tag="ai",
                hint="回复后不回复计数的减少量"
            )
            enable_post_reply_boost: bool = Field(
                default=True,
                description="是否启用回复后阈值降低机制",
                label="回复后阈值降低",
                tag="ai",
                hint="开启后回复后若干轮降低阈值，增强连续对话"
            )
            post_reply_threshold_reduction: float = Field(
                default=0.2,
                description="回复后初始阈值降低值",
                label="回复后初始降低",
                tag="ai",
                hint="回复后第一轮降低的阈值"
            )
            post_reply_boost_max_count: int = Field(
                default=5,
                description="回复后阈值降低的最大持续次数",
                label="回复后持续次数",
                tag="ai",
                hint="阈值降低持续的轮数"
            )
            post_reply_boost_decay_rate: float = Field(
                default=0.8,
                description="每次回复后阈值降低的衰减率",
                label="回复后衰减率",
                tag="ai",
                hint="每轮衰减因子，1.0=不衰减，0.8=每轮衰减20%"
            )

        @config_section("semantic_training", title="语义模型训练配置", tag="ai")
        class SemanticTrainingSection(SectionBase):
            """语义兴趣度模型训练参数。

            控制自动训练流程中的数据采样、LLM 标注和关键词生成参数。
            仅在 filter_mode 非 sub_only 时生效。
            """

            training_model_name: str = Field(
                default="utils",
                description="训练阶段使用的 LLM 模型任务名，对应 config/model.toml 中的 task key",
                label="训练模型任务名",
                tag="ai",
                hint="用更强的模型（如 actor）标注可提升训练数据质量"
            )
            training_days: int = Field(
                default=7,
                description="采样最近 N 天的消息用于训练",
                label="采样天数",
                tag="ai",
                hint="越大覆盖越多历史话题，但训练时间增加"
            )
            training_max_samples: int = Field(
                default=1000,
                description="训练时从数据库采样的最大消息条数",
                label="最大采样数",
                tag="ai",
                hint="2000-3000 性价比最高，越多标注 token 成本越高"
            )
            training_batch_size: int = Field(
                default=50,
                description="LLM 批量标注时每批的消息条数",
                label="标注批次大小",
                tag="ai",
                hint="每批 50 条平衡速度和质量"
            )
            keyword_iterations: int = Field(
                default=3,
                description="关键词生成的迭代次数",
                label="关键词迭代次数",
                tag="ai",
                hint="每次生成约 100 条关键词，多次迭代可增加覆盖"
            )
            min_train_interval_hours: int = Field(
                default=720,
                description="最小训练间隔（小时），避免频繁重训",
                label="最小训练间隔",
                tag="ai",
                hint="默认 720 小时（30天），人设变化时不受此限制"
            )

        @config_section("programmatic_probability", title="程序化概率配置", tag="ai")
        class ProgrammaticProbabilitySection(SectionBase):
            """程序化 sub-agent 直通概率参数。

            这些参数控制 enable_programmatic_controller 开启时的本地概率门逻辑：
            当随机值低于放行概率时，直接跳过 LLM sub-agent 决策。
            """

            base_bypass_probability: float = Field(
                default=0.1,
                description="群聊本地概率直通的基础放行概率。每轮 tick 的起始概率。",
                label="基础放行概率",
                tag="ai",
                hint="有效范围 0.0-1.0。值越大，群聊中越容易跳过 LLM sub-agent 直接响应。",
            )
            name_mention_bonus: float = Field(
                default=1.0,
                description="未读消息存在强提及（被@或被回复）时叠加的放行概率加成。",
                label="强提及加成",
                tag="ai",
                hint="有效范围 0.0-1.0。当未读消息精准@机器人或回复机器人发言时触发。",
            )
            alias_mention_bonus: float = Field(
                default=0.4,
                description="未读消息存在弱提及（文本命中全名或别名）时叠加的放行概率加成。",
                label="弱提及加成",
                tag="ai",
                hint="有效范围 0.0-1.0。当未读消息文本中包含机器人昵称或别名时触发。",
            )
            unread_message_bonus: float = Field(
                default=0.05,
                description="每条未读消息叠加的放行概率加成。累积值 = 未读消息数 * 该值。",
                label="未读消息加成",
                tag="ai",
                hint="有效范围 0.0-1.0。未读消息越多，直通概率越高。",
            )
            next_tick_reply_bonus: float = Field(
                default=0.5,
                description="上一次 send_text 成功后，下一 tick 叠加的放行概率加成，用于提升连续对话的连贯性。",
                label="回复后下一 tick 加成",
                tag="ai",
                hint="有效范围 0.0-1.0。发送成功后写入流上下文，下一次概率门判定时消耗。",
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
        programmatic_probability: ProgrammaticProbabilitySection = Field(
            default_factory=ProgrammaticProbabilitySection,
            description="程序化 sub-agent 直通概率参数",
            label="程序化概率配置"
        )
        theme_guide: ThemeGuideSection = Field(
            default_factory=ThemeGuideSection,
            description="按聊天类型区分的额外提示词",
            label="场景引导配置"
        )
        interest: InterestSection = Field(
            default_factory=InterestSection,
            description="兴趣值计算参数",
            label="兴趣值配置"
        )
        semantic_training: SemanticTrainingSection = Field(
            default_factory=SemanticTrainingSection,
            description="语义模型训练参数",
            label="语义训练配置"
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
