"""数据迁移脚本

将旧版 models.py 的数据迁移到新版 sql_alchemy.py 的表结构。

运行方式：
    python scripts/migrate_models.py
    
交互式引导用户输入旧数据库路径和目标数据库路径。
"""

import asyncio
import shutil
import sys
from pathlib import Path

from sqlalchemy import text

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.kernel.db import init_database_from_config, get_db_session
from src.kernel.logger import get_logger

logger = get_logger("migration", display="Migration")

# 存储交互式输入的 bot_id 信息
_bot_id_map: dict[str, str] = {}


def _parse_and_store_bot_ids(raw_input: str) -> None:
    """解析并存储用户输入的 bot_id。

    格式: "平台:ID,平台:ID"，如 "qq:1919810114,qq:1234567890"
    多个相同平台的 bot_id 只保留最后一个。

    Args:
        raw_input: 用户输入的原始字符串
    """
    _bot_id_map.clear()
    if not raw_input:
        return
    for pair in raw_input.split(","):
        pair = pair.strip()
        if ":" in pair:
            platform, bot_id = pair.split(":", 1)
            platform = platform.strip()
            bot_id = bot_id.strip()
            if platform and bot_id:
                _bot_id_map[platform] = bot_id


def _get_bot_ids() -> dict[str, str]:
    """获取已存储的 bot_id 映射。

    Returns:
        dict: {platform: bot_id}
    """
    return _bot_id_map


async def check_column_exists(session, table_name, column_name):
    """检查表中是否存在指定列"""
    db_type = session.bind.dialect.name
    if db_type == "postgresql":
        result = await session.execute(
            text("""
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_name = :table_name
                AND column_name = :column_name
            """),
            {"table_name": table_name, "column_name": column_name}
        )
        return (result.scalar() or 0) > 0
    else:  # SQLite
        result = await session.execute(text(f"PRAGMA table_info({table_name})"))
        columns = [row[1] for row in result.all()]
        return column_name in columns


