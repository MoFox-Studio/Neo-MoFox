"""Storage 模块使用示例

演示 kernel.storage 的核心用法：
- 保存数据到 JSON 文件
- 从 JSON 文件加载数据
- 检查数据是否存在
- 删除数据
- 列出所有存储的数据
- 使用自定义存储目录

运行：
    uv run python examples/src/kernel/storage/storage_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.kernel.storage import JSONStore
from src.kernel.logger import get_logger, COLOR


async def main() -> None:
    """主函数"""
    logger = get_logger("storage_example", display="Storage", color=COLOR.CYAN)

    # 创建临时目录用于演示
    temp_dir = tempfile.mkdtemp()
    logger.info(f"使用临时存储目录: {temp_dir}\n")

    # 创建 JSONStore 实例（使用自定义目录）
    store = JSONStore(storage_dir=temp_dir)

    logger.print_panel("1. 保存数据")

    # 保存简单数据
    await store.save("user_12345", {"name": "Alice", "age": 30, "email": "alice@example.com"})
    logger.info("[OK] 保存用户数据: user_12345")

    # 保存插件配置
    await store.save("my_plugin_config", {
        "enabled": True,
        "probability": 0.5,
        "welcome_message": "欢迎使用我的插件！"
    })
    logger.info("[OK] 保存插件配置: my_plugin_config")

    # 保存复杂数据结构
    await store.save("chat_history_recent", {
        "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"},
            {"role": "user", "content": "介绍一下你自己"},
        ],
        "metadata": {
            "stream_id": "stream_abc123",
            "timestamp": 1234567890.0,
            "message_count": 3
        }
    })
    logger.info("[OK] 保存聊天历史: chat_history_recent")

    logger.print_panel("2. 检查数据是否存在")

    exists = await store.exists("user:12345")
    logger.info(f"[OK] user:12345 存在: {exists}")

    not_exists = await store.exists("nonexistent")
    logger.info(f"[OK] nonexistent 存在: {not_exists}")

    logger.print_panel("3. 加载数据")

    # 加载用户数据
    user_data = await store.load("user:12345")
    if user_data:
        logger.info(f"[OK] 用户名: {user_data['name']}")
        logger.info(f"[OK] 年龄: {user_data['age']}")
        logger.info(f"[OK] 邮箱: {user_data['email']}")

    # 加载插件配置
    plugin_config = await store.load("my_plugin:config")
    if plugin_config:
        logger.info(f"[OK] 插件启用状态: {plugin_config['enabled']}")
        logger.info(f"[OK] 激活概率: {plugin_config['probability']}")
        logger.info(f"[OK] 欢迎消息: {plugin_config['welcome_message']}")

    # 加载不存在的数据
    nonexistent_data = await store.load("nonexistent")
    logger.info(f"[OK] 不存在的数据返回: {nonexistent_data}")

    logger.print_panel("4. 列出所有存储的数据")

    all_data = await store.list_all()
    logger.info(f"[OK] 共有 {len(all_data)} 个数据项:")
    for name in sorted(all_data):
        logger.info(f"  - {name}")

    logger.print_panel("5. 更新数据（覆盖保存）")

    # 更新用户数据
    await store.save("user:12345", {"name": "Alice", "age": 31, "email": "alice.new@example.com"})
    logger.info("[OK] 更新用户年龄: 30 -> 31")
    logger.info("[OK] 更新用户邮箱: alice@example.com -> alice.new@example.com")

    # 验证更新
    updated_user = await store.load("user:12345")
    if updated_user:
        logger.info(f"[OK] 新年龄: {updated_user['age']}")
        logger.info(f"[OK] 新邮箱: {updated_user['email']}")

    logger.print_panel("6. 删除数据")

    # 删除数据
    deleted = await store.delete("user:12345")
    logger.info(f"[OK] 删除 user:12345: {deleted}")

    # 验证删除
    exists_after_delete = await store.exists("user:12345")
    logger.info(f"[OK] 删除后存在检查: {exists_after_delete}")

    # 再次删除（应该返回 False）
    deleted_again = await store.delete("user:12345")
    logger.info(f"[OK] 再次删除返回: {deleted_again}")

    # 列出剩余数据
    remaining_data = await store.list_all()
    logger.info(f"[OK] 剩余数据项: {remaining_data}")

    logger.print_panel("7. 特殊字符和 Unicode 支持")

    # 测试 Unicode 支持
    await store.save("unicode_test", {
        "chinese": "中文测试",
        "emoji": "😀🎉🚀",
        "arabic": "مرحبا بك",
        "russian": "Привет",
        "special": "换行\n测试\t制表符"
    })
    logger.info("[OK] 保存包含 Unicode 字符的数据")

    unicode_data = await store.load("unicode_test")
    if unicode_data:
        logger.info(f"[OK] 中文: {unicode_data['chinese']}")
        # 尝试打印，如果遇到编码错误则跳过
        try:
            logger.info(f"[OK] Emoji: {unicode_data['emoji']}")
        except UnicodeEncodeError:
            logger.info("[OK] Emoji: (无法在当前控制台显示)")
        try:
            logger.info(f"[OK] 阿拉伯文: {unicode_data['arabic']}")
        except UnicodeEncodeError:
            logger.info("[OK] 阿拉伯文: (无法在当前控制台显示)")
        try:
            logger.info(f"[OK] 俄文: {unicode_data['russian']}")
        except UnicodeEncodeError:
            logger.info("[OK] 俄文: (无法在当前控制台显示)")

    logger.print_panel("8. 安全性检查")

    # 测试路径遍历攻击防护
    try:
        await store.save("../etc/passwd", {"malicious": "data"})
        logger.error("[FAIL] 安全检查失败：应该抛出 ValueError")
    except ValueError as e:
        logger.info(f"[OK] 安全检查通过: {e}")

    try:
        await store.load("../../sensitive_file")
        logger.error("[FAIL] 安全检查失败：应该抛出 ValueError")
    except ValueError as e:
        logger.info(f"[OK] 安全检查通过: {e}")

    logger.info("\n" + "=" * 60)
    logger.info("演示完成！")
    logger.info("=" * 60)

    # 显示存储目录中的文件
    logger.info("\n存储目录中的文件:")
    storage_path = Path(temp_dir)
    json_files = list(storage_path.glob("*.json"))
    for json_file in sorted(json_files):
        file_size = json_file.stat().st_size
        logger.info(f"  - {json_file.name} ({file_size} bytes)")

    # 清理临时目录
    import shutil
    shutil.rmtree(temp_dir)
    logger.info(f"\n已清理临时目录: {temp_dir}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
