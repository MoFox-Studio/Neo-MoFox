# manager 模块

对应源码：src/core/prompt/manager.py

## 概述

PromptManager 管理模板注册与检索，采用单例模式统一管理全局模板集合。

## 单例结构

- 类级单例：PromptManager.__new__ 控制 _instance
- 模块级单例：_global_manager + get_prompt_manager

两层单例共同保证全局一致性。

## 核心接口

- register_template
注册或覆盖模板。

- get_template
按名称获取模板 clone。

- get_or_create
存在则返回 clone，不存在则创建并注册后返回 clone。

- unregister_template/has_template/list_templates/clear/count
模板管理辅助接口。

## 关键语义

- get_template 返回 clone，不返回原对象。
- 调用方修改返回模板，不会污染管理器内原模板。

## reset 行为

reset_prompt_manager 会：

1. clear 全部模板
2. 置空模块级 _global_manager
3. 重置类级 PromptManager._instance

适合测试场景做完全隔离。
