# DB API 层详解

## 概述

API 层提供了高级的数据库操作接口，包括 CRUD 基类和 MongoDB 风格的查询构建器。

---

## CRUD 操作

### CRUDBase 类设计

CRUDBase 是一个通用的 CRUD 基类，使用泛型设计：

```python
T = TypeVar("T", bound=Any)

class CRUDBase(Generic[T]):
    def __init__(self, model: type[T]):
        self.model = model
        self.model_name = model.__tablename__
```

### 数据转换

#### _model_to_dict()

将 SQLAlchemy 模型实例转换为字典，确保对象分离状态：

```python
def _model_to_dict(instance: Any) -> dict[str, Any]:
    """转换为字典"""
    if instance is None:
        return {}
    
    model = type(instance)
    column_names = _get_model_column_names(model)
    
    # 提取所有列值
    result = {}
    for column_name in column_names:
        result[column_name] = getattr(instance, column_name)
    
    return result
```

**使用场景**：
- 确保对象在会话外有效
- 避免 lazy loading 问题
- 准备序列化或传输

#### _dict_to_model()

从字典创建模型实例：

```python
def _dict_to_model(model_class: type[T], data: dict[str, Any]) -> T:
    """从字典创建模型实例"""
    instance = model_class()
    valid_fields = _get_model_field_set(model_class)
    
    for key, value in data.items():
        if key in valid_fields:
            setattr(instance, key, value)
    
    return instance
```

### 核心方法

#### create()

创建新记录。

```python
async def create(self, **fields: Any) -> T:
    """创建新记录并返回"""
    async with get_db_session() as session:
        instance = self.model(**fields)
        session.add(instance)
        await session.flush()
        
        # 转换为字典以确保对象有效
        instance_dict = _model_to_dict(instance)
        return _dict_to_model(self.model, instance_dict)
```

**特点**：
- 自动保存到数据库
- 返回带 ID 的新实例
- 分离状态，会话外可用

**使用示例**：

```python
crud = CRUDBase(User)

user = await crud.create(
    name="Alice",
    email="alice@example.com",
    active=True
)

print(f"新用户 ID: {user.id}")
```

#### get()

根据 ID 获取单条记录。

```python
async def get(self, id: int) -> T | None:
    """根据 ID 获取记录"""
    async with get_db_session() as session:
        stmt = select(self.model).where(self.model.id == id)
        result = await session.execute(stmt)
        instance = result.scalar_one_or_none()
        
        if instance:
            return _dict_to_model(self.model, _model_to_dict(instance))
        return None
```

**使用示例**：

```python
from src.kernel.db import CRUDBase
import asyncio

async def test_get():
    crud = CRUDBase(User)
    
    user = await crud.get(1)
    
    if user:
        print(f"用户名: {user.name}")
    else:
        print("用户不存在")

asyncio.run(test_get())
```

#### get_by()

根据条件获取单条记录。

```python
async def get_by(self, **filters: Any) -> T | None:
    """根据条件获取单条记录"""
    stmt = select(self.model)
    
    for key, value in filters.items():
        if hasattr(self.model, key):
            stmt = stmt.where(getattr(self.model, key) == value)
    
    async with get_db_session() as session:
        result = await session.execute(stmt)
        instance = result.scalar_one_or_none()
        
        if instance:
            return _dict_to_model(self.model, _model_to_dict(instance))
        return None
```

**使用示例**：

```python
from src.kernel.db import CRUDBase
import asyncio

async def test_get_by():
    crud = CRUDBase(User)
    
    # 按邮箱查找用户
    user = await crud.get_by(email="alice@example.com")
    
    # 按多个条件查找
    user = await crud.get_by(email="alice@example.com", active=True)

asyncio.run(test_get_by())
```

#### get_all()

获取所有记录。

```python
async def get_all(self) -> list[T]:
    """获取所有记录"""
    stmt = select(self.model)
    
    async with get_db_session() as session:
        result = await session.execute(stmt)
        instances = result.scalars().all()
        
        # 转换所有实例
        return [_dict_to_model(self.model, _model_to_dict(i)) for i in instances]
```

**注意**：对于大数据集，建议使用 QueryBuilder.stream()

#### update()

更新记录。

```python
async def update(self, id: int, **fields: Any) -> T:
    """更新记录"""
    async with get_db_session() as session:
        stmt = select(self.model).where(self.model.id == id)
        result = await session.execute(stmt)
        instance = result.scalar_one()
        
        # 更新字段
        for key, value in fields.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        
        await session.flush()
        
        # 转换为字典并重建对象
        instance_dict = _model_to_dict(instance)
        return _dict_to_model(self.model, instance_dict)
```

**使用示例**：

```python
user = await crud.update(1, name="Alice Smith", active=False)
```

#### delete()

删除记录。

```python
async def delete(self, id: int) -> int:
    """删除记录，返回删除数量"""
    async with get_db_session() as session:
        stmt = delete(self.model).where(self.model.id == id)
        result = await session.execute(stmt)
        return result.rowcount
```

**使用示例**：

