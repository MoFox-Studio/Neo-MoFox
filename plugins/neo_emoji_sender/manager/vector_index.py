"""表情包向量索引。

直接复用 emoji_sender 的检索语义：每个 ``(emoji_id, tag)`` 一条向量，
同一表情的所有 tag 共用同一份 description embedding。metadata 不保存
文件路径，路径始终以 SQLite 为准。
"""

from __future__ import annotations

from typing import TypedDict, cast

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    get_model_set_by_task,
)
from src.kernel.logger import get_logger
from src.kernel.vector_db import VectorDBBase, get_vector_db_service

from .utils.vector_response import extract_ids
from .state import IndexMeta, StateStore
from .types import VectorHit

logger = get_logger("neo_emoji_sender.vector")

_SCAN_PAGE_LIMIT = 512
_MAX_SCAN_PAGES = 1000


class EmojiVectorMetadata(TypedDict):
    """单条向量 metadata（每个 ``(emoji_id, tag)`` 一条）。"""

    emoji_id: str
    tag: str
    description: str
    revision: str
    updated_at: float


class EmojiVectorIndex:
    """Chroma 向量索引封装。"""

    def __init__(self, *, db_path: str, collection_name: str, state: StateStore) -> None:
        self._db_path = db_path
        self.collection = collection_name
        self._state = state

    def _vdb(self) -> VectorDBBase:
        """获取本插件使用的向量数据库服务。

        Returns:
            连接本插件数据目录的向量数据库服务实例。
        """
        return get_vector_db_service(self._db_path)

    async def initialize(self) -> None:
        """创建集合并校验 / 写入索引元信息。"""
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        await self._ensure_index_meta()

    async def embed(self, text: str, request_name: str = "neo_emoji_sender_embedding") -> list[float] | None:
        """生成文本 embedding。

        未配置 embedding 任务模型或请求失败时返回 None，由调用方决定降级策略。

        Args:
            text: 待编码文本。
            request_name: 请求名，用于日志与限流统计。

        Returns:
            向量列表；失败时返回 None。
        """
        value = str(text or "").strip()
        if not value:
            return None
        try:
            model_set = get_model_set_by_task("embedding")
        except Exception as e:
            logger.warning(f"未配置 embedding 任务模型: {e}")
            return None
        try:
            request = create_embedding_request(
                model_set=model_set,
                request_name=request_name,
                inputs=[value],
            )
            response = await request.send()
            return list(response.embeddings[0])
        except Exception as e:
            logger.warning(f"生成 embedding 失败: {e}")
            return None

    async def upsert(
        self,
        *,
        emoji_id: str,
        description: str,
        tags: list[str],
        revision: str,
        updated_at: float,
    ) -> bool:
        """重建某个表情包的全部 tag 向量。

        以 ``(emoji_id, tag)`` 为向量粒度，同一描述只计算一次 embedding。

        Args:
            emoji_id: 表情包完整 ID。
            description: 表情包描述，作为 embedding 输入。
            tags: 情感标签列表，每个标签生成一条向量。
            revision: 内容指纹，写入 metadata 供对账比对。
            updated_at: 更新时间戳，写入 metadata。

        Returns:
            重建成功返回 True；无标签、embedding 失败或写入校验失败返回 False。
        """
        # 去重并排序：tags 相同则向量条数确定，避免不同来源传入顺序造成多余副本
        clean_tags = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
        if not clean_tags:
            return False

        # 一份 description 的 embedding 供该表情包全部 tag 复用：
        # 同描述不同 tag 只是检索标签不同，向量本身不必重算
        embedding = await self.embed(description)
        if embedding is None:
            return False

        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        # 先删后加：改标签后旧 tag 的向量不会残留，否则对账会判成"多余 tag"
        try:
            await vdb.delete(collection_name=self.collection, where={"emoji_id": emoji_id})
        except Exception as e:
            logger.warning(f"删除旧向量失败: {emoji_id[:8]}... - {e}")
            return False

        ids = [f"{emoji_id}:{tag}" for tag in clean_tags]
        metadatas: list[EmojiVectorMetadata] = [
            EmojiVectorMetadata(
                emoji_id=emoji_id,
                tag=tag,
                description=description,
                revision=revision,
                updated_at=float(updated_at),
            )
            for tag in clean_tags
        ]
        try:
            await vdb.add(
                collection_name=self.collection,
                ids=ids,
                embeddings=[list(embedding) for _ in clean_tags],
                documents=[description for _ in clean_tags],
                metadatas=metadatas,
            )
        except Exception as e:
            logger.warning(f"写入向量失败: {emoji_id[:8]}... - {e}")
            return False

        # 包装层会吞掉 add 异常，必须回查确认。
        try:
            data = await vdb.get(
                collection_name=self.collection,
                where={"emoji_id": emoji_id},
                limit=len(ids) + 1,
                include=["metadatas"],
            )
        except Exception as e:
            logger.warning(f"确认向量写入失败: {emoji_id[:8]}... - {e}")
            return False
        stored = extract_ids(data)
        if stored is None:
            logger.warning(f"确认向量写入失败: {emoji_id[:8]}... 向量库响应无效")
            return False
        return set(ids).issubset(set(stored))

    async def delete(self, emoji_id: str) -> bool:
        """删除某个表情包的全部向量。

        包装层会吞掉异常，因此删除后必须回查确认确实无残留。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            确认无残留返回 True；删除或回查失败返回 False。
        """
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        try:
            await vdb.delete(collection_name=self.collection, where={"emoji_id": emoji_id})
        except Exception as e:
            logger.warning(f"删除向量失败: {emoji_id[:8]}... - {e}")
            return False
        try:
            data = await vdb.get(
                collection_name=self.collection,
                where={"emoji_id": emoji_id},
                limit=1,
                include=["metadatas"],
            )
        except Exception as e:
            logger.warning(f"确认向量删除失败: {emoji_id[:8]}... - {e}")
            return False
        # 回查失败时包装层返回 {}，不能当成"已无残留"确认成功
        ids = extract_ids(data)
        if ids is None:
            logger.warning(f"确认向量删除失败: {emoji_id[:8]}... 向量库响应无效")
            return False
        return not ids

    async def search(
        self,
        *,
        query: str,
        tags: list[str] | None,
        limit: int,
        exclude_ids: set[str] | None = None,
    ) -> list[VectorHit]:
        """向量检索并按 emoji_id 去重，保留最小距离。

        Args:
            query: 检索文本。
            tags: 标签过滤集合；None 表示不过滤。
            limit: 返回结果数量上限。
            exclude_ids: 需要排除的表情包 ID 集合。

        Returns:
            按距离升序排列的命中列表。
        """
        text = str(query or "").strip()
        if not text:
            return []
        embedding = await self.embed(text, "neo_emoji_sender_search")
        if embedding is None:
            return []

        exclude = set(exclude_ids or ())
        # 多取一些再按 emoji_id 去重：同一表情包有多个 tag 就是多条向量，
        # 只取 limit 条会导致去重后候选不足。被排除的 ID 不减少召回量，
        # 因此额外加上 exclude 数量；上限 200 防止过度放大检索开销
        fetch_n = min(max((limit + len(exclude)) * 3, 20), 200)
        where = {"tag": list(tags)} if tags else None

        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        results = await vdb.query(
            collection_name=self.collection,
            query_embeddings=[embedding],
            n_results=fetch_n,
            where=where,
        )

        ids_list: list[list[str]] = list(results.get("ids") or [])
        distances_list: list[list[float]] = list(results.get("distances") or [])
        metadatas_list = cast(list[list[EmojiVectorMetadata]], results.get("metadatas") or [])
        if not ids_list or not ids_list[0]:
            return []

        # 同一 emoji_id 可能命中多个 tag，只保留距离最小的那条
        best: dict[str, VectorHit] = {}
        for index, _ in enumerate(ids_list[0]):
            distance = 999.0
            if distances_list and distances_list[0] and index < len(distances_list[0]):
                distance = float(distances_list[0][index])
            meta = (
                metadatas_list[0][index]
                if metadatas_list and metadatas_list[0] and index < len(metadatas_list[0])
                else {}
            )
            emoji_id = str(meta.get("emoji_id") or "").strip()
            tag = str(meta.get("tag") or "").strip()
            if not emoji_id or not tag or emoji_id in exclude:
                continue
            existing = best.get(emoji_id)
            if existing is None or distance < existing.distance:
                best[emoji_id] = VectorHit(emoji_id=emoji_id, tag=tag, distance=distance)

        ordered = sorted(best.values(), key=lambda item: item.distance)
        return ordered[: max(0, int(limit))]

    async def scan(self) -> dict[str, list[EmojiVectorMetadata]] | None:
        """扫描全部向量 metadata，按 emoji_id 分组。

        底层向量库读取失败时返回的 ``{}`` 与"没有更多记录"的空页无法区分，
        因此任一页判定为失败就整体返回 None。调用方（对账器）据此中止本轮
        向量修复：拿部分扫描结果做依据会把 N 条健康记录误判成缺向量，
        恢复后触发 N 次 embedding 请求。

        Returns:
            emoji_id 到该表情包全部向量 metadata 的映射；
            任一页读取失败时返回 None。
        """
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        grouped: dict[str, list[EmojiVectorMetadata]] = {}
        # 游标分页遍历整库：Chroma 单次 get 有上限，一次性取全在向量多时会截断，
        # 截断会让对账误判成"向量缺失"并重复重建
        offset = 0
        for _page in range(_MAX_SCAN_PAGES):
            data = await vdb.get(
                collection_name=self.collection,
                limit=_SCAN_PAGE_LIMIT,
                offset=offset,
                include=["metadatas"],
            )
            ids = extract_ids(data)
            if ids is None:
                logger.warning(f"扫描向量集合失败（offset={offset}），本轮不做向量修复")
                return None
            if not ids:
                break
            metadatas = cast(list[EmojiVectorMetadata], data.get("metadatas") or [])
            for index, record_id in enumerate(ids):
                meta = metadatas[index] if index < len(metadatas) else {}
                emoji_id = str(meta.get("emoji_id") or "").strip()
                # metadata 缺失时回退到向量 ID 前缀（格式 emoji_id:tag），
                # 保证早期或手工写入的向量仍能被归到正确的表情包下
                if not emoji_id:
                    emoji_id = record_id.split(":", 1)[0].strip()
                if emoji_id:
                    grouped.setdefault(emoji_id, []).append(dict(meta))
            offset += len(ids)
        else:
            logger.warning(
                f"向量集合扫描超过 {_MAX_SCAN_PAGES} 页仍未结束，本轮不做向量修复"
            )
            return None
        return grouped

    async def count(self) -> int:
        """统计向量条数。

        Returns:
            集合内向量总数；查询失败时返回 0。
        """
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        try:
            return int(await vdb.count(self.collection))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # index meta
    # ------------------------------------------------------------------

    async def _ensure_index_meta(self) -> None:
        """校验 embedding 模型指纹；不一致时清空集合并重建。"""
        current = await self._state.get_index_meta()
        expected = self._current_meta()
        # 首次运行只需落盘指纹；此后模型一换就必须整体重建——
        # 不同 embedding 模型的向量不可比，混在一个集合里检索结果会失真
        if current is None:
            await self._state.set_index_meta(expected)
            return
        if current.get("embedding_model") == expected.get("embedding_model"):
            return
        logger.warning(
            "embedding 模型已变化，清空向量集合并重建: "
            f"{current.get('embedding_model')!r} -> {expected.get('embedding_model')!r}"
        )
        # 重建只清集合不重建向量：清空后对账器会按 DB 逐条补齐，
        # 这样加载阶段不必发起大量 embedding 请求、拖慢启动
        vdb = self._vdb()
        await vdb.delete_collection(self.collection)
        await vdb.get_or_create_collection(self.collection)
        await self._state.set_index_meta(expected)

    def _current_meta(self) -> IndexMeta:
        """读取当前 embedding 配置指纹。

        从当前 embedding 任务模型推导，未配置时模型名留空。

        Returns:
            当前环境的索引元信息。
        """
        model_name = ""
        try:
            model_set = get_model_set_by_task("embedding")
            if model_set:
                model_name = str(model_set[0].get("model_identifier") or "")
        except Exception:
            pass
        return IndexMeta(embedding_model=model_name, collection=self.collection)



__all__ = ["EmojiVectorIndex", "EmojiVectorMetadata"]
