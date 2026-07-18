"""优化的语义兴趣度评分器。

将 TF-IDF + LR 权重融合为 token→weight 字典，绕过 sklearn 实现纯 Python 评分。
包含权重剪枝、全局线程池、批处理队列等优化。
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("default_chatter.semantic_interest.optimized")

# ============================================================================
# 全局线程池
# ============================================================================

_GLOBAL_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = asyncio.Lock()


def get_global_executor(max_workers: int = 4) -> ThreadPoolExecutor:
    """获取全局线程池（单例）。

    Args:
        max_workers: 最大工作线程数

    Returns:
        ThreadPoolExecutor 实例
    """
    global _GLOBAL_EXECUTOR
    if _GLOBAL_EXECUTOR is None:
        _GLOBAL_EXECUTOR = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="semantic_scorer"
        )
        logger.info(f"[优化评分器] 创建全局线程池，workers={max_workers}")
    return _GLOBAL_EXECUTOR


def shutdown_global_executor() -> None:
    """关闭全局线程池。"""
    global _GLOBAL_EXECUTOR
    if _GLOBAL_EXECUTOR is not None:
        _GLOBAL_EXECUTOR.shutdown(wait=False)
        _GLOBAL_EXECUTOR = None
        logger.info("[优化评分器] 全局线程池已关闭")


# ============================================================================
# 快速评分器
# ============================================================================

@dataclass
class FastScorerConfig:
    """快速评分器配置。"""

    analyzer: str = "char"
    ngram_range: tuple[int, int] = (2, 4)
    lowercase: bool = True
    weight_prune_threshold: float = 1e-4
    top_k_weights: int = 0
    sigmoid_alpha: float = 1.0
    score_timeout: float = 2.0


class FastScorer:
    """快速语义兴趣度评分器。

    将 TF-IDF + LR 融合成一个纯 Python 的 token→weight 字典 scorer。

    核心公式:
    - TF-IDF: x_i = tf_i * idf_i
    - LR: z = Σ_i (w_i * x_i) + b = Σ_i (w_i * idf_i * tf_i) + b
    - 定义 w'_i = w_i * idf_i，则 z = Σ_i (w'_i * tf_i) + b

    在线评分只需要:
    1. 手动做 n-gram tokenize
    2. 统计 tf
    3. 查表 w'_i，累加求和
    4. sigmoid 转 [0, 1]
    """

    def __init__(self, config: FastScorerConfig | None = None) -> None:
        """初始化快速评分器。

        Args:
            config: 评分器配置，None 则使用默认配置
        """
        self.config = config or FastScorerConfig()

        self.token_weights: dict[str, float] = {}
        self.bias: float = 0.0
        self.output_bias: float = 0.0
        self.output_scale: float = 1.0
        self.meta: dict[str, Any] = {}
        self.is_loaded: bool = False
        self.total_scores: int = 0
        self.total_time: float = 0.0
        self._tokenize_pattern = re.compile(r"\s+")

    @classmethod
    def from_sklearn_model(
        cls,
        vectorizer: Any,
        model: Any,
        config: FastScorerConfig | None = None,
    ) -> "FastScorer":
        """从 sklearn 模型创建快速评分器。

        Args:
            vectorizer: TF-IDF 向量化器
            model: Logistic Regression 模型
            config: 配置

        Returns:
            FastScorer 实例
        """
        scorer = cls(config)
        scorer._extract_weights(vectorizer, model)
        return scorer

    def _extract_weights(self, vectorizer: Any, model: Any) -> None:
        """从 sklearn 模型提取并融合权重。

        将 TF-IDF 的 idf 和 LR 的权重合并为单一的 token→weight 字典。

        Args:
            vectorizer: TF-IDF 向量化器
            model: Logistic Regression 模型
        """
        if hasattr(vectorizer, "vectorizer"):
            tfidf = vectorizer.vectorizer
        else:
            tfidf = vectorizer

        if hasattr(model, "clf"):
            clf = model.clf
        else:
            clf = model

        vocabulary = tfidf.vocabulary_
        idf = tfidf.idf_

        coef = np.asarray(clf.coef_)
        intercept = np.asarray(clf.intercept_)
        classes = np.asarray(clf.classes_)

        self.output_bias = 0.0
        self.output_scale = 1.0

        extraction_mode = "unknown"

        if len(classes) == 2 and coef.shape[0] == 1:
            w_interest = coef[0]
            b_interest: float = float(intercept[0]) if intercept.size else 0.0
            extraction_mode = "binary"

            class_set = {int(c) for c in classes.tolist()}
            pos_label = int(classes[1])
            if class_set == {-1, 1} and pos_label == 1:
                self.output_bias, self.output_scale = 0.0, 1.0
            elif class_set == {0, 1} and pos_label == 1:
                self.output_bias, self.output_scale = 0.5, 0.5
            elif class_set == {-1, 0} and pos_label == 0:
                self.output_bias, self.output_scale = 0.0, 0.5
            else:
                logger.warning(
                    f"[FastScorer] 非标准二分类标签 {classes.tolist()}，将直接使用 sigmoid(logit)"
                )
        else:
            if coef.ndim != 2 or coef.shape[0] != len(classes):
                raise ValueError(
                    f"不支持的模型权重形状: coef={coef.shape}, classes={classes.tolist()}"
                )

            if (-1 in classes) and (1 in classes):
                idx_neg = int(np.where(classes == -1)[0][0])
                idx_pos = int(np.where(classes == 1)[0][0])
                w_interest = coef[idx_pos] - coef[idx_neg]
                b_interest = float(intercept[idx_pos] - intercept[idx_neg])
                extraction_mode = "multiclass_diff"
            elif 1 in classes:
                idx_pos = int(np.where(classes == 1)[0][0])
                w_interest = coef[idx_pos]
                b_interest = float(intercept[idx_pos])
                extraction_mode = "multiclass_pos_only"
                logger.warning(
                    f"[FastScorer] 模型缺少 -1 类别: {classes.tolist()}，将仅使用 class=1 logit"
                )
            else:
                raise ValueError(
                    f"模型缺少 class=1，无法构建兴趣评分: classes={classes.tolist()}"
                )

        combined_weights = w_interest * idf

        token_weights: dict[str, float] = {}
        for token, idx in vocabulary.items():
            weight = combined_weights[idx]
            if abs(weight) >= self.config.weight_prune_threshold:
                token_weights[token] = weight

        if self.config.top_k_weights > 0 and len(token_weights) > self.config.top_k_weights:
            sorted_items = sorted(
                token_weights.items(), key=lambda x: abs(x[1]), reverse=True
            )
            token_weights = dict(sorted_items[: self.config.top_k_weights])

        self.token_weights = token_weights
        self.bias = b_interest
        self.is_loaded = True

        self.meta = {
            "original_vocab_size": len(vocabulary),
            "pruned_vocab_size": len(token_weights),
            "prune_ratio": 1 - len(token_weights) / len(vocabulary) if vocabulary else 0,
            "weight_prune_threshold": self.config.weight_prune_threshold,
            "top_k_weights": self.config.top_k_weights,
            "bias": self.bias,
            "ngram_range": self.config.ngram_range,
            "classes": classes.tolist(),
            "extraction_mode": extraction_mode,
            "output_bias": self.output_bias,
            "output_scale": self.output_scale,
        }

        logger.info(
            f"[FastScorer] 权重提取完成: "
            f"原始词表={len(vocabulary)}, 剪枝后={len(token_weights)}, "
            f"剪枝率={self.meta['prune_ratio']:.2%}"
        )

    def _tokenize(self, text: str) -> list[str]:
        """将文本转换为 n-gram tokens。

        Args:
            text: 输入文本

        Returns:
            n-gram token 列表
        """
        if self.config.lowercase:
            text = text.lower()

        min_n, max_n = self.config.ngram_range
        tokens: list[str] = []

        for n in range(min_n, max_n + 1):
            for i in range(len(text) - n + 1):
                tokens.append(text[i : i + n])

        return tokens

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        """计算词频（TF）。

        Args:
            tokens: token 列表

        Returns:
            {token: count} 字典
        """
        return dict(Counter(tokens))

    def score(self, text: str) -> float:
        """计算单条消息的语义兴趣度。

        Args:
            text: 消息文本

        Returns:
            兴趣分 [0.0, 1.0]
        """
        if not self.is_loaded:
            raise ValueError("评分器尚未加载，请先调用 from_sklearn_model() 或 load()")

        start_time = time.time()

        try:
            tokens = self._tokenize(text)

            if not tokens:
                return 0.5

            tf = self._compute_tf(tokens)

            z = self.bias
            for token, count in tf.items():
                if token in self.token_weights:
                    z += self.token_weights[token] * count

            alpha = self.config.sigmoid_alpha
            try:
                interest = 1.0 / (1.0 + math.exp(-alpha * z))
            except OverflowError:
                interest = 0.0 if z < 0 else 1.0

            interest = self.output_bias + self.output_scale * interest
            interest = max(0.0, min(1.0, interest))

            self.total_scores += 1
            self.total_time += time.time() - start_time

            return interest

        except Exception as e:
            logger.error(f"[FastScorer] 评分失败: {e}, 消息: {text[:50]}")
            return 0.5

    def score_batch(self, texts: list[str]) -> list[float]:
        """批量计算兴趣度。

        Args:
            texts: 消息文本列表

        Returns:
            兴趣分列表
        """
        if not texts:
            return []
        return [self.score(text) for text in texts]

    async def score_async(self, text: str, timeout: float | None = None) -> float:
        """异步计算兴趣度（使用全局线程池）。

        Args:
            text: 消息文本
            timeout: 超时时间（秒）

        Returns:
            兴趣分 [0.0, 1.0]
        """
        timeout = timeout or self.config.score_timeout
        executor = get_global_executor()
        loop = asyncio.get_running_loop()

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, self.score, text),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[FastScorer] 评分超时({timeout}s): {text[:30]}...")
            return 0.5

    async def score_batch_async(
        self, texts: list[str], timeout: float | None = None
    ) -> list[float]:
        """异步批量计算兴趣度。

        Args:
            texts: 消息文本列表
            timeout: 超时时间（秒）

        Returns:
            兴趣分列表
        """
        if not texts:
            return []

        timeout = timeout or self.config.score_timeout * len(texts)
        executor = get_global_executor()
        loop = asyncio.get_running_loop()

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, self.score_batch, texts),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[FastScorer] 批量评分超时({timeout}s), 批次大小: {len(texts)}"
            )
            return [0.5] * len(texts)

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息。

        Returns:
            统计信息字典
        """
        avg_time = self.total_time / self.total_scores if self.total_scores > 0 else 0
        return {
            "is_loaded": self.is_loaded,
            "total_scores": self.total_scores,
            "total_time": self.total_time,
            "avg_score_time_ms": avg_time * 1000,
            "vocab_size": len(self.token_weights),
            "meta": self.meta,
        }

    def save(self, path: Path | str) -> None:
        """保存快速评分器到文件。

        Args:
            path: 保存路径
        """
        import joblib

        path = Path(path)

        bundle = {
            "token_weights": self.token_weights,
            "bias": self.bias,
            "config": {
                "analyzer": self.config.analyzer,
                "ngram_range": self.config.ngram_range,
                "lowercase": self.config.lowercase,
                "weight_prune_threshold": self.config.weight_prune_threshold,
                "top_k_weights": self.config.top_k_weights,
                "sigmoid_alpha": self.config.sigmoid_alpha,
                "score_timeout": self.config.score_timeout,
            },
            "meta": self.meta,
        }

        joblib.dump(bundle, path)
        logger.info(f"[FastScorer] 已保存到: {path}")

    @classmethod
    def load(cls, path: Path | str) -> "FastScorer":
        """从文件加载快速评分器。

        Args:
            path: 模型文件路径

        Returns:
            FastScorer 实例
        """
        import joblib

        path = Path(path)

        bundle = joblib.load(path)

        config = FastScorerConfig(**bundle["config"])
        scorer = cls(config)
        scorer.token_weights = bundle["token_weights"]
        scorer.bias = bundle["bias"]
        scorer.meta = bundle.get("meta", {})
        scorer.is_loaded = True

        logger.info(
            f"[FastScorer] 已从 {path} 加载，词表大小: {len(scorer.token_weights)}"
        )
        return scorer


