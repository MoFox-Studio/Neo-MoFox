"""
Prompt management system.

提供提示词模板管理和渲染功能，支持占位符映射和渲染策略链。

用法示例:
    from src.core.prompt import PromptTemplate, get_prompt_manager
    from src.core.prompt.policies import trim, min_len, header, optional

    # 创建并使用模板
    tmpl = PromptTemplate(
        name="knowledge_base_query",
        template="用户问题：{user.query}\\n\\n{context.kb}\\n\\n",
        policies={
            "context.kb": trim().then(min_len(5)).then(header("# 知识库内容：")),
        }
    )

    prompt = (
        tmpl.set("user.query", "怎么设计 prompt 系统？")
            .set("context.kb", "")
            .build()
    )

    # 从管理器获取模板
    manager = get_prompt_manager()
    template = manager.get_template("knowledge_base_query")
"""

# 模板相关
from src.core.prompt.template import PromptTemplate, PROMPT_BUILD_EVENT

# 管理器相关
from src.core.prompt.manager import (
    PromptManager,
    get_prompt_manager,
    reset_prompt_manager,
)

# system reminder
from src.core.prompt.system_reminder import (
    SystemReminderBucket,
    SystemReminderInsertType,
    SystemReminderItem,
    SystemReminderStore,
    get_system_reminder_store,
    reset_system_reminder_store,
)

# 渲染策略相关
from src.core.prompt.policies import (
    RenderPolicy,
    optional,
    trim,
    header,
    wrap,
    join_blocks,
    min_len,
    _is_effectively_empty,
)

__all__ = [
    # 主要接口
    "PromptTemplate",
    "get_prompt_manager",
    "PromptManager",
    "reset_prompt_manager",
    # system reminder
    "SystemReminderBucket",
    "SystemReminderInsertType",
    "SystemReminderItem",
    "SystemReminderStore",
    "get_system_reminder_store",
    "reset_system_reminder_store",
    # 事件
    "PROMPT_BUILD_EVENT",
    # 渲染策略
    "RenderPolicy",
    "optional",
    "trim",
    "header",
    "wrap",
    "join_blocks",
    "min_len",
    # 内部函数（导出以便外部使用）
    "_is_effectively_empty",
]

# 版本信息
__version__ = "1.0.0"
