"""Event bus 模块使用示例（简化版）。

演示如何使用event bus进行事件的发布和订阅。
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.kernel.event import event_bus, Event, EventDecision
from src.kernel.logger import get_logger, COLOR

# 创建全局 logger
logger = get_logger("event_example", display="Event", color=COLOR.MAGENTA)


async def main():
    """主函数，演示event bus的各种用法。"""

    # ========== 示例1：基本的事件订阅和发布 ==========
    logger.print_panel("示例1：基本的事件订阅和发布")

    async def on_user_login(event: Event, shared: dict):
        """用户登录事件处理器"""
        user_id = event.data.get("user_id")
        username = event.data.get("username")
        logger.info(f"[INFO] 用户登录：{username} (ID: {user_id})")
        shared["last_login_user"] = {"user_id": user_id, "username": username}
        return (EventDecision.SUCCESS, shared)

    # 订阅事件
    event_bus.subscribe("user_login", on_user_login, priority=10)

    # 发布事件
    decision, shared = await event_bus.publish(
        Event(
            name="user_login",
            data={"user_id": "12345", "username": "张三"},
            source="auth_system",
        ),
        shared={},
    )
    logger.info(f"[RESULT] decision={decision}, shared={shared}")

    # ========== 示例2：多个处理器订阅同一事件 ==========
    logger.print_panel("示例2：多个处理器订阅同一事件")

    async def log_to_file(event: Event, shared: dict):
        """将日志写入文件的处理器"""
        logger.info(f"[FILE] 收到事件：{event.name}")
        shared.setdefault("steps", []).append("file")
        return (EventDecision.SUCCESS, shared)

    async def log_to_database(event: Event, shared: dict):
        """将日志写入数据库的处理器"""
        logger.info(f"[DB] 收到事件：{event.name}")
        shared.setdefault("steps", []).append("db")
        return (EventDecision.SUCCESS, shared)

    async def send_notification(event: Event, shared: dict):
        """发送通知的处理器"""
        logger.info(f"[NOTIFY] 新事件：{event.name}")
        shared.setdefault("steps", []).append("notify")
        return (EventDecision.SUCCESS, shared)

    # 多个处理器订阅同一事件
    # priority 越大越先执行
    event_bus.subscribe("new_message", send_notification, priority=100)
    event_bus.subscribe("new_message", log_to_database, priority=50)
    event_bus.subscribe("new_message", log_to_file, priority=10)

    # 发布事件，所有处理器都会被调用
    decision, shared = await event_bus.publish(
        Event(
            name="new_message",
            data={"content": "你好，世界！"},
        ),
        shared={"steps": []},
    )
    logger.info(f"[RESULT] decision={decision}, steps={shared['steps']}")

    # ========== 示例3：取消订阅 ==========
    logger.print_panel("示例3：取消订阅")

    async def temporary_handler(event: Event, shared: dict):
        """临时处理器"""
        logger.info("[TEMP] 临时处理器被调用")
        shared.setdefault("count", 0)
        shared["count"] += 1
        return (EventDecision.SUCCESS, shared)

    # 使用返回的取消订阅函数
    unsubscribe = event_bus.subscribe("temp_event", temporary_handler)

    logger.info("第一次发布：")
    await event_bus.publish(Event(name="temp_event"), shared={})

    # 取消订阅
    unsubscribe()
    logger.info("\n取消订阅后再次发布：")
    await event_bus.publish(Event(name="temp_event"), shared={})

    # ========== 示例4：同步处理器 ==========
    logger.print_panel("示例4：同步处理器（非async）")

    def sync_handler(event: Event, shared: dict):
        """同步处理器"""
        logger.info(f"[SYNC] 同步处理器处理事件：{event.name}")
        shared.setdefault("sync", 0)
        shared["sync"] += 1
        return (EventDecision.SUCCESS, shared)

    event_bus.subscribe("sync_event", sync_handler)
    await event_bus.publish(Event(name="sync_event"), shared={})

    # ========== 示例5：事件数据传递 ==========
    logger.print_panel("示例5：复杂事件数据")

    async def handle_order_created(event: Event, shared: dict):
        """处理订单创建事件"""
        order_data = event.data
        logger.info("[ORDER] 新订单创建：")
        logger.info(f"   订单号：{order_data['order_id']}")
        logger.info(f"   金额：¥{order_data['amount']:.2f}")
        logger.info(f"   商品：{order_data['product']}")
        logger.info(f"   来源：{event.source}")
        shared["last_order_id"] = order_data["order_id"]
        return (EventDecision.SUCCESS, shared)

    event_bus.subscribe("order_created", handle_order_created)

    await event_bus.publish(
        Event(
            name="order_created",
            data={
                "order_id": "ORD-2024-001",
                "amount": 299.99,
                "product": "机械键盘",
            },
            source="shop_system",
        ),
        shared={},
    )

    # ========== 示例6：混合使用同步和异步处理器 ==========
    logger.print_panel("示例6：混合使用同步和异步处理器")

    def sync_counter(event: Event, shared: dict):
        logger.info("  [SYNC] 同步计数器")
        shared.setdefault("count", 0)
        shared["count"] += 1
        return (EventDecision.SUCCESS, shared)

    async def async_counter(event: Event, shared: dict):
        await asyncio.sleep(0.01)  # 模拟异步操作
        logger.info("  [ASYNC] 异步计数器")
        shared.setdefault("count", 0)
        shared["count"] += 1
        return (EventDecision.SUCCESS, shared)

    event_bus.subscribe("counter_event", sync_counter)
    event_bus.subscribe("counter_event", async_counter)

    logger.info("计数器事件：")
    decision, shared = await event_bus.publish(Event(name="counter_event"), shared={"count": 0})
    logger.info(f"[RESULT] decision={decision}, count={shared['count']}")

    # ========== 示例7：查看事件总线状态 ==========
    logger.print_panel("示例7：事件总线状态")

    logger.info("[STATS] 事件总线统计：")
    logger.info(f"   总线名称：{event_bus.name}")
    logger.info(f"   已订阅事件数：{event_bus.event_count}")
    logger.info(f"   处理器总数：{event_bus.handler_count}")
    logger.info(f"   已订阅的事件：{', '.join(sorted(event_bus.subscribed_events))}")

    # ========== 示例8：使用全局event_bus ==========
    logger.print_panel("示例8：全局event_bus使用")

    # 全局event_bus已经自动导入并初始化
    # 可以在任何模块中使用它
    async def global_handler(event: Event, shared: dict):
        logger.info(f"[GLOBAL] 全局事件总线收到：{event.name}")
        shared.setdefault("global", 0)
        shared["global"] += 1
        return (EventDecision.SUCCESS, shared)

    event_bus.subscribe("global_test", global_handler)
    await event_bus.publish(Event(name="global_test"), shared={})

    # ========== 示例9：PASS 与 STOP（共享参数链） ==========
    logger.print_panel("示例9：PASS 与 STOP（共享参数链）")

    async def step1(event: Event, shared: dict):
        logger.info("  step1: PASS（返回的 shared 会被忽略）")
        return (EventDecision.PASS, {"ignored": True})

    async def step2(event: Event, shared: dict):
        logger.info("  step2: SUCCESS（更新 shared）")
        shared["value"] = shared.get("value", 0) + 1
        return (EventDecision.SUCCESS, shared)

    async def step3(event: Event, shared: dict):
        logger.info("  step3: STOP（终止后续订阅者）")
        shared["stopped"] = True
        return (EventDecision.STOP, shared)

    async def step4(event: Event, shared: dict):
        logger.info("  step4: SHOULD NOT RUN")
        shared["should_not_run"] = True
        return (EventDecision.SUCCESS, shared)

    event_bus.subscribe("chain_event", step4, priority=0)
    event_bus.subscribe("chain_event", step3, priority=10)
    event_bus.subscribe("chain_event", step2, priority=20)
    event_bus.subscribe("chain_event", step1, priority=30)

    decision, shared = await event_bus.publish(Event(name="chain_event"), shared={"value": 0})
    logger.info(f"[RESULT] decision={decision}, shared={shared}")

    logger.info("\n[SUCCESS] 所有示例执行完成！")


if __name__ == "__main__":
    # 运行示例
    asyncio.run(main())
