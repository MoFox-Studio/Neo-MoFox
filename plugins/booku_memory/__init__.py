"""Booku Memory 插件包。

此包实现命令驱动的长期记忆系统：
- 对外仅暴露 ``memory_command(command)`` 一个工具入口
- 底层由 ``BookuMemoryService`` 提供检索与 CRUD 能力
- ``BookuKnowledgeService`` 负责知识库导入与检索兼容

插件入口在 :mod:`plugin` 模块中由 ``@register_plugin`` 装饰的
``BookuMemoryAgentPlugin`` 类注册，通过插件加载器自动发现。
"""

__all__: list[str] = []
