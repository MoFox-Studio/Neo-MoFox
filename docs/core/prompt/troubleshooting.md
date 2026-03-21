# Prompt 排障手册

## build 结果为空或缺字段

排查：

1. 检查 values 是否设置对应占位符。
2. 检查 strict=False 时策略是否把值过滤为空。
3. 检查 min_len/header/optional 链式组合是否导致空输出。

## strict 模式报 KeyError

排查：

1. 模板中的占位符是否全部 set。
2. 是否在事件回调中替换了 template 但未补齐 values。

## 事件改写后 prompt 异常

排查：

1. 检查 PROMPT_BUILD_EVENT 订阅者是否返回了错误结构。
2. 检查 values/policies 被改写为非 dict 类型。
3. 检查事件处理器是否有异常被吞掉导致状态不一致。

## 模板修改影响全局

说明：

- 通过 manager.get_template 得到的是 clone。
- 若直接持有 register 时的原模板对象并修改，会影响全局行为。

建议：

- 调用侧只使用 get_template/get_or_create 的返回副本。

## system_reminder 未生效

排查：

1. bucket/name 是否匹配。
2. get(names=...) 时名称顺序和拼写是否正确。
3. 是否在 clear_bucket/clear_all 后未重新写入。

## 多线程并发下提醒错乱

说明：

- SystemReminderStore 使用 RLock 已保证线程安全。
- 若出现错乱，优先排查调用侧是否共享了错误的中间缓存。
