# MoFox Core 重构架构文档

```toml
version = 3
```

MoFox src目录将被严格分为三个层级：

kernel - 内核/基础能力 层 - 提供“与具体业务无关的技术能力”
core - 核心层/领域/心智 层 - 用 kernel 的能力实现记忆、对话、行为等核心功能，不关心插件或具体平台
app - 应用/装配/插件 层 - 把 kernel 和 core 组装成可运行的 Bot 系统，对外提供高级 API 和插件扩展点

## 基本文件目录结构

... 表示具体结构未定

### kernel层：

提供与业务逻辑无关的通用技术能力。

```text
src/kernel/
├── db/                     # 数据库抽象层 (遵循最简原则)
│   ├── core/               # 引擎、会话与异常
│   └── api/                # CRUD 与高级查询 API
├── vector_db/              # 向量数据库封装
├── scheduler/              # 统一调度器 (时间/任务调度)
├── event/                  # 通用事件总线 (Pub/Sub)
├── llm/                    # 语言模型请求框架
│   ├── model_client/       # 各供应商 Client 实现
│   └── payload/            # 标准输入输出协议
├── config/                 # 类型安全的配置文件系统
├── logger/                 # 统一日志系统
├── concurrency/            # 异步任务与并发管理
└── storage/                # 本地持久化 (JSON 等)
```

#### 模块详述：

- **db (数据库)**: 包含SQLAlchemy 引擎管理,python内的缓存系统、会话生命周期控制、以及基础的 `CRUDBase` 和 `QueryBuilder`。
- **scheduler (调度器)**: 提供基于时间（延迟、周期）或自定义条件的异步任务触发机制。
- **event (事件)**: 最小化的观察者模式实现，支持全局或局部总线的事件发布与订阅，不涉及具体业务逻辑。
- **llm (大模型)**: 统一的多供应商适配方案。支持标准 Payload 构建（Message, Tool, Response Format），提供同步/流式响应的标准化处理。
- **config (配置)**: 基于 Pydantic 的配置文件映射，支持自动化类型校验与 `toml` 存储。
- **logger (日志)**: 封装日志记录、着色渲染、自动清理及元数据跟踪功能。
- **concurrency (并发)**: 统一的 TaskManager 负责后台任务的追踪、生命周期管理及看门狗监控。
- **storage (存储)**: 简单的 KV 或 JSON 文件持久化存储，用于非结构化数据的快速读写。

### core层：

包含以下模块：
components：基本插件组件管理
    **init**.py：导出
    base：组件基类
        **init**.py：导出
        action.py
        adapter.py
        chatter.py
        command.py
        collection.py
        config.py
        event_handler.py
        router.py
        service.py
        plugin.py
        tool.py
    managers：组件应用管理，实际能力调用
        **init**.py：导出
        action_manager.py：动作管理器
        adapter_manager.py：适配器管理器
        chatter_manager.py：聊天器管理器
        command_manager.py：命令管理器
        collection_manager.py：集合管理器
        config_manager.py：配置管理器
        event_manager.py：事件管理器
        service_manager.py：服务管理器
        permission_manager.py：权限管理器
        plugin_manager.py：插件管理器
        prompt_manager.py：Prompt组件管理器
        tool_manager：工具相关管理
            **init**.py：导出
            mcp_adapter.py：mcp转义适配器
            tool_history.py：工具调用历史记录
            tool_use.py：实际工具调用器
    types.py：组件类型
    registry.py：组件注册管理
    state_manager.py：组件状态管理
    loader.py：插件加载器
prompt：提示词管理系统
    **init**.py：导出
    ...
transport：通讯传输系统
    **init**.py：导出
    message_receive：消息接收
    ...
    message_send：消息发送
    ...
    router：api路由
    ...
    sink：针对适配器的core sink和ws接收器
    ...
models：基本模型
    **init**.py：导出
    sql_alchemy.py：数据库使用的SQLAlchemy模型类
    protocols.py：service协议模型
    message.py：消息相关数据模型
    stream.py：聊天流相关数据模型
config：核心配置
    **init**.py：导出
    core_config.py：核心配置定义
    model_config.py：模型配置定义
    ...
utils：杂项工具
    **init**.py：导出
    ...

### app层:

包含以下模块：
plugin_system：插件系统封装
    base：基类集合
        **init**.py：从core层集中导出
    api：api接口
        ...
built_in：内置插件
    ...
scripts：脚本目录
    ...
runtime：bot运行时
    **init**.py：导出
    bot.py
    ...
main.py：主入口

## 模块详情

### kernel\db

对外接口：`crud`, `query`

- **crud**: 提供对数据库最基本的增删改查操作封装。
用法：

```python
from src.kernel.db import CRUDBase
from src.core.models.sql_alchemy import ChatStreams

# 创建模型的crud实例
crud = CRUDBase(ChatStreams)
# 根据stream_id查询
existing_stream = await crud.get_by(stream_id=stream_id)
if existing_stream:
    # 更新
    await crud.update(existing_stream.id, {"group_name": "墨狐狐起源之地"})
```

- **query**: 链式查询构建器，支持更复杂的过滤和排序。
  用法：

```python
from src.kernel.db import QueryBuilder
from src.core.models.sql_alchemy import Emoji

# 链式查询
emoji_record = await QueryBuilder(Emoji).filter(emoji_hash=hash).first()
```

实现原理：
与现在的实现基本保持一致。

### kernel\vector_db

对外接口：`vector_db_service`
提供标准化的向量存储与检索接口，支持多集合隔离与元数据过滤。目前底层基于 ChromaDB 实现。

用法：

```python
from src.kernel.vector_db import vector_db_service

# 1. 获取或创建集合 (Collection)
# 不同的业务模块（如 RAG、记忆）应使用不同的 collection 名称以实现物理隔离
vector_db_service.get_or_create_collection(name="semantic_cache")

# 2. 添加向量数据
vector_db_service.add(
    collection_name="memory",
    embeddings=[[0.1, 0.2, 0.3]],
    documents=["这是一个人类发送的消息"],
    metadatas=[{"chat_id": "12345", "timestamp": 123456789.0}],
    ids=["msg_001"]
)

# 3. 相似度查询
# 使用 where 子句可以实现更细粒度的数据隔离（如按用户或群组查询）
results = vector_db_service.query(
    collection_name="memory",
    query_embeddings=[[0.11, 0.21, 0.31]],
    n_results=5,
    where={"chat_id": "12345"}
)
# results 字典包含: 'ids', 'distances', 'metadatas', 'documents'
```

实现原理：
与现在的实现基本保持一致。

### kernel\config

对外接口：ConfigBase, SectionBase, config_section
通过ConfigBase定义配置文件，SectionBase定义配置节，config_section用于快捷注册节名称
使用时将读取的原始文件地址传入ConfigBase的load方法中，返回一个解析后的配置实例

用法:

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

class MyConfig(ConfigBase):
    @config_section("inner")
    class InnerSection(SectionBase):
        version: str = Field(...)
        enabled: bool = Field(...)

    @config_section("part1")
    class Part1Section(SectionBase):
        option1: str = Field(...)
        option2: int = Field(...)

my_config = MyConfig.load("config/my_config.toml")

print(my_config.part1.option1)
print(my_config.part1.option2)
```

实现原理：
核心：@config_section + ConfigBase.**init_subclass**

1. Section 基类

```python
from pydantic import BaseModel, ConfigDict

class SectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

2. section 装饰器：只挂元信息

```python
def config_section(name: str):
    def deco(cls):
        setattr(cls, "__config_section_name__", name)
        return cls
    return deco
```

3. 外层 ConfigBase：自动收集所有 section

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import ClassVar

class ConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 收集到的 section 映射：section_name -> SectionModel
    __sections__: ClassVar[dict[str, type[SectionBase]]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        sections: dict[str, type[SectionBase]] = {}
        for attr_name, obj in cls.__dict__.items():
            if isinstance(obj, type) and issubclass(obj, SectionBase):
                sec_name = getattr(obj, "__config_section_name__", None)
                if sec_name:
                    sections[sec_name] = obj

        cls.__sections__ = sections
```

这样，MyConfig.**sections** 就会自动变成：
{"inner": InnerSection, "part1": Part1Section}

最终实现demo：

```python
from pathlib import Path
from typing import ClassVar
from pydantic import BaseModel, Field, create_model, ConfigDict
import tomllib


class SectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


def config_section(name: str):
    def deco(cls):
        cls.__config_section_name__ = name
        return cls
    return deco


class ConfigBase:
    """
    抽象配置基类，不直接继承 BaseModel，
    因为最终 model 是动态 build 出来的
    """

    __sections__: ClassVar[dict[str, type[SectionBase]]] = {}
    __built_model__: ClassVar[type[BaseModel] | None] = None

    # ---------- 子类阶段：收集 section ----------
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        sections: dict[str, type[SectionBase]] = {}
        for obj in cls.__dict__.values():
            if isinstance(obj, type) and issubclass(obj, SectionBase):
                name = getattr(obj, "__config_section_name__", None)
                if name:
                    sections[name] = obj

        cls.__sections__ = sections
        cls.__built_model__ = None

    # ---------- build 真正的 Pydantic model ----------
    @classmethod
    def build_model(cls) -> type[BaseModel]:
        if cls.__built_model__ is not None:
            return cls.__built_model__

        fields = {
            name: (section_cls, Field(default_factory=section_cls))
            for name, section_cls in cls.__sections__.items()
        }

        model = create_model(
            f"{cls.__name__}Model",
            __base__=BaseModel,
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

        cls.__built_model__ = model
        return model

    # ---------- 从 dict 加载 ----------
    @classmethod
    def from_dict(cls, data: dict) -> BaseModel:
        model_cls = cls.build_model()
        return model_cls.model_validate(data)

    # ---------- 从 TOML 文件加载 ----------
    @classmethod
    def load(cls, path: str | Path) -> BaseModel:
        path = Path(path)
        with path.open("rb") as f:
            raw = tomllib.load(f)

        return cls.from_dict(raw)

    # ---------- 默认配置导出 ----------
    @classmethod
    def default(cls) -> dict:
        return cls.build_model()().model_dump()
```

注意：未实现配置迁移系统。实际上需要进一步实现配置验证、更新迁移等逻辑。此处不过多赘述。

关于section注释：section 类上可以放 docstring，自动变成 section 顶部注释。

```python
@config_section("inner")
class Inner(SectionBase):
    """Inner 是内部开关与版本信息。一般不需要用户修改。"""
    ...
```

输出：

```toml
[inner]
# Inner 是内部开关与版本信息。一般不需要用户修改。
...
```

### kernel\scheduler

对外接口：`unified_scheduler`, `TriggerType`
提供统一的任务调度能力，支持时间触发、事件触发和自定义条件触发。

用法：

```python
from src.kernel.scheduler import unified_scheduler, TriggerType

# 30秒后执行一次任务
await unified_scheduler.create_schedule(
    callback=my_async_func,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 30},
    task_name="delayed_job"
)

# 每隔1小时执行一次
await unified_scheduler.create_schedule(
    callback=my_async_func,
    trigger_type=TriggerType.TIME,
    trigger_config={"interval_seconds": 3600},
    is_recurring=True,
    task_name="hourly_job"
)
```

实现原理：
与现在的实现基本保持一致。

### kernel\event

对外接口：`event_bus`, `Event`
底层的 Pub/Sub 实现。`core` 层的 `event_manager` 是其之上的业务封装。

用法：

```python
from src.kernel.event import event_bus, Event

# 订阅事件
async def on_user_login(event: Event):
    print(f"User {event.data['user_id']} logged in")

event_bus.subscribe("user_login", on_user_login)

# 发布事件
await event_bus.publish(Event(
    name="user_login",
    data={"user_id": "12345"}
))
```

实现原理：
略。

### kernel\llm

对外接口：`LLMRequest`, `LLMPayload`, `ROLE`
所有 LLM 请求的流程：创建 `LLMRequest` 实例 -> 构建并填充 `payloads`（对话上下文）-> 执行 `LLMRequest` -> 拿到 `LLMResponse` 实例。

- **LLMRequest**:
  - `payloads`: `LLMPayload` 实例列表。
  - `send()`: 执行请求，返回 `LLMResponse`（支持 `await` 与 `async for` 两种消费方式）。
  - `add_payload(payload, position=None)`: 添加消息。`position` 可指定插入位置。
- **LLMPayload**: 包含 `role` 和 `content` 属性。
- **LLMResponse**:
  - `message`: 响应文本。
  - `call_list`: 模型返回的工具调用列表。
  - `add_call_reflex(results)`: 传入工具执行结果（`TOOL_RESULT` 列表）。
  - 拥有与 `LLMRequest` 类似的 `send()` 和 `add_payload()`，用于快捷发起多轮对话。

用法：

```python
# 带工具调用的调用
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text, Tool, ToolResult
model_set = ...
my_tool = ...

async def execute_call(name, args):
    ... # 执行具体工具逻辑

llm_request = LLMRequest(model_set, "usage_demo")

llm_request.add_payload(LLMPayload(ROLE.USER, Text("今天天气怎么样")))
llm_request.add_payload(LLMPayload(ROLE.TOOL, Tool(my_tool)))

llm_response = await llm_request.send()
while llm_response.call_list:
    call_results = []
    for call in llm_response.call_list:
        result = await execute_call(call.name, call.args)
        call_results.append(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result)))

    llm_response.add_call_reflex(call_results)
    llm_response = await llm_response.send()

print(llm_response.message)
```

```python
# 多轮对话的调用
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text, Image
model_set = ...

llm_request = LLMRequest(model_set, "my_request")

llm_request.add_payload(
    LLMPayload(ROLE.USER, Text("你叫什么")),
)

# 假设在async function中
llm_response = await llm_request.send(auto_append_response=False) # 演示auto_append_response参数
print(llm_response.message)

llm_response.add_payload(llm_response) # 将响应写回context
llm_response.add_payload(LLMPayload(ROLE.USER, [Text("这张图片里有什么？"),Image("base64|path")]))

llm_response = await llm_response.send()
print(llm_response.message)
```

实现原理：
此处不讨论负载均衡、重试等逻辑，只讨论最基本的请求与响应调用。

首先，任何请求最初都是构建一个LLMRequest实例。你需要传入model_set指定使用的模型，也可以指定你的llm request名。
然后，你可以通过调用llm_request的add_payload方法为其添加你要请求的prompt payload。
每一个payload都是一个单独的Content类。Content类中包含多个子类，包括Text、Image、Audio、Tool、ToolResult、Action等。他们与Content类为继承关系。
通过LLMPayload类来创建一个完整的Payload。
LLMPayload类拥有两个属性：role和content。

```python
from src.kernel.llm import Content, ROLE
from dataclasses import dataclass

@dataclass
class LLMPayload:
    role: ROLE
    content: list[Content]
```

content属性是有序的。你可以实现图文混排，构建时会保留其顺序，例如：

```python
from src.kernel.llm import LLMPayload, ROLE, Text, Image

LLMPayload(
    ROLE.USER,
    [
        Text("以下是两张图片："),
        Text("这是图一"),
        Image("pic1.jpg"),
        Text("这是图二"),
        Image("pic2.jpg"),
        Text("请你分别描述以上两张图片。"),
        ]
        )
```

Image和Audio接受文件路径和base64编码的文件。Tool和Action接受其component类。

```python
from src.kernel.llm import LLMPayload, ROLE, Text, Image
from src.app.plugin_system import BaseTool

class MyTool(BaseTool):
    ...

LLMPayload(
    ROLE.TOOL,
    MyTool # 单一content可以省略list
)
```

注意，llm模块本身不关心component类的实现。本质上是BaseTool和BaseAction组件类遵循了llm模块内定义的LLMUsable的Protocol。

```python
from typing import Protocol, Dict

class LLMUsable(Protocol):
    @classmethod
    def to_schema(cls) -> Dict:
        return {...}

# plugin_system
class BaseAction():
    ...
    @classmethod
    def to_schema(cls) -> Dict:
        ...

class BaseTool():
    ...
    @classmethod
    def to_schema(cls) -> Dict:
        ...
```

调用LLMRequest的send方法将会构建完整的请求并发送，然后返回一个LLMResponse对象。

```python
from typing import Self

class LLMRequest:
    def __init__(self, model_set:dict, request_name:str) -> None:
        self.model_set = model_set
        self.payloads = []
        self.request_name = request_name
        ...

    def add_payload(self, payload:LLMPayload, position=None) -> Self:
        if position:
            ...
        else:
            self.payloads.append(payload)
        return self

    async def send(self, auto_append_response=True) -> LLMResponse:
        ...
        return LLMResponse(..., self)
```

关键实现：LLMResponse
LLMResponse是重构后的llm模块的关键设计。他有以下关键点：

1. LLMResponse拥有与LLMRequest相同的add_payload和send方法。
2. LLMResponse实现了**await**和**aiter**方法，使得其同时支持流式与非流式响应。

这样可以达成以下效果：

对于流式响应与非流式响应，我们不是强制将其转换为非流式结果后统一输出，而是同时保留直接await和async for两种方法。

1. 如果插件作者 await response：Wrapper 会自动在内部把流跑完，拼接好字符串，一次性返回。
2. 如果插件作者 async for chunk in response：Wrapper 会表现得像个生成器，把数据一点点吐出来。
3. 如果底层本来就不是流：Wrapper 依然允许 async for，只是它只循环一次就结束了。

代码实现示例：

```python
import asyncio
from openai import AsyncStream

