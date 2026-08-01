"""Neo Booku Memory 插件包。

此包与 booku_memory_store 组合可完美平替原有 booku_memory 插件：
- booku_memory_store 提供底层记忆数据库（search/read/create/update/delete）
- neo_booku_memory 提供工具（memory_command/temporary_memo）、事件处理、
  记忆闪回、系统提醒等高级机制

分层关系：
- agent/     → 工具层（CLI 命令解析、工具注册）
- service/   → 服务层（封装 booku_memory_store API、reminder 同步）
- 顶层       → 事件处理（闪回注入、启动导入、工具使用告警）
"""
