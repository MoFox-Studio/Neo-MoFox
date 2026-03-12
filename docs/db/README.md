# DB 模块

## 概述

`db` 模块提供了一个完整的数据库抽象层，建立在 SQLAlchemy 异步 ORM 基础上。它分为两部分：

**核心层（core）**：
- 🔧 引擎管理 - 创建和管理异步数据库引擎
- 🔌 会话管理 - 提供会话工厂和上下文管理器
- ⚠️ 异常系统 - 统一的数据库异常定义

**API 层（api）**：
- 📝 CRUD 操作 - 基础的创建、读取、更新、删除
- 🔍 查询构建器 - MongoDB 风格的高级查询
- 📊 聚合查询 - 统计和分组操作

**特点**：
- ✅ 异步操作 - 完全支持 async/await
- ✅ 多数据库支持 - SQLite 和 PostgreSQL
- ✅ 类型安全 - 完整的类型提示
- ✅ 自动优化 - 数据库特定的性能优化
- ✅ 线程安全 - 单例模式和锁机制

---

## 快速开始

### 配置数据库

```python
from src.kernel.db import configure_engine, get_db_session
from sqlalchemy.orm import declarative_base

# 步骤 1: 配置引擎
configure_engine(
    url="sqlite+aiosqlite:///database.db",
    apply_optimizations=True
)

# 步骤 2: 创建模型基类
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id: int
    name: str
    email: str
```

### 基本 CRUD 操作

```python
from src.kernel.db import CRUDBase, get_db_session
import asyncio

class UserCRUD(CRUDBase[User]):
    pass

async def main():
    crud = UserCRUD(User)
    
    # 创建
    user = await crud.create(name="Alice", email="alice@example.com")
    
    # 读取
    retrieved = await crud.get(user.id)
    
    # 更新
    updated = await crud.update(user.id, name="Alice Smith")
    
    # 删除
    await crud.delete(user.id)

asyncio.run(main())
```

### 高级查询

```python
from src.kernel.db import QueryBuilder

async def search_users():
    qb = QueryBuilder(User)
    
    # 构建查询
    users = await qb\
        .filter(name__like="%Alice%")\
        .filter_or(email__like="%@example.com")\
        .order_by("name")\
        .limit(10)\
        .all()
    
    for user in users:
        print(f"{user.name}: {user.email}")

asyncio.run(search_users())
```

### 事务管理

```python
from src.kernel.db import get_db_session
from sqlalchemy import select

async def transfer_money():
    async with get_db_session() as session:
        # 所有数据库操作
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        # 自动提交（正常退出）或回滚（异常）
        # 不需要手动调用 commit/rollback

asyncio.run(transfer_money())
```

---

## 核心层

### 引擎管理

#### configure_engine()

配置数据库引擎的初始化参数。

```python
configure_engine(
    url: str,
    *,
    engine_kwargs: dict | None = None,
    db_type: str | None = None,
    apply_optimizations: bool = True
) -> None
```

**参数**：

| 参数 | 说明 |
|------|------|
| `url` | SQLAlchemy 异步 URL |
| `engine_kwargs` | 传给 `create_async_engine()` 的参数 |
| `db_type` | 数据库类型（sqlite/postgresql），自动推断 |
| `apply_optimizations` | 是否应用数据库特定优化 |

**示例**：

```python
# SQLite 配置
configure_engine(
    url="sqlite+aiosqlite:///db.sqlite",
    apply_optimizations=True
)

# PostgreSQL 配置
configure_engine(
    url="postgresql+asyncpg://user:pass@localhost/dbname",
    engine_kwargs={
        "echo": False,
        "pool_size": 20,
        "max_overflow": 0
    }
)

# 带密码转义
from urllib.parse import quote_plus
password = "p@ss%word"
configure_engine(
    url=f"postgresql+asyncpg://user:{quote_plus(password)}@localhost/dbname"
)
```

#### get_engine()

获取全局数据库引擎（单例）。

```python
engine = await get_engine() -> AsyncEngine
```

**说明**：
- 第一次调用时创建引擎
- 之后返回同一实例
- 线程安全（使用双重检查锁定）

**使用示例**：