class LLMResponse:
    def __init__(self, raw_response, upper:LLMResponse|LLMRequest):
        """
        初始化时传入 OpenAI 的原始 response（可能是 Stream，也可能是 ChatCompletion）
        """
        self._raw_response = raw_response
        self._is_stream = isinstance(raw_response, AsyncStream)
        self._consumed = False # 防止被重复消费

        self.payloads = upper.payloads
        self.model_set = upper.model_set

        self.message = None
        self.call_list = []

    def __await__(self):
        """
        魔法方法 1：允许直接 await 这个对象
        用法：result = await send()
        """
        return self._collect_full_response().__await__()

    async def __aiter__(self):
        """
        魔法方法 2：允许直接 async for 这个对象
        用法：async for chunk in send()
        """
        if self._consumed:
            raise RuntimeError("Response has already been consumed.")
        self._consumed = True

        if self._is_stream:
            # 场景 A: 底层是流，逐个 yield 内容
            async for chunk in self._raw_response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        else:
            # 场景 B: 底层是非流，伪装成流，yield 一次完整内容
            content = self._raw_response.choices[0].message.content
            if content:
                yield content

    async def _collect_full_response(self):
        """
        内部辅助方法：如果用户选择 await，我们帮他把流跑完并拼接
        """
        if self._consumed:
            raise RuntimeError("Response has already been consumed.")
        self._consumed = True

        if not self._is_stream:
            # 如果本来就是非流，直接返回 content
            return self._raw_response.choices[0].message.content

        # 如果是流，自动拼接
        full_content = []
        async for chunk in self._raw_response:
            content = chunk.choices[0].delta.content
            if content:
                full_content.append(content)
        return "".join(full_content)

    def add_payload(self, payload:LLMPayload|LLMResponse, position=None) -> Self:
        if isinstance(payload, LLMResponse):
            # 转换为 LLMPayload
            ...
        if position:
            ...
        else:
            self.payloads.append(payload)
        return self

    def add_call_reflex(self, tool_results) -> Self:
        ...
        return self

    async def send(self, auto_append_response=True) -> LLMResponse:
        ...
        return LLMResponse(..., self)
```

### kernel\logger

对外接口：core
基本与现在保持一致，通过get_logger获取logger。
移除统一的颜色映射表，转而在get_logger时及时指定颜色。
使用display来指定实际显示的前缀
用法：

```python
from src.kernel.logger import get_logger, COLOR

logger = get_logger("my_logger",display="我的日志",color=COLOR.BLUE)
logger.info("Hello World!")
```

实现原理：
与现在的实现基本保持一致。

### kernel\concurrency

concurrency是整个项目最核心、底层的异步任务管理系统，它用于取代不规范的asyncio的create_task，杜绝随意创建异步任务，优化异步系统管理。他提供一个完整的使用接口，并提供TaskGroup、WatchDog等工具，从而最大程度上保证异步系统的可靠性与性能问题。
对外接口：task_manager

用法：

```python
# 最基本的创建异步任务并等待完成
from src.kernel.concurrency import get_task_manager

async def func():
    ...

tm = get_task_manager()
tm.create_task(func(), name="my_task")
tm.create_task(func(), name="my_task", daemon=True) # daemon属性为True表示这是一个守护任务，会持续运行，此时WatchDog不会因为该任务长期存在而发出警告

# 等待所有任务（如一次消息处理）
await tm.wait_all_tasks()
```

实际上，我们并不推荐所有异步任务都直接通过task_manager直接创建。我们更推荐每一个模块创建一个一个自己的TaskGroup。
TaskGroup就像一个桶，他将不同的模块的异步任务分开来管理，提供一个干净的作用域，从而避免异步阻塞问题。
同时，TaskGroup支持共享以及async with上下文管理器，极大的简化了异步任务管理复杂度。

```python
# 创建TaskGroup
from src.kernel.concurrency import get_task_manager

async def func1():
    ...
async def func2():
    ...

tm = get_task_manager()
async with tm.group(
    name="my_task_group",
    timeout=30, # 整组超时（可选）
    cancel_on_error=True,   # 任一任务异常 -> 取消其余任务
    ) as tg:

    tg.create_task(func1())
    tg.create_task(func2())
print("所有任务已完成！")
```

```python
# TaskGroup的共享
# 主文件
import asyncio
from src.kernel.concurrency import get_task_manager
from .module import my_f

async def sleep1():
    await asyncio.sleep(1)
    print("sleep 1s")

async def sleep5():
    await asyncio.sleep(5)
    print("sleep 5s")

tm = get_task_manager()
async with tm.group(
    name="my_task_group",
    timeout=30, # 整组超时（可选）
    cancel_on_error=True,   # 任一任务异常 -> 取消其余任务
    ) as tg:

    tg.create_task(sleep1())
    tg.create_task(sleep5())
    tg.create_task(my_f())
print("所有任务已完成！")

# 文件module
import asyncio
from src.kernel.concurrency import get_task_manager

async def sleep3():
    await asyncio.sleep(3)
    print("sleep 3s")

tm = get_task_manager()

async def my_f():
    async with tm.group(name="my_task_group") as tg:
        tg.create_task(sleep3())

# 输出:
# sleep 1s
# sleep 3s
# sleep 5s
# 所有任务已完成！
```

实现原理：
task_manager为全局单例。调用其create_task时本质也是调用asyncio的create_task方法，但是会在内部存储并保存Task对象。每个Task都有一个TaskInfo对象记录其数据，如task_id，创建时间，超时时间等。
该模块初始化后会创建一个独立的WatchDog线程。WatchDog主要负责以下事情：
1、由于每个聊天流驱动器内部都是通过异步生成器不断产出tick运作的，所以每个活跃的聊天流驱动器在每一个tick都要显式的向WatchDog发送一个心跳信号（俗称“喂狗”）。WatchDog会记录每个聊天流的tick间隔，如果发现某个聊天流驱动器的tick间隔高于设定阈值，则会输出警告日志。如果间隔过长，则会视为该驱动器已经完全卡死，则会触发保护机制，强行移除该流驱动器并尝试重启。
2、WatchDog内部本身也有一个tick驱动器，他会遵循固定的间隔（默认1s）产生tick。在每一个tick中，他会记录当前时间，并和上一个tick的记录时间相减得到tick间隔。如果tick间隔高于设定值（如2倍设定间隔），则会输出警告日志。在每一个tick间隔中，会检查所有task_manager内部保存的Task对象状态，对于已经完成的或被取消、出错的Task将清理，只保留仍在进行中的Task。如果某个非daemon Task存活时间超过了其timeout时间，WatchDog将尝试cancel它。

TaskGroup会实现**aexit**，他会await组内所有任务的完成。TaskGroup的create_task没有daemon参数，也不允许在TaskGroup内创建守护任务。

task_manager.group方法会首先检查name，如果其内部已经有对应name的TaskGroup,那么将不会创建新的，而是直接返回对应的TaskGroup对象。

### kernel\storage

对外接口：`json_store`
提供极其简易的本地非结构化数据持久化能力。

用法：

```python
from src.kernel.storage import json_store

# 保存数据
await json_store.save("my_plugin_data", {"key": "value"})

# 读取数据
data = await json_store.load("my_plugin_data")
```

实现原理：
在data文件夹内创建一个json_storage文件夹，调用json_store.save时，会根据传入的名字在文件夹内创建一个对应的.json文件，然后保存数据。
load时会根据传入名字读取对应的文件并返回数据。

### core\components

components（组件）是MoFox插件生态的重要组成部分，任何插件都是不同组件的集合体，甚至plugin本身也是一个独立的组件。在以组件为基本单位的情况下，组件的设计与管理、交互就显得尤为重要。

`base`：
base里面提供所有组件的基类。每个组件都有其对应的基类，创建对应的组件就要继承其基类。
基类中提供了对于该组件的必要封装和接口设计。

目前包含的所有组件类型包括：

#### Action

action即“动作”，它定义了一个“动作”的行为，例如“发送消息”，“发送表情包”等。它是决策后的“结果”，llm并不会从中获得什么信息。
action是主动的“响应”。

action的调用通常发生在“Actor（动作器）”的Tool Calling。

经典流程：
插件定义action -> 注册到核心的组件管理器 -> 注册到action manager -> Bot接受到聊天消息 -> Chatter工作 -> 获取可用action -> LLM获取到上下文以及可用action -> LLM通过Tool Calling调用action -> 调用被action manager截获，构造action实例 -> 调用指定action实例的execute方法 -> 对话结束

基类：
```python
from abc import ABC, abstractmethod
from typing import Annotated


class BaseAction(ABC):
    action_name = ""    # action名
    action_description = "" # action的功能描述
    
    primary_action = False  # 是否为主action

    chatter_allow = []  # 支持的chatter列表
    chat_type = ChatType.ALL  # action支持的ChatType

    associated_platforms = []   # action支持的平台名
    associated_types = []   # action需要的内容类型，适配器需要支持这些类型action才能正常执行

    def __init__(self, chat_stream: ChatStream, plugin: BasePlugin) -> None:
        self.chat_stream = chat_stream
        self.plugin = plugin

    @abstractmethod
    async def execute(
        self, 
        arg1: int, 
        arg2, 
        kwarg1: str | None = None, 
        ) -> tuple[Annotated[bool, "是否成功"], Annotated[str, "结果详情"]]:
        """
        所有Action组件都必须重写此方法。并且必须写args文档来告诉llm每个参数的作用。

        Args:
            arg1: 一个整数类型的参数
            arg2: 未注明类型的参数将被默认为字符串类型
            kwarg1: kwarg将被认为“非必须的”
        
        action管理器会自动识别以上Type Hints 和 Docstring并生成对应的Tool Schema。
        """
        ...
    
    async def go_activate(self) -> bool:
        """action激活判定函数"""
        return True
    
    async def _random_activation(self, probability: float) -> bool:
        """随机激活工具函数

        Args:
            probability: 激活概率，范围 0.0 到 1.0

        Returns:
            bool: 是否激活
        """
        ...

    async def _keyword_match(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
    ) -> bool:
        """关键词匹配工具函数

        聊天内容会自动从实例属性中获取。

        Args:
            keywords: 关键词列表
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否匹配到关键词
        """
        ...

    async def _llm_judge_activation(
        self,
        judge_prompt: str = "",
        action_require: list[str] | None = None,
    ) -> bool:
        """LLM 判断激活工具函数

        使用action manager中的action modifier来统一判断是否应该激活此 Action。

        Args:
            judge_prompt: 判断用prompt
            action_require: 强调的激活需求列表

        Returns:
            bool: llm判定是否激活
        """
        ...
    
    async def _send_to_stream(self, content: Content, stream_id: str = None) -> bool:
        """发送任意内容到指定聊天流

        Args:
            content: 要发送的内容
            stream_id: 要发送的聊天流id，留空默认使用当前的聊天流

        Returns:
            bool: 发送是否成功
        """
        ...
