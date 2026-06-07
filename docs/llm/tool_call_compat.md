# Tool Call Compat 模块

## 概述

	ool_call_compat.py 提供工具调用兼容性支持。用于处理原生不支持 tool call 的模型，通过 prompt 注入和响应解析来模拟标准的工具调用行为。

## 核心功能

### 1. Prompt 构建

向不支持原生 tool call 的模型注入 JSON 格式指导，使其输出符合格式的响应。

### 2. 响应解析

解析 LLM 输出的兼容性响应，提取自然语言回复和工具调用列表。

---

## 核心函数

### build_tool_call_compat_prompt

`python
def build_tool_call_compat_prompt(tool_schemas: list[dict[str, Any]]) -> str:
`

构建工具调用兼容性提示文本，指导 LLM 输出符合格式的 JSON 响应。

**参数：**
- 	ool_schemas: 工具 schema 列表

**返回值：** 格式指导 prompt 文本

生成的 prompt 会引导模型返回如下 JSON：

`json
{
  "message": "给用户的可选回复",
  "tool_calls": [
    {"id": "可选字符串ID", "name": "工具名", "args": {"参数名": "参数值"}}
  ]
}
`

### parse_tool_call_compat_response

`python
def parse_tool_call_compat_response(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
`

解析 LLM 的兼容性响应，返回自然语言回复和工具调用列表。

**参数：**
- raw_text: LLM 输出的原始文本

**返回值：** (message_text, tool_calls) 元组
- message_text: 自然语言回复（可能为空字符串）
- 	ool_calls: 工具调用列表，每项包含 id/
ame/rgs

**容错能力：**
- 使用 json_repair 修复格式不严格的 JSON
- 支持 	ool_calls、calls 等多种字段名
- 支持 unction.name/unction.arguments 嵌套格式
- 支持字符串形式的 rgs（自动解析为 dict）
- 缺少 id 时返回 None

---

## 使用示例

### 基础使用

`python
from src.kernel.llm.tool_call_compat import (
    build_tool_call_compat_prompt,
    parse_tool_call_compat_response,
)

# 构建工具 schema
schemas = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "搜索互联网",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                }
            }
        }
    }
]

# 生成 prompt
compat_prompt = build_tool_call_compat_prompt(schemas)

# 添加到请求
request.add_payload(LLMPayload(ROLE.SYSTEM, Text(compat_prompt)))
request.add_payload(LLMPayload(ROLE.USER, Text("帮我搜索 Python 教程")))

response = await request.send()
raw_text = await response

# 解析响应
message, tool_calls = parse_tool_call_compat_response(raw_text)

if tool_calls:
    for call in tool_calls:
        print(f"调用工具: {call['name']}")
        print(f"参数: {call['args']}")
elif message:
    print(f"回复: {message}")
`

### 在模型配置中启用兼容模式

`python
model_set = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-3.5-turbo",
        "api_key": "sk-...",
        "tool_call_compat": True,  # 启用兼容模式
    }
]
`

当 	ool_call_compat 为 True 时，框架会自动构建兼容 prompt 并解析响应。

### 处理解析异常

`python
from src.kernel.llm import LLMError

try:
    message, tool_calls = parse_tool_call_compat_response(raw_text)
except LLMError as e:
    print(f"工具调用解析失败: {e}")
    # 作为普通文本处理
`

---

## JSON 容错机制

使用 json_repair 库修复格式不严格的 JSON：

- 缺失引号自动补全
- 尾部逗号自动移除
- 不完整 JSON 尽力修复

`python
# 这些不规范的输出都可以被正确解析：
raw1 = '{message: "hello", tool_calls: [{name: "search", args: {}}]}'
raw2 = '{"message": "hello", "tool_calls": [{"name": "search", "args": {},}]}'
raw3 = '{"tool_calls": [{"name": "search", "args": "{\\\"query\\\": \\\"test\\\"}"}]}'
`

---

## 配置说明

在 ModelEntry 中设置 	ool_call_compat 字段：

`python
{
    "tool_call_compat": True,  # 启用兼容模式
    # 或
    "tool_call_compat": False,  # 禁用（默认）
}
`

当该字段为 True 时：
1. 请求发送前会自动生成并注入兼容 prompt
2. 响应回来后会自动解析工具调用
3. 不适用于原生支持 tool call 的模型

---

## 依赖

本模块依赖 json_repair 库：

`ash
pip install json-repair
`

---

## 注意事项

- 兼容模式会增加 prompt token 消耗
- 任何 LLM 输出格式问题都可以通过 json_repair 修复
- 对于原生支持 tool call 的模型，不建议启用兼容模式
- 兼容模式下模型返回的 JSON 可能不稳定，建议做好异常处理

---

## 相关文档

- [Request 模块](./request.md)
- [Payload - Tooling 模块](./payload/tooling.md)
- [Types 模块](./types.md)