async def migrate_person_info():
    """迁移用户信息

    旧版字段 -> 新版字段：
    - person_name -> 移除
    - name_reason -> 移除
    - know_times -> interaction_count
    - know_since -> first_interaction
    - last_know -> last_interaction
    - 新增：created_at, updated_at
    - 保留：platform, user_id, nickname, cardname, impression, short_impression, points, info_list, attitude
    """
    async with get_db_session() as session:
        db_type = session.bind.dialect.name

        # 1. 检查并添加新字段
        if db_type == "postgresql":
            # PostgreSQL 使用 ALTER TABLE ADD COLUMN
            new_columns = [
                ("cardname", "TEXT"),
                ("last_interaction", "FLOAT"),
                ("first_interaction", "FLOAT"),
                ("interaction_count", "INTEGER"),
                ("created_at", "FLOAT"),
                ("updated_at", "FLOAT"),
            ]

            for col_name, col_type in new_columns:
                # 检查列是否存在
                check_result = await session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM information_schema.columns
                        WHERE table_name = 'person_info'
                        AND column_name = :col_name
                    """),
                    [{"col_name": col_name}]
                )
                exists = check_result.scalar()

                if not exists:
                    await session.execute(
                        text(f"ALTER TABLE person_info ADD COLUMN {col_name} {col_type}")
                    )
                    logger.info(f"添加字段: person_info.{col_name}")

        else:  # SQLite
            # SQLite 支持直接添加列
            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN cardname TEXT"))
                logger.info("添加字段: person_info.cardname")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN last_interaction FLOAT"))
                logger.info("添加字段: person_info.last_interaction")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN first_interaction FLOAT"))
                logger.info("添加字段: person_info.first_interaction")
            except Exception:
                pass  # 字段可能已存在

            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN interaction_count INTEGER DEFAULT 0"))
                logger.info("添加字段: person_info.interaction_count")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN created_at FLOAT"))
                logger.info("添加字段: person_info.created_at")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE person_info ADD COLUMN updated_at FLOAT"))
                logger.info("添加字段: person_info.updated_at")
            except Exception:
                pass

        await session.commit()

        # 2. 迁移数据（从旧字段到新字段）- 只有在源字段存在时才迁移
        if db_type == "sqlite":
            # 检查 know_since 是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM sqlite_master
                    WHERE type='table' AND name='person_info'
                    AND sql LIKE '%know_since%'
                """)
            )
            know_since_exists = (check_result.scalar() or 0) > 0

            if know_since_exists:
                await session.execute(
                    text("""
                        UPDATE person_info
                        SET
                            first_interaction = know_since,
                            interaction_count = CAST(COALESCE(know_times, 0) AS INTEGER),
                            created_at = COALESCE(know_since, strftime('%s', 'now')),
                            updated_at = COALESCE(last_know, strftime('%s', 'now'))
                        WHERE first_interaction IS NULL
                    """)
                )
        else:  # PostgreSQL
            # 检查源字段是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'person_info'
                    AND column_name = 'know_since'
                """)
            )
            know_since_exists = (check_result.scalar() or 0) > 0

            if know_since_exists:
                await session.execute(
                    text("""
                        UPDATE person_info
                        SET
                            first_interaction = know_since,
                            interaction_count = COALESCE(know_times, 0),
                            created_at = COALESCE(know_since, EXTRACT(EPOCH FROM NOW())),
                            updated_at = COALESCE(last_know, EXTRACT(EPOCH FROM NOW()))
                        WHERE first_interaction IS NULL
                    """)
                )

        # 3. 确保 interaction_count 有默认值
        await session.execute(
            text("""
                UPDATE person_info
                SET interaction_count = 0
                WHERE interaction_count IS NULL
            """)
        )

        # 4. 确保 created_at 和 updated_at 有值
        if db_type == "sqlite":
            await session.execute(
                text("""
                    UPDATE person_info
                    SET
                        created_at = COALESCE(created_at, strftime('%s', 'now')),
                        updated_at = COALESCE(updated_at, strftime('%s', 'now'))
                    WHERE created_at IS NULL OR updated_at IS NULL
                """)
            )
        else:  # PostgreSQL
            await session.execute(
                text("""
                    UPDATE person_info
                    SET
                        created_at = COALESCE(created_at, EXTRACT(EPOCH FROM NOW())),
                        updated_at = COALESCE(updated_at, EXTRACT(EPOCH FROM NOW()))
                    WHERE created_at IS NULL OR updated_at IS NULL
                """)
            )

        await session.commit()

        # 5. 删除不需要的旧字段
        if db_type in ["postgresql", "sqlite"]:
            # 删除旧字段
            old_fields = [
                "person_name",  # 已不再使用
                "name_reason",  # 已不再使用
                "know_times",  # 已被 interaction_count 替代
                "know_since",  # 已被 first_interaction 替代
                "last_know",  # 已被 last_interaction 替代（如果有的话）
            ]

            for field in old_fields:
                # 检查字段是否存在
                exists = await check_column_exists(session, 'person_info', field)

                if exists:
                    try:
                        await session.execute(
                            text(f"ALTER TABLE person_info DROP COLUMN {field}")
                        )
                        logger.info(f"删除字段: person_info.{field}")
                    except Exception as e:
                        logger.warning(f"删除字段 person_info.{field} 失败: {e}")

        await session.commit()
        logger.info("PersonInfo 迁移完成：添加新字段、迁移数据、删除旧字段")


async def migrate_chat_streams():
    """迁移聊天流

    旧版字段 -> 新版字段：
    - create_time -> created_at
    - user_id, user_nickname, user_cardname -> 移除
    - 新增：person_id (由 platform + user_id 组合而成)
    - 新增：chat_type (根据 group_id 判断：private/group/discuss)
    - 移除：energy_value, sleep_pressure, focus_energy, base_interest_energy 等旧字段
    - 保留：stream_id, platform, group_id, group_name, last_active_time
    """
    async with get_db_session() as session:
        db_type = session.bind.dialect.name

        # 1. 检查并添加新字段
        if db_type == "postgresql":
            # PostgreSQL 使用 ALTER TABLE ADD COLUMN
            new_columns = [
                ("person_id", "VARCHAR(100)"),
                ("chat_type", "VARCHAR(20)"),
                ("created_at", "FLOAT"),
            ]

            for col_name, col_type in new_columns:
                # 检查列是否存在
                check_result = await session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM information_schema.columns
                        WHERE table_name = 'chat_streams'
                        AND column_name = :col_name
                    """),
                    [{"col_name": col_name}]
                )
                exists = check_result.scalar()

                if not exists:
                    await session.execute(
                        text(f"ALTER TABLE chat_streams ADD COLUMN {col_name} {col_type}")
                    )
                    logger.info(f"添加字段: chat_streams.{col_name}")

        else:  # SQLite
            # SQLite 支持直接添加列
            try:
                await session.execute(text("ALTER TABLE chat_streams ADD COLUMN person_id VARCHAR(100)"))
                logger.info("添加字段: chat_streams.person_id")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE chat_streams ADD COLUMN chat_type VARCHAR(20)"))
                logger.info("添加字段: chat_streams.chat_type")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE chat_streams ADD COLUMN created_at FLOAT"))
                logger.info("添加字段: chat_streams.created_at")
            except Exception:
                pass

        await session.commit()

        # 2. 生成 person_id (从 user_platform 和 user_id) - 使用 SHA-256 哈希
        # person_id 必须是 sha256(f"{platform}_{user_id}") 格式，
        # 与 user_query_helper.generate_person_id() 保持一致
        import hashlib

        if db_type == "postgresql":
            # 检查 user_platform 和 user_id 是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'chat_streams'
                    AND column_name IN ('user_platform', 'user_id')
                """)
            )
            source_fields_exist = (check_result.scalar() or 0) >= 2

            if source_fields_exist:
                rows = await session.execute(
                    text("""
                        SELECT stream_id, user_platform, user_id
                        FROM chat_streams
                        WHERE person_id IS NULL AND user_id IS NOT NULL
                    """)
                )
                for row in rows.fetchall():
                    stream_id, platform, user_id = row
                    hashed = hashlib.sha256(f"{platform}_{user_id}".encode()).hexdigest()
                    await session.execute(
                        text("UPDATE chat_streams SET person_id = :pid WHERE stream_id = :sid"),
                        {"pid": hashed, "sid": stream_id},
                    )
        else:  # SQLite
            try:
                rows = await session.execute(
                    text("""
                        SELECT stream_id, user_platform, user_id
                        FROM chat_streams
                        WHERE person_id IS NULL AND user_id IS NOT NULL
                    """)
                )
                for row in rows.fetchall():
                    stream_id, platform, user_id = row
                    hashed = hashlib.sha256(f"{platform}_{user_id}".encode()).hexdigest()
                    await session.execute(
                        text("UPDATE chat_streams SET person_id = :pid WHERE stream_id = :sid"),
                        {"pid": hashed, "sid": stream_id},
                    )
            except Exception:
                pass  # 字段可能不存在，跳过

        # 3. 设置 chat_type（根据是否有 group_id 判断）
        await session.execute(
            text("""
                UPDATE chat_streams
                SET chat_type = CASE
                    WHEN group_id IS NULL OR group_id = '' THEN 'private'
                    ELSE 'group'
                END
                WHERE chat_type IS NULL OR chat_type = ''
            """)
        )

        # 4. 将 create_time 迁移到 created_at - 检查源字段是否存在
        if db_type == "postgresql":
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'chat_streams'
                    AND column_name = 'create_time'
                """)
            )
            create_time_exists = (check_result.scalar() or 0) > 0

            if create_time_exists:
                await session.execute(
                    text("""
                        UPDATE chat_streams
                        SET created_at = create_time
                        WHERE created_at IS NULL AND create_time IS NOT NULL
                    """)
                )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE chat_streams
                        SET created_at = create_time
                        WHERE created_at IS NULL AND create_time IS NOT NULL
                    """)
                )
            except Exception:
                pass  # 字段可能不存在，跳过

        # 5. 确保 created_at 有默认值
        if db_type == "sqlite":
            await session.execute(
                text("""
                    UPDATE chat_streams
                    SET created_at = COALESCE(created_at, strftime('%s', 'now'))
                    WHERE created_at IS NULL
                """)
            )
        else:  # PostgreSQL
            await session.execute(
                text("""
                    UPDATE chat_streams
                    SET created_at = COALESCE(created_at, EXTRACT(EPOCH FROM NOW()))
                    WHERE created_at IS NULL
                """)
            )

        await session.commit()

        # 6. 删除不需要的旧字段
        if db_type in ["postgresql", "sqlite"]:
            # 删除旧字段
            old_fields = [
                "create_time",  # 已被 created_at 替代
                "user_id",
                "user_platform",  # 旧版用户平台字段，已整合到 person_id
                "user_nickname",
                "user_cardname",
                "energy_value",
                "sleep_pressure",
                "focus_energy",
                "base_interest_energy",
                "message_interest_total",
                "message_count",
                "action_count",
                "reply_count",
                "last_interaction_time",
                "consecutive_no_reply",
                "interruption_count",
                "stream_impression_text",
                "stream_chat_style",
                "stream_topic_keywords",
                "stream_interest_score",
            ]

            for field in old_fields:
                # 检查字段是否存在
                exists = await check_column_exists(session, 'chat_streams', field)

                if exists:
                    try:
                        await session.execute(
                            text(f"ALTER TABLE chat_streams DROP COLUMN {field}")
                        )
                        logger.info(f"删除字段: chat_streams.{field}")
                    except Exception as e:
                        logger.warning(f"删除字段 chat_streams.{field} 失败: {e}")

        await session.commit()
        logger.info("ChatStreams 迁移完成：添加新字段、迁移数据、删除旧字段")


async def migrate_messages():
    """迁移消息

    旧版字段 -> 新版字段：
    - chat_id -> stream_id
    - 移除所有 chat_info_* 字段
    - 移除所有 user_* 字段
    - 移除：interest_value, key_words, key_words_lite, is_mentioned 等旧字段
    - 新增：message_id (使用原 message_id 字段作为唯一标识)
    - 新增：person_id (从 user_platform + user_id 生成)
    - 新增：message_type (默认为 'text')
    - 新增：content (使用 processed_plain_text 或 display_message)
    - 保留：time, reply_to, processed_plain_text
    """
    async with get_db_session() as session:
        db_type = session.bind.dialect.name

        # 1. 将旧字段改为 nullable（避免新插入时出错）
        if db_type == "postgresql":
            # 检查并修改 chat_id 字段为 nullable
            check_result = await session.execute(
                text("""
                    SELECT is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'messages'
                    AND column_name = 'chat_id'
                """)
            )
            nullable = check_result.scalar()

            if nullable == 'NO':
                await session.execute(
                    text("ALTER TABLE messages ALTER COLUMN chat_id DROP NOT NULL")
                )
                logger.info("将 messages.chat_id 改为 nullable")

        await session.commit()

        # 2. 检查并添加新字段
        if db_type == "postgresql":
            # PostgreSQL 使用 ALTER TABLE ADD COLUMN
            new_columns = [
                ("stream_id", "VARCHAR(64)"),
                ("person_id", "VARCHAR(100)"),
                ("message_type", "VARCHAR(20)"),
                ("content", "TEXT"),
                ("platform", "VARCHAR(50)"),
            ]

            for col_name, col_type in new_columns:
                # 检查列是否存在
                check_result = await session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM information_schema.columns
                        WHERE table_name = 'messages'
                        AND column_name = :col_name
                    """),
                    [{"col_name": col_name}]
                )
                exists = check_result.scalar()

                if not exists:
                    await session.execute(
                        text(f"ALTER TABLE messages ADD COLUMN {col_name} {col_type}")
                    )
                    logger.info(f"添加字段: messages.{col_name}")

        else:  # SQLite
            # SQLite 支持直接添加列
            try:
                await session.execute(text("ALTER TABLE messages ADD COLUMN stream_id VARCHAR(64)"))
                logger.info("添加字段: messages.stream_id")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE messages ADD COLUMN person_id VARCHAR(100)"))
                logger.info("添加字段: messages.person_id")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE messages ADD COLUMN message_type VARCHAR(20)"))
                logger.info("添加字段: messages.message_type")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE messages ADD COLUMN content TEXT"))
                logger.info("添加字段: messages.content")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE messages ADD COLUMN platform VARCHAR(50)"))
                logger.info("添加字段: messages.platform")
            except Exception:
                pass

        await session.commit()

        # 3. 生成 person_id（必须在表重建前完成，因为重建会移除 user_platform/user_id 列）
        # person_id 必须是 sha256(f"{platform}_{user_id}") 格式，
        # 与 user_query_helper.generate_person_id() 保持一致
        import hashlib

        if db_type == "postgresql":
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'messages'
                    AND column_name IN ('user_platform', 'user_id')
                """)
            )
            source_fields_exist = (check_result.scalar() or 0) >= 2

            if source_fields_exist:
                rows = await session.execute(
                    text("""
                        SELECT id, user_platform, user_id
                        FROM messages
                        WHERE person_id IS NULL AND user_id IS NOT NULL
                    """)
                )
                for row in rows.fetchall():
                    msg_id, platform, user_id = row
                    hashed = hashlib.sha256(f"{platform}_{user_id}".encode()).hexdigest()
                    await session.execute(
                        text("UPDATE messages SET person_id = :pid WHERE id = :mid"),
                        {"pid": hashed, "mid": msg_id},
                    )
                logger.info("PostgreSQL: 已为 messages 生成 person_id (SHA-256)")
        else:  # SQLite
            try:
                check_res = await session.execute(text("PRAGMA table_info(messages)"))
                cols = [r[1] for r in check_res.all()]
                if 'user_platform' in cols and 'user_id' in cols:
                    rows = await session.execute(
                        text("""
                            SELECT id, user_platform, user_id
                            FROM messages
                            WHERE person_id IS NULL AND user_id IS NOT NULL
                        """)
                    )
                    count = 0
                    for row in rows.fetchall():
                        msg_id, platform, user_id = row
                        hashed = hashlib.sha256(f"{platform}_{user_id}".encode()).hexdigest()
                        await session.execute(
                            text("UPDATE messages SET person_id = :pid WHERE id = :mid"),
                            {"pid": hashed, "mid": msg_id},
                        )
                        count += 1
                    logger.info(f"SQLite: 已为 {count} 条 messages 生成 person_id (SHA-256)")
            except Exception as e:
                logger.warning(f"生成 messages.person_id 失败: {e}")

        # 3.5 复制 user_platform 到 platform（必须在表重建前完成，重建会移除 user_platform 列）
        if db_type == "postgresql":
            if source_fields_exist:  # 复用上面的检查结果
                await session.execute(
                    text("""
                        UPDATE messages
                        SET platform = user_platform
                        WHERE platform IS NULL AND user_platform IS NOT NULL
                    """)
                )
                logger.info("PostgreSQL: 已复制 user_platform 到 platform")
        else:  # SQLite
            try:
                check_res2 = await session.execute(text("PRAGMA table_info(messages)"))
                cols2 = [r[1] for r in check_res2.all()]
                if 'user_platform' in cols2 and 'platform' in cols2:
                    await session.execute(
                        text("""
                            UPDATE messages
                            SET platform = user_platform
                            WHERE platform IS NULL AND user_platform IS NOT NULL
                        """)
                    )
                    logger.info("SQLite: 已复制 user_platform 到 platform")
            except Exception as e:
                logger.warning(f"复制 user_platform 到 platform 失败: {e}")

        # 3.6 为无 platform 的消息（如 bot 自己发的消息，旧库未存 user_platform）从所属 stream 补充
        if db_type == "postgresql":
            await session.execute(
                text("""
                    UPDATE messages m
                    SET platform = cs.platform
                    FROM chat_streams cs
                    WHERE m.stream_id = cs.stream_id
                    AND m.platform IS NULL
                    AND cs.platform IS NOT NULL
                """)
            )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET platform = (
                            SELECT cs.platform
                            FROM chat_streams cs
                            WHERE cs.stream_id = messages.stream_id
                        )
                        WHERE platform IS NULL
                        AND stream_id IS NOT NULL
                    """)
                )
            except Exception as e:
                logger.warning(f"从 stream 补充 platform 失败: {e}")
        logger.info("已从所属 chat_stream 补充缺失的 platform")

        # 3.7 为 bot 消息设置 person_id = "bot"（旧库中 bot 消息没有存 user_id，迁移后 person_id 为 NULL）
        bot_ids = _get_bot_ids()
        if bot_ids:
            for platform, bid in bot_ids.items():
                bot_person_id = hashlib.sha256(f"{platform}_{bid}".encode()).hexdigest()
                # 将 bot 的哈希 person_id 和 NULL person_id 统一设为 "bot"
                if db_type == "postgresql":
                    await session.execute(
                        text("""
                            UPDATE messages
                            SET person_id = 'bot'
                            WHERE (person_id IS NULL OR person_id = :bot_pid)
                            AND platform = :platform
                        """),
                        {"bot_pid": bot_person_id, "platform": platform},
                    )
                else:  # SQLite
                    await session.execute(
                        text("""
                            UPDATE messages
                            SET person_id = 'bot'
                            WHERE (person_id IS NULL OR person_id = :bot_pid)
                            AND platform = :platform
                        """),
                        {"bot_pid": bot_person_id, "platform": platform},
                    )
                logger.info(f"已为平台 {platform} 的 bot 消息设置 person_id='bot' (bot_id={bid})")
        else:
            logger.warning("未提供 bot_id，bot 消息的 person_id 将保持为 NULL")

        await session.commit()

        # 4. 将 chat_id 迁移到 stream_id - 检查源字段是否存在
        if db_type == "postgresql":
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'messages'
                    AND column_name = 'chat_id'
                """)
            )
            chat_id_exists = (check_result.scalar() or 0) > 0

            if chat_id_exists:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET stream_id = chat_id
                        WHERE stream_id IS NULL AND chat_id IS NOT NULL
                    """)
                )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET stream_id = chat_id
                        WHERE stream_id IS NULL AND chat_id IS NOT NULL
                    """)
                )

                # SQLite: 数据迁移后尝试立即删除 chat_id 以解除 NOT NULL 约束
                # 避免后续操作或运行时因缺少 chat_id 导致 IntegrityError
                try:
                    # 先尝试删除索引 idx_messages_chat_id (如果存在)
                    await session.execute(text("DROP INDEX IF EXISTS idx_messages_chat_id"))
                    # 再删除列
                    await session.execute(text("ALTER TABLE messages DROP COLUMN chat_id"))
                    logger.info("SQLite: 数据迁移后立即删除 messages.chat_id")
                except Exception as e:
                    logger.warning(f"SQLite: 尝试立即删除 chat_id 失败: {e}")

                    # 如果直接删除失败，尝试重建表策略（更稳健）
                    try:
                        logger.info("SQLite: 直接删除失败，尝试重建表策略...")
                        # 1. 创建新表
                        await session.execute(text("""
                            CREATE TABLE messages_new (
                                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                                message_id TEXT NOT NULL,
                                stream_id VARCHAR(64) NOT NULL,
                                person_id VARCHAR(100),
                                time FLOAT NOT NULL,
                                message_type VARCHAR(20) NOT NULL,
                                content TEXT NOT NULL,
                                processed_plain_text TEXT,
                                reply_to VARCHAR(100),
                                platform VARCHAR(50)
                            )
                        """))
                        # 2. 复制数据
                        await session.execute(text("""
                            INSERT INTO messages_new (
                                id, message_id, stream_id, person_id, time,
                                message_type, content, processed_plain_text, reply_to, platform
                            )
                            SELECT
                                id, message_id, stream_id, person_id, time,
                                COALESCE(message_type, 'text'),
                                COALESCE(content, processed_plain_text, ''),
                                processed_plain_text, reply_to, platform
                            FROM messages
                        """))
                        # 3. 删除旧表
                        await session.execute(text("DROP TABLE messages"))
                        # 4. 重命名新表
                        await session.execute(text("ALTER TABLE messages_new RENAME TO messages"))
                        
                        # 5. 重建索引
                        await session.execute(text("CREATE UNIQUE INDEX ix_messages_message_id ON messages (message_id)"))
                        await session.execute(text("CREATE INDEX ix_messages_stream_id ON messages (stream_id)"))
                        await session.execute(text("CREATE INDEX ix_messages_person_id ON messages (person_id)"))
                        await session.execute(text("CREATE INDEX ix_messages_time ON messages (time)"))
                        await session.execute(text("CREATE INDEX idx_messages_stream_time ON messages (stream_id, time)"))
                        
                        logger.info("SQLite: 通过重建表成功移除 chat_id")
                    except Exception as recreate_err:
                         logger.error(f"SQLite: 重建表失败: {recreate_err}")

            except Exception:
                pass  # 字段可能不存在

        # 5. 设置默认 message_type
        await session.execute(
            text("""
                UPDATE messages
                SET message_type = 'text'
                WHERE message_type IS NULL OR message_type = ''
            """)
        )

        # 6. 设置 content（优先使用 processed_plain_text，其次 display_message）- 检查源字段是否存在
        if db_type == "postgresql":
            # 检查 display_message 是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'messages'
                    AND column_name = 'display_message'
                """)
            )
            display_message_exists = (check_result.scalar() or 0) > 0

            if display_message_exists:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET content = COALESCE(
                            processed_plain_text,
                            display_message,
                            ''
                        )
                        WHERE content IS NULL OR content = ''
                    """)
                )
            else:
                # display_message 不存在，只使用 processed_plain_text
                await session.execute(
                    text("""
                        UPDATE messages
                        SET content = COALESCE(processed_plain_text, '')
                        WHERE content IS NULL OR content = ''
                    """)
                )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET content = COALESCE(
                            processed_plain_text,
                            display_message,
                            ''
                        )
                        WHERE content IS NULL OR content = ''
                    """)
                )
            except Exception:
                # display_message 不存在，只使用 processed_plain_text
                await session.execute(
                    text("""
                        UPDATE messages
                        SET content = COALESCE(processed_plain_text, '')
                        WHERE content IS NULL OR content = ''
                    """)
                )

        # 7. 添加 platform 字段（从 chat_info_platform）- 检查源字段是否存在
        if db_type == "postgresql":
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'messages'
                    AND column_name = 'chat_info_platform'
                """)
            )
            chat_info_platform_exists = (check_result.scalar() or 0) > 0

            if chat_info_platform_exists:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET platform = chat_info_platform
                        WHERE (platform IS NULL OR platform = '') AND chat_info_platform IS NOT NULL
                    """)
                )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE messages
                        SET platform = chat_info_platform
                        WHERE (platform IS NULL OR platform = '') AND chat_info_platform IS NOT NULL
                    """)
                )
            except Exception:
                pass  # 字段可能不存在，跳过

        # 8. 删除不需要的旧字段
        if db_type in ["postgresql", "sqlite"]:
            # 删除所有 chat_info_* 字段
            old_chat_info_fields = [
                "chat_info_stream_id",
                "chat_info_platform",
                "chat_info_user_platform",
                "chat_info_user_id",
                "chat_info_user_nickname",
                "chat_info_user_cardname",
                "chat_info_group_platform",
                "chat_info_group_id",
                "chat_info_group_name",
                "chat_info_create_time",
                "chat_info_last_active_time",
            ]

            # 删除所有 user_* 字段
            old_user_fields = [
                "user_platform",
                "user_id",
                "user_nickname",
                "user_cardname",
            ]

            # 删除其他旧字段
            old_other_fields = [
                "chat_id",  # 已被 stream_id 替代
                "display_message",  # 已被 content 替代
                "memorized_times",  # 新模型不再使用
                "priority_mode",  # 新模型不再使用
                "priority_info",  # 新模型不再使用
                "additional_config",  # 新模型不再使用
                "is_emoji",  # 新模型不再使用
                "is_picid",  # 新模型不再使用
                "is_command",  # 新模型不再使用
                "is_notify",  # 新模型不再使用
                "actions",  # 新模型不再使用
                "should_reply",  # 新模型不再使用
                "interest_degree",  # 新模型不再使用
                "should_act",  # 新模型不再使用
                "is_public_notice",  # 新模型不再使用
                "notice_type",  # 新模型不再使用
                "is_notice",  # 新模型不再使用
                "interest_value",  # 新模型不再使用
                "key_words",  # 新模型不再使用
                "key_words_lite",  # 新模型不再使用
                "is_mentioned",  # 新模型不再使用
            ]

            all_old_fields = old_chat_info_fields + old_user_fields + old_other_fields

            for field in all_old_fields:
                # 检查字段是否存在
                exists = await check_column_exists(session, 'messages', field)

                if exists:
                    try:
                        await session.execute(
                            text(f"ALTER TABLE messages DROP COLUMN {field}")
                        )
                        logger.info(f"删除字段: messages.{field}")
                    except Exception as e:
                        logger.warning(f"删除字段 messages.{field} 失败: {e}")

        await session.commit()
        logger.info("Messages 迁移完成：添加新字段、迁移数据、删除旧字段")


async def migrate_action_records():
    """迁移动作记录

    旧版字段 -> 新版字段：
    - chat_id -> stream_id
    - 移除：chat_info_stream_id, chat_info_platform
    - 新增：person_id (从关联的 chat_streams 获取)
    - 保留：action_id, time, action_name, action_data, action_done,
            action_build_into_prompt, action_prompt_display
    """
    async with get_db_session() as session:
        db_type = session.bind.dialect.name

        # 1. 将旧字段改为 nullable（避免新插入时出错）
        if db_type == "postgresql":
            # 检查并修改 chat_id 字段为 nullable
            check_result = await session.execute(
                text("""
                    SELECT is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'action_records'
                    AND column_name = 'chat_id'
                """)
            )
            nullable = check_result.scalar()

            if nullable == 'NO':
                await session.execute(
                    text("ALTER TABLE action_records ALTER COLUMN chat_id DROP NOT NULL")
                )
                logger.info("将 action_records.chat_id 改为 nullable")

        await session.commit()

        # 2. 检查并添加新字段
        if db_type == "postgresql":
            # 检查 stream_id 列是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'action_records'
                    AND column_name = 'stream_id'
                """)
            )
            stream_id_exists = (check_result.scalar() or 0) > 0

            if not stream_id_exists:
                await session.execute(
                    text("ALTER TABLE action_records ADD COLUMN stream_id VARCHAR(64)")
                )
                logger.info("添加字段: action_records.stream_id")

            # 检查 person_id 列是否存在
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'action_records'
                    AND column_name = 'person_id'
                """)
            )
            person_id_exists = (check_result.scalar() or 0) > 0

            if not person_id_exists:
                await session.execute(
                    text("ALTER TABLE action_records ADD COLUMN person_id VARCHAR(100)")
                )
                logger.info("添加字段: action_records.person_id")

        else:  # SQLite
            try:
                await session.execute(text("ALTER TABLE action_records ADD COLUMN stream_id VARCHAR(64)"))
                logger.info("添加字段: action_records.stream_id")
            except Exception:
                pass

            try:
                await session.execute(text("ALTER TABLE action_records ADD COLUMN person_id VARCHAR(100)"))
                logger.info("添加字段: action_records.person_id")
            except Exception:
                pass

        await session.commit()

        # 3. 将 chat_id 迁移到 stream_id - 检查源字段是否存在
        if db_type == "postgresql":
            check_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = 'action_records'
                    AND column_name = 'chat_id'
                """)
            )
            chat_id_exists = (check_result.scalar() or 0) > 0

            if chat_id_exists:
                await session.execute(
                    text("""
                        UPDATE action_records
                        SET stream_id = chat_id
                        WHERE stream_id IS NULL AND chat_id IS NOT NULL
                    """)
                )
        else:  # SQLite
            try:
                await session.execute(
                    text("""
                        UPDATE action_records
                        SET stream_id = chat_id
                        WHERE stream_id IS NULL AND chat_id IS NOT NULL
                    """)
                )

                # SQLite: 数据迁移后尝试立即删除 chat_id 以解除 NOT NULL 约束
                try:
                    # 先删除引用 chat_id 的索引
                    await session.execute(text("DROP INDEX IF EXISTS ix_action_records_chat_id"))
                    # 再删除列
                    await session.execute(text("ALTER TABLE action_records DROP COLUMN chat_id"))
                    logger.info("SQLite: 数据迁移后立即删除 action_records.chat_id")
                except Exception as e:
                    logger.warning(f"SQLite: 尝试立即删除 chat_id 失败: {e}")

                    # 如果直接删除失败，尝试重建表策略（更稳健）
                    try:
                        logger.info("SQLite: 直接删除失败，尝试重建表策略...")
                        # 1. 创建新表（结构参照 ActionRecords 模型定义）
                        await session.execute(text("""
                            CREATE TABLE action_records_new (
                                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                                action_id VARCHAR(100) NOT NULL,
                                stream_id VARCHAR(64) NOT NULL,
                                person_id VARCHAR(100),
                                time FLOAT NOT NULL,
                                action_name TEXT NOT NULL,
                                action_data TEXT NOT NULL,
                                action_done BOOLEAN NOT NULL DEFAULT 0,
                                action_build_into_prompt BOOLEAN NOT NULL DEFAULT 0,
                                action_prompt_display TEXT NOT NULL
                            )
                        """))
                        # 2. 从旧表复制数据
                        await session.execute(text("""
                            INSERT INTO action_records_new (
                                id, action_id, stream_id, person_id, time,
                                action_name, action_data, action_done,
                                action_build_into_prompt, action_prompt_display
                            )
                            SELECT
                                id, action_id, stream_id, person_id, time,
                                action_name, action_data,
                                COALESCE(action_done, 0),
                                COALESCE(action_build_into_prompt, 0),
                                COALESCE(action_prompt_display, '')
                            FROM action_records
                        """))
                        # 3. 删除旧表
                        await session.execute(text("DROP TABLE action_records"))
                        # 4. 重命名新表
                        await session.execute(text("ALTER TABLE action_records_new RENAME TO action_records"))

                        # 5. 重建索引（参照 ActionRecords.__table_args__）
                        await session.execute(text("CREATE INDEX idx_actionrecords_action_id ON action_records (action_id)"))
                        await session.execute(text("CREATE INDEX idx_actionrecords_stream_id ON action_records (stream_id)"))
                        await session.execute(text("CREATE INDEX idx_actionrecords_time ON action_records (time)"))
                        await session.execute(text("CREATE INDEX idx_actionrecords_stream_time ON action_records (stream_id, time)"))

                        logger.info("SQLite: 通过重建表成功移除 action_records.chat_id")
                    except Exception as recreate_err:
                        logger.error(f"SQLite: 重建 action_records 表失败: {recreate_err}")

            except Exception:
                pass  # 字段可能不存在，跳过

        # 4. 添加 person_id（从关联的 chat_streams 获取）
        await session.execute(
            text("""
                UPDATE action_records
                SET person_id = (
                    SELECT person_id
                    FROM chat_streams
                    WHERE chat_streams.stream_id = action_records.stream_id
                )
                WHERE person_id IS NULL AND stream_id IS NOT NULL
            """)
        )

        await session.commit()

        # 5. 删除不需要的旧字段
        if db_type in ["postgresql", "sqlite"]:
            # 删除旧字段
            old_fields = [
                "chat_id",  # 已被 stream_id 替代
                "chat_info_stream_id",
                "chat_info_platform",
            ]

            for field in old_fields:
                # 检查字段是否存在
                exists = await check_column_exists(session, 'action_records', field)

                if exists:
                    try:
                        await session.execute(
                            text(f"ALTER TABLE action_records DROP COLUMN {field}")
                        )
                        logger.info(f"删除字段: action_records.{field}")
                    except Exception as e:
                        logger.warning(f"删除字段 action_records.{field} 失败: {e}")

        await session.commit()
        logger.info("ActionRecords 迁移完成：添加新字段、迁移数据、删除旧字段")


