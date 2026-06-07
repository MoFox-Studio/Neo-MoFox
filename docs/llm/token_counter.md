# Token Counter 模块

## 概述

	oken_counter.py 提供基于 	iktoken 的 token 计数功能，用于计算一段文本或一组 LLMPayload 中包含的 token 数量。主要用于上下文裁剪和预算控制。

## 核心函数

### count_payload_tokens

`python
def count_payload_tokens(payloads: list[LLMPayload], *, model_identifier: str) -> int:
`

计算一组 LLMPayload 中包含的 token 总数。

**参数：**
- payloads: 待计数的 payload 列表
- model_identifier: 模型标识符，用于选择合适的 tokenizer

**返回值：** token 数量

**序列化策略：**
1. 以 role:{role} 开头
2. 对于每个 content 部分，按类型处理：
   - Text: 直接使用 	ext 属性
   - ToolResult: 调用 	o_text() 方法
   - ToolCall: 使用 
ame + JSON 序列化的 rgs
   - 其他类型：优先调用 	o_schema() 并 JSON 序列化，否则尝试 	ext/alue 属性
3. 各部分以换行符拼接

### count_text_tokens

`python
def count_text_tokens(text: str, *, model_identifier: str) -> int:
`

计算一段文本的 token 数量。

**参数：**
- 	ext: 待计数的文本
- model_identifier: 模型标识符

**返回值：** token 数量

---

## Tokenizer 选择

模块根据 model_identifier 自动选择合适的 tiktoken 编码器：

`python
def _get_tiktoken_encoding(model_identifier: str):
    try:
        return tiktoken.encoding_for_model(model_identifier)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")
`

若指定模型的编码器不可用，则回退到 cl100k_base（GPT-4/GPT-3.5-turbo 使用的编码器）。

---

## 使用示例

### 计算 payload 的 token 数

`python
from src.kernel.llm import count_payload_tokens, LLMPayload, Text, ROLE

payloads = [
    LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")),
    LLMPayload(ROLE.USER, Text("What is machine learning?")),
]

tokens = count_payload_tokens(payloads, model_identifier="gpt-4")
print(f"Token 数: {tokens}")
`

### 计算文本的 token 数

`python
from src.kernel.llm import count_text_tokens

text = "The quick brown fox jumps over the lazy dog."
tokens = count_text_tokens(text, model_identifier="gpt-4")
print(f"Token 数: {tokens}")
`

### 与上下文管理器配合使用

`python
from src.kernel.llm import LLMContextManager, count_payload_tokens

ctx = LLMContextManager(max_payloads=40)

# 自定义 token 计数回调
def my_token_counter(payloads):
    return count_payload_tokens(payloads, model_identifier="gpt-4")
`

### 检查是否超出上下文窗口

`python
from src.kernel.llm import count_payload_tokens

model_context_limit = 8192  # gpt-4 上下文窗口

tokens = count_payload_tokens(request.payloads, model_identifier="gpt-4")
if tokens > model_context_limit:
    print(f"警告: {tokens} tokens 超出限制 {model_context_limit}")
else:
    print(f"{tokens}/{model_context_limit} tokens 使用中")
`

---

## 依赖

本模块依赖 	iktoken 库。使用前需确保已安装：

`ash
pip install tiktoken
`

---

## 注意事项

- Token 计数是**估算值**，实际 API 消费的 token 数可能略有差异
- 不同模型的 tokenizer 不同，务必传入正确的 model_identifier
- 序列化策略产生额外的格式化 token（如 role: 前缀、换行符），尽量贴近实际 API 的 token 计数

---

## 相关文档

- [Context 模块](./context.md) - 上下文管理（使用 token 计数进行裁剪）
- [Payload 模块](./payload/README.md) - 消息负载
- [Types 模块](./types.md) - ModelEntry 定义
