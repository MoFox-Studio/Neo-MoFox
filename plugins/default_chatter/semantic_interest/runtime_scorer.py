"""运行时语义兴趣度评分器。

在线推理时使用，提供快速的兴趣度评分。
支持异步加载、超时保护、批量优化、模型预热。
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from .features_tfidf import TfidfFeatureExtractor
from .model_lr import SemanticInterestModel

logger = get_logger("default_chatter.semantic_interest.scorer")

DEFAULT_SCORE_TIMEOUT = 2.0

_GLOBAL_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_MAX_WORKERS = 4


def _get_global_executor() -> ThreadPoolExecutor:
    """获取全局线程池（单例）。

    Returns:
        ThreadPoolExecutor 实例
    """
    global _GLOBAL_EXECUTOR
    if _GLOBAL_EXECUTOR is None:
        _GLOBAL_EXECUTOR = ThreadPoolExecutor(
            max_workers=_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="semantic_scorer",
        )
        logger.info(f"[评分器] 创建全局线程池，workers={_EXECUTOR_MAX_WORKERS}")
    return _GLOBAL_EXECUTOR


_scorer_instances: dict[str, "SemanticInterestScorer"] = {}
_instance_lock = asyncio.Lock()


class SemanticInterestScorer:
    """语义兴趣度评分器。

    加载训练好的模型，在运行时快速计算消息的语义兴趣度。

    特性：
    - 异步加载支持（非阻塞）
    - 批量评分优化
    - 超时保护
    - 模型预热
    - 全局线程池（避免重复创建 executor）
    - 可选的 FastScorer 模式（绕过 sklearn）
    """

    def __init__(self, model_path: str | Path, use_fast_scorer: bool = True) -> None:
        """初始化评分器。

        Args:
            model_path: 模型文件路径 (.pkl)
            use_fast_scorer: 是否使用快速评分器模式（推荐）
        """
        self.model_path = Path(model_path)
        self.vectorizer: TfidfFeatureExtractor | None = None
        self.model: SemanticInterestModel | None = None
        self.meta: dict[str, Any] = {}
        self.is_loaded: bool = False

        self._use_fast_scorer = use_fast_scorer
        self._fast_scorer = None

        self.total_scores: int = 0
        self.total_time: float = 0.0

    def _get_underlying_clf(self) -> Any:
        """获取底层 sklearn 分类器对象。"""
        model = self.model
        if model is None:
            return None
        return model.clf if hasattr(model, "clf") else model

    def _proba_to_three(self, proba_row: Any) -> tuple[float, float, float]:
        """将任意 predict_proba 输出对齐为 (-1, 0, 1) 三类概率。

        Args:
            proba_row: predict_proba 输出的单行

        Returns:
            (P(-1), P(0), P(1)) 三元组
        """
        proba_row = list(proba_row)
        clf = self._get_underlying_clf()
        classes = getattr(clf, "classes_", None)

        if classes is not None and len(classes) == len(proba_row):
            mapping = {int(cls): float(p) for cls, p in zip(classes, proba_row)}
            return (
                mapping.get(-1, 0.0),
                mapping.get(0, 0.0),
                mapping.get(1, 0.0),
            )

        if len(proba_row) == 3:
            return float(proba_row[0]), float(proba_row[1]), float(proba_row[2])

        if len(proba_row) == 2:
            return float(proba_row[0]), 0.0, float(proba_row[1])
        if len(proba_row) == 1:
            return 0.0, float(proba_row[0]), 0.0

        raise ValueError(f"不支持的 proba 形状: len={len(proba_row)}")

    def load(self) -> None:
        """同步加载模型（阻塞）。"""
        import joblib

        if not self.model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {self.model_path}")

        logger.info(f"开始加载模型: {self.model_path}")
        start_time = time.time()

        try:
            bundle = joblib.load(self.model_path)

            self.vectorizer = bundle["vectorizer"]
            self.model = bundle["model"]
            self.meta = bundle.get("meta", {})

            if self._use_fast_scorer and self.vectorizer is not None:
                from .optimized_scorer import FastScorer, FastScorerConfig

                config = FastScorerConfig(
                    ngram_range=self.vectorizer.get_config().get("ngram_range", (2, 3)),
                    weight_prune_threshold=1e-4,
                )
                try:
                    self._fast_scorer = FastScorer.from_sklearn_model(
                        self.vectorizer, self.model, config
                    )
                    logger.info(
                        f"[FastScorer] 已启用，词表从 {self.vectorizer.get_vocabulary_size()} "
                        f"剪枝到 {len(self._fast_scorer.token_weights)}"
                    )
                except Exception as e:
                    self._fast_scorer = None
                    logger.warning(
                        f"[FastScorer] 初始化失败，将回退到 sklearn 评分路径: {e}"
                    )

            self.is_loaded = True
            load_time = time.time() - start_time

            logger.info(
                f"模型加载成功，耗时: {load_time:.3f}秒, "
                f"词表大小: {self.vectorizer.get_vocabulary_size()}"  # type: ignore[union-attr]
            )

            if self.meta:
                logger.info(f"模型元信息: {self.meta}")

        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise

    async def load_async(self) -> None:
        """异步加载模型（非阻塞）。"""
        import joblib

        if not self.model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {self.model_path}")

        logger.info(f"开始异步加载模型: {self.model_path}")
        start_time = time.time()

        try:
            executor = _get_global_executor()
            loop = asyncio.get_running_loop()
            bundle = await loop.run_in_executor(executor, joblib.load, self.model_path)

            self.vectorizer = bundle["vectorizer"]
            self.model = bundle["model"]
            self.meta = bundle.get("meta", {})

            if self._use_fast_scorer and self.vectorizer is not None:
                from .optimized_scorer import FastScorer, FastScorerConfig

                config = FastScorerConfig(
                    ngram_range=self.vectorizer.get_config().get("ngram_range", (2, 3)),
                    weight_prune_threshold=1e-4,
                )
                try:
                    self._fast_scorer = FastScorer.from_sklearn_model(
                        self.vectorizer, self.model, config
                    )
                    logger.info(
                        f"[FastScorer] 已启用，词表从 {self.vectorizer.get_vocabulary_size()} "
                        f"剪枝到 {len(self._fast_scorer.token_weights)}"
                    )
                except Exception as e:
                    self._fast_scorer = None
                    logger.warning(
                        f"[FastScorer] 初始化失败，将回退到 sklearn 评分路径: {e}"
                    )

            self.is_loaded = True
            load_time = time.time() - start_time

            logger.info(
                f"模型异步加载成功，耗时: {load_time:.3f}秒, "
                f"词表大小: {self.vectorizer.get_vocabulary_size()}"  # type: ignore[union-attr]
            )

            if self.meta:
                logger.info(f"模型元信息: {self.meta}")

            await self._warmup_async()

        except Exception as e:
            logger.error(f"模型异步加载失败: {e}")
            raise

    def reload(self) -> None:
        """重新加载模型（热更新）。"""
        logger.info("重新加载模型...")
        self.is_loaded = False
        self.load()

    async def reload_async(self) -> None:
        """异步重新加载模型。"""
        logger.info("异步重新加载模型...")
        self.is_loaded = False
        await self.load_async()

    def score(self, text: str) -> float:
        """计算单条消息的语义兴趣度。

        Args:
            text: 消息文本

        Returns:
            兴趣分 [0.0, 1.0]，越高表示越感兴趣
        """
        if not self.is_loaded:
            raise ValueError("模型尚未加载，请先调用 load() 或 load_async() 方法")

        start_time = time.time()

        try:
            if self._fast_scorer is not None:
                interest = self._fast_scorer.score(text)
            else:
                X = self.vectorizer.transform([text])  # type: ignore[union-attr]
                proba = self.model.predict_proba(X)[0]  # type: ignore[union-attr]
                p_neg, p_neu, p_pos = self._proba_to_three(proba)
                interest = float(p_pos + 0.5 * p_neu)

            interest = max(0.0, min(1.0, interest))

            self.total_scores += 1
            self.total_time += time.time() - start_time

            return interest

        except Exception as e:
            logger.error(f"兴趣度计算失败: {e}, 消息: {text[:50]}")
            return 0.5

    async def score_async(self, text: str, timeout: float = DEFAULT_SCORE_TIMEOUT) -> float:
        """异步计算兴趣度（带超时保护）。

        Args:
            text: 消息文本
            timeout: 超时时间（秒），超时返回中立值 0.5

        Returns:
            兴趣分 [0.0, 1.0]
        """
        executor = _get_global_executor()
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, self.score, text),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"兴趣度计算超时（{timeout}秒），消息: {text[:50]}")
            return 0.5

    def score_batch(self, texts: list[str]) -> list[float]:
        """批量计算兴趣度。

        Args:
            texts: 消息文本列表

        Returns:
            兴趣分列表
        """
        if not self.is_loaded:
            raise ValueError("模型尚未加载")

        if not texts:
            return []

        start_time = time.time()

        try:
            if self._fast_scorer is not None:
                interests = self._fast_scorer.score_batch(texts)
                self.total_scores += len(texts)
                self.total_time += time.time() - start_time
                return interests
            else:
                X = self.vectorizer.transform(texts)  # type: ignore[union-attr]
                proba = self.model.predict_proba(X)  # type: ignore[union-attr]

                interests: list[float] = []
                for row in proba:
                    _, p_neu, p_pos = self._proba_to_three(row)
                    interest = float(p_pos + 0.5 * p_neu)
                    interest = max(0.0, min(1.0, interest))
                    interests.append(interest)

                self.total_scores += len(texts)
                self.total_time += time.time() - start_time

                return interests

        except Exception as e:
            logger.error(f"批量兴趣度计算失败: {e}")
            return [0.5] * len(texts)

    async def score_batch_async(
        self, texts: list[str], timeout: float | None = None
    ) -> list[float]:
        """异步批量计算兴趣度。

        Args:
            texts: 消息文本列表
            timeout: 超时时间（秒），None 则使用单条超时*文本数

        Returns:
            兴趣分列表
        """
        if not texts:
            return []

        if timeout is None:
            timeout = DEFAULT_SCORE_TIMEOUT * len(texts)

        executor = _get_global_executor()
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, self.score_batch, texts),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"批量兴趣度计算超时（{timeout}秒），批次大小: {len(texts)}"
            )
            return [0.5] * len(texts)

    def _warmup(self, sample_texts: list[str] | None = None) -> None:
        """预热模型（执行几次推理以优化性能）。

        Args:
            sample_texts: 预热用的样本文本，None 则使用默认样本
        """
        if not self.is_loaded:
            return

        if sample_texts is None:
            sample_texts = ["你好", "今天天气怎么样？", "我对这个话题很感兴趣"]

        logger.debug(f"开始预热模型，样本数: {len(sample_texts)}")
        start_time = time.time()

        for text in sample_texts:
            try:
                self.score(text)
            except Exception:
                pass

        warmup_time = time.time() - start_time
        logger.debug(f"模型预热完成，耗时: {warmup_time:.3f}秒")

    async def _warmup_async(self, sample_texts: list[str] | None = None) -> None:
        """异步预热模型。

        Args:
            sample_texts: 预热用的样本文本
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._warmup, sample_texts)

    def get_detailed_score(self, text: str) -> dict[str, Any]:
        """获取详细的兴趣度评分信息。

        Args:
            text: 消息文本

        Returns:
            包含概率分布和最终分数的详细信息
        """
        if not self.is_loaded:
            raise ValueError("模型尚未加载")

        X = self.vectorizer.transform([text])  # type: ignore[union-attr]
        proba = self.model.predict_proba(X)[0]  # type: ignore[union-attr]
        pred_label = self.model.predict(X)[0]  # type: ignore[union-attr]

        p_neg, p_neu, p_pos = self._proba_to_three(proba)
        interest = float(p_pos + 0.5 * p_neu)

        return {
            "interest_score": max(0.0, min(1.0, interest)),
            "proba_distribution": {
                "dislike": float(p_neg),
                "neutral": float(p_neu),
                "like": float(p_pos),
            },
            "predicted_label": int(pred_label),
            "text_preview": text[:100],
        }

    def get_statistics(self) -> dict[str, Any]:
        """获取评分器统计信息。

        Returns:
            统计信息字典
        """
        avg_time = self.total_time / self.total_scores if self.total_scores > 0 else 0

        stats: dict[str, Any] = {
            "is_loaded": self.is_loaded,
            "model_path": str(self.model_path),
            "total_scores": self.total_scores,
            "total_time": self.total_time,
            "avg_score_time": avg_time,
            "avg_score_time_ms": avg_time * 1000,
            "vocabulary_size": (
                self.vectorizer.get_vocabulary_size()
                if self.vectorizer and self.is_loaded
                else 0
            ),
            "use_fast_scorer": self._use_fast_scorer,
            "fast_scorer_enabled": self._fast_scorer is not None,
            "meta": self.meta,
        }

        if self._fast_scorer is not None:
            stats["fast_scorer_stats"] = self._fast_scorer.get_statistics()

        return stats

    def __repr__(self) -> str:
        mode = "fast" if self._fast_scorer else "sklearn"
        return (
            f"SemanticInterestScorer("
            f"loaded={self.is_loaded}, "
            f"mode={mode}, "
            f"model={self.model_path.name})"
        )