```

使用示例：
```python
from src.app.plugin_system import BaseAction, Emoji

class SendEmoji(BaseAction):
    action_name = "send_emoji"    # action名
    action_description = "发送一个emoji" # action的功能描述
  
    primary_action = False  # 是否为主action

    chatter_allow = []  # 支持的chatter列表
    chat_type = ChatType.ALL  # action支持的ChatType
    
    associated_platforms = ["qq"]   # action支持的平台名
    associated_types = ["emoji"]   # action需要的内容类型，适配器需要支持这些类型action才能正常执行

    async def execute(self, emoji_tag: str):
        """
        Args:
            emoji_tag: 要发送的emoji的情感标签
        """

        # 这里假设emoji manager能直接通过emoji_tag查找emoji
        emoji_path = emoji_manager.find_emoji_by_tag(emoji_tag)
        res = await self._send_to_stream(Emoji(emoji_path))
        if res:
            return True, "发送成功"  
        else:
            return False, "发送失败"
    
    async def go_activate(self) -> bool:
        return await self._random_activation(self.plugin.config.probability)
```

#### Adapter

adapter是适配器，用于在核心和平台之间建立通信。依赖于mofox-wire标准。

经典流程：
插件定义adapter -> 注册到核心的组件管理器 -> 注册到adapter manager -> 平台接收到消息 -> 适配器转义为MessageEnvelope -> 发送到核心 -> 核心处理 -> 核心返回MessageEnvelope -> 适配器转义为平台接受的消息格式 -> 发送到平台

基类：
```python
import asyncio

from abc import ABC, abstractmethod
from mofox_wire import AdapterBase

from src.kernel.concurrency import get_task_manager

class BaseAdapter(AdapterBase, ABC):
    """
    插件系统的 Adapter 基类

    相比 mofox_wire.AdapterBase，增加了以下特性：
    1. 插件生命周期管理 (on_adapter_loaded, on_adapter_unloaded)
    2. 配置管理集成
    3. 自动重连与健康检查
    4. 子进程启动支持
    """

    # 适配器元数据
    adapter_name: str = "unknown_adapter"
    adapter_description: str = "No description"

    # 是否在子进程中运行
    run_in_subprocess: bool = False

    def __init__(
        self,
        core_sink: CoreSink,
        plugin: BasePlugin,
        **kwargs
    ):
        """
        Args:
            core_sink: 核心消息接收器
            plugin: 所属插件实例
            **kwargs: 传递给 AdapterBase 的其他参数
        """
        super().__init__(core_sink, **kwargs)
        self.plugin = plugin
        self._health_check_task: asyncio.Task | None = None
        self._running = False
        # 标记是否在子进程中运行
        self._is_subprocess = False

    @classmethod
    def from_process_queues(
        cls,
        to_core_queue,
        from_core_queue,
        plugin: "BasePlugin" | None = None,
        **kwargs: Any,
    ) -> "BaseAdapter":
        """
        子进程入口便捷构造：使用 multiprocessing.Queue 与核心建立 ProcessCoreSink 通讯。

        Args:
            to_core_queue: 发往核心的 multiprocessing.Queue
            from_core_queue: 核心回传的 multiprocessing.Queue
            plugin: 可选插件实例
            **kwargs: 透传给适配器构造函数
        """
        sink = ProcessCoreSink(to_core_queue=to_core_queue, from_core_queue=from_core_queue)
        return cls(core_sink=sink, plugin=plugin, **kwargs)

    async def start(self) -> None:
        """启动适配器"""
        logger.info(f"启动适配器: {self.adapter_name}")

        # 调用生命周期钩子
        await self.on_adapter_loaded()

        # 调用父类启动
        await super().start()

        # 启动健康检查
        self._health_check_task = get_task_manager().create_task(self._health_check_loop(),daemon=True)

        self._running = True
        logger.info(f"适配器 {self.adapter_name} 启动成功")

    async def stop(self) -> None:
        """停止适配器"""
        logger.info(f"停止适配器: {self.adapter_name}")

        self._running = False

        # 停止健康检查
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # 调用父类停止
        await super().stop()

        # 调用生命周期钩子
        await self.on_adapter_unloaded()

        logger.info(f"适配器 {self.adapter_name} 已停止")

    async def on_adapter_loaded(self) -> None:
        """
        适配器加载时的钩子
        子类可重写以执行初始化逻辑
        """
        pass

    async def on_adapter_unloaded(self) -> None:
        """
        适配器卸载时的钩子
        子类可重写以执行清理逻辑
        """
        pass

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        interval = self.config.get("health_check_interval", 30)

        while self._running:
            try:
                await asyncio.sleep(interval)

                # 执行健康检查
                is_healthy = await self.health_check()

                if not is_healthy:
                    logger.warning(f"适配器 {self.adapter_name} 健康检查失败，尝试重连...")
                    await self.reconnect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"适配器 {self.adapter_name} 健康检查异常: {e}")

    async def health_check(self) -> bool:
        """
        健康检查
        子类可重写以实现自定义检查逻辑

        Returns:
            bool: 是否健康
        """
        # 默认检查 WebSocket 连接状态
        if self._ws and not self._ws.closed:
            return True
        return False

    async def reconnect(self) -> None:
        """
        重新连接
        子类可重写以实现自定义重连逻辑
        """
        ...

    @abstractmethod
    async def from_platform_message(self, raw: Any) -> MessageEnvelope:
        """
        将平台原始消息转换为 MessageEnvelope

        子类必须实现此方法

        Args:
            raw: 平台原始消息

        Returns:
            MessageEnvelope: 统一的消息信封
        """
        raise NotImplementedError

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """
        发送消息到平台

        如果使用了 WebSocketAdapterOptions 或 HttpAdapterOptions，
        此方法会自动处理。否则子类需要重写此方法。

        Args:
            envelope: 要发送的消息信封
        """
        ...
```

使用示例：
```python
import orjson
from src.app.plugin_system import BaseAdapter
from mofox_wire import MessageBuilder, SegPayload, MessageEnvelope

class MyAdapter(BaseAdapter):
    adapter_name: str = "my_adapter"
    adapter_description: str = "This is a demo"

    run_in_subprocess: bool = False

    async def on_adapter_loaded(self) -> None:
        print("欢迎使用！")

    async def on_adapter_unloaded(self) -> None:
        print("适配器已关闭！")

    async def from_platform_message(self, raw: dict[str, Any]) -> MessageEnvelope | None:
        sender_info = raw.get("user")
        text = raw.get("text")
        message_id = raw.get("id")

        return (
            MessageBuilder()
            .text(text)
            .from_user(
                user_id=str(sender_info.get("user_id", "")),
                platform="demo",
                nickname=sender_info.get("nickname", ""),
                cardname=sender_info.get("card", ""),
                user_avatar=sender_info.get("avatar", ""),
                )
            .message_id(message_id)
            .direction("incoming")
            )

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        if not envelope:
            logger.warning("空的消息，跳过处理")
            return

        if not self._ws:
            raise RuntimeError("WebSocket 连接未建立")

        # 构造请求
        message_segment: SegPayload = envelope.get("message_segment", {})
        request = orjson.dumps(
            {
                "text": message_segment.get("data"),
            }
        ).decode()

        # 发送请求
        await self._ws.send(request)
```

#### Chatter

chatter是Bot的智慧核心，它决定了Bot以什么样的逻辑和方式去和外界交互。

经典流程：
插件定义chatter -> 注册到核心的组件管理器 -> 注册到chatter manager -> 核心接收到消息 -> 查找并激活可用的chatter -> chatter执行 -> Bot做出响应 -> 对话结束

基类：
```python
from abc import ABC, abstractmethod
from typing import Annotated

class ChatterResult:
    pass

class Wait(ChatterResult):
    pass

class Success(ChatterResult):
    pass

class Failure(ChatterResult):
    pass

