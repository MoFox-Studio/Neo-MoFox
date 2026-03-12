# NEW_napcat_adapter

基于 mofox-wire v2.x 的 Napcat 适配器（使用 BaseAdapter 架构）

## 🏗️ 架构设计

本插件采用 **BaseAdapter 继承模式** 重写，完全抛弃旧版 maim_message 库，改用 mofox-wire 的 TypedDict 数据结构。

### 核心组件
- **NapcatAdapter**: 继承自 `mofox_wire.AdapterBase`，负责 OneBot 11 协议与 MessageEnvelope 的双向转换
- **WebSocketAdapterOptions**: 自动管理 WebSocket 连接，提供 incoming_parser 和 outgoing_encoder
- **CoreMessageSink**: 通过 `InProcessCoreSink` 将消息递送到核心系统
- **Handlers**: 独立的消息处理器，分为 to_core（接收）和 to_napcat（发送）两个方向

## 📁 项目结构

```
NEW_napcat_adapter/
├── plugin.py                      # ✅ 主插件文件（BaseAdapter实现）
├── _manifest.json                 # 插件清单
│
└── src/
    ├── event_models.py            # ✅ OneBot事件类型常量
    ├── common/
    │   └── core_sink.py           # ✅ 全局CoreSink访问点
    │
    ├── utils/
    │   ├── utils.py               # ⏳ 工具函数（待实现）
    │   ├── qq_emoji_list.py       # ⏳ QQ表情映射（待实现）
    │   ├── video_handler.py       # ⏳ 视频处理（待实现）
    │   └── message_chunker.py     # ⏳ 消息切片（待实现）
    │
    ├── websocket/
    │   └── (无需单独实现，使用WebSocketAdapterOptions)
    │
    ├── database/
    │   └── database.py            # ⏳ 数据库模型（待实现）
    │
    └── handlers/
        ├── to_core/               # Napcat → MessageEnvelope 方向
        │   ├── message_handler.py # ⏳ 消息处理（部分完成）
        │   ├── notice_handler.py  # ⏳ 通知处理（待完成）
        │   └── meta_event_handler.py  # ⏳ 元事件（待完成）
        │
        └── to_napcat/             # MessageEnvelope → Napcat API 方向
            └── send_handler.py    # ⏳ 发送处理（部分完成）
```

## 🚀 快速开始

### 使用方式

1. **配置文件**: 在 `config/plugins/NEW_napcat_adapter.toml` 中配置 WebSocket URL 和其他参数
2. **启动插件**: 插件自动在系统启动时加载
3. **WebSocket连接**: 自动连接到 Napcat OneBot 11 服务器

### 黑白名单过滤系统

本适配器内置了完整的黑白名单过滤系统，支持群聊和私聊消息的精细控制。

#### 🛡️ 过滤功能说明

1. **群聊过滤**
   - **黑名单模式** (`blacklist`): 屏蔽指定群聊的消息
   - **白名单模式** (`whitelist`): 只接收指定群聊的消息

2. **私聊过滤**
   - **黑名单模式** (`blacklist`): 屏蔽指定用户的私聊消息
   - **白名单模式** (`whitelist`): 只接收指定用户的私聊消息

3. **全局封禁**
   - **用户封禁列表**: 无论在群聊还是私聊中都会被过滤

#### 📝 配置示例

```toml
[features]
# 群聊配置：只允许特定群聊的消息
group_list_type = "whitelist"
group_list = ["123456789", "987654321"]

# 私聊配置：屏蔽特定用户的消息
private_list_type = "blacklist"
private_list = ["111111111", "222222222"]

# 全局封禁：这些用户的所有消息都会被过滤
ban_user_id = ["333333333", "444444444"]
```

#### 🎯 常见使用场景

1. **个人机器人**（只服务特定群组和用户）：
   ```toml
   group_list_type = "whitelist"
   private_list_type = "whitelist"
   ```

2. **群管机器人**（屏蔽捣乱用户）：
   ```toml
   group_list_type = "blacklist"
   ban_user_id = ["捣乱用户1", "捣乱用户2"]
   ```

3. **公开服务机器人**（无限制接收所有消息）：
   ```toml
   # 保持默认的 blacklist 模式，名单为空即可
   group_list_type = "blacklist"
   private_list_type = "blacklist"
   ```

#### 📋 配置参数详解

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `group_list_type` | string | "blacklist" | 群聊过滤模式："blacklist" 或 "whitelist" |
| `group_list` | array | [] | 群聊QQ号列表 |
| `private_list_type` | string | "blacklist" | 私聊过滤模式："blacklist" 或 "whitelist" |
| `private_list` | array | [] | 用户QQ号列表 |
| `ban_user_id` | array | [] | 全局封禁的用户QQ号列表 |