class ModelManager:
    """模型管理器。

    支持模型热更新、版本管理和人设感知的模型切换。
    """

    def __init__(self, model_dir: Path) -> None:
        """初始化管理器。

        Args:
            model_dir: 模型目录
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.current_scorer: SemanticInterestScorer | None = None
        self.current_version: str | None = None
        self.current_persona_info: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

        self._auto_trainer = None
        self._auto_training_started = False

    async def load_model(
        self,
        version: str = "latest",
        persona_info: dict[str, Any] | None = None,
        use_async: bool = True,
    ) -> SemanticInterestScorer:
        """加载指定版本的模型，支持人设感知。

        Args:
            version: 模型版本号或 "latest" 或 "auto"
            persona_info: 人设信息，用于自动选择匹配的模型
            use_async: 是否使用异步加载（推荐）

        Returns:
            评分器实例（单例）
        """
        async with self._lock:
            if persona_info is not None and version == "auto":
                model_path = await self._get_persona_model(persona_info)
            elif version == "latest":
                model_path = self._get_latest_model()
            else:
                model_path = self.model_dir / f"semantic_interest_{version}.pkl"

            if not model_path or not model_path.exists():
                raise FileNotFoundError(f"模型文件不存在: {model_path}")

            scorer = await get_semantic_scorer(
                model_path, force_reload=False, use_async=use_async
            )

            self.current_scorer = scorer
            self.current_version = version
            self.current_persona_info = persona_info

            logger.info(f"模型管理器已加载版本: {version}, 文件: {model_path.name}")
            return scorer

    async def reload_current_model(self) -> None:
        """重新加载当前模型。"""
        if not self.current_scorer:
            raise ValueError("尚未加载任何模型")

        async with self._lock:
            await self.current_scorer.reload_async()
            logger.info("模型已重新加载")

    def _get_latest_model(self) -> Path:
        """获取最新的模型文件。

        Returns:
            最新模型文件路径
        """
        model_files = list(self.model_dir.glob("semantic_interest_*.pkl"))

        if not model_files:
            raise FileNotFoundError(f"在 {self.model_dir} 中未找到模型文件")

        latest = max(model_files, key=lambda p: p.stat().st_mtime)
        return latest

    def get_scorer(self) -> SemanticInterestScorer:
        """获取当前评分器。

        Returns:
            当前评分器实例
        """
        if not self.current_scorer:
            raise ValueError("尚未加载任何模型")

        return self.current_scorer

    async def _get_persona_model(self, persona_info: dict[str, Any]) -> Path | None:
        """根据人设信息获取或训练模型。

        Args:
            persona_info: 人设信息

        Returns:
            模型文件路径
        """
        try:
            from .auto_trainer import get_auto_trainer

            if self._auto_trainer is None:
                self._auto_trainer = get_auto_trainer()

            trained, model_path = await self._auto_trainer.auto_train_if_needed(
                persona_info=persona_info,
                days=7,
                max_samples=1000,
            )

            if trained and model_path:
                logger.info(f"[模型管理器] 使用新训练的模型: {model_path.name}")
                return model_path

            model_path = self._auto_trainer.get_model_for_persona(persona_info)
            if model_path:
                return model_path

            logger.warning("[模型管理器] 未找到人设模型，使用 latest")
            return self._get_latest_model()

        except Exception as e:
            logger.error(f"[模型管理器] 获取人设模型失败: {e}")
            return self._get_latest_model()

    async def check_and_reload_for_persona(
        self, persona_info: dict[str, Any]
    ) -> bool:
        """检查人设变化并重新加载模型。

        Args:
            persona_info: 当前人设信息

        Returns:
            True 如果重新加载了模型
        """
        if self.current_persona_info == persona_info:
            return False

        logger.info("[模型管理器] 检测到人设变化，重新加载模型...")

        try:
            await self.load_model(version="auto", persona_info=persona_info)
            return True
        except Exception as e:
            logger.error(f"[模型管理器] 重新加载模型失败: {e}")
            return False

    async def start_auto_training(
        self, persona_info: dict[str, Any], interval_hours: int = 24
    ) -> None:
        """启动自动训练任务。

        Args:
            persona_info: 人设信息
            interval_hours: 检查间隔（小时）
        """
        from src.kernel.concurrency import get_task_manager

        async with self._lock:
            if self._auto_training_started:
                logger.debug("[模型管理器] 自动训练任务已启动，跳过")
                return

            try:
                from .auto_trainer import get_auto_trainer

                if self._auto_trainer is None:
                    self._auto_trainer = get_auto_trainer()

                logger.info(
                    f"[模型管理器] 启动自动训练任务，间隔: {interval_hours}小时"
                )

                self._auto_training_started = True

                task_manager = get_task_manager()
                task_manager.create_task(
                    self._auto_trainer.scheduled_train(persona_info, interval_hours),
                    name="semantic_interest_auto_training",
                )

            except Exception as e:
                logger.error(f"[模型管理器] 启动自动训练失败: {e}")
                self._auto_training_started = False


async def get_semantic_scorer(
    model_path: str | Path,
    force_reload: bool = False,
    use_async: bool = True,
) -> SemanticInterestScorer:
    """获取语义兴趣度评分器实例（单例模式）。

    同一个模型路径只会创建一个评分器实例，避免重复加载模型。

    Args:
        model_path: 模型文件路径
        force_reload: 是否强制重新加载模型
        use_async: 是否使用异步加载（推荐）

    Returns:
        评分器实例（单例）
    """
    model_path = Path(model_path)
    path_key = str(model_path.resolve())

    async with _instance_lock:
        if not force_reload and path_key in _scorer_instances:
            scorer = _scorer_instances[path_key]
            if scorer.is_loaded:
                logger.debug(f"[单例] 复用已加载的评分器: {model_path.name}")
                return scorer
            else:
                logger.info(f"[单例] 评分器未加载，重新加载: {model_path.name}")

        if path_key not in _scorer_instances:
            logger.info(f"[单例] 创建新的评分器实例: {model_path.name}")
            scorer = SemanticInterestScorer(model_path)
            _scorer_instances[path_key] = scorer
        else:
            scorer = _scorer_instances[path_key]
            logger.info(f"[单例] 强制重新加载评分器: {model_path.name}")

        if use_async:
            await scorer.load_async()
        else:
            scorer.load()

        return scorer


def get_semantic_scorer_sync(
    model_path: str | Path,
    force_reload: bool = False,
) -> SemanticInterestScorer:
    """获取语义兴趣度评分器实例（同步版本，单例模式）。

    Args:
        model_path: 模型文件路径
        force_reload: 是否强制重新加载模型

    Returns:
        评分器实例（单例）
    """
    model_path = Path(model_path)
    path_key = str(model_path.resolve())

    if not force_reload and path_key in _scorer_instances:
        scorer = _scorer_instances[path_key]
        if scorer.is_loaded:
            logger.debug(f"[单例] 复用已加载的评分器: {model_path.name}")
            return scorer

    if path_key not in _scorer_instances:
        logger.info(f"[单例] 创建新的评分器实例: {model_path.name}")
        scorer = SemanticInterestScorer(model_path)
        _scorer_instances[path_key] = scorer
    else:
        scorer = _scorer_instances[path_key]
        logger.info(f"[单例] 强制重新加载评分器: {model_path.name}")

    scorer.load()
    return scorer


def clear_scorer_instances() -> None:
    """清空所有评分器实例（释放内存）。"""
    global _scorer_instances
    count = len(_scorer_instances)
    _scorer_instances.clear()
    logger.info(f"[单例] 已清空 {count} 个评分器实例")


def get_all_scorer_instances() -> dict[str, SemanticInterestScorer]:
    """获取所有已创建的评分器实例。

    Returns:
        {模型路径: 评分器实例} 的字典
    """
    return _scorer_instances.copy()
