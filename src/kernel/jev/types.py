"""Jev System One 的请求/响应数据类型。

对应官方契约 ``POST /v1/systemone``：
请求体固定为 ``{"model": ..., "state": ..., "questions": {...}}``，
响应体固定为 ``{"model": ..., "answers": {...}, "usage": {...}}``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

JevState: TypeAlias = str | dict[str, Any] | list[Any]
JevInstruction: TypeAlias = str | dict[str, Any] | list[Any]

#: TypeSafe 支持的三种 typed question。
JEV_QUESTION_TYPES = ("noul", "choice", "score")
#: Score 问题允许的有序 rubric 数量区间。
JEV_SCORE_CRITERIA_RANGE = (2, 10)


@dataclass(frozen=True, slots=True)
class JevQuestion:
    """单个 typed question 定义。

    Attributes:
        type: ``noul`` / ``choice`` / ``score``。
        instructions: 自然语言、对象或列表形式的指令。
        criteria: Noul 可选，Choice 必填对象，Score 必填有序列表。
    """

    type: str
    instructions: JevInstruction
    criteria: Any = None

    def to_payload(self) -> dict[str, Any]:
        """转换为请求体中可直接 JSON 序列化的对象。

        Returns:
            ``{"type": ..., "instructions": ...}``，criteria 非 None 时附加。

        Raises:
            ValueError: type 不属于支持的三种 typed question 时抛出。
        """
        if self.type not in JEV_QUESTION_TYPES:
            raise ValueError(f"question.type 必须是 {JEV_QUESTION_TYPES} 之一：{self.type!r}")

        payload: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            payload["criteria"] = self.criteria
        return payload


@dataclass(frozen=True, slots=True)
class JevUsage:
    """TypeSafe 返回的 token 使用量。"""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class JevAnswer:
    """单个问题的结构化答案。

    按问题类型填充对应字段：Noul 使用 ``noul``；Choice 使用 ``choice``/
    ``probabilities``/``confidence``；Score 使用 ``score``/``legend``/
    ``probabilities``/``confidence``。
    """

    type: str
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    legend: dict[str, Any] | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "JevAnswer":
        """校验并解析一个 answer 对象。

        Args:
            payload: 响应中的单个 answer JSON 对象。

        Returns:
            校验通过的 JevAnswer。

        Raises:
            ValueError: 字段缺失或类型不合法时抛出。
        """
        if not isinstance(payload, dict):
            raise ValueError("answer 必须是 object")

        answer_type = payload.get("type")
        if answer_type not in JEV_QUESTION_TYPES:
            raise ValueError(f"answer.type 必须是 {JEV_QUESTION_TYPES} 之一")

        def read_number(name: str, *, required: bool) -> float | None:
            """读取可选/必填数字字段，bool 不算数字。"""
            value = payload.get(name)
            if value is None:
                if required:
                    raise ValueError(f"answer.{name} 必须是 number")
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"answer.{name} 必须是 number")
            return float(value)

        noul = read_number("noul", required=answer_type == "noul")
        score = read_number("score", required=answer_type == "score")
        confidence = read_number("confidence", required=answer_type in {"choice", "score"})

        choice = payload.get("choice")
        if choice is not None and not isinstance(choice, str):
            raise ValueError("answer.choice 必须是 string")
        if answer_type == "choice" and choice is None:
            raise ValueError("answer.choice 必须是 string")

        legend = payload.get("legend")
        if legend is not None and not isinstance(legend, dict):
            raise ValueError("answer.legend 必须是 object")

        return cls(
            type=answer_type,
            noul=noul,
            choice=choice,
            score=score,
            legend=legend,
            probabilities=cls._parse_probabilities(payload.get("probabilities")),
            confidence=confidence,
        )

    @staticmethod
    def _parse_probabilities(payload: Any) -> dict[str, float] | None:
        """解析概率分布，非对象或含非数字值时失败。"""
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("answer.probabilities 必须是 object")
        probabilities: dict[str, float] = {}
        for key, value in payload.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("answer.probabilities 的值必须是 number")
            probabilities[str(key)] = float(value)
        return probabilities


@dataclass(frozen=True, slots=True)
class JevResponse:
    """一次 System One 调用的结构化响应。"""

    model: str
    answers: dict[str, JevAnswer]
    usage: JevUsage

    def answer(self, question_id: str) -> JevAnswer:
        """按问题 ID 取答案。

        Args:
            question_id: 请求时使用的 questions key。

        Returns:
            对应的 JevAnswer。

        Raises:
            KeyError: 问题 ID 不存在时抛出。
        """
        return self.answers[question_id]