async def migrate_images():
    """迁移图像信息

    旧版字段 -> 新版字段：
    - emoji_hash -> 移除（新版本不再关联 emoji）
    - 保留：image_id, description, path, count, timestamp, type, vlm_processed
    """
    async with get_db_session() as session:
        # 新版本的 images 表结构与旧版基本兼容
        # 只需要确保所有必需字段都有默认值
        await session.execute(
            text("""
                UPDATE images
                SET image_id = COALESCE(image_id, '')
                WHERE image_id IS NULL
            """)
        )

        await session.commit()
        logger.info("Images 迁移完成：确保必需字段有默认值")


async def migrate_image_descriptions():
    """迁移图像描述信息

    新版本的 image_descriptions 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("ImageDescriptions 迁移完成：表结构未变化")


async def migrate_online_time():
    """迁移在线时长记录

    新版本的 online_time 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("OnlineTime 迁移完成：表结构未变化")


async def migrate_ban_users():
    """迁移封禁用户

    新版本的 ban_users 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("BanUsers 迁移完成：表结构未变化")


async def migrate_permission_nodes():
    """迁移权限节点

    新版本的 permission_nodes 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("PermissionNodes 迁移完成：表结构未变化")


async def migrate_user_permissions():
    """迁移用户权限

    新版本的 user_permissions 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("UserPermissions 迁移完成：表结构未变化")


async def migrate_llm_usage():
    """迁移 LLM 使用记录

    新版本的 llm_usage 表结构保持不变
    """
    async with get_db_session() as session:
        # 表结构未变化，无需迁移
        pass

    await session.commit() if 'session' in locals() else None
    logger.info("LLMUsage 迁移完成：表结构未变化")


async def verify_migration():
    """验证迁移结果"""
    async with get_db_session() as session:
        logger.info("\n" + "=" * 60)
        logger.info("验证迁移结果...")
        logger.info("=" * 60)

        # 1. 检查 PersonInfo
        result = await session.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(first_interaction) as with_first,
                    COUNT(interaction_count) as with_count,
                    COUNT(created_at) as with_created,
                    COUNT(updated_at) as with_updated
                FROM person_info
            """)
        )
        row = result.fetchone()
        if row:
            logger.info(
                f"PersonInfo: 总数={row[0]}, "
                f"有first_interaction={row[1]}, "
                f"有interaction_count={row[2]}, "
                f"有created_at={row[3]}, "
                f"有updated_at={row[4]}"
            )

        # 2. 检查 ChatStreams
        result = await session.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(person_id) as with_person,
                    COUNT(chat_type) as with_type,
                    COUNT(created_at) as with_created
                FROM chat_streams
            """)
        )
        row = result.fetchone()
        if row:
            logger.info(
                f"ChatStreams: 总数={row[0]}, "
                f"有person_id={row[1]}, "
                f"有chat_type={row[2]}, "
                f"有created_at={row[3]}"
            )

        # 3. 检查 Messages
        result = await session.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(stream_id) as with_stream,
                    COUNT(person_id) as with_person,
                    COUNT(message_type) as with_type,
                    COUNT(content) as with_content,
                    COUNT(platform) as with_platform
                FROM messages
            """)
        )
        row = result.fetchone()
        if row:
            logger.info(
                f"Messages: 总数={row[0]}, "
                f"有stream_id={row[1]}, "
                f"有person_id={row[2]}, "
                f"有message_type={row[3]}, "
                f"有content={row[4]}, "
                f"有platform={row[5]}"
            )

        # 4. 检查 ActionRecords
        result = await session.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(stream_id) as with_stream,
                    COUNT(person_id) as with_person
                FROM action_records
            """)
        )
        row = result.fetchone()
        if row:
            logger.info(
                f"ActionRecords: 总数={row[0]}, "
                f"有stream_id={row[1]}, "
                f"有person_id={row[2]}"
            )


async def migrate_emoji():
    """清理 emoji 表冗余旧字段。

    移除 query_count、emotion、record_time 三个已被新字段替代的列。
    """
    drop_columns = ["query_count", "emotion", "record_time"]

    async with get_db_session() as session:
        db_type = session.bind.dialect.name

        # 先检查 emoji 表是否存在
        if db_type == "postgresql":
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_name = 'emoji'"
                )
            )
        else:  # SQLite
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='emoji'"
                )
            )
        if (result.scalar() or 0) == 0:
            logger.info("emoji 表不存在，跳过")
            return

        dropped = []
        for col in drop_columns:
            exists = await check_column_exists(session, "emoji", col)
            if not exists:
                logger.debug(f"emoji.{col} 已不存在，跳过")
                continue

            if db_type == "postgresql":
                await session.execute(
                    text(f"ALTER TABLE emoji DROP COLUMN IF EXISTS {col}")
                )
                dropped.append(col)
            else:  # SQLite >= 3.35.0 支持 DROP COLUMN
                try:
                    await session.execute(
                        text(f"ALTER TABLE emoji DROP COLUMN {col}")
                    )
                    dropped.append(col)
                except Exception as e:
                    logger.warning(
                        f"SQLite 删除 emoji.{col} 失败（版本可能过低）: {e}"
                    )

        await session.commit()

    if dropped:
        logger.info(f"Emoji 迁移完成：已删除列 {', '.join(dropped)}")
    else:
        logger.info("Emoji 迁移完成：无需删除列")


async def run_all_migrations():
    """运行所有迁移"""
    logger.info("=" * 60)
    logger.info("开始数据迁移...")
    logger.info("从旧版 models.py 迁移到新版 sql_alchemy.py")
    logger.info("=" * 60)

    try:
        # 1. 迁移用户信息
        logger.info("\n[1/13] 迁移 PersonInfo...")
        await migrate_person_info()

        # 2. 迁移聊天流
        logger.info("\n[2/13] 迁移 ChatStreams...")
        await migrate_chat_streams()

        # 3. 迁移消息
        logger.info("\n[3/13] 迁移 Messages...")
        await migrate_messages()

        # 4. 迁移动作记录
        logger.info("\n[4/13] 迁移 ActionRecords...")
        await migrate_action_records()

        # 5. 迁移图像信息
        logger.info("\n[5/13] 迁移 Images...")
        await migrate_images()

        # 6. 迁移图像描述
        logger.info("\n[6/13] 迁移 ImageDescriptions...")
        await migrate_image_descriptions()

        # 7. 迁移在线时长
        logger.info("\n[7/13] 迁移 OnlineTime...")
        await migrate_online_time()

        # 8. 迁移封禁用户
        logger.info("\n[8/13] 迁移 BanUsers...")
        await migrate_ban_users()

        # 9. 迁移权限节点
        logger.info("\n[9/13] 迁移 PermissionNodes...")
        await migrate_permission_nodes()

        # 10. 迁移用户权限
        logger.info("\n[10/13] 迁移 UserPermissions...")
        await migrate_user_permissions()

        # 11. 迁移 LLM 使用记录
        logger.info("\n[11/13] 迁移 LLMUsage...")
        await migrate_llm_usage()

        # 12. 清理 Emoji 旧字段
        logger.info("\n[12/13] 清理 Emoji 旧字段...")
        await migrate_emoji()

        # 13. 验证迁移结果
        logger.info("\n[13/13] 验证迁移结果...")
        await verify_migration()

        logger.info("\n" + "=" * 60)
        logger.info("✅ 数据迁移完成！")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ 数据迁移失败：{e}", exc_info=True)
        raise


async def main_async(db_path: str):
    """异步主函数

    Args:
        db_path: 要迁移的数据库文件绝对路径
    """
    await init_database_from_config(
        database_type="sqlite",
        sqlite_path=db_path,
    )

    # 运行迁移
    await run_all_migrations()


def main():
    """交互式主函数"""
    print("=" * 60)
    print("  Neo-MoFox 数据库迁移工具")
    print("=" * 60)
    print()

    # 1. 输入旧数据库路径
    old_db = input("请输入旧数据库的绝对路径: ").strip().strip('"').strip("'")
    old_path = Path(old_db)
    if not old_path.is_file():
        print(f"❌ 文件不存在: {old_path}")
        sys.exit(1)

    # 2. 输入目标路径
    default_target = str(project_root / "data" / "MoFox.db")
    target_db = input(f"请输入迁移目标路径 [默认: {default_target}]: ").strip().strip('"').strip("'")
    if not target_db:
        target_db = default_target
    target_path = Path(target_db)

    # 3. 如果旧库和目标不同，复制一份到目标
    if old_path.resolve() != target_path.resolve():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, target_path)
        print(f"✅ 已复制数据库: {old_path} → {target_path}")
    else:
        print(f"📂 原地迁移: {target_path}")

    # 4. 输入 bot 信息（用于填充旧库 bot 消息的 person_id）
    print()
    print("旧数据库中 bot 发送的消息没有存储发送者信息，需要提供 bot 的 ID 来补充。")
    print("格式: 平台:ID，多个用逗号分隔。例如: qq:3905802962,qq:1234567890")
    print("直接回车跳过（bot 消息将显示为未知用户）")
    bot_input = input("请输入 bot ID: ").strip()
    _parse_and_store_bot_ids(bot_input)

    # 5. 确认
    print()
    print(f"  数据库路径: {target_path}")
    confirm = input("确认开始迁移? [Y/n]: ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("已取消。")
        sys.exit(0)

    print()
    asyncio.run(main_async(str(target_path)))


if __name__ == "__main__":
    main()
