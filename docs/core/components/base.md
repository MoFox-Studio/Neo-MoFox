# base 子模块

对应源码目录：src/core/components/base

## 概述

base 子模块定义插件系统的基础抽象类型，是所有组件实现的继承起点。

## 基类清单

- BasePlugin
- BaseAction
- BaseAgent
- BaseTool
- BaseAdapter
- BaseChatter
- BaseCommand
- BaseConfig
- BaseEventHandler
- BaseService
- BaseRouter

## 辅助类型

- CommandNode: 命令树节点
- Wait、Success、Failure、Stop: chatter 执行结果语义类型

## 设计特点

- 多数基础组件围绕组件签名和插件实例进行约束。
- Action、Tool、Agent 与 LLMUsable 协议协同，支持 schema 暴露。
- 各基类将通用约束前置，减少插件作者重复实现。

## 维护建议

- 新增基类能力时优先保持向后兼容。
- 改动公有属性名称前先检查 plugin_system/base 导出影响。
