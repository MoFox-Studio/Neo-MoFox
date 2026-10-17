# agent_manager 模块

对应源码：src/core/managers/agent_manager.py

## 概述

AgentManager 负责 Agent 组件的查询、筛选和执行。Agent 是 Chatter 的任务协助者，
拥有专属的私有 usables 套件。
筛选仅针对调用方传入的组件类列表进行，不从聊天流或全局注册表获取组件。

## 核心职责

- 获取全量 Agent 或插件级 Agent。
- 按传入组件类列表做统一筛选（chat_type、chatter、platform、关联类型、激活判定），
  筛选前发布 `BEFORE_AGENT_FILTER` 事件。
- 提供 Agent schema 生成。
- 执行 Agent 及其专属 usables。

## 关键入口

- filter_agents（筛选，需先通过 get_all_agents 等获取组件类）
- execute_agent
- execute_agent_usable
- get_agent_manager

## 设计要点

- Agent 只可调用自身 usables 中声明的组件，不可访问全局组件注册表。
- 筛选逻辑复用 `src.core.managers.utils.filtering` 中的公共实现。