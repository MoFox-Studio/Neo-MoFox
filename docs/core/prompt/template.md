# template 模块

对应源码：src/core/prompt/template.py

## 概述

PromptTemplate 是 prompt 构建核心对象，负责占位符值管理、策略渲染和构建前事件钩子。

## 数据结构

- name: 模板名称
- template: 包含占位符的模板字符串
- policies: key -> RenderPolicy 映射
- values: key -> 任意值映射

## 核心接口

- set/get/has/remove/clear
作用：管理占位符值，支持链式调用。

- build(strict=False)
作用：触发 on_prompt_build 事件后执行渲染并返回最终字符串。

- build_partial()
作用：仅替换已设置占位符，未设置占位符保留原样。

- clone()/with_values()
作用：复制模板并避免原模板被外部污染。

## build 执行细节

1. 复制 template/values/policies 为 effective_xxx。
2. 尝试发布 PROMPT_BUILD_EVENT。
3. 订阅者可回写 template/values/policies。
4. 调用 _render 完成最终渲染。

## strict 模式差异

- strict=True: 模板引用未设置占位符会触发 KeyError。
- strict=False: 未设置占位符按策略处理（默认 optional 空串）。

## 设计优势

- 支持事件驱动的动态 prompt 改写。
- 支持可复用策略链，避免模板内部硬编码逻辑。
- clone 语义降低并发和共享状态问题。