#### ⚡ 过滤逻辑流程

1. **消息接收** → 检查全局封禁列表
2. **通过** → 根据消息类型应用对应过滤规则：
   - **群聊消息**: 应用群聊黑白名单规则
   - **私聊消息**: 应用私聊黑白名单规则
3. **通过所有过滤** → 正常处理消息
4. **任一过滤失败** → 丢弃消息，记录调试日志

#### 🔧 配置文件位置

- **示例配置**: `src/plugins/built_in/napcat_adapter/config.example.toml`
- **实际配置**: `config/plugins/NEW_napcat_adapter.toml`

#### 📊 日志信息

被过滤的消息会在调试日志中记录：
```
[DEBUG] napcat_adapter: 群聊 123456789 在黑名单中，消息被过滤
[DEBUG] napcat_adapter: 私聊用户 111111111 在黑名单中，消息被过滤
[DEBUG] napcat_adapter: 用户 333333333 在全局封禁列表中，消息被过滤
```

## 🔑 核心数据结构

### MessageEnvelope (mofox-wire v2.x)

```python
from mofox_wire import MessageEnvelope, SegPayload, MessageInfoPayload

# 创建消息信封
envelope: MessageEnvelope = {
    "direction": "input",
    "message_info": {
        "message_type": "group",
        "message_id": "12345",
        "self_id": "bot_qq",
        "user_info": {
            "user_id": "sender_qq",
            "user_name": "发送者",
            "user_displayname": "昵称"
        },
        "group_info": {
            "group_id": "group_id",
            "group_name": "群名"
        },
        "to_me": False
    },
    "message_segment": {
        "type": "seglist",
        "data": [
            {"type": "text", "data": "hello"},
            {"type": "image", "data": "base64_data"}
        ]
    },
    "raw_message": "hello[图片]",
    "platform": "napcat",
    "message_id": "12345",
    "timestamp_ms": 1234567890
}
```

### BaseAdapter 核心方法

```python
class NapcatAdapter(BaseAdapter):
    async def from_platform_message(self, message: dict[str, Any]) -> MessageEnvelope | None:
        """将 OneBot 11 事件转换为 MessageEnvelope"""
        # 路由到对应的 Handler
        
    async def _send_platform_message(self, envelope: MessageEnvelope) -> dict[str, Any]:
        """将 MessageEnvelope 转换为 OneBot 11 API 调用"""
        # 调用 SendHandler 处理
```

## 📝 实现进度

### ✅ 已完成的核心架构

1. **BaseAdapter 实现** (plugin.py)
   - ✅ WebSocket 自动连接管理
   - ✅ from_platform_message() 事件路由
   - ✅ _send_platform_message() 消息发送
   - ✅ API 响应池机制（echo-based request-response）
   - ✅ CoreSink 集成

2. **Handler 基础结构**
   - ✅ MessageHandler 骨架（text、image、at 基本实现）
   - ✅ NoticeHandler 骨架
   - ✅ MetaEventHandler 骨架
   - ✅ SendHandler 骨架（基本类型转换）

3. **辅助组件**
   - ✅ event_models.py（事件类型常量）
   - ✅ core_sink.py（全局 CoreSink 访问）
   - ✅ 配置 Schema 定义

### ⏳ 部分完成的功能

4. **消息类型处理** (MessageHandler)
   - ✅ 基础消息类型：text, image, at
   - ❌ 高级消息类型：face, reply, forward, video, json, file, rps, dice, shake

5. **发送处理** (SendHandler)
   - ✅ 基础 SegPayload 转换：text, image
   - ❌ 高级 Seg 类型：emoji, voice, voiceurl, music, videourl, file, command

### ❌ 待实现的功能

6. **通知事件处理** (NoticeHandler)
   - ❌ 戳一戳事件
   - ❌ 表情回应事件
   - ❌ 撤回事件
   - ❌ 禁言事件

7. **工具函数** (utils.py)
   - ❌ get_group_info
   - ❌ get_member_info
   - ❌ get_image_base64
   - ❌ get_message_detail
   - ❌ get_record_detail

8. **权限系统**
   - ❌ check_allow_to_chat()
   - ❌ 群组黑名单/白名单
   - ❌ 私聊黑名单/白名单

9. **其他组件**
   - ❌ 视频处理器
   - ❌ 消息切片器
   - ❌ 数据库模型
   - ❌ QQ 表情映射表

## 📋 下一步工作

### 优先级 1：完善消息处理（参考旧版 recv_handler/message_handler.py）