```python
async def get_db_info():
    engine = await get_engine()
    # 使用引擎进行低级操作
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT 1"))
```

#### close_engine()

关闭数据库引擎。

```python
await close_engine() -> None
```

**说明**：
- 释放所有数据库连接
- 通常在应用关闭时调用
- 关闭后可重新配置和创建新引擎

**使用示例**：

```python
import atexit

async def shutdown():
    await close_engine()

# 在应用启动时
atexit.register(lambda: asyncio.run(shutdown()))
```

#### get_engine_info()

获取引擎信息。

```python
info = get_engine_info() -> dict
```

**返回信息**：
- `configured` - 是否已配置
- `created` - 是否已创建
- `db_type` - 数据库类型
- `url` - 数据库连接字符串（部分隐藏）

**使用示例**：

```python
info = get_engine_info()
print(f"数据库类型: {info['db_type']}")
print(f"已创建: {info['created']}")
```

#### reset_engine_state()

重置引擎状态（用于测试）。

```python
await reset_engine_state() -> None
```

**说明**：
- 关闭现有引擎
- 清理所有状态
- 主要用于单元测试

### 会话管理

#### get_db_session()

获取数据库会话上下文管理器。

```python
async with get_db_session() as session: AsyncSession
    # 数据库操作
```

**特性**：
- 自动提交（正常退出）
- 自动回滚（异常发生）
- 自动关闭
- 数据库特定的会话设置

**使用示例**：

```python
from sqlalchemy import select

async def get_users():
    async with get_db_session() as session:
        stmt = select(User).where(User.active == True)
        result = await session.execute(stmt)
        return result.scalars().all()

asyncio.run(get_users())
```

**异常处理**：

```python
from src.kernel.db import DatabaseTransactionError

async def update_with_error_handling():
    try:
        async with get_db_session() as session:
            # 如果此处异常，会自动回滚
            await session.execute(update(User).values(active=True))
    except Exception as e:
        print(f"事务失败: {e}")
```

#### get_session_factory()

获取会话工厂。

```python
factory = await get_session_factory() -> async_sessionmaker
```

**说明**：
- 返回 SQLAlchemy 的 async_sessionmaker
- 用于高级用途
- 通常使用 `get_db_session()` 上下文管理器

**使用示例**：

```python
factory = await get_session_factory()

async with factory() as session:
    # 直接使用工厂创建会话
    result = await session.execute(select(User))
```

#### reset_session_factory()

重置会话工厂（用于测试）。

```python
await reset_session_factory() -> None
```

### 异常系统

```python
from src.kernel.db import (
    DatabaseError,              # 基础异常
    DatabaseInitializationError,  # 初始化异常
    DatabaseConnectionError,    # 连接异常
    DatabaseQueryError,         # 查询异常
    DatabaseTransactionError,   # 事务异常
)
```

**异常层次**：

```
DatabaseError
├── DatabaseInitializationError  # 初始化失败
├── DatabaseConnectionError      # 连接失败
├── DatabaseQueryError           # 查询失败
└── DatabaseTransactionError     # 事务失败
```

**使用示例**：

```python
from src.kernel.db import (
    DatabaseError,
    DatabaseConnectionError
)

try:
    engine = await get_engine()
except DatabaseConnectionError as e:
    print(f"无法连接数据库: {e}")
except DatabaseError as e:
    print(f"数据库错误: {e}")
```

---

## API 层

### CRUD 操作

#### CRUDBase 类

基础 CRUD 操作类。

```python
class CRUDBase(Generic[T]):
    def __init__(self, model: type[T]):
        """初始化 CRUD 操作
        
        Args:
            model: SQLAlchemy 模型类
        """
```

**方法**：

##### create()

创建新记录。

```python
record = await crud.create(**fields) -> T
```

**参数**：字段名和值

**返回**：创建的模型实例

**使用示例**：

```python
crud = CRUDBase(User)

user = await crud.create(
    name="Alice",
    email="alice@example.com",
    active=True
)

print(f"创建用户 ID: {user.id}")
```

##### get()

根据 ID 获取单条记录。

```python
record = await crud.get(id) -> T | None
```

##### get_by()

根据条件获取单条记录。

```python
record = await crud.get_by(**filters) -> T | None
```

**使用示例**：

