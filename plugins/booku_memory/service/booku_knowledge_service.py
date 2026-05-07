"""Booku Knowledge Service 实现。"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .booku_memory_service import BookuMemoryService
from .metadata_repository import BookuMemoryMetadataRepository
from src.core.components.base.service import BaseService
from src.core.prompt import get_system_reminder_store
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

from ..config import BookuMemoryConfig

logger = get_logger("booku_knowledge_service")

_KNOWLEDGE_REMINDER_BUCKET = "actor"
_KNOWLEDGE_REMINDER_NAME = "专业知识引导语"
_SEMANTIC_CONTINUE_SIMILARITY = 0.68
_SEMANTIC_MIN_TITLE_MERGE_SIMILARITY = 0.56
_SHORT_CHUNK_RATIO = 0.35
_TITLE_MAX_CHARS = 64


@dataclass(slots=True)
class _ChunkDraft:
    """知识块草稿，包含标题与正文。"""

    title: str
    content: str


def _create_memory_service(plugin: Any) -> BookuMemoryService:
    """构建并返回绑定到指定插件实例的记忆服务对象。

    Args:
        plugin: 当前工具所属的插件实例，会被传递给 BookuMemoryService 构造函数。
            类型使用 Any 是因为工具基类未对 plugin 字段强制类型，实际运行时
            始终为 BasePlugin 子类实例。

    Returns:
        BookuMemoryService: 与该插件绑定的记忆服务实例。
    """
    return BookuMemoryService(plugin=plugin)


def _normalize_document_title(title: str) -> str:
    """将片段标题规整为文档标题。"""
    cleaned = title.strip()
    if "》-片段" in cleaned:
        cleaned = cleaned.split("》-片段", 1)[0] + "》"
    return cleaned


def _collect_unique_titles(records: list[Any]) -> list[str]:
    """从知识记录中提取去重后的文档标题。"""
    titles: list[str] = []
    for record in records:
        title = _normalize_document_title(str(getattr(record, "title", "") or ""))
        if title and title not in titles:
            titles.append(title)
    return titles


def _collect_unique_titles_from_strings(raw_titles: list[str]) -> list[str]:
    """从字符串列表中提取去重后的文档标题（去掉片段后缀）。"""
    titles: list[str] = []
    for raw in raw_titles:
        title = _normalize_document_title(raw)
        if title and title not in titles:
            titles.append(title)
    return titles


def _sanitize_title(title: str) -> str:
    """清理文档标题，确保其符合存储要求。

    1. 移除首尾空格
    2. 限制长度为 100 个字符
    3. 如果为空，返回默认值 "未命名文档"

    Args:
        title: 原始文档标题

    Returns:
        清理后的标题字符串
    """
    text = title.strip()
    if not text:
        return "未命名文档"
    return text[:100]


def _normalize_text(text: str) -> str:
    """标准化文本内容，移除多余换行符。

    1. 替换 Windows 换行符 (\r\n) 为 Unix 换行符 (\n)
    2. 替换 Mac 换行符 (\r) 为 Unix 换行符 (\n)
    3. 合并多个连续换行符为最多 2 个换行符
    4. 移除首尾空格

    Args:
        text: 原始文本内容

    Returns:
        标准化后的文本字符串
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _extract_docx_text(path: Path) -> str:
    """从 DOCX 文件中提取文本内容。

    1. 打开 ZIP 文件，读取 document.xml
    2. 解析 XML，提取所有段落文本
    3. 合并段落，保留段落间空行

    Args:
        path: DOCX 文件路径

    Returns:
        提取到的文本内容字符串

    Raises:
        FileNotFoundError: 若文件不存在
        PermissionError: 若文件权限不足
        zipfile.BadZipFile: 若文件不是有效 DOCX 文件
    """
    with zipfile.ZipFile(path, "r") as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        texts = [node.text for node in paragraph.findall(".//w:t", ns) if node.text]
        line = "".join(texts).strip()
        if line:
            parts.append(line)
    return "\n".join(parts)


