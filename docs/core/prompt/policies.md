# policies 模块

对应源码：src/core/prompt/policies.py

## 概述

policies 模块提供 RenderPolicy 及一组策略工厂函数，用于控制占位符渲染行为。

## RenderPolicy

- 本质：fn: Callable[[Any], str]
- 调用：policy(value) -> str
- 组合：policy_a.then(policy_b)

then 语义是串联执行：先执行当前策略，再把输出交给下一策略。

## 内置策略

- optional(empty='')
空值替换为默认文本。

- trim()
去除首尾空白。

- header(title, sep='\n')
非空内容前添加标题。

- wrap(prefix='', suffix='')
非空内容加前后包裹。

- join_blocks(block_sep='\n\n')
列表/元组内容按块拼接。

- min_len(n)
长度不足时返回空。

## 空值判定

_is_effectively_empty 统一判定空值：

- None
- 空白字符串
- 空 list/tuple/set/dict

## 组合示例

常见链路：trim -> min_len -> header

效果：先清洗，再过滤短文本，再加章节标题。

## 维护建议

- 新策略应保证输入任意类型可安全转换。
- 组合策略应保持幂等，避免重复调用结果漂移。