```python
# 按 ID
user = await crud.get(1)

# 按其他字段
user = await crud.get_by(email="alice@example.com")
```

##### get_all()

获取所有记录。

```python
records = await crud.get_all() -> list[T]
```

##### update()

更新记录。

```python
updated = await crud.update(id, **fields) -> T
```

**使用示例**：

```python
user = await crud.update(1, name="Alice Smith", active=False)
```

##### delete()

删除记录。

```python
deleted_count = await crud.delete(id) -> int
```

##### count()

统计记录数。

```python
total = await crud.count() -> int
```

**使用示例**：

```python
crud = CRUDBase(User)

total_users = await crud.count()
print(f"总用户数: {total_users}")
```

### 查询构建器

#### QueryBuilder 类

MongoDB 风格的查询构建器。

```python
class QueryBuilder(Generic[T]):
    def __init__(self, model: type[T]):
        """初始化查询构建器"""
```

**特点**：
- 链式调用
- 延迟执行
- 自动内存优化

#### filter()

添加过滤条件。

```python
qb = qb.filter(**conditions) -> QueryBuilder
```

**操作符**：

| 操作符 | 含义 | 示例 |
|-------|------|------|
| (无) | 等于 | `name="Alice"` |
| `__gt` | 大于 | `age__gt=18` |
| `__lt` | 小于 | `age__lt=65` |
| `__gte` | 大于等于 | `age__gte=18` |
| `__lte` | 小于等于 | `age__lte=65` |
| `__ne` | 不等于 | `status__ne="inactive"` |
| `__in` | 包含 | `status__in=["active", "pending"]` |
| `__nin` | 不包含 | `status__nin=["deleted"]` |
| `__like` | 模糊匹配 | `name__like="%Alice%"` |
| `__isnull` | 为空 | `email__isnull=True` |

**使用示例**：

```python
qb = QueryBuilder(User)

# 单条件过滤
users = await qb.filter(active=True).all()

# 多条件过滤（AND）
users = await qb\
    .filter(active=True)\
    .filter(age__gte=18)\
    .filter(country="China")\
    .all()

# 复杂条件
users = await qb\
    .filter(name__like="%Alice%")\
    .filter(age__gte=18)\
    .filter(status__in=["active", "pending"])\
    .all()
```

#### filter_or()

添加 OR 条件。

```python
qb = qb.filter_or(**conditions) -> QueryBuilder
```

**使用示例**：

```python
# 查找名字是 Alice 或 Bob 的用户
users = await QueryBuilder(User)\
    .filter_or(name="Alice", name="Bob")\
    .all()

# 注意：filter_or 只能用一次（SQL OR 的限制）
# 对于复杂的 OR 条件，建议使用多个独立查询或自定义 SQL
```

#### order_by()

排序结果。

```python
qb = qb.order_by(*fields) -> QueryBuilder
```

**参数**：
- 字段名升序排列
- 以 `-` 前缀表示降序

**使用示例**：

```python
users = await QueryBuilder(User)\
    .order_by("name")  # 名字升序
    .all()

users = await QueryBuilder(User)\
    .order_by("-age", "name")  # 年龄降序，名字升序
    .all()
```

#### limit() 和 offset()

分页。

```python
qb = qb.limit(count) -> QueryBuilder
qb = qb.offset(skip) -> QueryBuilder
```

**使用示例**：

```python
# 第 1 页，每页 10 条
page = 1
per_page = 10

users = await QueryBuilder(User)\
    .order_by("id")\
    .offset((page - 1) * per_page)\
    .limit(per_page)\
    .all()
```

#### all()

获取所有匹配的记录。

```python
records = await qb.all() -> list[T]
```

#### first()

获取第一条匹配的记录。

```python
record = await qb.first() -> T | None
```

#### count()

统计匹配的记录数。

```python
total = await qb.count() -> int
```

**使用示例**：

```python
qb = QueryBuilder(User)

# 统计活跃用户
active_count = await qb.filter(active=True).count()

# 统计年龄在 18-65 岁的用户
adult_count = await qb\
    .filter(age__gte=18)\
    .filter(age__lte=65)\
    .count()
```

#### stream()

流式迭代结果（内存优化）。