def _split_document_into_chunks(
    text: str,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[str]:
    """将文档文本拆分成多个文本块（段落为单位）。

    1. 标准化文本，移除多余换行符
    2. 按段落分割，保留段落间空行
    3. 每个块最大字符数不超过 max_chunk_chars
    4. 块与块之间重叠 overlap_chars 个字符

    Args:
        text: 原始文档文本内容
        max_chunk_chars: 每个块最大字符数
        overlap_chars: 块与块之间重叠字符数

    Returns:
        拆分后的文本块列表，每个元素为一个段落或多个段落的组合
    """
    normalized = _normalize_text(text)
    if not normalized:
        return []

    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()
    ]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        next_candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(next_candidate) <= max_chunk_chars:
            current = next_candidate
            continue

        if current:
            chunks.append(current)
        if len(paragraph) <= max_chunk_chars:
            current = paragraph
            continue

        start = 0
        while start < len(paragraph):
            end = min(len(paragraph), start + max_chunk_chars)
            piece = paragraph[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= len(paragraph):
                break
            step = max(1, max_chunk_chars - max(0, overlap_chars))
            start += step
        current = ""

    if current:
        chunks.append(current)

    compacted: list[str] = []
    for chunk in chunks:
        cleaned = _normalize_text(chunk)
        if cleaned:
            compacted.append(cleaned)
    return compacted


def _split_markdown_by_structure(
    text: str,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[_ChunkDraft]:
    """按 Markdown 标题结构切分文档。"""
    normalized = _normalize_text(text)
    if not normalized:
        return []

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    heading_stack: list[str] = []
    current_section_title = "导言"
    section_lines: list[str] = []
    section_items: list[tuple[str, str]] = []

    def flush_section() -> None:
        section_body = "\n".join(section_lines).strip()
        if section_body:
            section_items.append((current_section_title, section_body))

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        matched = heading_pattern.match(line)
        if not matched:
            section_lines.append(line)
            continue

        flush_section()
        section_lines = []
        level = len(matched.group(1))
        heading_text = matched.group(2).strip("# ").strip()
        if not heading_text:
            heading_text = f"章节{level}"

        while len(heading_stack) >= level:
            heading_stack.pop()
        heading_stack.append(heading_text)
        current_section_title = "：".join(heading_stack)

    flush_section()

    chunk_items: list[_ChunkDraft] = []
    for section_title, section_body in section_items:
        pieces = _split_document_into_chunks(
            section_body,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        )
        for index, piece in enumerate(pieces, start=1):
            title = section_title if len(pieces) == 1 else f"{section_title}（{index}）"
            chunk_items.append(_ChunkDraft(title=_sanitize_title(title), content=piece))

    return chunk_items


def _split_text_to_semantic_units(text: str) -> list[str]:
    """将文档拆分为语义单元（优先段落，其次句子）。"""
    normalized = _normalize_text(text)
    if not normalized:
        return []

    units: list[str] = []
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()
    ]
    sentence_pattern = re.compile(r"(?<=[。！？!?；;\.])\s+")
    for paragraph in paragraphs:
        if len(paragraph) <= 260:
            units.append(paragraph)
            continue
        sentences = [s.strip() for s in sentence_pattern.split(paragraph) if s.strip()]
        if len(sentences) <= 1:
            units.append(paragraph)
            continue
        units.extend(sentences)
    return units


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """计算两条向量的余弦相似度。"""
    if not vec1 or not vec2:
        return 0.0
    length = min(len(vec1), len(vec2))
    if length <= 0:
        return 0.0
    dot = sum(vec1[i] * vec2[i] for i in range(length))
    norm1 = math.sqrt(sum(vec1[i] * vec1[i] for i in range(length)))
    norm2 = math.sqrt(sum(vec2[i] * vec2[i] for i in range(length)))
    if norm1 <= 1e-12 or norm2 <= 1e-12:
        return 0.0
    return dot / (norm1 * norm2)


def _mean_embedding(vectors: list[list[float]]) -> list[float]:
    """计算向量集合的平均向量。"""
    if not vectors:
        return []
    base_len = len(vectors[0])
    if base_len == 0:
        return []
    sums = [0.0] * base_len
    for vector in vectors:
        if len(vector) < base_len:
            continue
        for idx in range(base_len):
            sums[idx] += vector[idx]
    count = max(1, len(vectors))
    return [value / count for value in sums]


def _build_semantic_title(text: str) -> str:
    """根据块内容生成简洁标题。"""
    normalized = _normalize_text(text)
    if not normalized:
        return "未命名片段"

    first_line = normalized.split("\n", 1)[0].strip()
    if len(first_line) >= 8:
        head = re.sub(r"[\s\t]+", " ", first_line)[:_TITLE_MAX_CHARS]
    else:
        head = ""
    keywords = _extract_keywords(normalized, limit=3)
    if keywords:
        kw = " / ".join(keywords)
        title = f"{head} | {kw}" if head else kw
    else:
        title = head or "知识片段"
    return _sanitize_title(title[:_TITLE_MAX_CHARS])


async def _split_document_semantically(
    text: str,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
    memory_service: BookuMemoryService,
) -> list[_ChunkDraft]:
    """通过句段 embedding 相似度进行语义分块。"""
    units = _split_text_to_semantic_units(text)
    if not units:
        return []

    unit_vectors = [await memory_service._embed_text(unit) for unit in units]
    groups: list[list[str]] = []
    current_units: list[str] = [units[0]]
    current_vectors: list[list[float]] = [unit_vectors[0]]

    for idx in range(1, len(units)):
        unit = units[idx]
        vector = unit_vectors[idx]
        candidate = "\n\n".join([*current_units, unit])
        centroid = _mean_embedding(current_vectors)
        similarity = _cosine_similarity(centroid, vector)
        should_continue = (
            similarity >= _SEMANTIC_CONTINUE_SIMILARITY
            and len(candidate) <= max_chunk_chars
        )
        if should_continue:
            current_units.append(unit)
            current_vectors.append(vector)
            continue

        groups.append(current_units)
        current_units = [unit]
        current_vectors = [vector]

    if current_units:
        groups.append(current_units)

    drafts: list[_ChunkDraft] = []
    for group in groups:
        merged = "\n\n".join(group).strip()
        if not merged:
            continue
        pieces = _split_document_into_chunks(
            merged,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        )
        for piece in pieces:
            drafts.append(_ChunkDraft(title=_build_semantic_title(piece), content=piece))
    return drafts


async def _merge_short_chunks_by_title_similarity(
    chunks: list[_ChunkDraft],
    *,
    min_chunk_chars: int,
    memory_service: BookuMemoryService,
) -> list[_ChunkDraft]:
    """将过短块按标题语义相似度与相邻块合并，并更新标题摘要。"""
    if len(chunks) <= 1:
        return chunks

    working = [_ChunkDraft(title=item.title, content=item.content) for item in chunks]
    title_embedding_cache: dict[str, list[float]] = {}

    async def embed_title(text: str) -> list[float]:
        if text not in title_embedding_cache:
            title_embedding_cache[text] = await memory_service._embed_text(text)
        return title_embedding_cache[text]

    index = 0
    while index < len(working):
        current = working[index]
        if len(current.content) >= min_chunk_chars:
            index += 1
            continue

        candidate_positions: list[tuple[int, float]] = []
        current_vector = await embed_title(current.title)
        if index > 0:
            prev_vector = await embed_title(working[index - 1].title)
            candidate_positions.append(
                (index - 1, _cosine_similarity(current_vector, prev_vector))
            )
        if index + 1 < len(working):
            next_vector = await embed_title(working[index + 1].title)
            candidate_positions.append(
                (index + 1, _cosine_similarity(current_vector, next_vector))
            )

        if not candidate_positions:
            index += 1
            continue

        best_pos, best_score = max(candidate_positions, key=lambda item: item[1])
        if best_score < _SEMANTIC_MIN_TITLE_MERGE_SIMILARITY:
            index += 1
            continue

        left = min(index, best_pos)
        right = max(index, best_pos)
        merged_content = (
            f"{working[left].content}\n\n{working[right].content}".strip()
        )
        merged_title = _build_semantic_title(merged_content)
        working[left] = _ChunkDraft(title=merged_title, content=merged_content)
        working.pop(right)
        title_embedding_cache.pop(current.title, None)
        if left < index:
            index = max(0, left)

    return working


async def _build_document_chunks(
    *,
    text: str,
    is_markdown: bool,
    max_chunk_chars: int,
    overlap_chars: int,
    memory_service: BookuMemoryService,
) -> list[_ChunkDraft]:
    """按文档类型执行分块，再对短块进行语义合并。"""
    if is_markdown:
        drafts = _split_markdown_by_structure(
            text,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        )
        if not drafts:
            drafts = await _split_document_semantically(
                text,
                max_chunk_chars=max_chunk_chars,
                overlap_chars=overlap_chars,
                memory_service=memory_service,
            )
    else:
        drafts = await _split_document_semantically(
            text,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
            memory_service=memory_service,
        )
        if len(drafts) <= 1 and "\n\n" in _normalize_text(text):
            fallback_chunks = _split_document_into_chunks(
                text,
                max_chunk_chars=max_chunk_chars,
                overlap_chars=overlap_chars,
            )
            if len(fallback_chunks) > 1:
                drafts = [
                    _ChunkDraft(
                        title=_build_semantic_title(chunk),
                        content=chunk,
                    )
                    for chunk in fallback_chunks
                ]

    min_chunk_chars = min(
        max_chunk_chars,
        max(12, int(max_chunk_chars * _SHORT_CHUNK_RATIO)),
    )
    return await _merge_short_chunks_by_title_similarity(
        drafts,
        min_chunk_chars=min_chunk_chars,
        memory_service=memory_service,
    )


def _extract_keywords(text: str, *, limit: int = 8) -> list[str]:
    """从文本中提取关键词（英文单词和中文字符）。

    1. 移除首尾空格
    2. 限制关键词长度为 30 个字符
    3. 提取所有连续的英文单词（包含下划线）和中文字符（2-8 个字符）
    4. 去重并按出现顺序排序

    Args:
        text: 原始文本内容
        limit: 最多提取的关键词数量（默认 8 个）

    Returns:
        提取到的关键词列表，每个元素为一个英文单词或中文字符串
    """
    lowered = text.lower()
    english = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,30}", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    merged: list[str] = []
    for token in [*chinese, *english]:
        t = token.strip()
        if not t:
            continue
        if t not in merged:
            merged.append(t)
        if len(merged) >= limit:
            break
    return merged


