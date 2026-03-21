# Prompt 模块文档

本目录对应 src/core/prompt，负责提示词模板、渲染策略与系统提醒组织。

## 模块职责

- 管理提示词模板和渲染策略
- 提供系统提醒的聚合与注入能力
- 支撑 Chatter/Agent 等组件的提示词构建

## 文档导航

- [template](./template.md): PromptTemplate 数据结构、build 行为与事件钩子。
- [policies](./policies.md): RenderPolicy 组合与策略函数语义。
- [system_reminder](./system_reminder.md): 系统提醒存储、bucket 规范与并发安全。
- [manager](./manager.md): PromptManager 单例、模板注册与 clone 语义。
- [build_flow](./build_flow.md): 从模板设置到最终 prompt 的构建流程。
- [troubleshooting](./troubleshooting.md): 常见提示词链路问题排查。

## 当前代码结构

- template.py: 提示词模板定义
- policies.py: 渲染策略
- system_reminder.py: 系统提醒管理
- manager.py: prompt 管理入口
- __init__.py: 对外导出

## 导出入口

src/core/prompt/__init__.py 聚合导出：

- 模板体系：PromptTemplate、PROMPT_BUILD_EVENT
- 管理器：PromptManager、get_prompt_manager、reset_prompt_manager
- 系统提醒：SystemReminderStore、SystemReminderBucket、get_system_reminder_store
- 渲染策略：RenderPolicy 与 optional/trim/header/wrap/join_blocks/min_len

## 模块内关键关系

- template.build 在渲染前可发布 PROMPT_BUILD_EVENT，允许事件订阅者动态改写。
- manager.get_template 返回 clone，防止调用方修改全局模板原件。
- policies 支持 then 链式组合，形成可复用渲染管线。
- system_reminder 与 prompt 拼装解耦，按 bucket/name 提供结构化文本块。

## 依赖关系

- 服务对象：src/core/managers、src/app/plugin_system/api/prompt_api
- 相关能力：src/kernel/llm

## 维护建议

- 调整模板字段命名时，优先保持占位符向后兼容。
- 新增策略函数时，建议同时补充链式组合示例。
- 事件订阅引入 prompt 改写时，应记录是否会影响下游审计与复现。
