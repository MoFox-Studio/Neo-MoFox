# DB 核心层详解

## 概述

核心层（core）提供了数据库引擎和会话的底层管理，包括单例实例、线程安全、自动优化等功能。

---

## 架构设计

### 全局单例模式

引擎和会话工厂都采用全局单例模式，确保整个应用只有一个数据库连接池：

```python
# 全局引擎实例
_engine: AsyncEngine | None = None
_engine_lock: asyncio.Lock | None = None

# 全局会话工厂实例
_session_factory: async_sessionmaker | None = None
_factory_lock: asyncio.Lock | None = None
```

### 双重检查锁定（Double-Check Locking）

在异步环境中安全地初始化单例：

```python
import asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

async def get_engine() -> AsyncEngine:
    global _engine, _engine_lock
    
    # 快速路径：已初始化时直接返回
    if _engine is not None:
        return _engine
    
    # 延迟创建锁
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()
    
    # 获取锁
    async with _engine_lock:
        # 双重检查
        if _engine is not None:
            return _engine
        
        # 创建引擎
        _engine = create_async_engine(...)
        return _engine
```

**优势**：
- 线程安全
- 避免重复创建
- 最小化锁竞争

---

## 引擎管理

### 引擎配置流程

```
configure_engine()
    ↓
保存配置
    ↓
get_engine()
    ↓
使用配置创建引擎
    ↓
应用优化
    ↓
返回引擎实例
```

### 数据库类型推断

```python
def _infer_db_type_from_url(url: str) -> str | None:
    """从 URL 推断数据库类型"""
    
    scheme = url.split(":", 1)[0]
    backend = scheme.split("+", 1)[0].lower()
    
    if backend in {"sqlite", "postgresql"}:
        return backend
    return backend or None
```

**支持的 URL 格式**：

| 数据库 | URL 格式 |
|------|---------|
| SQLite | `sqlite+aiosqlite:///path/to/db.sqlite` |
| SQLite (内存) | `sqlite+aiosqlite:///:memory:` |
| PostgreSQL | `postgresql+asyncpg://user:pass@host/dbname` |

### 引擎优化

#### SQLite 优化

```python
from sqlalchemy.ext.asyncio import AsyncConnection

async def optimize_sqlite(connection: AsyncConnection):
    # 设置 busy_timeout，避免 "database is locked" 错误
    await connection.exec_driver_sql("PRAGMA busy_timeout = 60000")
    
    # 启用外键约束
    await connection.exec_driver_sql("PRAGMA foreign_keys = ON")
```

#### PostgreSQL 优化

PostgreSQL 默认配置通常足够，可根据需要添加自定义设置。

### 引擎信息

```python
def get_engine_info() -> dict:
    """获取引擎配置信息"""
    return {
        "configured": _engine_config is not None,
        "created": _engine is not None,
        "db_type": get_configured_db_type(),
        "url": _engine_config.url if _engine_config else None,
    }
```

---

## 会话管理

### 会话工厂配置

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import async_sessionmaker

engine = await create_async_engine(url="sqlite+aiosqlite:///db.sqlite")

_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 避免 detached 问题
)
```

**配置说明**：
- `bind` - 绑定到异步引擎
- `class_` - 使用 SQLAlchemy 的 AsyncSession
- `expire_on_commit=False` - 提交后不清除对象属性缓存

### 会话生命周期

```
get_db_session()
    ↓
创建会话
    ↓
应用数据库特定设置
    ↓
执行数据库操作（yield）
    ↓
    正常退出 → 自动提交
    异常 → 自动回滚
    ↓
关闭会话
```

### 数据库特定的会话设置

#### SQLite 会话设置

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

async def apply_sqlite_settings(session: AsyncSession):
    # busy_timeout：60 秒
    await session.execute(text("PRAGMA busy_timeout = 60000"))
    
    # 启用外键约束
    await session.execute(text("PRAGMA foreign_keys = ON"))
```

#### PostgreSQL 会话设置

```python
elif db_type == "postgresql":
    # 可选的 PostgreSQL 特定设置
    # 例如：设置 search_path、timezone 等
    pass
```

### 事务自动管理

```python
from src.kernel.db import get_db_session
from sqlalchemy import select

async def example_transaction():
    async with get_db_session() as session:
        try:
            # 执行操作
            result = await session.execute(select(User))
            
            # 如果正常完成，自动提交
            if session.is_active:
                await session.commit()
        except Exception:
            # 发生异常时自动回滚
            if session.is_active:
                await session.rollback()
            raise
        finally:
            # 总是关闭会话
            await session.close()
```

---

## 异常系统

### 异常层次结构

```
DatabaseError（基础异常）
├── DatabaseInitializationError
│   ├── 配置缺失
│   ├── URL 无效
│   └── 引擎创建失败
├── DatabaseConnectionError
│   ├── 连接超时
│   ├── 认证失败
│   └── 主机不可达
├── DatabaseQueryError
│   ├── SQL 语法错误
│   ├── 字段不存在
│   └── 约束违反
└── DatabaseTransactionError
    ├── 事务冲突
    └── 死锁
```

### 异常使用示例

