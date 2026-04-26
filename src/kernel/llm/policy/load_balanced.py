"""
提供了一个基于负载均衡和失败惩罚的动态模型选择策略
核心特性：
- 综合考虑 token 使用量、延迟、失败惩罚等多个维度
- 动态更新使用惩罚以实现短期负载均衡
- 对失败的模型施加惩罚，自动规避不可靠的模型
"""

from __future__ import annotations

import threading
from collections import namedtuple
from typing import Any

from .base import ModelStep, Policy, PolicySession

# 定义用于跟踪模型使用情况的具名元组
ModelUsageStats = namedtuple(
    "ModelUsageStats", ["total_tokens", "penalty", "usage_penalty", "avg_latency", "request_count"]
)


def _normalize_max_retry(value: Any) -> int:
    try:
        max_retry = int(value) if value is not None else 2
    except Exception:
        max_retry = 0
    return max(0, max_retry)


def _normalize_retry_interval(value: Any) -> float:
    try:
        delay = float(value) if value is not None else 3.0
    except Exception:
        delay = 0.0
    return max(0.0, delay)


class LoadBalancedPolicy(Policy):
    """
    基于负载均衡和失败惩罚的动态模型选择策略。
    
    核心特性：
    - 综合考虑 token 使用量、延迟、失败惩罚等多个维度
    - 动态更新使用惩罚以实现短期负载均衡
    - 对失败的模型施加惩罚，自动规避不可靠的模型
    """

    def __init__(
        self,
        *,
        critical_penalty_multiplier: float = 5.0,
        default_penalty_increment: float = 1.0,
        latency_weight: float = 200.0,
        penalty_weight: float = 300.0,
        usage_penalty_weight: float = 1000.0,
    ) -> None:
        """
        初始化负载均衡策略。

        Args:
            critical_penalty_multiplier: 严重错误的惩罚倍数
            default_penalty_increment: 默认惩罚增量
            latency_weight: 延迟权重
            penalty_weight: 失败惩罚权重
            usage_penalty_weight: 使用惩罚权重
        """
        self._lock = threading.Lock()
        # 全局共享的模型使用统计
        self._model_usage: dict[str, ModelUsageStats] = {}
        
        # 配置参数
        self.critical_penalty_multiplier = critical_penalty_multiplier
        self.default_penalty_increment = default_penalty_increment
        self.latency_weight = latency_weight
        self.penalty_weight = penalty_weight
        self.usage_penalty_weight = usage_penalty_weight

    def new_session(self, *, model_set: Any, request_name: str) -> PolicySession:
        if not isinstance(model_set, list) or not model_set:
            raise ValueError("model_set 必须是非空 list[dict]")
        if not all(isinstance(x, dict) for x in model_set):
            raise ValueError("model_set 必须是 list[dict]")

        # 初始化模型使用统计（如果还没有）
        with self._lock:
            for model in model_set:
                model_name = model.get("model_identifier", "unknown")
                if model_name not in self._model_usage:
                    self._model_usage[model_name] = ModelUsageStats(
                        total_tokens=0,
                        penalty=0.0,
                        usage_penalty=0,
                        avg_latency=0.0,
                        request_count=0,
                    )

        return _LoadBalancedSession(
            model_set=model_set,
            model_usage=self._model_usage,
            lock=self._lock,
            critical_penalty_multiplier=self.critical_penalty_multiplier,
            default_penalty_increment=self.default_penalty_increment,
            latency_weight=self.latency_weight,
            penalty_weight=self.penalty_weight,
            usage_penalty_weight=self.usage_penalty_weight,
        )


