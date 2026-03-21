# Prompt 构建流程

## 目标

说明 PromptTemplate 从输入值到最终 prompt 字符串的完整流水线。

## 标准流程

1. 创建模板
- 定义 template 与 policies。

2. 注入值
- 通过 set 或 with_values 写入占位符值。

3. 触发构建
- 调用 build(strict=...)。

4. 事件改写阶段
- build 内发布 PROMPT_BUILD_EVENT。
- 事件处理器可改写 template/values/policies。

5. 策略渲染阶段
- 对每个值应用对应 RenderPolicy。
- strict=False 下补齐未设置占位符渲染。

6. 模板替换
- format_map 输出最终 prompt。

## 数据流说明

输入：template + values + policies

中间态：effective_template + effective_values + effective_policies

输出：final prompt string

## 与 SystemReminder 协作

推荐做法：

1. 从 SystemReminderStore.get 拉取 bucket 文本。
2. 作为某个占位符值注入模板。
3. 使用 header/wrap/join_blocks 等策略规范展示。

## 常见模式

- 模板内定义最小骨架。
- 业务层负责注入上下文。
- 事件层负责全局约束修正。
- 策略层负责格式清洗与容错。