```python
from src.kernel.db import (
    DatabaseError,
    DatabaseInitializationError,
    DatabaseConnectionError,
    DatabaseQueryError,
    configure_engine,
    get_engine,
)
import asyncio

async def test_exceptions():
    try:
        configure_engine("invalid://url")
        await get_engine()
    except DatabaseInitializationError as e:
        print(f"初始化失败: {e}")
    except DatabaseConnectionError as e:
        print(f"连接失败: {e}")
    except DatabaseError as e:
        print(f"数据库错误: {e}")

asyncio.run(test_exceptions())
```

---

## 状态管理

### 引擎状态

```
未配置
  ↓ configure_engine()
已配置未初始化
  ↓ get_engine()
已初始化
  ↓ close_engine()
已关闭
  ↓ 可重新配置
```

### 重置机制

#### reset_engine_state()

用于测试环境，完全重置引擎状态：

```python
from src.kernel.db import reset_engine_state
import asyncio

async def reset_db_state():
    # 1. 关闭现有引擎
    # 2. 清理锁
    # 3. 清理配置
    await reset_engine_state()

# 使用示例
import pytest

@pytest.fixture
async def clean_db():
    # 每个测试前重置
    await reset_engine_state()
    yield
    # 清理
    await reset_engine_state()
```
    
    # 配置测试数据库
    configure_engine("sqlite+aiosqlite:///:memory:")
    
    yield
    
    # 测试后清理
    await reset_engine_state()
```

#### reset_session_factory()

仅重置会话工厂：

```python
async def reset_session_factory():
    global _session_factory
    _session_factory = None
```

---

## 连接池管理

### 连接池配置

可通过 `engine_kwargs` 配置连接池参数：

```python
configure_engine(
    url="postgresql+asyncpg://...",
    engine_kwargs={
        "pool_size": 20,           # 连接池大小
        "max_overflow": 10,        # 超出池大小的额外连接数
        "pool_recycle": 3600,      # 连接回收周期
        "pool_pre_ping": True,     # 使用前 ping 连接
        "echo": False,             # SQL 日志
    }
)
```

**参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `pool_size` | 保持打开的连接数 | 5 |
| `max_overflow` | 超出 pool_size 的额外连接 | 10 |
| `pool_recycle` | 连接重新创建的秒数 | -1 (禁用) |
| `pool_pre_ping` | 使用前检查连接有效性 | False |
| `echo` | 打印 SQL 语句 | False |

### 连接回收（Connection Recycling）

对于长期运行的应用，建议启用连接回收：

```python
configure_engine(
    url="postgresql+asyncpg://...",
    engine_kwargs={
        "pool_recycle": 3600,  # 1 小时后重新创建连接
    }
)
```

这防止了数据库服务器在超时后断开连接。

---

## 日志记录

核心层使用统一的日志系统：

```python
logger = get_logger("database.engine", display="DB 引擎")
logger = get_logger("database.session", display="DB 会话")
```

**日志级别**：
- DEBUG - 详细的操作日志
- INFO - 引擎创建、会话创建
- WARNING - 连接警告、优化信息
- ERROR - 错误和异常

**常见日志**：

```
[DEBUG] 会话工厂已创建
[DEBUG] 应用 SQLite 优化
[INFO] 数据库引擎已初始化
[WARNING] 无法应用会话设置: ...
```

---

## 性能考虑

### 1. 连接池大小

- **小应用**：4-10 个连接
- **中等应用**：10-20 个连接
- **大型应用**：20-50 个连接

```python
# 示例：根据并发数配置
import os

max_concurrent = int(os.getenv("MAX_CONCURRENT", "10"))
pool_size = max_concurrent + 5

configure_engine(
    url="postgresql+asyncpg://...",
    engine_kwargs={"pool_size": pool_size}
)
```

### 2. 连接回收

长期运行的服务应启用连接回收：

```python
configure_engine(
    url="postgresql+asyncpg://...",
    engine_kwargs={
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }
)
```

### 3. SQL 回显

在开发环境启用，生产环境禁用：

```python
import os

debug = os.getenv("DEBUG", "false").lower() == "true"

configure_engine(
    url="...",
    engine_kwargs={"echo": debug}
)
```

---

## 故障排除

### 问题 1: "database is locked"

**原因**：SQLite 并发冲突

**解决**：

```python
configure_engine(
    url="sqlite+aiosqlite:///db.sqlite",
    apply_optimizations=True  # 自动应用 PRAGMA busy_timeout
)
```

### 问题 2: 连接超时

**原因**：连接池耗尽或数据库响应慢

**解决**：

```python
configure_engine(
    url="postgresql+asyncpg://...",
    engine_kwargs={
        "pool_size": 20,
        "connect_args": {"timeout": 30},
    }
)
```

### 问题 3: 对象状态不一致

**原因**：提交后访问对象属性触发延迟加载

**解决**：使用 `expire_on_commit=False`（已配置）或在会话内访问属性

---

## 相关资源

- [DB 主文档](./README.md) - 概览
- [API 层详解](./api.md) - CRUD 和查询
- [高级用法](./advanced.md) - 性能优化
