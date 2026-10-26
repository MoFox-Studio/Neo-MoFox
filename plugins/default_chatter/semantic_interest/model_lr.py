"""Logistic Regression 模型训练与推理。

使用多分类 Logistic Regression 预测消息的兴趣度标签 (-1, 0, 1)。
"""

from __future__ import annotations

import time
from typing import Any, Literal

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

from src.app.plugin_system.api.log_api import get_logger

from .features_tfidf import TfidfFeatureExtractor

logger = get_logger("default_chatter.semantic_interest.model")


class SemanticInterestModel:
    """语义兴趣度模型。

    使用 Logistic Regression 进行多分类（-1: 不感兴趣, 0: 中立, 1: 感兴趣）。
    """

    def __init__(
        self,
        class_weight: str | dict | None = "balanced",
        max_iter: int = 1000,
        solver: Literal["lbfgs", "liblinear", "newton-cg", "newton-cholesky", "sag", "saga"] = "lbfgs",
        n_jobs: int = -1,
    ) -> None:
        """初始化模型。

        Args:
            class_weight: 类别权重配置
                - "balanced": 自动平衡类别权重
                - dict: 自定义权重，如 {-1: 0.8, 0: 0.6, 1: 1.6}
                - None: 不使用权重
            max_iter: 最大迭代次数
            solver: 求解器 ('lbfgs', 'saga', 'liblinear' 等)
            n_jobs: 并行任务数，-1 表示使用所有 CPU 核心
        """
        self.clf = LogisticRegression(
            solver=solver,
            max_iter=max_iter,
            class_weight=class_weight,
            n_jobs=n_jobs,
            random_state=42,
        )
        self.is_fitted: bool = False
        self.label_mapping: dict[int, int] = {-1: 0, 0: 1, 1: 2}
        self.training_metrics: dict[str, Any] = {}

        logger.info(
            f"Logistic Regression 模型初始化: class_weight={class_weight}, "
            f"max_iter={max_iter}, solver={solver}"
        )

    def train(
        self,
        X_train,
        y_train,
        X_val=None,
        y_val=None,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """训练模型。

        Args:
            X_train: 训练集特征矩阵
            y_train: 训练集标签（-1, 0, 1）
            X_val: 验证集特征矩阵（可选）
            y_val: 验证集标签（可选）
            verbose: 是否输出详细日志

        Returns:
            训练指标字典
        """
        start_time = time.time()
        logger.info(f"开始训练模型，训练样本数: {len(y_train)}")

        self.clf.fit(X_train, y_train)
        self.is_fitted = True

        training_time = time.time() - start_time
        logger.info(f"模型训练完成，耗时: {training_time:.2f}秒")

        y_train_pred = self.clf.predict(X_train)
        train_accuracy = (y_train_pred == y_train).mean()

        metrics: dict[str, Any] = {
            "training_time": training_time,
            "train_accuracy": train_accuracy,
            "train_samples": len(y_train),
        }

        if verbose:
            logger.info(f"训练集准确率: {train_accuracy:.4f}")
            logger.info(f"类别分布: {dict(zip(*np.unique(y_train, return_counts=True)))}")

        if X_val is not None and y_val is not None:
            val_metrics = self.evaluate(X_val, y_val, verbose=verbose)
            metrics.update(val_metrics)

        self.training_metrics = metrics
        return metrics

    def evaluate(
        self,
        X_test,
        y_test,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """评估模型。

        Args:
            X_test: 测试集特征矩阵
            y_test: 测试集标签
            verbose: 是否输出详细日志

        Returns:
            评估指标字典
        """
        if not self.is_fitted:
            raise ValueError("模型尚未训练")

        y_pred = self.clf.predict(X_test)
        accuracy = (y_pred == y_test).mean()

        metrics: dict[str, Any] = {
            "test_accuracy": accuracy,
            "test_samples": len(y_test),
        }

        if verbose:
            logger.info(f"测试集准确率: {accuracy:.4f}")
            logger.info("\n分类报告:")
            report = classification_report(
                y_test,
                y_pred,
                labels=[-1, 0, 1],
                target_names=["不感兴趣(-1)", "中立(0)", "感兴趣(1)"],
                zero_division=0,
            )
            logger.info(f"\n{report}")

            logger.info("\n混淆矩阵:")
            cm = confusion_matrix(y_test, y_pred, labels=[-1, 0, 1])
            logger.info(f"\n{cm}")

        return metrics

    def predict_proba(self, X) -> np.ndarray:
        """预测概率分布。

        Args:
            X: 特征矩阵

        Returns:
            概率矩阵，形状为 (n_samples, 3)，对应 [-1, 0, 1] 的概率
        """
        if not self.is_fitted:
            raise ValueError("模型尚未训练")

        proba = self.clf.predict_proba(X)

        classes = self.clf.classes_
        if not np.array_equal(classes, [-1, 0, 1]):
            sorted_proba = np.zeros((proba.shape[0], 3), dtype=proba.dtype)
            for i, cls in enumerate([-1, 0, 1]):
                idx = np.where(classes == cls)[0]
                if len(idx) > 0:
                    sorted_proba[:, i] = proba[:, int(idx[0])]
            return sorted_proba

        return proba

    def predict(self, X) -> np.ndarray:
        """预测类别。

        Args:
            X: 特征矩阵

        Returns:
            预测标签数组
        """
        if not self.is_fitted:
            raise ValueError("模型尚未训练")

        return self.clf.predict(X)

    def get_config(self) -> dict:
        """获取模型配置。

        Returns:
            配置字典
        """
        params = self.clf.get_params()
        return {
            "solver": params["solver"],
            "max_iter": params["max_iter"],
            "class_weight": params["class_weight"],
            "is_fitted": self.is_fitted,
            "classes": self.clf.classes_.tolist() if self.is_fitted else None,
        }


def train_semantic_model(
    texts: list[str],
    labels: list[int],
    test_size: float = 0.1,
    random_state: int = 42,
    tfidf_config: dict | None = None,
    model_config: dict | None = None,
) -> tuple[TfidfFeatureExtractor, SemanticInterestModel, dict]:
    """训练完整的语义兴趣度模型。

    Args:
        texts: 消息文本列表
        labels: 对应的标签列表 (-1, 0, 1)
        test_size: 验证集比例
        random_state: 随机种子
        tfidf_config: TF-IDF 配置
        model_config: 模型配置

    Returns:
        (特征提取器, 模型, 训练指标)
    """
    logger.info(f"开始训练语义兴趣度模型，总样本数: {len(texts)}")

    X_train_texts, X_val_texts, y_train, y_val = train_test_split(
        texts,
        labels,
        test_size=test_size,
        stratify=labels,
        random_state=random_state,
    )

    logger.info(f"训练集: {len(X_train_texts)}, 验证集: {len(X_val_texts)}")

    tfidf_config = tfidf_config or {}
    feature_extractor = TfidfFeatureExtractor(**tfidf_config)
    X_train = feature_extractor.fit_transform(X_train_texts)
    X_val = feature_extractor.transform(X_val_texts)

    model_config = model_config or {}
    model = SemanticInterestModel(**model_config)
    metrics = model.train(X_train, y_train, X_val, y_val)

    logger.info("语义兴趣度模型训练完成")

    return feature_extractor, model, metrics
