"""Booku Memory Agent 插件包。

此包实现了基于 Agent 架构的 Booku 长期记忆系统，提供：
- 写入 Agent（BookuMemoryWriteAgent）：将对话中包含的用户信息写入记忆库
- 读取 Agent（BookuMemoryReadAgent）：根据语义查询检索相关记忆并汇总返回
- 服务组件（BookuMemoryService）：对外暴露完整的记忆 CRUD/检索 API

插件入口在 :mod:`plugin` 模块中由 ``@register_plugin`` 装饰的
``BookuMemoryAgentPlugin`` 类注册，通过插件加载器自动发现。
"""

__all__: list[str] = []
