"""使用临时 SQLite 数据库演示前馈式记忆检索的每轮必然激活与复述学习。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from ..vnext.domain import CreateMemoryInput, EvidenceInput, SubjectInput, WriteContext
from ..vnext.enums import ActorType, EvidenceSourceType, MemoryKind, SubjectKind
from ..vnext.feedforward_service import FeedForwardRetrievalService, FeedForwardSettings
from ..vnext.memory_service import MemoryService
from ..vnext.retrieval_service import RetrievalService, VectorSearchBackend
from ..vnext.schema import VNextSchema


class _LexicalOnly(VectorSearchBackend):
    """示例不连接向量模型，只使用词法与结构化召回。"""


async def main() -> None:
    """写入两条记忆，连续三轮前馈并打印激活明细。"""
    with TemporaryDirectory() as directory:
        schema = VNextSchema(str(Path(directory) / "feedforward.db"))
        await schema.initialize()
        try:
            now = datetime.now(UTC)
            memories = MemoryService(schema, "example-embedding")
            for title, content, kind in (
                ("咖啡失眠", "小明喝咖啡之后会失眠。", MemoryKind.FACT),
                ("喜欢茶", "小明很喜欢喝茶。", MemoryKind.PREFERENCE),
            ):
                await memories.create_memory(
                    CreateMemoryInput(
                        title=title,
                        content=content,
                        memory_kind=kind,
                        subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
                        observed_at=now - timedelta(days=3),
                        evidence=(
                            EvidenceInput(
                                EvidenceSourceType.ACTOR_WRITE,
                                now - timedelta(days=3),
                                note="示例来源",
                            ),
                        ),
                    ),
                    WriteContext(ActorType.ADMIN),
                )
            service = FeedForwardRetrievalService(
                schema,
                RetrievalService(schema, _LexicalOnly()),
                FeedForwardSettings(noise_scale=0.0),
            )
            for turn, text in enumerate(("我又喝咖啡了", "所以晚上怎么办", "还是喜欢喝茶")):
                moment = now + timedelta(minutes=turn)
                service.perceive("demo", text, person_id="person-1", observed_at=moment)
                cue = service.build_cue("demo", chat_type="private", now=moment)
                if cue is None:
                    continue
                result = await service.feed_forward(cue)
                print(f"第 {turn + 1} 轮：{text}（候选 {result.candidate_count}）")
                for item in result.selected:
                    parts = item.activation
                    print(
                        f"  {item.title}  P={item.probability:.2f}  "
                        f"B={parts.base_level:.2f} S={parts.spreading:.2f} "
                        f"T={item.transformer_score:.2f}  通道={','.join(item.channels)}"
                    )
            print("L1/L2 统计：", service.layers.snapshot())
        finally:
            await schema.close()


if __name__ == "__main__":
    asyncio.run(main())