```python
async for record in qb.stream():
    # 处理每条记录
```

**使用示例**：

```python
# 处理大量数据时推荐使用
qb = QueryBuilder(User)

count = 0
async for user in qb.stream():
    # 逐条处理，而不是一次性加载到内存
    process_user(user)
    count += 1

print(f"处理了 {count} 条记录")
```

### 聚合查询

#### AggregateQuery 类

聚合操作（统计、分组等）。

```python
class AggregateQuery(Generic[T]):
    def __init__(self, model: type[T]):
        """初始化聚合查询"""
```

**方法**：

##### count()

统计记录数。

```python
total = await aq.count() -> int
```

##### sum()

求和。

```python
total = await aq.sum(field_name) -> Any
```

**使用示例**：

```python
aq = AggregateQuery(Order)

# 计算订单总金额
total_amount = await aq.sum("amount")
```

##### avg()

平均值。

```python
average = await aq.avg(field_name) -> Any
```

##### min() 和 max()

最小值和最大值。

```python
minimum = await aq.min(field_name) -> Any
maximum = await aq.max(field_name) -> Any
```

##### group_by()

分组聚合。

```python
groups = await aq.group_by(field_name) -> list[tuple]
```

---

## 使用模式

### 模式 1: 基本操作

```python
from src.kernel.db import CRUDBase, get_db_session

class Product(Base):
    __tablename__ = "products"
    id: int
    name: str
    price: float

crud = CRUDBase(Product)

async def basic_crud():
    # 创建
    p = await crud.create(name="Laptop", price=999.99)
    
    # 读取
    product = await crud.get(p.id)
    
    # 更新
    await crud.update(p.id, price=899.99)
    
    # 删除
    await crud.delete(p.id)
```

### 模式 2: 复杂查询

```python
from src.kernel.db import QueryBuilder

async def search():
    users = await QueryBuilder(User)\
        .filter(active=True)\
        .filter(age__gte=18)\
        .filter(country="China")\
        .order_by("-created_at")\
        .limit(20)\
        .all()
    
    return users
```

### 模式 3: 事务

```python
from src.kernel.db import get_db_session
from sqlalchemy import select

async def transfer():
    async with get_db_session() as session:
        # 查询
        stmt = select(Account).where(Account.id == 1)
        result = await session.execute(stmt)
        account = result.scalar_one()
        
        # 修改（自动追踪）
        account.balance -= 100
        
        # 自动提交
```

### 模式 4: 批量操作

```python
async def batch_import():
    crud = CRUDBase(User)
    
    users_data = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
        # ...
    ]
    
    for data in users_data:
        await crud.create(**data)
```

---

## 最佳实践

### 1. 始终使用会话上下文管理器

```python
# ✓ 好的做法
async with get_db_session() as session:
    result = await session.execute(select(User))

# ✗ 不好的做法（可能泄漏连接）
factory = await get_session_factory()
session = factory()
result = await session.execute(select(User))
```

### 2. 为不同的表创建专用 CRUD 类

```python
# ✓ 好的做法
class UserCRUD(CRUDBase[User]):
    async def get_active_users(self):
        return await QueryBuilder(User).filter(active=True).all()

class ProductCRUD(CRUDBase[Product]):
    async def get_on_sale(self):
        return await QueryBuilder(Product).filter(on_sale=True).all()

# ✗ 不好的做法（混乱）
crud = CRUDBase(User)
crud_product = CRUDBase(Product)
```

### 3. 使用流式查询处理大量数据

```python
# ✓ 好的做法 - 流式处理
async for user in QueryBuilder(User).stream():
    process_user(user)

# ✗ 不好的做法 - 一次性加载
users = await QueryBuilder(User).all()  # 可能内存溢出
for user in users:
    process_user(user)
```

### 4. 异常处理

```python
from src.kernel.db import DatabaseError, DatabaseConnectionError

try:
    await crud.create(...)
except DatabaseError as e:
    print(f"数据库错误: {e}")
except Exception as e:
    print(f"未知错误: {e}")
```

---

## 相关资源

- [核心层详解](./core.md) - 引擎和会话管理
- [API 层详解](./api.md) - CRUD 和查询
- [高级用法](./advanced.md) - 性能优化和扩展
