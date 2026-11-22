"""向量库响应归一化。

``ChromaDBImpl`` 在查询异常时吞掉异常并返回 ``{}``，而成功响应必定包含
``ids`` 键（空结果也是 ``{"ids": [], ...}``）。因此调用方不能用
``data.get("ids") or []`` 判断"有没有数据"——那会把读取失败当成正常空页，
进而误删数据或误判"整库缺向量"。本模块统一这个判定。
"""

from __future__ import annotations


def extract_ids(data: object) -> list[str] | None:
    """从向量库 ``get`` 响应中取出记录 ID 列表。

    Args:
        data: ``VectorDBBase.get`` 的原始返回值。

    Returns:
        记录 ID 列表（可为空）；响应无效、无法区分"失败"与"空页"时返回 None。
    """
    if not isinstance(data, dict) or "ids" not in data:
        return None
    ids = data["ids"]
    if not isinstance(ids, list):
        return None
    return [str(item) for item in ids]


__all__ = ["extract_ids"]
