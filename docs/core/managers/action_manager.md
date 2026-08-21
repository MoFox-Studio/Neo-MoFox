# action_manager 模块

对应源码：src/core/managers/action_manager.py

## 概述

ActionManager 负责 Action 组件的查询、筛选和执行，是主动响应链路的核心入口。
筛选仅针对调用方传入的组件类列表进行，不从聊天流或全局注册表获取组件。

## 核心职责

- 获取全量 Action 或插件级 Action。
- 按传入组件类列表做统一筛选（chat_type、chatter、platform、关联类型、激活判定），
  筛选前发布 `BEFORE_ACTION_FILTER` 事件。
- 提供 Action schema 生成与缓存。
- 执行 Action 并处理参数兼容（如 reason 参数剥离）。

## 关键入口

- filter_actions（筛选，需先通过 get_all_actions 等获取组件类）
- execute_action
- get_action_manager

## 设计要点

- Action 被视为 LLM Tool Calling 的主动行为。
- 执行流程与 task_manager 协作，避免阻塞主链路。