1. **完整实现 MessageHandler.handle_raw_message()**
   - [ ] face（表情）消息段
   - [ ] reply（回复）消息段
   - [ ] forward（转发）消息段解析
   - [ ] video（视频）消息段
   - [ ] json（JSON卡片）消息段
   - [ ] file（文件）消息段
   - [ ] rps/dice/shake（特殊消息）

2. **实现工具函数**（参考旧版 utils.py）
   - [ ] `get_group_info()` - 获取群组信息
   - [ ] `get_member_info()` - 获取成员信息
   - [ ] `get_image_base64()` - 下载图片并转Base64
   - [ ] `get_message_detail()` - 获取消息详情
   - [ ] `get_record_detail()` - 获取语音详情

3. **实现权限检查**
   - [ ] `check_allow_to_chat()` - 检查是否允许聊天
   - [ ] 群组白名单/黑名单逻辑
   - [ ] 私聊白名单/黑名单逻辑

### 优先级 2：完善发送处理（参考旧版 send_handler.py）

4. **完整实现 SendHandler._convert_seg_to_onebot()**
   - [ ] emoji（表情回应）命令
   - [ ] voice（语音）消息段
   - [ ] voiceurl（语音URL）消息段
   - [ ] music（音乐卡片）消息段
   - [ ] videourl（视频URL）消息段
   - [ ] file（文件）消息段
   - [ ] command（命令）消息段

5. **实现命令处理**
   - [ ] GROUP_BAN（禁言）
   - [ ] GROUP_KICK（踢人）
   - [ ] SEND_POKE（戳一戳）
   - [ ] DELETE_MSG（撤回消息）
   - [ ] GROUP_WHOLE_BAN（全员禁言）
   - [ ] SET_GROUP_CARD（设置群名片）
   - [ ] SET_GROUP_ADMIN（设置管理员）

### 优先级 3：补全其他组件（参考旧版对应文件）

6. **NoticeHandler 实现**
   - [ ] 戳一戳通知（notify.poke）
   - [ ] 表情回应通知（notice.group_emoji_like）
   - [ ] 消息撤回通知（notice.group_recall）
   - [ ] 禁言通知（notice.group_ban）

7. **辅助组件**
   - [ ] `qq_emoji_list.py` - QQ表情ID映射表
   - [ ] `video_handler.py` - 视频处理（ffmpeg封面提取）
   - [ ] `message_chunker.py` - 消息分块与重组
   - [ ] `database.py` - 数据库模型（如有需要）

### 优先级 4：测试与优化

8. **功能测试**
   - [ ] 文本消息收发
   - [ ] 图片消息收发
   - [ ] @消息处理
   - [ ] 表情/语音/视频消息
   - [ ] 转发消息解析
   - [ ] 所有命令功能
   - [ ] 通知事件处理

9. **性能优化**
   - [ ] 消息处理并发性能
   - [ ] API响应池性能
   - [ ] 内存占用优化

## 🔍 关键实现细节

### 1. MessageEnvelope vs 旧版 MessageBase

**不再使用 Seg dataclass**，全部使用 TypedDict：

```python
# ❌ 旧版（maim_message）
from mofox_wire import Seg, MessageBase

seg = Seg(type="text", data="hello")
message = MessageBase(message_info=info, message_segment=seg)

# ✅ 新版（mofox-wire v2.x）
from mofox_wire import SegPayload, MessageEnvelope

seg_payload: SegPayload = {"type": "text", "data": "hello"}
envelope: MessageEnvelope = {
    "direction": "input",
    "message_info": {...},
    "message_segment": seg_payload,
    ...
}
```

### 2. Handler 架构模式

**接收方向** (to_core):
```python
class MessageHandler:
    def __init__(self, adapter: "NapcatAdapter"):
        self.adapter = adapter
    
    async def handle_raw_message(self, data: dict[str, Any]) -> MessageEnvelope:
        # 1. 解析 OneBot 11 数据
        # 2. 构建 message_info（MessageInfoPayload）
        # 3. 转换消息段为 SegPayload
        # 4. 返回完整的 MessageEnvelope
```

**发送方向** (to_napcat):
```python
class SendHandler:
    def __init__(self, adapter: "NapcatAdapter"):
        self.adapter = adapter
    
    async def handle_message(self, envelope: MessageEnvelope) -> dict[str, Any]:
        # 1. 从 envelope 提取 message_segment
        # 2. 递归转换 SegPayload → OneBot 格式
        # 3. 调用 adapter.send_napcat_api() 发送
```

### 3. API 调用模式（响应池）