async def build_booku_knowledge_actor_reminder(plugin: Any) -> str:
    """构建 booku_knowledge 插件的系统提醒内容。

    1. 从配置中获取存储路径
    2. 从数据库中检索所有知识记录
    3. 提取唯一的标题（移除片段标识）
    4. 格式化为 Markdown 列表

    Args:
        plugin: 插件实例，需包含 config 属性

    Returns:
        格式化后的系统提醒字符串，包含专业知识标题列表
    """
    config = getattr(plugin, "config", None)
    if not isinstance(config, BookuMemoryConfig):
        return ""
    if not config.plugin.inject_system_prompt:
        return ""

    repo = BookuMemoryMetadataRepository(db_path=config.storage.metadata_db_path)
    await repo.initialize()
    try:
        raw_titles = await repo.list_knowledge_chunk_titles(folder_id="default")
    finally:
        await repo.close()

    titles = _collect_unique_titles_from_strings(raw_titles)

    if not titles:
        return ""

    lines = "\n".join(f"- {item}" for item in titles)
    return (
        "## 知识检索引导\n"
        "以下是当前记忆内已学习的专业知识标题集合：\n"
        f"{lines}\n"
        "当你的回答需要涉及专业知识时，请优先调用 memory_command，使用 search -type knowledge -include_knowledge true 检索相关知识。\n"
        "不要把这些知识直接用于回答问题，不要暴露这些知识的标题，而应该根据问题的具体内容，从这些知识中提取相关信息。\n\n"
    )


