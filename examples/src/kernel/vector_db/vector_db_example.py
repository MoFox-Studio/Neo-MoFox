"""Vector DB 模块使用示例

演示 kernel.vector_db 的核心用法：
- 获取或创建集合 (Collection)
- 添加向量数据
- 相似度查询
- 元数据过滤
- 获取和删除数据
- 统计集合条目数
- 删除集合

运行：
    uv run python examples/src/kernel/vector_db/vector_db_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.kernel.vector_db import get_vector_db_service, create_vector_db_service
from src.kernel.logger import get_logger, COLOR


async def main() -> None:
    """主函数"""
    logger = get_logger("vector_db_example", display="VectorDB", color=COLOR.MAGENTA)

    # 创建临时目录用于演示
    temp_dir = tempfile.mkdtemp()
    logger.info(f"使用临时数据库目录: {temp_dir}\n")

    # 创建向量数据库服务实例
    vector_db = create_vector_db_service(db_path=temp_dir)

    logger.print_panel("1. 获取或创建集合")

    # 获取或创建集合
    collection = await vector_db.get_or_create_collection(name="semantic_cache")
    logger.info("[OK] 成功获取或创建集合: semantic_cache")

    collection2 = await vector_db.get_or_create_collection(name="memory")
    logger.info("[OK] 成功获取或创建集合: memory")

    logger.print_panel("2. 添加向量数据")

    # 添加简单的语义缓存数据
    await vector_db.add(
        collection_name="semantic_cache",
        embeddings=[[0.1, 0.2, 0.3, 0.4, 0.5]],
        documents=["这是一个人类发送的消息"],
        metadatas=[{"stream_id": "12345", "timestamp": 1234567890.0}],
        ids=["msg_001"]
    )
    logger.info("[OK] 添加消息到 semantic_cache")

    # 批量添加记忆数据
    await vector_db.add(
        collection_name="memory",
        embeddings=[
            [0.11, 0.21, 0.31, 0.41, 0.51],
            [0.12, 0.22, 0.32, 0.42, 0.52],
            [0.13, 0.23, 0.33, 0.43, 0.53],
        ],
        documents=[
            "用户询问如何使用Python",
            "我介绍了Python的基本语法",
            "用户想要了解更多关于列表的知识"
        ],
        metadatas=[
            {"type": "user_message", "timestamp": 1234567890.0},
            {"type": "assistant_message", "timestamp": 1234567891.0},
            {"type": "user_message", "timestamp": 1234567892.0},
        ],
        ids=["mem_001", "mem_002", "mem_003"]
    )
    logger.info("[OK] 批量添加3条记忆到 memory 集合")

    logger.print_panel("3. 统计集合条目数")

    # 统计条目数
    count1 = await vector_db.count(collection_name="semantic_cache")
    logger.info(f"[OK] semantic_cache 集合条目数: {count1}")

    count2 = await vector_db.count(collection_name="memory")
    logger.info(f"[OK] memory 集合条目数: {count2}")

    logger.print_panel("4. 相似度查询")

    # 查询相似的向量
    results = await vector_db.query(
        collection_name="memory",
        query_embeddings=[[0.115, 0.215, 0.315, 0.415, 0.515]],
        n_results=2
    )

    if results:
        logger.info("[OK] 查询结果:")
        logger.info(f"  - IDs: {results.get('ids', [])}")
        logger.info(f"  - 距离: {results.get('distances', [])}")
        logger.info(f"  - 文档: {results.get('documents', [])}")
        logger.info(f"  - 元数据: {results.get('metadatas', [])}")

    logger.print_panel("5. 元数据过滤查询")

    # 使用元数据过滤
    filtered_results = await vector_db.query(
        collection_name="memory",
        query_embeddings=[[0.12, 0.22, 0.32, 0.42, 0.52]],
        n_results=5,
        where={"type": "user_message"}
    )

    if filtered_results:
        logger.info("[OK] 过滤 type=user_message 的查询结果:")
        logger.info(f"  - 找到 {len(filtered_results.get('ids', [[]])[0])} 条记录")
        logger.info(f"  - IDs: {filtered_results.get('ids', [])}")
        logger.info(f"  - 文档: {filtered_results.get('documents', [])}")

    logger.print_panel("6. 根据ID获取数据")

    # 根据ID获取数据
    get_results = await vector_db.get(
        collection_name="memory",
        ids=["mem_001", "mem_003"],
        include=["documents", "metadatas", "embeddings"]
    )

    if get_results:
        logger.info("[OK] 根据 ID 获取的数据:")
        logger.info(f"  - IDs: {get_results.get('ids', [])}")
        logger.info(f"  - 文档: {get_results.get('documents', [])}")
        logger.info(f"  - 元数据: {get_results.get('metadatas', [])}")

    # 使用 where 条件获取数据
    get_filtered = await vector_db.get(
        collection_name="memory",
        where={"type": "assistant_message"},
        include=["documents", "metadatas"]
    )

    if get_filtered:
        logger.info("[OK] 根据 where 条件获取的数据:")
        logger.info(f"  - 找到 {len(get_filtered.get('ids', []))} 条记录")
        logger.info(f"  - IDs: {get_filtered.get('ids', [])}")

    logger.print_panel("7. 更新数据（通过覆盖实现）")

    # 先删除旧数据，再添加新数据（模拟更新）
    await vector_db.delete(
        collection_name="semantic_cache",
        ids=["msg_001"]
    )

    await vector_db.add(
        collection_name="semantic_cache",
        embeddings=[[0.1, 0.2, 0.3, 0.4, 0.5]],
        documents=["这是一个更新后的消息内容"],
        metadatas=[{"stream_id": "12345", "timestamp": 1234567895.0, "updated": True}],
        ids=["msg_001"]
    )

    # 验证更新
    updated = await vector_db.get(
        collection_name="semantic_cache",
        ids=["msg_001"]
    )

    if updated and updated.get("documents"):
        logger.info("[OK] 更新后的文档:")
        logger.info(f"  - {updated['documents'][0]}")
        logger.info(f"  - 元数据: {updated.get('metadatas', [[]])[0]}")

    logger.print_panel("8. 根据条件删除数据")

    # 根据元数据条件删除
    await vector_db.delete(
        collection_name="memory",
        where={"type": "assistant_message"}
    )

    remaining_count = await vector_db.count(collection_name="memory")
    logger.info(f"[OK] 删除 assistant_message 后剩余条目数: {remaining_count}")

    logger.print_panel("9. 获取服务实例缓存")

    # 使用默认路径获取服务（会缓存实例）
    vector_db_default = get_vector_db_service()
    logger.info(f"[OK] 获取默认向量数据库服务: {type(vector_db_default).__name__}")

    # 使用自定义路径获取服务
    vector_db_custom = get_vector_db_service(db_path=temp_dir)
    logger.info(f"[OK] 获取自定义路径的向量数据库服务: {type(vector_db_custom).__name__}")

    # 创建新实例（不走缓存）
    vector_db_new = create_vector_db_service(db_path=temp_dir)
    logger.info(f"[OK] 创建新的向量数据库服务实例: {type(vector_db_new).__name__}")

    logger.print_panel("10. 删除集合")

    # 删除集合
    await vector_db.delete_collection(name="semantic_cache")
    logger.info("[OK] 删除集合: semantic_cache")

    # 尝试获取已删除的集合（会创建新的空集合）
    new_collection = await vector_db.get_or_create_collection(name="semantic_cache")
    new_count = await vector_db.count(collection_name="semantic_cache")
    logger.info(f"[OK] 重新创建的集合条目数: {new_count}")

    logger.print_panel("11. 复杂元数据过滤")

    # 添加更多测试数据
    await vector_db.add(
        collection_name="memory",
        embeddings=[
            [0.14, 0.24, 0.34, 0.44, 0.54],
            [0.15, 0.25, 0.35, 0.45, 0.55],
        ],
        documents=[
            "用户询问关于字典的问题",
            "我介绍了字典的基本用法"
        ],
        metadatas=[
            {"type": "user_message", "timestamp": 1234567893.0, "topic": "dict"},
            {"type": "assistant_message", "timestamp": 1234567894.0, "topic": "dict"},
        ],
        ids=["mem_004", "mem_005"]
    )

    # 使用多个条件过滤（$and 逻辑）
    multi_filter_results = await vector_db.query(
        collection_name="memory",
        query_embeddings=[[0.15, 0.25, 0.35, 0.45, 0.55]],
        n_results=5,
        where={"type": "user_message", "topic": "dict"}
    )

    if multi_filter_results:
        logger.info("[OK] 多条件过滤 (type=user_message AND topic=dict):")
        logger.info(f"  - 找到 {len(multi_filter_results.get('ids', [[]])[0])} 条记录")

    logger.info("\n" + "=" * 60)
    logger.info("演示完成！")
    logger.info("=" * 60)

    # 显示集合信息
    logger.info("\n当前集合状态:")
    final_count = await vector_db.count(collection_name="memory")
    logger.info(f"  - memory 集合: {final_count} 条数据")

    # 关闭数据库连接
    await vector_db.close()
    logger.info("\n向量数据库连接已关闭")

    # 清理临时目录
    import shutil
    shutil.rmtree(temp_dir)
    logger.info(f"已清理临时目录: {temp_dir}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