# ============================================================================
# 批处理评分队列
# ============================================================================

@dataclass
class ScoringRequest:
    """评分请求。"""

    text: str
    future: asyncio.Future
    timestamp: float = field(default_factory=time.time)


class BatchScoringQueue:
    """批处理评分队列。

    攒一小撮消息一起算，提高 CPU 利用率。
    """

    def __init__(
        self,
        scorer: FastScorer,
        batch_size: int = 16,
        flush_interval_ms: float = 50.0,
    ) -> None:
        """初始化批处理队列。

        Args:
            scorer: 评分器实例
            batch_size: 批次大小，达到后立即处理
            flush_interval_ms: 刷新间隔（毫秒），超过后强制处理
        """
        self.scorer = scorer
        self.batch_size = batch_size
        self.flush_interval = flush_interval_ms / 1000.0

        self._pending: list[ScoringRequest] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._running = False

        self.total_batches: int = 0
        self.total_requests: int = 0

    async def start(self) -> None:
        """启动批处理队列。"""
        if self._running:
            return

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            f"[BatchQueue] 启动，batch_size={self.batch_size}, "
            f"interval={self.flush_interval * 1000}ms"
        )

    async def stop(self) -> None:
        """停止批处理队列。"""
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        await self._flush()
        logger.info("[BatchQueue] 已停止")

    async def score(self, text: str) -> float:
        """提交评分请求并等待结果。

        Args:
            text: 消息文本

        Returns:
            兴趣分
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        request = ScoringRequest(text=text, future=future)

        async with self._lock:
            self._pending.append(request)
            self.total_requests += 1

            if len(self._pending) >= self.batch_size:
                asyncio.create_task(self._flush())

        return await future

    async def _flush_loop(self) -> None:
        """定时刷新循环。"""
        while self._running:
            await asyncio.sleep(self.flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """处理当前待处理的请求。"""
        async with self._lock:
            if not self._pending:
                return

            batch = self._pending.copy()
            self._pending.clear()

        if not batch:
            return

        self.total_batches += 1

        try:
            texts = [req.text for req in batch]
            scores = await self.scorer.score_batch_async(texts)

            for req, score in zip(batch, scores):
                if not req.future.done():
                    req.future.set_result(score)

        except Exception as e:
            logger.error(f"[BatchQueue] 批量评分失败: {e}")
            for req in batch:
                if not req.future.done():
                    req.future.set_result(0.5)

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息。

        Returns:
            统计信息字典
        """
        avg_batch_size = (
            self.total_requests / self.total_batches if self.total_batches > 0 else 0
        )
        return {
            "total_batches": self.total_batches,
            "total_requests": self.total_requests,
            "avg_batch_size": avg_batch_size,
            "pending_count": len(self._pending),
            "batch_size": self.batch_size,
            "flush_interval_ms": self.flush_interval * 1000,
        }