```python
# 在 NapcatAdapter 中
async def send_napcat_api(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
    # 1. 生成唯一 echo
    echo = f"{action}_{uuid.uuid4()}"
    
    # 2. 创建 Future 等待响应
    future = asyncio.Future()
    self._response_pool[echo] = future
    
    # 3. 发送请求（通过 WebSocket）
    await self._send_request({"action": action, "params": params, "echo": echo})
    
    # 4. 等待响应（带超时）
    try:
        result = await asyncio.wait_for(future, timeout=10.0)
        return result
    finally:
        self._response_pool.pop(echo, None)

# 响应回来时（在 incoming_parser 中）
def _handle_api_response(data: dict[str, Any]):
    echo = data.get("echo")
    if echo in adapter._response_pool:
        adapter._response_pool[echo].set_result(data)
```

### 4. 类型提示技巧

处理 TypedDict 的严格类型检查：

```python
# 使用 type: ignore 标注（编译时是 TypedDict，运行时是 dict）
envelope: MessageEnvelope = {
    "direction": "input",
    ...
}  # type: ignore[typeddict-item]

# 或在函数签名中使用 dict[str, Any]
async def from_platform_message(self, message: dict[str, Any]) -> MessageEnvelope | None:
    ...
    return envelope  # type: ignore[return-value]
```

## 🔍 测试检查清单

- [ ] 文本消息接收/发送
- [ ] 图片消息接收/发送
- [ ] 语音消息接收/发送
- [ ] 视频消息接收/发送
- [ ] @消息接收/发送
- [ ] 回复消息接收/发送
- [ ] 转发消息接收
- [ ] JSON消息接收
- [ ] 文件消息接收/发送
- [ ] 禁言命令
- [ ] 踢人命令
- [ ] 戳一戳命令
- [ ] 表情回应命令
- [ ] 通知事件处理
- [ ] 元事件处理

## 📚 参考资料

- **mofox-wire 文档**: 查看 `mofox_wire/types.py` 了解 TypedDict 定义
- **BaseAdapter 示例**: 参考 `docs/mofox_wire_demo_adapter.py`
- **旧版实现**: `src/plugins/built_in/napcat_adapter_plugin/` (仅参考逻辑)
- **OneBot 11 协议**: [OneBot 11 标准](https://github.com/botuniverse/onebot-11)

## ⚠️ 重要注意事项

1. **完全抛弃旧版数据结构**
   - ❌ 不再使用 `Seg` dataclass
   - ❌ 不再使用 `MessageBase` 类
   - ✅ 全部使用 `SegPayload`（TypedDict）
   - ✅ 全部使用 `MessageEnvelope`（TypedDict）

2. **BaseAdapter 生命周期**
   - `__init__()` 中初始化同步资源
   - `start()` 中执行异步初始化（WebSocket连接自动建立）
   - `stop()` 中清理资源（WebSocket自动断开）

3. **WebSocketAdapterOptions 自动管理**
   - 无需手动管理 WebSocket 连接
   - incoming_parser 自动解析接收数据
   - outgoing_encoder 自动编码发送数据
   - 重连机制由基类处理

4. **CoreSink 依赖注入**
   - 必须在插件加载后调用 `set_core_sink(sink)`
   - 通过 `get_core_sink()` 全局访问
   - 用于将消息递送到核心系统

5. **类型安全与灵活性平衡**
   - TypedDict 在编译时提供类型检查
   - 运行时仍是普通 dict，可灵活操作
   - 必要时使用 `type: ignore` 抑制误报

6. **参考旧版但不照搬**
   - 旧版逻辑流程可参考
   - 数据结构需完全重写
   - API调用模式已改变（响应池）

## 📊 预估工作量

- ✅ 核心架构: **已完成** (BaseAdapter + Handlers 骨架)
- ⏳ 消息处理完善: **4-6 小时** (所有消息类型 + 工具函数)
- ⏳ 发送处理完善: **3-4 小时** (所有 Seg 类型 + 命令)
- ⏳ 通知事件处理: **2-3 小时** (poke/emoji_like/recall/ban)
- ⏳ 测试调试: **2-4 小时** (全流程测试)
- **总剩余时间: 11-17 小时**

## ✅ 完成标准

当以下条件全部满足时，重写完成：

1. ✅ BaseAdapter 架构实现完成
2. ⏳ 所有 OneBot 11 消息类型支持
3. ⏳ 所有发送消息段类型支持
4. ⏳ 所有通知事件正确处理
5. ⏳ 权限系统集成完成
6. ⏳ 与旧版功能完全对等
7. ⏳ 所有测试用例通过

---

**最后更新**: 2025-11-23
**架构状态**: ✅ 核心架构完成
**实现状态**: ⏳ 消息处理部分完成，需完善细节
**预计完成**: 根据优先级，核心功能预计 1-2 个工作日