class BaseChatter(ABC):
    chatter_name: str = ""  # Chatter组件名称
    chatter_description: str = ""   # Chatter组件描述

    chat_types: ChatType = ChatType.ALL   # 允许的chat type
    associated_platforms = []   # 支持的平台名

    def __init__(self, stream_id: str, plugin: BasePlugin):
        """
        初始化聊天处理器

        Args:
            stream_id: 聊天流idd
            plugin: 插件实例
        """
        self.stream_id = stream_id
        self.plugin = plugin

    @abstractmethod
    async def execute(self, unreads: list[Message]) -> ChatterResult:
        """
        执行聊天处理逻辑(生成器函数)

        Args:
            unreads: 未读消息列表

        Returns:
            处理结果
        """
        pass

    async def get_llm_usables(self) -> list[LLMUsable]:
        """
        获取当前所有可用的LLMUsable的集合

        根据聊天流，平台信息，聊天类型初步过滤。

        Returns:
            list[LLMUsable]: 所有可用的LLMUsable的列表
        """
        ...
    
    async def modify_llm_usables(self, llm_usables: list[LLMUsable] | None = None) -> list[LLMUsable]:
        """
        过滤的LLMUsable的集合

        会执行action的go_activate方法，并解包激活的collection
        如果存在嵌套collection，则会持续筛选并解包，直到只存在action和tool。

        Args:
            llm_usables: 初始的未过滤的LLMUsable的列表。留空则会自动执行get_llm_usables
        Returns:
            list[LLMUsable]: 所有过滤后的LLMUsable的列表
        """
        ...
    
    async def pre_exec_llm_usables(self, llm_usables: list[LLMUsable], allow_primary_action: bool = False) -> dict:
        """
        使用sub_actor预执行llm_usables。

        Args:
            llm_usables: 过滤的LLMUsable的列表。
            allow_primary_action: 是否允许预执行主action
            
        Returns:
            dict: 预执行结果字典
            {
                "success": bool,    # 是否全部预执行成功
                "need_primary_action": bool, # 是否需要主action介入
                "results": {        # 预执行结果详情
                    "LLMUsable名": {
                        "success": bool,    # 该组件是否预执行成功
                        "detail": tuple,      # 预执行结果详情
                    },
                    ...
            }
        """
        ...
    
    async def exec_llm_usables(self, llm_usables: list[LLMUsable]) -> dict:
        """
        使用主actor执行llm_usables。

        Args:
            llm_usables: 过滤的LLMUsable的列表。
            
        Returns:
            dict: 执行结果字典
            {
                "success": bool,    # 是否全部执行成功
                "results": {        # 执行结果详情
                    "LLMUsable名": {
                        "success": bool,    # 该组件是否执行成功
                        "detail": tuple,      # 执行结果详情
                    },
                    ...
            }
        """
        ...
    
    def get_this_stream(self) -> ChatStream:
        """
        获取当前Chatter所属的聊天流实例

        Returns:
            ChatStream: 聊天流实例
        """
        ...
```

使用示例：
```python
from src.app.plugin_system import BaseChatter, Success

class MyChatter(BaseChatter):
    chatter_name: str = "my_chatter"
    chatter_description: str = "This is a demo"

    chat_types: ChatType = ChatType.ALL
    associated_platforms = ["qq"]

    async def execute(self, unreads: list[Message]) -> ChatterResult:
        llm_usables = await self.modify_llm_usables()   # 获取过滤后的LLMUsable列表

        exec_results = await self.pre_exec_llm_usables(llm_usables)  # 预执行

        if exec_results["need_primary_action"]: 
            # 存在需要主action介入的情况
            # 先等待最新消息
            new_unreads = yield Wait()
            # 使用主actor执行
            exec_results = await self.exec_llm_usables(llm_usables)

        if exec_results["success"]:
            yield Success()
        else:
            yield Failure()
```

#### Command
command是命令，用于处理特定的指令请求，例如“/help”，“/mute”等。命令通常是用户主动触发的，具有明确的触发条件。

经典流程：
插件定义command -> 注册到核心的组件管理器 -> 注册到command manager -> 核心接受到聊天消息 -> 核心处理 -> 查找并激活可用的command -> command执行 -> 对话结束

核心功能点：
@cmd_route 装饰器：支持像文件路径一样定义多级命令。

智能参数解析：基于 Python Type Hint 自动将字符串转换为 int, bool, float 等。

自动帮助文档：如果命令不完整，自动提示下一级可用的子命令。

正则生成：自动根据 command_name 生成匹配规则。

基类：
(使用Gemini编写的参考实现，非最终版本，仅供参考)
```python
import inspect
import re
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Callable, Optional, List, Tuple, Any, Annotated, Union

# ==========================================
# 路由节点与装饰器
# ==========================================

@dataclass
class CommandNode:
    """命令树节点 (Trie Node)"""
    name: str
    handler: Optional[Callable] = None  # 如果是叶子节点，存储处理函数
    children: Dict[str, 'CommandNode'] = field(default_factory=dict)
    help_text: str = ""

    def get_or_create_child(self, name: str) -> 'CommandNode':
        if name not in self.children:
            self.children[name] = CommandNode(name=name)
        return self.children[name]

def cmd_route(*path_segments: str, help: str = ""):
    """
    路由装饰器：用于将方法注册为子命令
    
    Usage:
        @cmd_route("set", "seconds", help="设置秒数")
        async def set_seconds(self, value: int): ...
    """
    def decorator(func):
        func._is_route = True
        func._route_path = path_segments
        func._route_help = help
        return func
    return decorator


# ==========================================
# BaseCommand 完整实现
# ==========================================

