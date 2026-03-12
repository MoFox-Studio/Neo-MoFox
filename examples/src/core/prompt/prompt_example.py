"""Prompt 模块使用示例

演示 core.prompt 的核心用法：
- 创建和使用 PromptTemplate
- 使用渲染策略（RenderPolicy）控制占位符渲染
- 使用 PromptManager 管理模板

运行：
    uv run python examples/src/core/prompt/prompt_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.prompt import (
    PromptTemplate,
    get_prompt_manager,
    reset_prompt_manager,
    optional,
    trim,
    header,
    wrap,
    join_blocks,
    min_len,
)

from src.kernel.logger import get_logger, COLOR

# 创建全局 logger
logger = get_logger("prompt_example", display="Prompt", color=COLOR.CYAN)


def example_basic_template() -> None:
    """示例 1: 基础模板使用"""
    logger.print_panel("示例 1: 基础模板使用")

    # 创建模板
    tmpl = PromptTemplate(
        name="greet",
        template="你好，{name}！今天是{day}。",
    )

    # 设置值并构建
    result = tmpl.set("name", "Alice").set("day", "星期一").build()

    logger.info(f"结果: {result}")
    logger.info("[OK] 基础模板示例完成\n")


def example_render_policies() -> None:
    """示例 2: 使用渲染策略"""
    logger.print_panel("示例 2: 使用渲染策略")

    # 创建带策略的模板
    tmpl = PromptTemplate(
        name="kb_query",
        template="用户问题：{query}\n\n{context_kb}\n\n请基于以上内容回答。",
        policies={
            # 使用策略链：先去除空格 -> 检查最小长度 -> 添加标题
            "context_kb": trim().then(min_len(10)).then(header("# 知识库内容：")),
        },
    )

    # 场景 1: 有知识库内容
    result1 = tmpl.set("query", "如何学习 Python？").set(
        "context_kb", "Python 是一种广泛使用的编程语言..."
    ).build()
    logger.info("场景 1 - 有知识库内容:")
    logger.info(result1)

    # 场景 2: 知识库内容为空
    result2 = tmpl.set("query", "如何学习 Python？").set("context_kb", "").build()
    logger.info("\n场景 2 - 知识库内容为空:")
    logger.info(result2)

    logger.info("[OK] 渲染策略示例完成\n")


def example_optional_policy() -> None:
    """示例 3: 使用可选值策略"""
    logger.print_panel("示例 3: 使用可选值策略")

    tmpl = PromptTemplate(
        name="with_optional",
        template="姓名：{name}\n年龄：{age}\n备注：{note}",
        policies={
            # note 为空时显示"无"
            "note": optional("无"),
        },
    )

    # 有备注
    result1 = tmpl.set("name", "Bob").set("age", 25).set("note", "VIP 客户").build()
    logger.info("有备注:")
    logger.info(result1)

    # 无备注
    result2 = tmpl.set("name", "Charlie").set("age", 30).set("note", "").build()
    logger.info("\n无备注:")
    logger.info(result2)

    logger.info("[OK] 可选值策略示例完成\n")


def example_wrap_policy() -> None:
    """示例 4: 使用包裹策略"""
    logger.print_panel("示例 4: 使用包裹策略")

    tmpl = PromptTemplate(
        name="code_block",
        template="请分析以下 JSON 数据：\n{data}",
        policies={
            # 将 JSON 数据包裹在代码块中
            "data": wrap("```json\n", "\n```"),
        },
    )

    result = tmpl.set("data", '{"name": "Alice", "age": 25}').build()
    logger.info("包裹代码块:")
    logger.info(result)

    logger.info("[OK] 包裹策略示例完成\n")


def example_join_blocks_policy() -> None:
    """示例 5: 使用连接块策略"""
    logger.print_panel("示例 5: 使用连接块策略")

    tmpl = PromptTemplate(
        name="summary",
        template="任务摘要：\n{tasks}",
        policies={
            # 将任务列表用换行符连接
            "tasks": join_blocks("\n- "),
        },
    )

    result = tmpl.set("tasks", ["完成设计", "编写代码", "单元测试"]).build()
    logger.info("任务列表:")
    logger.info(result)

    logger.info("[OK] 连接块策略示例完成\n")


def example_template_methods() -> None:
    """示例 6: 模板方法"""
    logger.print_panel("示例 6: 模板方法")

    tmpl = PromptTemplate(
        name="methods",
        template="姓名: {name}, 年龄: {age}",
    )

    # has / get / remove
    tmpl.set("name", "David")
    logger.info(f"has 'name': {tmpl.has('name')}")
    logger.info(f"get 'name': {tmpl.get('name')}")

    tmpl.remove("name")
    logger.info(f"remove 后 has 'name': {tmpl.has('name')}")

    # clone
    tmpl.set("name", "Eve").set("age", 28)
    cloned = tmpl.clone()
    cloned.set("name", "Frank")
    logger.info(f"原模板 name: {tmpl.get('name')}")
    logger.info(f"克隆模板 name: {cloned.get('name')}")

    # with_values
    new_tmpl = tmpl.with_values(name="Grace", age=30)
    logger.info(f"with_values 结果: {new_tmpl.build()}")

    logger.info("[OK] 模板方法示例完成\n")


def example_manager() -> None:
    """示例 7: 使用管理器"""
    logger.print_panel("示例 7: 使用管理器")

    # 重置管理器
    reset_prompt_manager()

    # 获取管理器
    manager = get_prompt_manager()

    # 注册模板
    tmpl1 = PromptTemplate(
        name="greet",
        template="你好，{name}！",
    )
    tmpl2 = PromptTemplate(
        name="farewell",
        template="再见，{name}！",
    )

    manager.register_template(tmpl1)
    manager.register_template(tmpl2)

    logger.info(f"已注册模板: {manager.list_templates()}")
    logger.info(f"模板数量: {manager.count()}")

    # 获取并使用模板
    greet_tmpl = manager.get_template("greet")
    if greet_tmpl:
        result = greet_tmpl.set("name", "用户").build()
        logger.info(f"使用 greet 模板: {result}")

    # get_or_create
    tmpl3 = manager.get_or_create(
        name="question",
        template="{question}？",
    )
    logger.info(f"get_or_create 后模板数量: {manager.count()}")

    logger.info("[OK] 管理器示例完成\n")


def example_complex_scenario() -> None:
    """示例 8: 复杂场景 - RAG 查询"""
    logger.print_panel("示例 8: 复杂场景 - RAG 查询")

    # 创建 RAG 查询模板
    tmpl = PromptTemplate(
        name="rag_query",
        template="""# 用户问题
{query}

