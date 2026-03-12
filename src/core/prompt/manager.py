"""
Prompt 管理器模块

提供 PromptManager 类，用于注册、存储和检索 PromptTemplate 实例。
支持按名称获取模板，以及模板的自动注册。

用法示例:
    from src.core.prompt import PromptTemplate, get_prompt_manager
    from src.core.prompt.policies import trim, header

    # 创建模板时会自动注册到管理器
    tmpl = PromptTemplate(
        name="my_template",
        template="Hello {name}",
    )

    # 从管理器获取模板
    manager = get_prompt_manager()
    template = manager.get_template("my_template")
    prompt = template.set("name", "World").build()
"""

from __future__ import annotations

from typing import Any

from src.core.prompt.template import PromptTemplate


class PromptManager:
    """提示词模板管理器

    负责管理所有 PromptTemplate 实例，提供注册、检索和删除功能。
    实现为单例模式，确保全局只有一个管理器实例。

    Attributes:
        _templates: 存储模板的字典，键为模板名称
    """

    _instance: PromptManager | None = None

    def __new__(cls) -> PromptManager:
        """实现单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """初始化管理器"""
        if self._initialized:
            return
        self._templates: dict[str, PromptTemplate] = {}
        self._initialized = True

    def register_template(self, template: PromptTemplate) -> None:
        """注册提示词模板

        如果同名模板已存在，会覆盖原有模板。

        Args:
            template: 要注册的 PromptTemplate 实例

        Examples:
            >>> manager = PromptManager()
            >>> tmpl = PromptTemplate(name="test", template="Hello {name}")
            >>> manager.register_template(tmpl)
        """
        self._templates[template.name] = template

    def unregister_template(self, name: str) -> bool:
        """注销提示词模板

        Args:
            name: 模板名称

        Returns:
            bool: 如果模板存在并删除成功返回 True，否则返回 False
        """
        if name in self._templates:
            del self._templates[name]
            return True
        return False

    def get_template(self, name: str) -> PromptTemplate | None:
        """获取提示词模板

        Args:
            name: 模板名称

        Returns:
            PromptTemplate | None: 如果找到返回模板实例，否则返回 None

        Examples:
            >>> manager = PromptManager()
            >>> tmpl = manager.get_template("my_template")
            >>> if tmpl:
            ...     prompt = tmpl.set("key", "value").build()
        """
        tmpl = self._templates.get(name)
        return tmpl.clone() if tmpl is not None else None

    def get_or_create(
        self,
        name: str,
        template: str,
        policies: dict[str, Any] | None = None,
    ) -> PromptTemplate:
        """获取或创建提示词模板

        如果模板已存在则返回现有模板，否则创建新模板并注册。

        Args:
            name: 模板名称
            template: 模板字符串
            policies: 可选的渲染策略映射

        Returns:
            PromptTemplate: 模板实例

        Examples:
            >>> manager = PromptManager()
            >>> # 第一次调用会创建模板
            >>> tmpl1 = manager.get_or_create("test", "Hello {name}")
            >>> # 第二次调用会返回已存在的模板
            >>> tmpl2 = manager.get_or_create("test", "Goodbye {name}")
            >>> tmpl1 is tmpl2  # True
        """
        existing = self.get_template(name)
        if existing is not None:
            return existing  # get_template 已返回 clone，无需再次 clone

        from src.core.prompt.policies import RenderPolicy

        # 确保 policies 中的值是 RenderPolicy 实例
        resolved_policies: dict[str, Any] = {}
        if policies:
            for key, value in policies.items():
                if not isinstance(value, RenderPolicy):
                    # 如果不是 RenderPolicy，直接使用原值
                    resolved_policies[key] = value
                else:
                    resolved_policies[key] = value

        new_template = PromptTemplate(
            name=name,
            template=template,
            policies=resolved_policies or {},
        )
        self.register_template(new_template)
        return new_template.clone()

    def has_template(self, name: str) -> bool:
        """检查模板是否存在

        Args:
            name: 模板名称

        Returns:
            bool: 如果模板存在返回 True，否则返回 False
        """
        return name in self._templates

    def list_templates(self) -> list[str]:
        """列出所有已注册的模板名称

        Returns:
            list[str]: 模板名称列表

        Examples:
            >>> manager = PromptManager()
            >>> names = manager.list_templates()
            >>> print(names)
            ['template1', 'template2', ...]
        """
        return list(self._templates.keys())

    def clear(self) -> None:
        """清空所有已注册的模板

        Examples:
            >>> manager = PromptManager()
            >>> manager.clear()
        """
        self._templates.clear()

    def count(self) -> int:
        """获取已注册模板的数量

        Returns:
            int: 模板数量
        """
        return len(self._templates)

    def __repr__(self) -> str:
        """返回管理器的字符串表示

        Returns:
            str: 管理器的字符串表示
        """
        return f"PromptManager(templates={self.count()})"


# 全局单例实例
_global_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """获取全局提示词管理器实例

    Returns:
        PromptManager: 全局唯一的提示词管理器实例

    Examples:
        >>> from src.core.prompt.manager import get_prompt_manager
        >>> manager = get_prompt_manager()
        >>> tmpl = manager.get_template("my_template")
    """
    global _global_manager
    if _global_manager is None:
        _global_manager = PromptManager()
    return _global_manager


def reset_prompt_manager() -> None:
    """重置全局提示词管理器

    主要用于测试场景，清空所有已注册的模板。

    Examples:
        >>> from src.core.prompt.manager import reset_prompt_manager
        >>> reset_prompt_manager()
    """
    global _global_manager
    if _global_manager is not None:
        _global_manager.clear()
    _global_manager = None
    # Reset the class-level singleton instance as well
    PromptManager._instance = None
