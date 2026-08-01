"""Neo Booku Memory 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class NeoBookuMemoryConfig(BaseConfig):
    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Neo Booku Memory 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        enabled: bool = Field(default=True, description="是否启用插件", label="启用插件", tag="plugin")
        inject_system_prompt: bool = Field(
            default=True, description="是否将记忆引导语同步到 actor system reminder",
            label="注入系统提示", tag="ai",
            hint="开启后会在 AI 系统提示中添加记忆相关引导"
        )
        memory_tool_miss_warning_threshold: int = Field(
            default=6, description="actor 连续多少轮未调用 memory_command 后注入警告",
            label="记忆工具缺失告警阈值", ge=1, le=20, tag="ai",
            hint="达到阈值后仅对对应 stream 注入一次告警，直到再次调用记忆工具才重置"
        )

    @config_section("flashback", title="记忆闪回", tag="ai")
    class FlashbackSection(SectionBase):
        enabled: bool = Field(default=False, description="是否启用记忆闪回机制", label="启用闪回", tag="ai")
        trigger_probability: float = Field(
            default=0.05, description="每次构建 user prompt 时触发闪回的概率（0~1）",
            label="触发概率", ge=0.0, le=1.0, step=0.01, input_type="slider",
            tag="performance", depends_on="enabled", depends_value=True
        )
        archived_probability: float = Field(
            default=0.6, description="触发闪回后抽取归档层记忆的概率",
            label="归档概率", ge=0.0, le=1.0, step=0.05, input_type="slider",
            tag="performance", depends_on="enabled", depends_value=True
        )
        candidate_limit: int = Field(
            default=50, description="每次抽取时最多加载的候选记忆数量",
            label="候选数量", ge=10, le=200, tag="performance",
            depends_on="enabled", depends_value=True
        )
        activation_weight_exponent: float = Field(
            default=1.0, description="激活次数权重指数",
            label="权重指数", ge=0.5, le=3.0, step=0.1, tag="performance",
            depends_on="enabled", depends_value=True
        )
        cooldown_seconds: int = Field(
            default=3600, description="闪回去重冷却时间（秒），0 表示不启用去重",
            label="冷却时间（秒）", ge=0, le=86400, input_type="slider",
            tag="timer", depends_on="enabled", depends_value=True,
            hint="0 表示不启用去重"
        )

    @config_section("time_window", title="隐现记忆窗口", tag="timer")
    class TimeWindowSection(SectionBase):
        emergent_days: int = Field(
            default=7, description="隐现记忆时间窗口（天）", label="时间窗口（天）",
            ge=1, le=30, tag="timer"
        )
        activation_threshold: int = Field(
            default=2, description="窗口内最少激活次数，达到后晋升为归档", label="激活阈值",
            ge=1, le=20, tag="performance"
        )

    @config_section("internal_llm", title="内部 LLM 配置", tag="ai")
    class InternalLLMSection(SectionBase):
        task_name: str = Field(
            default="tool_use", description="内部决策使用的模型任务名", label="模型任务",
            placeholder="tool_use", tag="ai", hint="确保该任务在 model.toml 中已配置"
        )
        max_reasoning_steps: int = Field(
            default=12, description="内部 tool-calling 最大推理轮数", label="最大推理轮数",
            ge=1, le=50, tag="performance"
        )

    @config_section("startup_ingest", title="启动自动导入", tag="file")
    class StartupIngestSection(SectionBase):
        enabled: bool = Field(default=True, description="是否在启动时自动导入配置路径文档", label="启用启动导入", tag="plugin")
        paths: list[str] = Field(
            default_factory=lambda: [r"data\booku_memory\knowledges"],
            description="启动时自动导入的文件或目录路径列表", label="导入路径",
            input_type="list", item_type="str", tag="file"
        )
        recursive: bool = Field(default=True, description="目录路径是否递归扫描子目录", label="递归扫描目录", tag="file")
        skip_missing_paths: bool = Field(default=True, description="路径不存在时是否跳过并继续", label="跳过不存在路径", tag="file")
        skip_existing_title: bool = Field(default=True, description="文档标题已存在时是否跳过导入", label="跳过已存在标题", tag="file")

    plugin: PluginSection = Field(default_factory=PluginSection)
    flashback: FlashbackSection = Field(default_factory=FlashbackSection)
    time_window: TimeWindowSection = Field(default_factory=TimeWindowSection)
    internal_llm: InternalLLMSection = Field(default_factory=InternalLLMSection)
    startup_ingest: StartupIngestSection = Field(default_factory=StartupIngestSection)