class BaseCommand(ABC):
    """命令基类
    
    提供基于字典树(Trie)的命令分发系统，支持无限层级的子命令和自动参数类型转换。
    """

    # --- 配置元数据 ---
    command_name: str = ""          # 必填：命令主名称，如 'echo', 'time'
    command_description: str = ""   # 必填：命令总体描述

    chat_type = ChatType.ALL        # 支持的会话类型
    associated_platforms = []       # 支持的平台
    associated_types = []           # 需要的内容类型
    intercept_message: bool = True  # 默认为 True 通常更合理，命中命令后不再传递给其他逻辑

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化命令组件"""
        self.plugin = plugin
        # stream_id 会在运行时被command manager设置
        self.stream_id: Optional[ChatStream] = None
        
        # 初始化命令树根节点
        self.root = CommandNode(name="root")
        
        # 扫描并构建路由树
        self._build_tree()

    @classmethod
    def _generate_command_pattern(cls) -> str:
        """生成命令匹配的正则表达式
        
        用于主程序快速判断一条消息是否属于该命令。
        匹配模式: ^(前缀)(命令名)(\s+.*)?$
        例如: ^[/.]time(\s+.*)?$
        """
        if not cls.command_name:
            return r""
        
        # 转义前缀以防止正则特殊字符 (如 .) 导致错误
        escaped_prefixes = [re.escape(p) for p in cls.command_prefixes]
        prefix_group = f"(?:{'|'.join(escaped_prefixes)})"
        
        # 匹配以 前缀+命令名 开头，后面跟着空格或者直接结束的字符串
        return f"^{prefix_group}{re.escape(cls.command_name)}(?:\\s+.*)?$"

    def _build_tree(self):
        """反射扫描：将带有 @cmd_route 的方法注册到 Trie 树中"""
        # 获取当前实例的所有方法
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(method, "_is_route", False):
                path = getattr(method, "_route_path")
                help_text = getattr(method, "_route_help") or method.__doc__ or ""
                
                # 遍历路径，构建/寻找节点
                current_node = self.root
                for segment in path:
                    current_node = current_node.get_or_create_child(segment)
                
                # 在路径终点挂载处理函数
                current_node.handler = method
                current_node.help_text = help_text

    async def _send_to_stream(self, content: Content, stream_id: str = None) -> bool:
        """发送任意内容到指定聊天流

        Args:
            content: 要发送的内容
            stream_id: 要发送的聊天流id，留空默认使用当前的聊天流

        Returns:
            bool: 发送是否成功
        """
        ...

    async def execute(self, message_text: str) -> Tuple[bool, str]:
        """执行命令的入口方法
        
        Args:
            message_text: 完整的消息文本 (例如 "/time set seconds 30")
        
        Returns:
            Tuple[bool, str]: (是否成功, 返回结果/错误信息)
        """
        # 1. 解析命令行参数 (处理引号包裹的参数)
        try:
            # 替换掉前缀和命令名本身，只保留参数部分
            full_args = shlex.split(message_text)
        except ValueError as e:
            return False, f"参数解析错误: {str(e)}"

        if not full_args:
             return False, "命令为空"

        # 移除第一个元素（即命令名本身，如 '/time'）
        # 假设传入的是 "/time set 30"，我们需要处理的是 ["set", "30"]
        args = full_args[1:]

        # 2. 路由分发
        current_node = self.root
        consumed_count = 0

        # 树遍历：匹配子命令路径
        for arg in args:
            if arg in current_node.children:
                current_node = current_node.children[arg]
                consumed_count += 1
            else:
                # 遇到不认识的词，说明路径结束，剩下的都是函数参数
                break
        
        # 3. 执行逻辑
        if current_node.handler:
            # 剩下的未被路由消耗的参数
            func_args_str = args[consumed_count:]
            try:
                # 自动类型转换并调用
                result = await self._call_handler(current_node.handler, func_args_str)
                # 约定：handler 返回 (bool, str) 或者只返回 str (视为成功)
                if isinstance(result, tuple) and len(result) == 2:
                    return result
                elif isinstance(result, str):
                    return True, result
                else:
                    return True, str(result)
            except Exception as e:
                return False, f"执行错误: {str(e)}"
        else:
            # 命中了中间节点 (例如只输入了 /time set，但 set 下面还有 seconds/minutes)
            suggestions = list(current_node.children.keys())
            if not suggestions:
                # 既没有 handler 也没有子节点，说明这是一个空定义的路径
                return False, f"该命令 '{current_node.name}' 未实现具体功能。"
            
            return False, f"命令不完整。可用子命令: {' | '.join(suggestions)}"

    async def _call_handler(self, handler: Callable, args_list: List[str]) -> Any:
        """核心黑魔法：基于 Type Hint 的自动参数注入与类型转换"""
        sig = inspect.signature(handler)
        bound_args = []
        
        params = list(sig.parameters.values())
        
        # 遍历函数签名所需的参数 (跳过 self)
        # 注意：这里假设 args_list 的顺序与函数参数顺序一致
        arg_index = 0
        
        for param in params:
            # 跳过 self (inspect.ismethod 获取的方法通常不包含 self，但在某些情况下要注意)
            if param.name == 'self': 
                continue
            
            # 处理可变参数 (*args) - 将剩余所有字符串传入
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                bound_args.extend(args_list[arg_index:])
                arg_index = len(args_list)
                break

            # 处理常规参数
            if arg_index < len(args_list):
                val_str = args_list[arg_index]
                arg_index += 1
                
                # 类型转换
                if param.annotation is not inspect.Parameter.empty and param.annotation is not Any:
                    try:
                        # 特殊处理 bool ("true"/"false")
                        if param.annotation is bool:
                            val = val_str.lower() in ('true', 'yes', '1', 'on')
                        else:
                            # 尝试直接构造，如 int("10"), float("1.5")
                            val = param.annotation(val_str)
                        bound_args.append(val)
                    except ValueError:
                        raise ValueError(f"参数 '{param.name}' 需要 {param.annotation.__name__} 类型，但收到了 '{val_str}'")
                else:
                    # 没有类型提示，默认传字符串
                    bound_args.append(val_str)
            
            elif param.default is not inspect.Parameter.empty:
                # 用户没传参，但有默认值
                bound_args.append(param.default)
            else:
                raise ValueError(f"缺少必要参数: {param.name}")

        return await handler(*bound_args)
```

使用示例：
```python
from src.app.plugin_system import BaseCommand, cmd_route

class TimeCommand(BaseCommand):
    command_name = "time"
    command_description = "时间管理工具"

    chat_type = ChatType.ALL
    associated_platforms = []
    associated_types = ["text"]
    intercept_message: bool = True 

    # 1. 根命令处理 (直接输入 /time 时触发)
    # 注意：如果不定义这个，输入 /time 会提示子命令
    # 这里的路径为空 tuple
    @cmd_route() 
    async def root_handler(self):
        return True, "欢迎使用时间工具。请使用 /time set 或 /time check"

    # 2. 多级路径: /time set seconds <int>
    @cmd_route("set", "seconds", help="设置秒数")
    async def set_seconds(self, value: int):
        # value 已经被自动转为 int 了
        return True, f"成功设置: {value} 秒"

    # 3. 多级路径: /time set msg <str> <int>
    @cmd_route("set", "msg", help="设置提醒消息")
    async def set_message(self, text: str, delay: int = 60):
        # delay 有默认值，所以用户可以只输 text
        return True, f"将在 {delay} 秒后提醒: {text}"

    # 4. 任意长度路径: /time deep a b c <val>
    @cmd_route("deep", "a", "b", "c")
    async def deep_test(self, val: str):
        return True, f"你进入了很深的层级，值为 {val}"
```

#### Collection
collection是LLMUsable的集合体，他可以包含多个action和tool，甚至嵌套的collection。

经典流程：
插件定义collection -> 注册到核心的组件管理器 -> 注册到collection manager -> Chatter工作 -> 获取可用LLMUsable -> LLM获取到上下文以及可用LLMUsable列表 -> LLM通过Tool Calling调用collection -> 解包collection，内部组件被标记为激活 -> LLM获取到上下文以及新的LLMUsable列表 -> LLM通过Tool Calling调用LLMUsable -> 对话结束

基类：
```python
from abc import ABC, abstractmethod

class BaseCollection(ABC):
    collection_name: str = ""  # Collection组件名称
    collection_description: str = ""   # Collection组件描述

    associated_platforms = []   # 支持的平台名

    chatter_allow = []  # 支持的chatter列表
    chat_type = ChatType.ALL  # collection支持的ChatType

    cover_go_activate = True  # 是否覆盖内部组件的go_activate结果，默认为True

    def __init__(self, plugin: BasePlugin):
        """
        初始化Collection组件

        Args:
            plugin: 插件实例
        """
        self.plugin = plugin

    @abstractmethod
    async def get_contents(self) -> list[str]:
        """
        获取Collection内部包含的所有LLMUsable组件

        Returns:
            list[str]: 包含的所有LLMUsable组件列表签名，格式：插件名:组件类型:组件名
        """
        ...
    
    async def go_activate(self) -> bool:
        """
        Collection激活判定函数

        Returns:
            bool: 是否激活
        """
        return True

    async def _random_activation(self, probability: float) -> bool:
        """随机激活工具函数

        Args:
            probability: 激活概率，范围 0.0 到 1.0

        Returns:
            bool: 是否激活
        """
        ...

    async def _keyword_match(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
    ) -> bool:
        """关键词匹配工具函数

        聊天内容会自动从实例属性中获取。

        Args:
            keywords: 关键词列表
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否匹配到关键词
        """
        ...

    async def _llm_judge_activation(
        self,
        judge_prompt: str = "",
        action_require: list[str] | None = None,
    ) -> bool:
        """LLM 判断激活工具函数

        使用action manager中的action modifier来统一判断是否应该激活此 Action。

        Args:
            judge_prompt: 判断用prompt
            action_require: 强调的激活需求列表

        Returns:
            bool: llm判定是否激活
        """
        ...
```

使用示例：
```python
from src.app.plugin_system import BaseCollection

class MyCollection(BaseCollection):
    collection_name: str = "my_collection"
    collection_description: str = "该Collection包含发送表情和时间命令。当你需要发送表情或管理时间时，可以使用这个Collection。"

    associated_platforms = ["qq"]

    chatter_allow = []
    chat_type = ChatType.ALL

    cover_go_activate = True

    async def get_contents(self) -> list[str]:
        return [
            "my_plugin:action:send_emoji",
            "my_plugin:command:time_command",
        ]
```

#### Config
config是插件配置管理的核心模块，提供统一的配置存取接口和热更新能力。
它是对kernel\config的封装，插件开发者无需直接操作config_manager。

经典流程：
插件定义config组件 -> 注册到核心的组件管理器 -> 配置管理器生成默认配置文件 -> 用户通过UI或手动编辑配置文件进行配置 -> 插件运行时通过plugin.config获取配置

基类：
```python
from abc import ABC, abstractmethod
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

class BaseConfig(ABC, ConfigBase):
    config_name: str = "config"  # 配置文件名称
    config_description: str = ""   # 配置组件描述

    @config_section("inner")
    class InnerSection(ABC, SectionBase):
        """插件内部配置区"""
        version: str = Field("1.0.0", description="配置文件版本号")
        enabled: bool = Field(False, description="是否启用该插件")
```

使用示例：
```python
from src.app.plugin_system import BaseConfig, config_section, Field, SectionBase

class MyPluginConfig(BaseConfig):
    config_name: str = "config"
    config_description: str = "这是我的插件配置文件"

    @config_section("inner")
    class InnerSection(ABC, SectionBase):
        """插件内部配置区"""
        version: str = Field("1.0.0", description="配置文件版本号")
        enabled: bool = Field(False, description="是否启用该插件")

    @config_section("general")
    class GeneralSection(SectionBase):
        """常规设置"""
        probability: float = Field(0.5, description="动作激活概率", ge=0.0, le=1.0)
        welcome_message: str = Field("欢迎使用我的插件！", description="欢迎消息内容")
```

#### Event Handler
event handler是事件处理器，用于响应系统或插件触发的各种事件，例如“插件加载”，“消息接收”等。

经典流程：
插件定义event handler -> 注册到核心的组件管理器 -> 注册到event manager -> 事件触发 -> event manager查找对应的event handler并执行

基类：
```python
from abc import ABC, abstractmethod

class BaseEventHandler(ABC):
    """事件处理器基类

    所有事件处理器都应该继承这个基类，提供事件处理的基本接口
    """

    handler_name: str = ""  # 处理器名称
    handler_description: str = "" # 处理器描述
    weight: int = 0 # 处理器权重，越大权重越高
    intercept_message: bool = False # 是否拦截消息，默认为否
    init_subscribe: list[EventType | str] = [EventType.UNKNOWN]   # 初始化时订阅的事件名称


    def __init__(self, plugin: BasePlugin):
        self.subscribed_events = [] # 订阅的事件列表
        self.plugin = plugin   

        if EventType.UNKNOWN in self.init_subscribe:
            raise NotImplementedError("事件处理器必须指定 event_type")

    @abstractmethod
    async def execute(self, kwargs: dict | None) -> tuple[bool, bool, str | None]:
        """执行事件处理的抽象方法，子类必须实现
        Args:
            kwargs (dict | None): 事件消息对象，当你注册的事件为ON_START和ON_STOP时message为None
        Returns:
            Tuple[bool, bool, Optional[str]]: (是否执行成功, 是否需要继续处理, 可选的返回消息)
        """
        raise NotImplementedError("子类必须实现 execute 方法")

    def subscribe(self, event_name: str) -> None:
        """订阅一个事件

        Args:
            event_name (str): 要订阅的事件名称
        """
        ...

    def unsubscribe(self, event_name: str) -> None:
        """取消订阅一个事件

        Args:
            event_name (str): 要取消订阅的事件名称
        """
        ...
```

使用示例：
```python
from src.app.plugin_system import BaseEventHandler, EventType

class MyEventHandler(BaseEventHandler):
    handler_name: str = "my_event_handler"
    handler_description: str = "这是我的事件处理器"
    weight: int = 10
    intercept_message: bool = False
    init_subscribe: ClassVar[list[EventType | str]] = [EventType.ON_MESSAGE_RECEIVED]

    async def execute(self, kwargs: dict | None) -> tuple[bool, bool, str | None]:
        if kwargs is None:
            return False, True, None

        message = kwargs.get("message", "")
        user_id = kwargs.get("user_id", "")

        # 简单示例：当消息包含特定关键词时，回复一条消息
        if "hello" in message.lower():
            response = f"Hello, user {user_id}!"
            return True, False, response  # 执行成功，不继续处理，返回回复消息

        return True, True, None  # 执行成功，继续处理，返回无消息
```

#### Service
service是服务组件，它用于插件向外界暴露特定的功能接口，例如提供API服务，数据处理服务等。

经典流程：
插件定义service -> 注册到核心的组件管理器 -> 注册到service manager -> 其他插件或系统通过service manager获取service对象 -> service执行 -> 返回结果

基类：
```python
from abc import ABC, abstractmethod

class ServiceBase(ABC):
    service_name: str = ""  # 服务名称
    protocol_type: ProtocolType = ProtocolType.OTHER  # 服务协议类型

    def __init__(self, plugin: BasePlugin):
        self.plugin = plugin
```

使用示例：
```python
from src.app.plugin_system import ServiceBase, ProtocolType

class MyAPIService(ServiceBase):
    service_name: str = "my_api_service"
    protocol_type: ProtocolType = ProtocolType.OTHER

    async def fetch_data(self, query: str) -> dict:
        # 模拟数据获取逻辑
        data = {
            "query": query,
            "result": f"Data for {query}" 
        }
        return data

# 其他插件或系统可以通过 service api 获取该服务实例并调用 fetch_data 方法

from src.app.plugin_system import service_api, ProtocolType

my_service = service_api.get_service("my_plugin", "my_api_service", protocol=ProtocolType.OTHER)

result = await my_service.fetch_data("example_query")
```

#### Router
router是路由组件，用于对外界提供HTTP接口。

经典流程：
插件定义router -> 注册到核心的组件管理器 -> 启动系统服务器 ->  系统HTTP服务器将Router的端点包含进去->  处理HTTP请求 -> 返回响应

基类：
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from abc import ABC, abstractmethod

class BaseRouter(ABC):
    """
    对外暴露HTTP接口的基类。
    插件路由类应继承本类,并实现 register_endpoints 方法注册API路由。
    """

    router_name: str
    router_description: str
    
    # 新增:CORS配置(类属性)
    cors_origins: list[str] | None = None  # 允许的源,None表示使用全局默认
    cors_methods: list[str] | None = None  # 允许的方法,None表示使用全局默认
    cors_allow_credentials: bool = True    # 是否允许凭证
    cors_enabled: bool = True              # 是否启用CORS
    
    # 新增:自定义路由路径(如果设置,则挂载到此路径;否则使用默认路径)
    custom_route_path: str | None = None   # 例如: "/custom/path" 或 "" (根路径)

    def __init__(self,plugin: BasePlugin):
        self.plugin = plugin

        # 创建独立的 FastAPI 子应用(实例属性)
        self.app = FastAPI(
            title=f"{self.router_name}",
            description=self.router_description,
            version=1.0,
        )

        # 应用 CORS 配置
        self._apply_cors_config()
        
        # 注册端点
        self.register_endpoints()

    def _apply_cors_config(self):
        """应用CORS配置"""
        if not self.cors_enabled:
            return
        
        # 如果没有配置CORS origins，则不添加CORS中间件，使用服务器默认配置
        if not self.cors_origins:
            return
        
        methods = self.cors_methods or ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        
        # 应用CORS中间件
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=self.cors_origins,
            allow_credentials=self.cors_allow_credentials,
            allow_methods=methods,
            allow_headers=["*"],
            max_age=600,
        )

    @abstractmethod
    def register_endpoints(self) -> None:
        """
        子类需要实现的方法。
        在此方法中定义插件的HTTP接口。
        注意:现在使用 self.app 而非 self.router
        """
        ...
```

组件注册:
:::warning
下面的_register_router方法展示了如何注册Router组件并将其HTTP端点挂载到主FastAPI应用中,但因为它是直接在旧版本component_registry的逻辑基础上修改,所以我不知道适不适用于新版本,酌情参考。
:::
```python
def _register_router(self, info: ComponentInfo, cls: ComponentClassType) -> bool:
    """注册 Router 组件并将其 HTTP 端点挂载到主 FastAPI 应用"""
    if not bot_config.plugin_http_system.enable_plugin_http_endpoints:
        logger.info("插件HTTP端点功能已禁用,跳过路由注册")
        return True

    try:
        from src.common.server import get_global_server
        router_class = cast(type[BaseRouter], cls) # 类型转换,以便后续使用
        _assign_plugin_attrs(router_class, info.plugin_name, self.get_plugin_config(info.plugin_name) or {}) #为组件类动态赋予插件相关属性。

        # 实例化组件(现在返回配置好CORS的FastAPI应用)
        component_instance = router_class()
        server = get_global_server()
        
        # 确定路由前缀
        if router_class.custom_route_path is not None:
            # 使用自定义路径(可以是""表示根路径)
            prefix = router_class.custom_route_path
            if prefix and not prefix.startswith("/"):
                prefix = f"/{prefix}"
            logger.info(f"组件 '{info.name}' 使用自定义路径: {prefix or '(根路径)'}")
        else:
            # 默认路径: /plugin-api/{plugin_name}/{component_name}
            prefix = f"/plugin-api/{info.plugin_name}/{info.name}"
            logger.info(f"组件 '{info.name}' 使用默认路径: {prefix}")
        
        # 检查路径冲突
        if self._check_route_conflict(prefix, info.name):
            logger.error(f"路由冲突: {prefix}")
            return False
        
        # 使用 mount 挂载子应用而非 include_router
        server.app.mount(prefix, component_instance.app, name=info.name)
        
        # 注册路由前缀,以便冲突检查
        self._registered_routes[prefix] = info.name

        logger.debug(f"路由组件 '{info.name}' 已挂载到: {prefix}")
        return True
    except Exception as e:
        logger.error(f"注册路由组件时出错: {e}", exc_info=True)
        return False
```

示例1:简单路由:
```python
class MyAPIRouter(BaseRouter):
    router_name = "my_api"
    router_description = "自定义API接口"
    
    def register_endpoints(self) -> None:
        @self.app.get("/status")
        async def get_status():
            return {"status": "ok"}
        
        @self.app.post("/data")
        async def post_data(data: dict):
            return {"received": data}

# 结果: 挂载到 /plugin-api/{plugin_name}/my_api/status 和 /plugin-api/{plugin_name}/my_api/data
# CORS使用服务器默认配置
```

示例2:自定义CORS和路径:
```python
class CustomAPIRouter(BaseRouter):
    router_name = "custom_api"
    router_description = "自定义API"
    
    # 自定义CORS
    cors_origins = ["https://myapp.com", "https://admin.myapp.com"]
    cors_methods = ["GET", "POST", "PUT"]
    
    # 自定义路径(挂载到根目录下的v1/api)
    custom_route_path = "/v1/api"
    
    def register_endpoints(self) -> None:
        @self.app.get("/users")
        async def get_users():
            return []

# 结果: 挂载到 /v1/api/users
# CORS只允 许来自 myapp.com 和 admin.myapp.com 的请求
```

#### Plugin
plugin是插件的核心，每一个插件都一定有且只有一个plugin组件，其中包含了该插件的各种元数据以及子组件的注册信息。

经典流程：
插件定义plugin -> 注册到核心的组件管理器 -> 插件管理器加载插件 -> 插件生命周期管理 -> 插件运行

基类:
```python
from abc import ABC, abstractmethod

class BasePlugin(ABC):
    plugin_name: str = "unknown_plugin"  # 插件名称
    plugin_description: str = "No description"  # 插件描述

    dependent_components: list[str] = []  # 依赖的其他组件列表,格式：插件名:组件类型:组件名
    
    def __init__(self, config: BaseConfig):
        self.config = config
    
    def get_components(self) -> list[type[Component]]:
        """
        获取插件内所有组件

        Returns:
            list[type[Component]]: 插件内所有组件的列表
        """
        ...
    
    async def on_plugin_loaded(self) -> None:
        """插件加载时的钩子
        子类可重写以执行初始化逻辑
        """
        pass

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时的钩子
        子类可重写以执行清理逻辑
        """
        pass