```python
from src.kernel.db import CRUDBase
import asyncio

async def test_delete():
    crud = CRUDBase(User)
    
    deleted = await crud.delete(1)
    if deleted > 0:
        print("用户已删除")

asyncio.run(test_delete())
```

#### count()

统计记录数。

```python
async def count(self, **filters: Any) -> int:
    """统计记录数"""
    stmt = select(func.count()).select_from(self.model)
    
    for key, value in filters.items():
        if hasattr(self.model, key):
            stmt = stmt.where(getattr(self.model, key) == value)
    
    async with get_db_session() as session:
        result = await session.execute(stmt)
        return result.scalar() or 0
```

**使用示例**：

```python
from src.kernel.db import CRUDBase
import asyncio

async def test_count():
    crud = CRUDBase(User)
    
    total = await crud.count()
    active_count = await crud.count(active=True)
    
    print(f"总用户数: {total}")
    print(f"活跃用户: {active_count}")

asyncio.run(test_count())
```

---

## 查询构建器

### QueryBuilder 设计理念

QueryBuilder 提供 MongoDB 风格的查询 API，支持链式调用和延迟执行：

```python
from src.kernel.db import QueryBuilder
import asyncio

async def test_querybuilder():
    # 链式调用示例
    users = await QueryBuilder(User)\
        .filter(active=True)\
        .filter(age__gte=18)\
        .order_by("-created_at")\
        .limit(10)\
        .all()

asyncio.run(test_querybuilder())
```

### 内部实现

```python
class QueryBuilder(Generic[T]):
    def __init__(self, model: type[T]):
        self.model = model
        self._stmt = select(model)  # 初始 SELECT 语句
```

每个方法都返回 `self`，支持链式调用：

```python
def filter(self, **conditions) -> Self:
    # 处理条件
    for key, value in conditions.items():
        # 添加 WHERE 子句
        self._stmt = self._stmt.where(...)
    return self
```

### 查询操作符

#### 比较操作符

```
eq    : field == value (默认)
ne    : field != value
gt    : field > value
lt    : field < value
gte   : field >= value
lte   : field <= value
```

**使用示例**：

```python
# 等于（默认）
qb.filter(status="active")

# 不等于
qb.filter(status__ne="deleted")

# 大于
qb.filter(age__gt=18)

# 范围
qb.filter(price__gte=100).filter(price__lte=1000)
```

#### 包含操作符

```
in    : field in [values]
nin   : field not in [values]
```

**使用示例**：

```python
# 包含
qb.filter(status__in=["active", "pending"])

# 不包含
qb.filter(country__nin=["CN", "JP"])
```

#### 字符串操作符

```
like  : 模糊匹配（SQL LIKE）
```

**使用示例**：

```python
# 前缀匹配
qb.filter(name__like="Alice%")

# 包含匹配
qb.filter(email__like="%@example.com")

# 任意位置
qb.filter(description__like="%python%")
```

#### 空值检查

```
isnull : field IS NULL (True) 或 IS NOT NULL (False)
```

**使用示例**：

```python
# 为空
qb.filter(deleted_at__isnull=True)

# 不为空
qb.filter(email__isnull=False)
```

### 过滤方法

#### filter()

AND 条件过滤。

```python
qb = qb.filter(**conditions) -> QueryBuilder
```

**多次调用 filter() 时为 AND 关系**：

```python
users = await QueryBuilder(User)\
    .filter(active=True)\          # AND
    .filter(age__gte=18)\          # AND
    .filter(country="China")\      # AND
    .all()

# 等价于：WHERE active=true AND age>=18 AND country='China'
```

#### filter_or()

OR 条件过滤。

```python
qb = qb.filter_or(**conditions) -> QueryBuilder
```

**注意**：SQL 的 OR 构造相对简单

```python
users = await QueryBuilder(User)\
    .filter_or(name="Alice", email="alice@example.com")\
    .all()

# WHERE name='Alice' OR email='alice@example.com'
```

### 排序和分页

#### order_by()

排序结果。

```python
qb = qb.order_by(*fields) -> QueryBuilder
```

**特点**：
- 升序（默认）
- 降序（`-` 前缀）
- 支持多字段排序

**使用示例**：

```python
# 升序
users = await QueryBuilder(User).order_by("name").all()

# 降序
users = await QueryBuilder(User).order_by("-created_at").all()

# 多字段排序
users = await QueryBuilder(User).order_by("-age", "name").all()
# 按年龄降序，然后按名字升序
```

#### limit()

限制结果数量。

```python
qb = qb.limit(count) -> QueryBuilder
```

#### offset()

跳过前 N 条记录。

```python
qb = qb.offset(skip) -> QueryBuilder
```

**分页示例**：

```python
async def get_page(model, page, per_page):
    skip = (page - 1) * per_page
    
    return await QueryBuilder(model)\
        .order_by("id")\
        .offset(skip)\
        .limit(per_page)\
        .all()

# 获取第 2 页，每页 10 条
page_2 = await get_page(User, 2, 10)
```

### 结果获取

#### all()

获取所有匹配的记录。

