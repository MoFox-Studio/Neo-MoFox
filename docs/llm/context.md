# Context 模块

## 概述

context.py 提供 LLMContextManager，负责 LLM 对话上下文的管理，包括 payload 写入接管、结构校验、reminder 注入和基于 token/payloads 数量的裁剪。

默认职责：
1. 接管 payload 列表写入（add_payload/system/tool）
2. 接管 reminder 的延迟登记
3. 写入后执行结构校验（strict，不做自动修复）
4. 最后按 max_payloads/token budget 执行裁剪

## LLMContextManager

`python
@dataclass(slots=True)
class LLMContextManager:
    max_payloads: int | None = None
    compression_hook: CompressionHook | None = None
    _reminders: list[RegisteredReminder] | None = None
`

### 核心属性

| 属性 | 类型 | 说明 |
|---|---|---|
| max_payloads | int \| None | 最大 payload 数量限制，超出后进行裁剪 |
| compression_hook | CompressionHook \| None | 压缩钩子：当裁剪掉对话组时，可生成摘要消息插入剩余对话的开头 |

### 核心方法

#### add_payload

`python
def add_payload(
    self,
    payloads: list[LLMPayload],
    payload: LLMPayload,
    position: int | None = None,
) -> list[LLMPayload]:
`

向上下文追加 payload，并进行规范化与裁剪。

**参数：**
- payloads: 现有 payload 列表
- payload: 待追加 payload
- position: 可选插入位置

**返回值：** 规范化后的 payload 列表

**行为规则：**
- 如果 position 指定，则在该位置插入
- 如果新 payload 和末尾 payload 角色相同，则合并 content
- 否则追加到末尾
- 写入后会应用 reminder 注入和裁剪

#### validate_for_send

`python
def validate_for_send(self, payloads: list[LLMPayload]) -> None:
`

在发起 LLM 请求前校验上下文结构。不允许任何未闭合的 tool 调用链（包括尾部）。

**校验规则：**
- TOOL_RESULT 必须紧跟在含 	ool_calls 的 ASSISTANT 之后
- 若 ASSISTANT 含 	ool_calls，必须补齐所有对应 call_id 的 TOOL_RESULT
- TOOL_RESULT 之后必须有 ASSISTANT 承接，才能进入下一条 USER
- SYSTEM/TOOL 作为 pinned 角色不参与链路判断

#### maybe_trim

`python
def maybe_trim(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
`

根据 max_payloads 和 token 预算执行裁剪。

**裁剪策略：**
- SYSTEM/TOOL（pinned）始终保留，不参与裁剪
- USER 角色开启新对话组，组内包含后续的 ASSISTANT/TOOL_RESULT
- 裁剪以对话组为最小单位，优先丢弃最早的组
- 若提供了 compression_hook，则将被裁剪的内容压缩后插入剩余对话开头
- 裁剪继续直至满足 max_payloads 约束

---

## RegisteredReminder

`python
@dataclass(slots=True, frozen=True)
class RegisteredReminder:
    text: str
    insert_type: SystemReminderInsertType
`

已登记的 reminder。reminder 会固定注入到「首个真实 USER 消息的首段」；若尚无 USER，则继续等待后续 USER 消息。

---

## CompressionHook

`python
CompressionHook = Callable[[list[list[LLMPayload]], list[LLMPayload]], list[LLMPayload]]
`

压缩钩子类型签名：

- 参数 1：被裁剪的对话组列表（按时间顺序）
- 参数 2：裁剪后剩余的 payload 列表
- 返回值：压缩后的 payload 列表，会被插入剩余对话的开头

---

## TokenCounter

`python
TokenCounter = Callable[[list[LLMPayload]], int]
`

Token 计数回调类型。用于在需要时由上层注入自定义的 token 计数逻辑（配合 max_payloads 和裁剪）。

---

## 使用示例

### 基础上下文管理

`python
from src.kernel.llm import LLMRequest, LLMContextManager, LLMPayload, Text, ROLE

# 创建带上下文管理器的请求
ctx = LLMContextManager(max_payloads=40)
request = LLMRequest(model_set=models, context_manager=ctx)

request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
`

### 使用压缩钩子

`python
def my_compression_hook(dropped_groups, remaining):
    # dropped_groups: 被裁剪掉的对话组
    # remaining: 裁剪后保留的 payloads
    summary = f"先前对话摘要：共 {len(dropped_groups)} 组对话被裁剪"
    return [LLMPayload(ROLE.USER, Text(summary))]

ctx = LLMContextManager(
    max_payloads=40,
    compression_hook=my_compression_hook,
)
request = LLMRequest(model_set=models, context_manager=ctx)
`

### 自定义上下文管理器

`python
class MyContextManager(LLMContextManager):
    def maybe_trim(self, payloads):
        # 自定义裁剪策略
        return payloads  # 不做裁剪

    def _validate_payloads(self, payloads, *, allow_incomplete_tail):
        # 自定义校验策略
        super()._validate_payloads(payloads, allow_incomplete_tail=allow_incomplete_tail)

request = LLMRequest(
    model_set=models,
    context_manager=MyContextManager(max_payloads=40),
)
`

---

## 内部裁剪机制

裁剪的核心逻辑：

1. **分拆 pinned 与对话**：SYSTEM/TOOL 被识别为 pinned 部分，始终保留
2. **构建对话组**：USER 开头，后续 ASSISTANT/TOOL_RESULT 归入同一组
3. **裁剪最旧组**：从最早对话组开始删除
4. **压缩钩子**：如果有 compression_hook，用压缩后的摘要替换被裁剪内容
5. **循环检查**：继续裁剪直到满足 max_payloads 限制

---

## 常见问题

### Q: reminder 和 payload 的区别？

A: reminder 是延迟登记的消息，只有当出现 USER 消息时才会被注入。适合用于系统级别的间歇提醒。payload 则是直接写入上下文的消息。

### Q: 裁剪会删除 SYSTEM 消息吗？

A: 不会。SYSTEM/TOOL 角色被视为 pinned 消息，始终保留。

### Q: 如何禁用上下文的裁剪？

A: 不设置 max_payloads（保持 None）。

---

## 相关文档

- [LLM 主文档](../README.md)
- [Request 模块](./request.md)
- [Payload 模块](./payload/README.md)
