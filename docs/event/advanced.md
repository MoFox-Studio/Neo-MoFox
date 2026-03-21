# Event 模块高级模式

本文档聚焦“如何把 EventBus 用在复杂业务链路中”，并严格遵守当前实现协议：`next_params` 的 key 集合必须与初始参数一致。

---

## 使用前约定

在复杂事件链中，先定义固定参数结构，避免处理中新增 key：

```python
PAYLOAD_TEMPLATE = {
    "request_id": "",
    "user_id": "",
    "valid": False,
    "reason": "",
    "processed": False,
    "stopped": False,
}
```

发布时按模板传值：

```python
decision, final_params = await bus.publish("user.process", {
    **PAYLOAD_TEMPLATE,
    "request_id": "req-001",
    "user_id": "u-42",
})
```

---

## 模式 1：验证前置链

目标：高优先级验证器先运行，不通过直接 STOP。

```python
from src.kernel.event import EventDecision


async def check_user_id(event_name: str, params: dict):
    if not params["user_id"]:
        params["valid"] = False
        params["reason"] = "empty_user_id"
        params["stopped"] = True
        return (EventDecision.STOP, params)

    params["valid"] = True
    return (EventDecision.SUCCESS, params)


async def do_main_process(event_name: str, params: dict):
    if not params["valid"]:
        return (EventDecision.PASS, params)

    # do main process
    params["processed"] = True
    return (EventDecision.SUCCESS, params)


bus.subscribe("user.process", check_user_id, priority=100)
bus.subscribe("user.process", do_main_process, priority=10)
```

要点：

1. 验证处理器只改已有字段值。
2. 主处理器用 `PASS` 表达“条件不满足时跳过”。

---

## 模式 2：多阶段流水线

目标：把复杂流程切成多个阶段，利用 priority 控制顺序。

```python
PIPELINE_PAYLOAD = {
    "raw": "",
    "normalized": "",
    "validated": False,
    "saved": False,
    "error": "",
}


def normalize(event_name: str, params: dict):
    params["normalized"] = params["raw"].strip().lower()
    return (EventDecision.SUCCESS, params)


def validate(event_name: str, params: dict):
    if not params["normalized"]:
        params["validated"] = False
        params["error"] = "empty_after_normalize"
        return (EventDecision.STOP, params)
    params["validated"] = True
    return (EventDecision.SUCCESS, params)


async def save(event_name: str, params: dict):
    if not params["validated"]:
        return (EventDecision.PASS, params)
    # await repo.save(params["normalized"])
    params["saved"] = True
    return (EventDecision.SUCCESS, params)


bus.subscribe("data.pipeline", normalize, priority=100)
bus.subscribe("data.pipeline", validate, priority=80)
bus.subscribe("data.pipeline", save, priority=10)
```

---

## 模式 3：幂等副作用分层

目标：核心链路与副作用链路解耦，副作用失败不影响主链。

```python
ORDER_PAYLOAD = {
    "order_id": "",
    "ok": False,
    "audit_done": False,
    "metrics_done": False,
}


async def core_logic(event_name: str, params: dict):
    # 核心逻辑
    params["ok"] = True
    return (EventDecision.SUCCESS, params)


async def audit(event_name: str, params: dict):
    if not params["ok"]:
        return (EventDecision.PASS, params)
    # await write_audit(...)
    params["audit_done"] = True
    return (EventDecision.SUCCESS, params)


async def metrics(event_name: str, params: dict):
    if not params["ok"]:
        return (EventDecision.PASS, params)
    # await report_metrics(...)
    params["metrics_done"] = True
    return (EventDecision.SUCCESS, params)


bus.subscribe("order.created", core_logic, priority=100)
bus.subscribe("order.created", audit, priority=20)
bus.subscribe("order.created", metrics, priority=10)
```

---

## 模式 4：事件级联（Fan-out）

目标：一个事件触发多个后续事件，使用 `publish_sync()` 异步扇出。

```python
USER_CREATED_PAYLOAD = {
    "user_id": "",
    "email": "",
    "ok": False,
}


async def on_user_created(event_name: str, params: dict):
    params["ok"] = True

    bus.publish_sync("mail.welcome", {
        "user_id": params["user_id"],
        "email": params["email"],
        "ok": True,
    })

    bus.publish_sync("metrics.user_created", {
        "user_id": params["user_id"],
        "email": params["email"],
        "ok": True,
    })

    return (EventDecision.SUCCESS, params)
```

注意：

1. 扇出事件的 payload 也应遵守各自事件的 key 固定约束。
2. `publish_sync()` 返回 task，必要时应在上层收集或监控。

---

## 模式 5：可观测性增强

建议给每个事件保留这些通用字段：

1. `request_id`
2. `trace_id`
3. `error_code`
4. `error_message`

这样可以在不新增 key 的前提下逐步填充追踪信息。

示例：

```python
TRACE_PAYLOAD = {
    "request_id": "",
    "trace_id": "",
    "error_code": "",
    "error_message": "",
}


def trace_start(event_name: str, params: dict):
    if not params["trace_id"]:
        params["trace_id"] = params["request_id"]
    return (EventDecision.SUCCESS, params)
```

---

## 性能建议

1. 高频事件尽量减少订阅者数量。
2. 将重 I/O 的非关键任务放到单独事件，并用 `publish_sync()`。
3. 在模块生命周期结束时清理订阅，避免长生命周期泄漏。
4. 对热点事件定义稳定 payload，减少处理器分支判断。

---

## 反模式与修复

1. 反模式：处理器里新增 `params["new_key"] = ...`。
2. 修复：在初始 payload 模板中提前声明 `new_key`。

1. 反模式：所有处理器都设相同优先级且依赖执行顺序。
2. 修复：显式分层 priority（如 100/80/50/10）。

1. 反模式：关键路径使用 `publish_sync()` 后不追踪任务失败。
2. 修复：关键路径改用 `await publish()` 或集中处理 task 状态。

---

## 故障排查清单

1. 处理器是否返回二元组？
2. decision 是否为 `SUCCESS/PASS/STOP`？
3. `next_params` 是否为 dict 且 key 全是 str？
4. key 集合是否与初始 payload 一致？
5. priority 是否符合预期执行顺序？
6. 是否有处理器异常被日志吞掉导致你误判链路？

---

## 相关资源

- [主文档](./README.md) - API 与基础约束
- [核心实现](./core.md) - 内部执行流程
- [示例代码](../../examples/src/kernel/event/event_example.py) - 可运行示例