# ============================================================================
# 优化评分器工厂
# ============================================================================

_fast_scorer_instances: dict[str, FastScorer] = {}
_batch_queue_instances: dict[str, BatchScoringQueue] = {}


async def get_fast_scorer(
    model_path: str | Path,
    use_batch_queue: bool = False,
    batch_size: int = 16,
    flush_interval_ms: float = 50.0,
    force_reload: bool = False,
) -> FastScorer | BatchScoringQueue:
    """获取快速评分器实例（单例）。

    Args:
        model_path: 模型文件路径（.pkl 格式）
        use_batch_queue: 是否使用批处理队列
        batch_size: 批处理大小
        flush_interval_ms: 批处理刷新间隔（毫秒）
        force_reload: 是否强制重新加载

    Returns:
        FastScorer 或 BatchScoringQueue 实例
    """
    import joblib

    model_path = Path(model_path)
    path_key = str(model_path.resolve())

    if not force_reload:
        if use_batch_queue and path_key in _batch_queue_instances:
            return _batch_queue_instances[path_key]
        elif not use_batch_queue and path_key in _fast_scorer_instances:
            return _fast_scorer_instances[path_key]

    logger.info(f"[优化评分器] 加载模型: {model_path}")

    bundle = joblib.load(model_path)

    if "token_weights" in bundle:
        scorer = FastScorer.load(model_path)
    else:
        vectorizer = bundle["vectorizer"]
        model = bundle["model"]

        config = FastScorerConfig(
            ngram_range=vectorizer.get_config().get("ngram_range", (2, 4)),
            weight_prune_threshold=1e-4,
        )
        scorer = FastScorer.from_sklearn_model(vectorizer, model, config)

    _fast_scorer_instances[path_key] = scorer

    if use_batch_queue:
        queue = BatchScoringQueue(scorer, batch_size, flush_interval_ms)
        await queue.start()
        _batch_queue_instances[path_key] = queue
        return queue

    return scorer