```

使用示例：
```python
from .my_action import MyAction
from .time_command import TimeCommand
from .my_chatter import MyChatter

from src.app.plugin_system import config_section, Field, SectionBase, register_plugin

class MyPluginConfig(BaseConfig):
    config_name: str = "config"
    config_description: str = "这是我的插件配置文件"

    @config_section("inner")
    class InnerSection(ABC, SectionBase):
        """插件内部配置区"""
        version: str = Field("1.0.0", description="配置文件版本号")
        enabled: bool = Field(False, description="是否启用该插件")

    @config_section("general")
    class GeneralSection(SectionBase):
        """常规设置"""
        probability: float = Field(0.5, description="动作激活概率", ge=0.0, le=1.0)
        welcome_message: str = Field("欢迎使用我的插件！", description="欢迎消息内容")

@register_plugin
class MyPlugin(BasePlugin):
    plugin_name: str = "my_plugin"
    plugin_description: str = "这是我的插件示例"

    dependent_components: list[str] = [
        "other_plugin:action:bye_action",
        "other_plugin:command:fun_command",
        "other_plugin:chatter:abc_chatter",
    ]

    def __init__(self, config: MyPluginConfig):
        super().__init__(config)

    def get_components(self) -> list[type[Component]]:
        return [
            MyAction,
            TimeCommand,
            MyChatter,
        ]
