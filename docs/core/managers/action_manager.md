# action_manager 模块

对应源码：src/core/managers/action_manager.py

## 概述

ActionManager 负责 Action 组件的查询、上下文筛选和执行，是主动响应链路的核心入口。

## 核心职责

- 获取全量 Action 或插件级 Action。
- 按 chat_type、chatter、platform 过滤可用 Action。
- 提供 Action schema 生成与缓存。
- 执行 Action 并处理参数兼容（如 reason 参数剥离）。

## 关键入口

- get_actions_for_chat
- execute_action
- get_action_manager

## 设计要点

- Action 被视为 LLM Tool Calling 的主动行为。
- 执行流程与 task_manager 协作，避免阻塞主链路。