def convert_sklearn_to_fast(
    sklearn_model_path: str | Path,
    output_path: str | Path | None = None,
    config: FastScorerConfig | None = None,
) -> FastScorer:
    """将 sklearn 模型转换为 FastScorer 格式。

    Args:
        sklearn_model_path: sklearn 模型路径
        output_path: 输出路径（可选）
        config: FastScorer 配置

    Returns:
        FastScorer 实例
    """
    import joblib

    sklearn_model_path = Path(sklearn_model_path)
    bundle = joblib.load(sklearn_model_path)

    vectorizer = bundle["vectorizer"]
    model = bundle["model"]

    if config is None:
        vconfig = vectorizer.get_config() if hasattr(vectorizer, "get_config") else {}
        config = FastScorerConfig(
            ngram_range=vconfig.get("ngram_range", (2, 4)),
            weight_prune_threshold=1e-4,
        )

    scorer = FastScorer.from_sklearn_model(vectorizer, model, config)

    if output_path:
        output_path = Path(output_path)
        scorer.save(output_path)

    return scorer


def clear_fast_scorer_instances() -> None:
    """清空所有快速评分器实例。"""
    global _fast_scorer_instances, _batch_queue_instances

    for queue in _batch_queue_instances.values():
        asyncio.create_task(queue.stop())

    _fast_scorer_instances.clear()
    _batch_queue_instances.clear()

    logger.info("[优化评分器] 已清空所有实例")