class _LoadBalancedSession(PolicySession):
    def __init__(
        self,
        *,
        model_set: list[dict[str, Any]],
        model_usage: dict[str, ModelUsageStats],
        lock: threading.Lock,
        critical_penalty_multiplier: float,
        default_penalty_increment: float,
        latency_weight: float,
        penalty_weight: float,
        usage_penalty_weight: float,
    ) -> None:
        self._models = model_set
        self._model_usage = model_usage
        self._lock = lock
        
        # 配置参数
        self._critical_penalty_multiplier = critical_penalty_multiplier
        self._default_penalty_increment = default_penalty_increment
        self._latency_weight = latency_weight
        self._penalty_weight = penalty_weight
        self._usage_penalty_weight = usage_penalty_weight
        
        # 会话状态
        self._failed_models: set[str] = set()
        self._current_model_name: str | None = None
        self._model_retry_used = 0
        self._attempts_used = 0
        
        # 计算最大尝试次数
        self._max_total_attempts = 0
        for m in model_set:
            self._max_total_attempts += 1 + _normalize_max_retry(m.get("max_retry"))
        if self._max_total_attempts <= 0:
            self._max_total_attempts = len(model_set)

    def first(self) -> ModelStep:
        """选择最佳可用模型作为第一次尝试。"""
        model = self._select_best_model()
        if model is None:
            return ModelStep(model=None, meta={"reason": "no_available_models"})
        
        model_name = model.get("model_identifier", "unknown")
        self._current_model_name = model_name
        self._model_retry_used = 0
        self._attempts_used = 1
        
        # 增加使用惩罚
        self._update_usage_penalty(model_name, increase=True)
        
        return ModelStep(
            model=model,
            meta={
                "model_name": model_name,
                "attempt": 1,
                "strategy": "load_balanced",
            },
        )

    def next_after_error(self, error: BaseException) -> ModelStep:
        """在错误后选择下一步行动：重试或切换模型。"""
        if self._attempts_used >= self._max_total_attempts:
            return ModelStep(model=None, meta={"reason": "exhausted"})
        
        if self._current_model_name is None:
            return ModelStep(model=None, meta={"reason": "no_current_model"})
        
        # 更新失败惩罚
        self._update_failure_penalty(self._current_model_name, error)
        
        # 找到当前模型的配置
        current_model = None
        for m in self._models:
            if m.get("model_identifier") == self._current_model_name:
                current_model = m
                break
        
        if current_model is None:
            return ModelStep(model=None, meta={"reason": "model_not_found"})
        
        # 获取重试配置
        max_retry_int = _normalize_max_retry(current_model.get("max_retry"))
        delay = _normalize_retry_interval(current_model.get("retry_interval"))
        
        # 判断是否应该重试当前模型
        if self._model_retry_used < max_retry_int:
            self._model_retry_used += 1
            self._attempts_used += 1
            return ModelStep(
                model=current_model,
                delay_seconds=delay,
                meta={
                    "model_name": self._current_model_name,
                    "attempt": self._attempts_used,
                    "retry": self._model_retry_used,
                },
            )
        
        # 当前模型重试次数已用完，标记为失败并切换
        self._failed_models.add(self._current_model_name)
        self._update_usage_penalty(self._current_model_name, increase=False)
        
        # 选择下一个最佳模型
        next_model = self._select_best_model()
        if next_model is None:
            return ModelStep(model=None, meta={"reason": "all_models_failed"})
        
        next_model_name = next_model.get("model_identifier", "unknown")
        self._current_model_name = next_model_name
        self._model_retry_used = 0
        self._attempts_used += 1
        
        # 增加新模型的使用惩罚
        self._update_usage_penalty(next_model_name, increase=True)
        
        return ModelStep(
            model=next_model,
            meta={
                "model_name": next_model_name,
                "attempt": self._attempts_used,
                "switch": True,
            },
        )

    def record_success(self, *, latency: float = 0.0, tokens: int = 0) -> None:
        """记录当前模型成功完成一次请求，用于后续负载均衡评分。"""
        if self._current_model_name is None:
            return

        model_name = self._current_model_name
        self._update_usage_penalty(model_name, increase=False)
        with self._lock:
            stats = self._model_usage.get(model_name)
            if stats is None:
                return

            request_count = stats.request_count + 1
            normalized_latency = max(0.0, float(latency))
            avg_latency = (
                (stats.avg_latency * stats.request_count + normalized_latency)
                / request_count
            )
            self._model_usage[model_name] = stats._replace(
                total_tokens=stats.total_tokens + max(0, int(tokens)),
                avg_latency=avg_latency,
                request_count=request_count,
            )

    def _select_best_model(self) -> dict[str, Any] | None:
        """
        选择负载均衡评分最低的可用模型。
        
        评分公式：
        total_tokens + penalty * PENALTY_WEIGHT
        + (usage_penalty + request_count) * USAGE_PENALTY_WEIGHT
        + avg_latency * LATENCY_WEIGHT
        """
        with self._lock:
            candidate_models = [
                m for m in self._models
                if m.get("model_identifier") not in self._failed_models
            ]
            
            if not candidate_models:
                return None
            
            # 计算每个候选模型的评分
            best_model = None
            best_score = float("inf")
            
            for model in candidate_models:
                model_name = model.get("model_identifier", "unknown")
                stats = self._model_usage.get(model_name)
                if stats is None:
                    # 如果没有统计数据，给予最高优先级（评分为0）
                    return model
                
                score = (
                    stats.total_tokens
                    + stats.penalty * self._penalty_weight
                    + (stats.usage_penalty + stats.request_count)
                    * self._usage_penalty_weight
                    + stats.avg_latency * self._latency_weight
                )
                
                if score < best_score:
                    best_score = score
                    best_model = model
            
            return best_model

    def _update_usage_penalty(self, model_name: str, increase: bool) -> None:
        """
        更新模型的使用惩罚值。
        
        在模型被选中时增加惩罚值，请求完成或失败后减少惩罚值。
        这有助于在短期内将请求分散到不同的模型，实现更动态的负载均衡。
        """
        with self._lock:
            stats = self._model_usage.get(model_name)
            if stats is None:
                return
            
            adjustment = 1 if increase else -1
            new_usage_penalty = max(0, stats.usage_penalty + adjustment)
            self._model_usage[model_name] = stats._replace(usage_penalty=new_usage_penalty)

    def _update_failure_penalty(self, model_name: str, error: BaseException) -> None:
        """
        根据异常类型动态调整模型的失败惩罚值。
        
        关键错误（如网络连接、服务器错误）会获得更高的惩罚。
        """
        with self._lock:
            stats = self._model_usage.get(model_name)
            if stats is None:
                return
            
            penalty_increment = self._default_penalty_increment
            
            # 根据错误类型判断严重程度
            error_name = type(error).__name__
            
            # 严重错误类型
            critical_errors = [
                "NetworkConnectionError",
                "ReqAbortException",
                "ConnectionError",
                "TimeoutError",
                "LLMTimeoutError",
            ]
            
            # 服务器错误类型
            server_errors = [
                "RespNotOkException",
                "HTTPError",
                "ServerError",
            ]
            
            if error_name in critical_errors:
                penalty_increment *= self._critical_penalty_multiplier
            elif error_name in server_errors:
                # 服务器错误可能是暂时的，使用中等惩罚
                penalty_increment *= 2.0
            
            self._model_usage[model_name] = stats._replace(penalty=stats.penalty + penalty_increment)