async def sync_booku_knowledge_actor_reminder(plugin: Any) -> str:
    """同步 booku_knowledge 插件的系统提醒内容到全局存储。

    1. 调用 ``build_booku_knowledge_actor_reminder`` 构建提醒内容
    2. 若内容为空，删除全局存储中的记录
    3. 否则，更新全局存储中的记录

    Args:
        plugin: 插件实例，需包含 config 属性

    Returns:
        格式化后的系统提醒字符串，包含专业知识标题列表
    """
    store = get_system_reminder_store()
    content = await build_booku_knowledge_actor_reminder(plugin)
    if not content:
        store.delete(_KNOWLEDGE_REMINDER_BUCKET, _KNOWLEDGE_REMINDER_NAME)
        logger.info("booku_knowledge system reminder 已清理")
        return ""

    store.set(_KNOWLEDGE_REMINDER_BUCKET, _KNOWLEDGE_REMINDER_NAME, content)
    title_set = [
        line[2:].strip() for line in content.splitlines() if line.startswith("- ")
    ]
    logger.info(
        f"booku_knowledge system reminder 已同步，专业知识标题集合(count={len(title_set)}): {json.dumps(title_set, ensure_ascii=False)}"
    )
    return content


class BookuKnowledgeService(BaseService):
    """知识库服务，支持文档分块入库与语义检索。"""

    service_name: str = "booku_knowledge"
    service_description: str = "知识库服务，支持文档分块入库与语义检索"
    version: str = "1.0.0"
    dependencies: list[str] = []
    _memory_service: BookuMemoryService | None = None

    def _get_config(self) -> BookuMemoryConfig:
        """获取插件配置对象。

        若当前插件配置不是 ``BookuMemoryConfig`` 实例（如默认占位符），
        则创建并返回一个全默认值的新实例。

        Returns:
            插件配置对象（永远不为 None）。
        """
        if isinstance(self.plugin.config, BookuMemoryConfig):
            return self.plugin.config
        return BookuMemoryConfig()

    def _get_memory_service(self) -> BookuMemoryService:
        """获取与当前插件绑定的记忆服务实例。"""
        if self._memory_service is None:
            self._memory_service = _create_memory_service(plugin=self.plugin)
        return self._memory_service

    def _create_repo(self) -> BookuMemoryMetadataRepository:
        """创建知识库使用的元数据仓储实例。"""
        config = self._get_config()
        return BookuMemoryMetadataRepository(db_path=config.storage.metadata_db_path)

    async def _list_knowledge_records(self, *, limit: int) -> list[Any]:
        """列出知识库记录。"""
        repo = self._create_repo()
        await repo.initialize()
        try:
            return await repo.list_records_by_bucket(
                bucket="knowledge",
                folder_id="default",
                limit=max(1, int(limit)),
            )
        finally:
            await repo.close()

    async def ingest_document(
        self,
        *,
        title: str,
        content: str = "",
        file_path: str | None = None,
        source: str = "agent",
    ) -> dict[str, Any]:
        """入库文档到知识库。

        文档内容可直接传入 ``content`` 参数，也可通过 ``file_path`` 指向本地文件。
        若 ``title`` 为空，会根据 ``content`` 或 ``file_path`` 自动生成。

        Args:
            title: 文档标题，用于检索时显示。
            content: 文档原始文本内容。
            file_path: 指向本地文档文件的路径。
            source: 文档来源，默认 "agent"。

        Returns:
            包含文档 ID、分块数、向量索引状态等信息的字典。

        Raises:
            ValueError: 若 ``content`` 与 ``file_path`` 同时为空，或文档分块失败。
            FileNotFoundError: 若 ``file_path`` 指向的文件不存在。
            PermissionError: 若 ``file_path`` 指向的文件权限不足。
        """
        config = self._get_config()
        memory_service = self._get_memory_service()
        resolved_title = _sanitize_title(title)
        text = content.strip()
        resolved_path = Path(file_path).expanduser().resolve() if file_path else None
        is_markdown = False
        if not text and resolved_path is not None:
            if not resolved_path.exists() or not resolved_path.is_file():
                raise FileNotFoundError(f"文件不存在: {resolved_path}")
            suffix = resolved_path.suffix.lower()
            is_markdown = suffix in {".md", ".markdown"}
            if suffix in {".txt", ".md", ".markdown", ".json", ".csv", ".log"}:
                text = resolved_path.read_text(encoding="utf-8", errors="ignore")
            elif suffix == ".docx":
                text = _extract_docx_text(resolved_path)
            else:
                raise ValueError("仅支持 txt/md/json/csv/log/docx 文件上传")
            if not title.strip():
                resolved_title = _sanitize_title(resolved_path.stem)

        if not text.strip():
            raise ValueError("content 与 file_path 不能同时为空")

        chunk_drafts = await _build_document_chunks(
            text=text,
            is_markdown=is_markdown,
            max_chunk_chars=int(config.chunking.max_chunk_chars),
            overlap_chars=int(config.chunking.overlap_chars),
            memory_service=memory_service,
        )
        if not chunk_drafts:
            raise ValueError("文档分块失败，文本内容为空")

        doc_id = f"doc-{uuid.uuid4().hex}"
        bucket = "knowledge"
        folder_id = "default"
        collection = memory_service._collection_name(bucket, folder_id)
        vector_db = get_vector_db_service(config.storage.vector_db_path)
        repo = self._create_repo()
        await repo.initialize()
        try:
            embeddings: list[list[float]] = []
            for chunk in chunk_drafts:
                embeddings.append(await memory_service._embed_text(chunk.content))
                await asyncio.sleep(0.1)
            now = time.time()
            ids: list[str] = []
            docs: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for index, chunk in enumerate(chunk_drafts):
                chunk_id = f"kb-{doc_id}-{index + 1:04d}"
                chunk_title = f"《{resolved_title}》-片段{index + 1}：{chunk.title}"
                ids.append(chunk_id)
                docs.append(chunk.content)
                core_tags = _extract_keywords(
                    f"{resolved_title} {chunk.title} {chunk.content[:120]}", limit=8
                )
                diffusion_tags = (
                    _extract_keywords(chunk.content[:300], limit=8) or ["文档检索"]
                )
                opposing_tags = ["无关", "闲聊"]
                metadata = {
                    "title": chunk_title,
                    "bucket": bucket,
                    "folder_id": folder_id,
                    "source": source,
                    "timestamp": now,
                    "document_id": doc_id,
                    "document_title": f"《{resolved_title}》",
                    "chunk_title": chunk.title,
                    "chunk_index": index + 1,
                    "chunk_total": len(chunk_drafts),
                }
                metadatas.append(memory_service._sanitize_vector_metadata(metadata))
                await repo.upsert_record(
                    memory_id=chunk_id,
                    title=chunk_title,
                    folder_id=folder_id,
                    bucket=bucket,
                    content=chunk.content,
                    source=source,
                    novelty_energy=1.0,
                    tags=["booku_knowledge", resolved_title],
                    core_tags=core_tags or ["知识库"],
                    diffusion_tags=diffusion_tags,
                    opposing_tags=opposing_tags,
                )

            await vector_db.add(
                collection_name=collection,
                embeddings=embeddings,
                documents=docs,
                metadatas=metadatas,
                ids=ids,
            )
        finally:
            await repo.close()

        return {
            "action": "booku_knowledge_ingest",
            "document_id": doc_id,
            "title": f"《{resolved_title}》",
            "chunk_count": len(chunk_drafts),
            "bucket": bucket,
            "folder_id": folder_id,
            "collection": collection,
            "items": ids[:20],
        }

    async def export_document_titles(self) -> list[str]:
        repo = self._create_repo()
        await repo.initialize()
        try:
            raw_titles = await repo.list_knowledge_chunk_titles(folder_id="default")
        finally:
            await repo.close()
        return _collect_unique_titles_from_strings(raw_titles)

    async def dump_documents(self, *, limit: int = 100) -> dict[str, Any]:
        """导出知识库文档内容(该方法导出所有文档数据，未封装进tool)

        Args:
            limit: 最大导出文档数，默认 100

        Returns:
            包含文档 ID、标题、内容、更新时间和标签的列表
        """
        records = await self._list_knowledge_records(limit=limit)
        items = [
            {
                "id": item.memory_id,
                "title": item.title,
                "content": item.content,
                "updated_at": item.updated_at,
                "tags": item.tags,
            }
            for item in records
        ]
        return {"action": "booku_knowledge_dump", "total": len(items), "items": items}

    async def remember_titles_json(self) -> str:
        records = await self._list_knowledge_records(limit=5000)
        titles = _collect_unique_titles(records)
        return json.dumps(titles, ensure_ascii=False)