```

#### Tool
tool是工具组件，提供特定的功能接口供LLM调用，例如计算器、翻译器等，相比于action执行“响应式”的动作，tool更侧重于提供“查询”的功能。

经典流程：
插件定义tool -> 注册到核心的组件管理器 -> 注册到tool manager -> Chatter工作 -> 获取可用LLMUsable -> LLM获取到上下文以及可用LLMUsable列表 -> LLM通过Tool Calling调用tool -> tool执行 -> 返回结果 -> LLM继续执行对话 -> 对话结束

基类：
```python
from abc import ABC, abstractmethod

class BaseTool(ABC):
    tool_name: str = ""  # Tool组件名称
    tool_description: str = ""   # Tool组件描述

    associated_platforms = []   # 支持的平台名

    chatter_allow = []  # 支持的chatter列表
    chat_type = ChatType.ALL  # tool支持的ChatType

    def __init__(self, plugin: BasePlugin):
        """
        初始化Tool组件

        Args:
            plugin: 插件实例
        """
        self.plugin = plugin
    
    @abstractmethod
    async def execute(self, ...) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """
        执行Tool的主要逻辑，和Action一样需要编写docstring说明参数

        Args:
            ...: Tool执行所需的参数

        Returns:
            tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]: Tool的执行结果
        """
        ...
```

使用示例：
```python
from src.app.plugin_system import BaseTool

class MyCalculatorTool(BaseTool):
    tool_name: str = "my_calculator"
    tool_description: str = "这是一个简单的计算器工具"

    associated_platforms = ["qq", "wechat"]

    chatter_allow = []
    chat_type = ChatType.ALL

    async def execute(self, expression: str) -> tuple[bool, str]:
        """
        执行计算逻辑

        Args:
            expression: 要计算的数学表达式，支持加减乘除

        Returns:
            tuple[bool, str]: 计算结果，成功与否及结果详情
        """

        try:
            # 简单计算逻辑（仅支持加减乘除）
            result = eval(expression, {"__builtins__": None}, {})
            return True, str(result)
        except Exception as e:
            return False, f"计算错误: {str(e)}"
```

`managers`:
managers是插件系统的管理器模块，负责管理和协调各种组件的注册、加载、卸载和运行。
它们提供统一的接口，方便插件系统对各类组件进行操作和维护。

#### Action Manager
action manager负责管理所有的action组件，提供注册、加载、卸载和执行action的功能。

除了基础的管理功能外，action manager还提供以下高级功能：
- 生成标准schema：根据action的execute方法自动生成调用schema，方便LLM调用。
- 统一激活判定：提供统一的go_activate调用接口，方便chatter获取action的激活状态。
以及其他辅助功能。

其他manager总体功能都类似，提供对应组件的管理功能。在此不再赘述。

`types`:
types模块定义了插件系统中使用的各种类型和数据结构，确保组件之间的数据交换和交互的一致性和可靠性。

`registry`:
registry模块负责插件系统中组件的注册和查找，提供统一的注册接口和查找机制，确保各类组件能够被正确识别和使用。

`state_manager`:
state manager负责管理插件系统的状态信息，提供状态的存储、更新和查询功能，确保插件系统能够正确维护和恢复其运行状态。

`loader`:
loader模块负责插件系统中插件整体的加载功能。它支持文件夹，zip包，.mfp等多种加载方式，确保插件系统能够灵活地加载和使用各种组件。

### core\prompt
prompt模块负责管理和处理与LLM交互相关的提示词和模板，确保插件系统能够有效地与LLM进行对话和交互。
它提供提示词的存储、加载和渲染功能。

它提供一个PromptTemplate类，通过该类可以实现强大的提示词模板功能。
该系统支持占位符映射，且支持默认占位符值，方便在提示词中使用动态内容.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

def _is_effectively_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return len(v.strip()) == 0
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) == 0
    return False

@dataclass(frozen=True)
class RenderPolicy:
    fn: Callable[[Any], str]

    def __call__(self, value: Any) -> str:
        return self.fn(value)

    def then(self, other: "RenderPolicy") -> "RenderPolicy":
        # 串联：先把 value 渲染成字符串，再交给下一个策略处理
        return RenderPolicy(lambda v: other(self(v)))

# --- 常用策略工厂 ---
def optional(empty: str = "") -> RenderPolicy:
    return RenderPolicy(lambda v: empty if _is_effectively_empty(v) else str(v))

def trim() -> RenderPolicy:
    return RenderPolicy(lambda v: str(v).strip())

def header(title: str, sep: str = "\n") -> RenderPolicy:
    def _fn(v: Any) -> str:
        s = str(v)
        if _is_effectively_empty(s):
            return ""
        return f"{title}{sep}{s}"
    return RenderPolicy(_fn)

def wrap(prefix: str = "", suffix: str = "") -> RenderPolicy:
    def _fn(v: Any) -> str:
        s = str(v)
        if _is_effectively_empty(s):
            return ""
        return f"{prefix}{s}{suffix}"
    return RenderPolicy(_fn)

def join_blocks(block_sep: str = "\n\n") -> RenderPolicy:
    def _fn(v: Any) -> str:
        if _is_effectively_empty(v):
            return ""
        if isinstance(v, (list, tuple)):
            parts = [str(x).strip() for x in v if not _is_effectively_empty(x)]
            return block_sep.join(parts)
        return str(v)
    return RenderPolicy(_fn)

def min_len(n: int) -> RenderPolicy:
    def _fn(v: Any) -> str:
        s = str(v)
        return "" if len(s.strip()) < n else s
    return RenderPolicy(_fn)

@dataclass
class PromptTemplate:
    name: str
    template: str
    policies: dict[str, RenderPolicy] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> "PromptTemplate":
        self.values[key] = value
        return self

    def build(self) -> str:
        rendered = {}
        for key, value in self.values.items():
            policy = self.policies.get(key, optional())  # 默认也是 optional
            rendered[key] = policy(value)

        # 没设置但模板里有的 key，会 KeyError；你也可以改成 strict/loose
        return self.template.format_map(rendered)
```

使用:
```python
from src.core.prompt import PromptTemplate, trim, min_len, header

tmpl = PromptTemplate(
    name="knowledge_base_query",
    template="用户问题：{user.query}\n\n{context.kb}\n\n",
    policies={
        "context.kb": trim().then(min_len(5)).then(header("# 知识库内容：")),
    }
)

prompt = (
    tmpl.set("user.query", "怎么设计 prompt 系统？")
        .set("context.kb", "")     # 没检索到
        .build()
)
print(prompt)   # 用户问题：怎么设计 prompt 系统？

# 如果 context.kb 有内容，就变成:
#
# 用户问题：...

# # 知识库内容：
#（检索结果...）
```

创建PromptTemplate实例时，会自动将其注册到prompt manager中，方便后续通过名称获取和使用。

```python
from src.core.prompt import get_prompt_manager

prompt_manager = get_prompt_manager()
prompt = prompt_manager.get_prompt_template("knowledge_base_query")
```

### core/transport
transport模块负责管理和处理核心与适配器或外界系统之间的通信和数据传输。

`message_receive`:
它负责接受标准的MessageEnvlope格式的消息，并将其转换为标准Message对象，供核心系统进一步处理。

`message_send`:
它负责将核心系统生成的Message对象转换为MessageEnvlope格式的消息，并发送出去。

`router`:
router模块负责管理和处理HTTP请求的路由，将外部请求映射到核心系统的相应处理逻辑。

`sink`:
sink模块负责与适配器建立连接，包括插件形式的core sink，以及独立进程形式的core sink，同时保留ws连接，确保核心系统能够与适配器进行有效的通信和数据交换。

### core/config
config模块负责管理和处理核心系统的配置文件。
它定义了标准core_config.toml和model_config.toml的结构和内容，并提供统一的配置存取接口。

### core/utils
utils模块提供了各种辅助工具和函数，支持核心系统的各项功能和操作。此处不再赘述。