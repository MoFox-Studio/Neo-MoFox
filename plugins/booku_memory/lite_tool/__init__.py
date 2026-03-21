"""
Booku Memory Lite Tool
    - 写记忆不堵塞主回复器：写入通过后台任务执行，主回复器可快速返回“已提交”，显著缩短响应时间。
    - 最小化 LLM 调用：仅在必要步骤（例如标签补全、相对时间解析、少数兜底决策）才触发内部轻量模型调用。
    - 标准读写工作流：读写路径固定、可预测，减少策略漂移，提升交互速度与排障可控性。
    - 大幅提速：Agent模式下读写耗时较高；Lite Tool在典型场景下平均可在约 3 秒内返回读取结果。
"""

from .write_workflow import BookuMemoryWriteTool
from .read_workflow import BookuMemoryReadTool

__all__ = [
    "BookuMemoryWriteTool",
    "BookuMemoryReadTool",
]
