"""
Prompt 模板模块

提供 PromptTemplate 类，用于管理和渲染 LLM 提示词模板。
支持占位符映射和渲染策略链。

用法示例:
    from src.core.prompt.template import PromptTemplate
    from src.core.prompt.policies import trim, min_len, header

    tmpl = PromptTemplate(
        name="knowledge_base_query",
        template="用户问题：{user.query}\\n\\n{context.kb}\\n\\n",
        policies={
            "context.kb": trim().then(min_len(5)).then(header("# 知识库内容：")),
        }
    )

    prompt = await (
        tmpl.set("user.query", "怎么设计 prompt 系统？")
            .set("context.kb", "")
            .build()
    )
    print(prompt)  # 用户问题：怎么设计 prompt 系统？
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.core.prompt.policies import RenderPolicy, optional

# 事件名常量，供订阅方引用
PROMPT_BUILD_EVENT = "on_prompt_build"


@dataclass
class PromptTemplate:
    """提示词模板类

    用于管理 LLM 提示词模板，支持占位符替换和渲染策略。

    Attributes:
        name: 模板名称，用于标识和检索模板
        template: 模板字符串，使用 Python format 语法的占位符
        policies: 占位符到渲染策略的映射，控制如何渲染每个占位符
        values: 占位符的值映射
    """

    name: str
    template: str
    policies: dict[str, RenderPolicy] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> PromptTemplate:
        """设置占位符的值

        支持链式调用。

        Args:
            key: 占位符名称，如 "user.query"
            value: 占位符的值，可以是任意类型

        Returns:
            PromptTemplate: 返回自身，支持链式调用

        Examples:
            >>> tmpl = PromptTemplate(name="test", template="Hello {name}")
            >>> await tmpl.set("name", "World").build()
            "Hello World"
        """
        self.values[key] = value
        return self

    def get(self, key: str, default: Any = None) -> Any:
        """获取占位符的值

        Args:
            key: 占位符名称
            default: 默认值，如果占位符不存在则返回此值

        Returns:
            Any: 占位符的值，或默认值
        """
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        """检查是否设置了指定占位符

        Args:
            key: 占位符名称

        Returns:
            bool: 如果占位符已设置返回 True，否则返回 False
        """
        return key in self.values

    def remove(self, key: str) -> PromptTemplate:
        """移除占位符的值

        Args:
            key: 要移除的占位符名称

        Returns:
            PromptTemplate: 返回自身，支持链式调用
        """
        if key in self.values:
            del self.values[key]
        return self

    def clear(self) -> PromptTemplate:
        """清空所有占位符的值

        Returns:
            PromptTemplate: 返回自身，支持链式调用
        """
        self.values.clear()
        return self

    async def build(self, strict: bool = False) -> str:
        """构建最终的提示词字符串。

        根据设置的值和渲染策略生成最终的提示词。
        构建前会触发 ``on_prompt_build`` 事件，订阅者可以修改
        ``values``、``template``、``policies`` 后再继续渲染。

        Args:
            strict: 是否严格模式。严格模式下，如果模板中使用了
                   未设置的占位符会抛出 KeyError。非严格模式下，
                   未设置的占位符会被替换为空字符串。

        Returns:
            str: 渲染后的提示词字符串

        Raises:
            KeyError: 严格模式下，模板使用了未设置的占位符

        Examples:
            >>> prompt = await tmpl.set("name", "Alice").set("age", 25).build()
            "Hello Alice, you are 25 years old"
        """
        effective_template = self.template
        effective_values: dict[str, Any] = dict(self.values)
        effective_policies: dict[str, RenderPolicy] = dict(self.policies)

        try:
            from src.kernel.event import get_event_bus

            event_bus = get_event_bus()
            if event_bus.get_subscribers(PROMPT_BUILD_EVENT):
                _, final_params = await event_bus.publish(
                    PROMPT_BUILD_EVENT,
                    {
                        "name": self.name,
                        "template": effective_template,
                        "values": effective_values,
                        "policies": effective_policies,
                        "strict": strict,
                    },
                )
                effective_template = str(final_params.get("template", effective_template))
                if isinstance(final_params.get("values"), dict):
                    effective_values = final_params["values"]
                if isinstance(final_params.get("policies"), dict):
                    effective_policies = final_params["policies"]
        except Exception:
            # 事件触发失败不中断 prompt 构建，静默降级
            pass

        return self._render(effective_template, effective_values, effective_policies, strict)

    def _render(
        self,
        template: str,
        values: dict[str, Any],
        policies: dict[str, RenderPolicy],
        strict: bool,
    ) -> str:
        """内部渲染方法，根据给定的模板、值和策略生成提示词字符串。

        Args:
            template: 模板字符串
            values: 占位符值映射
            policies: 占位符渲染策略映射
            strict: 是否严格模式

        Returns:
            str: 渲染后的提示词字符串
        """
        rendered: dict[str, str] = {}

        for key, value in values.items():
            policy = policies.get(key, optional())
            rendered[key] = policy(value)

        # 在非严格模式下，提取模板中所有占位符，将未设置的设为空字符串
        if not strict:
            placeholders = set(re.findall(r'\{([^{}]+)\}', template))
            for placeholder in placeholders:
                if placeholder not in rendered:
                    # 应用策略处理未设置的占位符（如可选值策略会返回默认值）
                    policy = policies.get(placeholder, optional())
                    rendered[placeholder] = policy(None)

        return template.format_map(rendered)

    def build_partial(self) -> str:
        """部分构建模板，只替换已设置的占位符

        未设置的占位符保持原样（如 {name}）。

        Returns:
            str: 部分渲染的提示词字符串

        Examples:
            >>> tmpl = PromptTemplate(
            ...     name="greet",
            ...     template="Hello {name}, you are {age} years old"
            ... )
            >>> tmpl.set("name", "Alice").build_partial()
            "Hello Alice, you are {age} years old"
        """
        rendered = {}

        # 渲染所有已设置的值
        for key, value in self.values.items():
            policy = self.policies.get(key, optional())
            rendered[key] = policy(value)

        # 使用自定义的 dict 来处理缺失的 key
        class SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return f"{{{key}}}"

        return self.template.format_map(SafeDict(rendered))

    def clone(self) -> PromptTemplate:
        """克隆当前模板

        创建一个模板的深拷贝，包括名称、模板字符串、策略和值。

        Returns:
            PromptTemplate: 当前模板的副本
        """
        return PromptTemplate(
            name=self.name,
            template=self.template,
            policies=self.policies.copy(),
            values=self.values.copy(),
        )

    def with_values(self, **kwargs: Any) -> PromptTemplate:
        """创建一个包含新值的模板副本

        不会修改当前模板，而是返回一个新模板。

        Args:
            **kwargs: 占位符键值对

        Returns:
            PromptTemplate: 包含新值的新模板副本

        Examples:
            >>> tmpl = PromptTemplate(name="test", template="Hello {name}")
            >>> new_tmpl = tmpl.with_values(name="World")
            >>> await new_tmpl.build()
            "Hello World"
            >>> tmpl.values  # 原模板不受影响
            {}
        """
        new_tmpl = self.clone()
        for key, value in kwargs.items():
            new_tmpl.values[key] = value
        return new_tmpl

    def __repr__(self) -> str:
        """返回模板的字符串表示

        Returns:
            str: 模板的字符串表示
        """
        return f"PromptTemplate(name={self.name!r}, values={list(self.values.keys())})"