```python
records = await qb.all() -> list[T]
```

**使用示例**：

```python
users = await QueryBuilder(User)\
    .filter(active=True)\
    .all()
```

#### first()

获取第一条匹配的记录。

```python
record = await qb.first() -> T | None
```

**使用示例**：

```python
# 获取最早的活跃用户
user = await QueryBuilder(User)\
    .filter(active=True)\
    .order_by("created_at")\
    .first()
```

#### count()

统计匹配的记录数。

```python
total = await qb.count() -> int
```

**使用示例**：

```python
# 统计活跃用户
active_count = await QueryBuilder(User).filter(active=True).count()

# 条件统计
adult_count = await QueryBuilder(User)\
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

**特点**：
- 逐条加载记录
- 内存占用低
- 适合大数据集

**使用示例**：

```python
# 处理大量数据
total = 0
async for user in QueryBuilder(User).stream():
    # 逐条处理
    process_user(user)
    total += 1

print(f"处理 {total} 条记录")
```

---

## 聚合查询

### AggregateQuery 类

提供统计和分组功能。

```python
class AggregateQuery(Generic[T]):
    def __init__(self, model: type[T]):
        self.model = model
```

### 聚合方法

#### count()

统计记录总数。

```python
total = await aq.count() -> int
```

#### sum()

求和。

```python
total = await aq.sum("field_name") -> Any
```

**使用示例**：

```python
aq = AggregateQuery(Order)

# 计算订单总金额
total_amount = await aq.sum("amount")
```

#### avg()

平均值。

```python
average = await aq.avg("field_name") -> Any
```

**使用示例**：

```python
# 计算平均订单金额
avg_amount = await aq.avg("amount")
```

#### min() / max()

最小值/最大值。

```python
minimum = await aq.min("field_name") -> Any
maximum = await aq.max("field_name") -> Any
```

**使用示例**：

```python
aq = AggregateQuery(Product)

min_price = await aq.min("price")
max_price = await aq.max("price")

print(f"价格范围: {min_price} - {max_price}")
```

#### group_by()

分组聚合。

```python
groups = await aq.group_by("field_name") -> list[tuple]
```

**使用示例**：

```python
aq = AggregateQuery(Order)

# 按国家分组，统计订单数
groups = await aq.group_by("country")
# 返回：[("China", 100), ("USA", 50), ...]
```

---

## 高效查询模式

### 模式 1: 条件过滤

```python
async def find_users_by_criteria(filters):
    qb = QueryBuilder(User)
    
    if filters.get("name"):
        qb = qb.filter(name__like=f"%{filters['name']}%")
    
    if filters.get("min_age"):
        qb = qb.filter(age__gte=filters["min_age"])
    
    if filters.get("max_age"):
        qb = qb.filter(age__lte=filters["max_age"])
    
    if filters.get("country"):
        qb = qb.filter(country=filters["country"])
    
    return await qb.all()
```

### 模式 2: 分页查询

```python
async def paginate(model, page, per_page, **filters):
    qb = QueryBuilder(model)
    
    # 应用过滤
    for key, value in filters.items():
        qb = qb.filter(**{key: value})
    
    # 排序和分页
    total = await qb.count()
    
    items = await qb\
        .order_by("id")\
        .limit(per_page)\
        .offset((page - 1) * per_page)\
        .all()
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }
```

### 模式 3: 大数据处理

```python
async def process_large_dataset(model, process_func):
    """处理大量数据的流式模式"""
    
    count = 0
    async for record in QueryBuilder(model).stream():
        process_func(record)
        count += 1
        
        if count % 1000 == 0:
            print(f"已处理 {count} 条记录")
    
    return count
```

### 模式 4: 统计分析

```python
async def analyze_sales():
    """销售数据分析"""
    
    aq = AggregateQuery(Order)
    
    total_orders = await aq.count()
    total_revenue = await aq.sum("amount")
    avg_order = await aq.avg("amount")
    
    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order": avg_order
    }
```

---

## 性能最佳实践

### 1. 使用 stream() 处理大数据集

```python
# ✓ 好 - 流式处理，内存占用低
async for user in QueryBuilder(User).stream():
    process(user)

# ✗ 差 - 一次性加载，可能内存溢出
users = await QueryBuilder(User).all()
```

### 2. 合理使用过滤条件

```python
# ✓ 好 - 先过滤再获取
users = await QueryBuilder(User)\
    .filter(active=True)\
    .filter(age__gte=18)\
    .limit(100)\
    .all()

# ✗ 差 - 加载所有后在内存中过滤
users = await QueryBuilder(User).all()
users = [u for u in users if u.active and u.age >= 18][:100]
```

### 3. 使用聚合而不是循环

```python
# ✓ 好 - 数据库聚合
total = await AggregateQuery(Order).sum("amount")

# ✗ 差 - 内存累加
orders = await QueryBuilder(Order).all()
total = sum(o.amount for o in orders)
```

---

## 相关资源

- [DB 主文档](./README.md) - 概览
- [核心层详解](./core.md) - 引擎和会话
- [高级用法](./advanced.md) - 性能优化
