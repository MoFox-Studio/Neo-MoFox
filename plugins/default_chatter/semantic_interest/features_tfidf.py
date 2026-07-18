"""TF-IDF 特征向量化器。

使用字符级 n-gram 提取中文消息的 TF-IDF 特征。
"""

from __future__ import annotations

from typing import Literal

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("default_chatter.semantic_interest.features")


class TfidfFeatureExtractor:
    """TF-IDF 特征提取器。

    使用字符级 n-gram 策略，适合中文/多语言场景。
    """

    def __init__(
        self,
        analyzer: Literal["word", "char", "char_wb"] = "char",
        ngram_range: tuple[int, int] = (2, 4),
        max_features: int = 10000,
        min_df: int = 3,
        max_df: float = 0.95,
    ) -> None:
        """初始化特征提取器。

        Args:
            analyzer: 分析器类型 ('char' 或 'word')
            ngram_range: n-gram 范围，例如 (2, 4) 表示 2~4 字符的 n-gram
            max_features: 词表最大大小，防止特征爆炸
            min_df: 最小文档频率，至少出现在 N 个样本中才纳入词表
            max_df: 最大文档频率，出现频率超过此比例的词将被过滤（如停用词）
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(
            analyzer=analyzer,
            ngram_range=ngram_range,
            max_features=max_features,
            min_df=min_df,
            max_df=max_df,
            lowercase=True,
            strip_accents=None,
            sublinear_tf=True,
            norm="l2",
        )
        self.is_fitted: bool = False

        logger.info(
            f"TF-IDF 特征提取器初始化: analyzer={analyzer}, "
            f"ngram_range={ngram_range}, max_features={max_features}"
        )

    def fit(self, texts: list[str]) -> "TfidfFeatureExtractor":
        """训练向量化器。

        Args:
            texts: 训练文本列表

        Returns:
            self
        """
        logger.info(f"开始训练 TF-IDF 向量化器，样本数: {len(texts)}")
        self.vectorizer.fit(texts)
        self.is_fitted = True

        vocab_size = len(self.vectorizer.vocabulary_)
        logger.info(f"TF-IDF 向量化器训练完成，词表大小: {vocab_size}")

        return self

    def transform(self, texts: list[str]):
        """将文本转换为 TF-IDF 向量。

        Args:
            texts: 待转换文本列表

        Returns:
            稀疏矩阵
        """
        if not self.is_fitted:
            raise ValueError("向量化器尚未训练，请先调用 fit() 方法")

        return self.vectorizer.transform(texts)

    def fit_transform(self, texts: list[str]):
        """训练并转换文本。

        Args:
            texts: 训练文本列表

        Returns:
            稀疏矩阵
        """
        logger.info(f"开始训练并转换 TF-IDF 向量，样本数: {len(texts)}")
        result = self.vectorizer.fit_transform(texts)
        self.is_fitted = True

        vocab_size = len(self.vectorizer.vocabulary_)
        logger.info(f"TF-IDF 向量化完成，词表大小: {vocab_size}")

        return result

    def get_feature_names(self) -> list[str]:
        """获取特征名称列表。

        Returns:
            特征名称列表
        """
        if not self.is_fitted:
            raise ValueError("向量化器尚未训练")

        return self.vectorizer.get_feature_names_out().tolist()

    def get_vocabulary_size(self) -> int:
        """获取词表大小。

        Returns:
            词表大小
        """
        if not self.is_fitted:
            return 0
        return len(self.vectorizer.vocabulary_)

    def get_config(self) -> dict:
        """获取配置信息。

        Returns:
            配置字典
        """
        params = self.vectorizer.get_params()
        return {
            "analyzer": params["analyzer"],
            "ngram_range": params["ngram_range"],
            "max_features": params["max_features"],
            "min_df": params["min_df"],
            "max_df": params["max_df"],
            "vocabulary_size": self.get_vocabulary_size() if self.is_fitted else 0,
            "is_fitted": self.is_fitted,
        }