# 相关上下文
{context_list}

# 历史对话
{history}

请基于以上上下文回答用户问题。""",
        policies={
            # 上下文列表：如果有内容，显示为编号列表
            "context_list": join_blocks("\n")
            .then(
                header("# 相关文档", sep="\n")
            ).then(
                min_len(20)
            ).then(
                optional("（无上下文）")
                ),
            # 历史对话：可选，为空时不显示
            "history": optional("（无历史对话）"),
        },
    )

    # 场景 1: 有上下文和历史
    result1 = (
        tmpl.set("query", "什么是 Neo-MoFox？")
        .set(
            "context_list",
            [
                "Neo-MoFox 是一个聊天机器人框架",
                "它采用三层架构设计",
                "core 层包含 prompt 模块",
            ],
        )
        .set(
            "history",
            ["用户: 你好", "助手: 您好！有什么可以帮助您的？"],
        )
        .build()
    )
    logger.info("场景 1 - 有上下文和历史:")
    logger.info(result1)
    logger.info("")

    # 场景 2: 无上下文
    result2 = (
        tmpl.set("query", "今天天气怎么样？")
        .set("context_list", [])
        .set("history", "")
        .build()
    )
    logger.info("场景 2 - 无上下文:")
    logger.info(result2)

    logger.info("[OK] 复杂场景示例完成\n")


def main() -> None:
    """运行所有示例"""
    example_basic_template()
    example_render_policies()
    example_optional_policy()
    example_wrap_policy()
    example_join_blocks_policy()
    example_template_methods()
    example_manager()
    example_complex_scenario()

    logger.print_panel("[完成] 所有示例执行完毕")


if __name__ == "__main__":
    main()
