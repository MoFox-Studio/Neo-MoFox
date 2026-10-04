"""Neo4j Episode 图适配器；驱动是可选依赖，默认不连接外部服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Neo4jUnavailableError(RuntimeError):
    """Neo4j 驱动或连接配置不可用。"""


class Neo4jEpisodeGraph:
    """将 Episode 关系映射为 Neo4j 节点和关系。"""

    def __init__(self, uri: str, user: str, password: str, *, database: str = "neo4j") -> None:
        """保存连接信息但不立即建立网络连接。"""
        if not uri.strip() or not user.strip() or not password:
            raise ValueError("Neo4j uri/user/password 不能为空")
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver: Any = None

    async def connect(self) -> None:
        """加载官方异步驱动并验证连接。"""
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as error:
            raise Neo4jUnavailableError("未安装可选依赖 neo4j") from error
        self._driver = AsyncGraphDatabase.driver(self.uri, auth=(self.user, self.password))
        await self._driver.verify_connectivity()

    async def close(self) -> None:
        """关闭驱动连接。"""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def upsert_episode(
        self,
        episode_id: str,
        properties: Mapping[str, object],
        relations: tuple[tuple[str, str, float], ...] = (),
    ) -> None:
        """写入 Episode 节点及有界关系边。"""
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j 尚未连接")
        async with self._driver.session(database=self.database) as session:
            await session.run(
                "MERGE (e:Episode {episode_id: $episode_id}) SET e += $properties",
                episode_id=episode_id,
                properties=dict(properties),
            )
            for target_id, relation_type, weight in relations:
                if not target_id.strip() or not relation_type.strip():
                    continue
                await session.run(
                    """MATCH (a:Episode {episode_id: $source_id}), (b:Episode {episode_id: $target_id})
                    MERGE (a)-[r:RELATED {relation_type: $relation_type}]->(b)
                    SET r.weight = $weight""",
                    source_id=episode_id,
                    target_id=target_id,
                    relation_type=relation_type,
                    weight=float(weight),
                )

    async def related_episode_ids(self, episode_id: str, *, hops: int = 2, limit: int = 20) -> tuple[str, ...]:
        """读取最多两跳的关联 Episode ID。"""
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j 尚未连接")
        if hops not in {1, 2} or limit <= 0:
            raise ValueError("hops 必须为 1/2，limit 必须大于 0")
        async with self._driver.session(database=self.database) as session:
            result = await session.run(
                f"""MATCH (a:Episode {{episode_id: $episode_id}})-[:RELATED*1..{hops}]->(b:Episode)
                RETURN DISTINCT b.episode_id AS episode_id LIMIT $limit""",
                episode_id=episode_id,
                limit=limit,
            )
            records = await result.data()
        return tuple(str(row["episode_id"]) for row in records if row.get("episode_id"))
