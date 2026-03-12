# ----- 构建阶段 -----
FROM python:3.12.9-slim AS builder

# 修复：直接从 uv 官方镜像中复制核心二进制文件
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 设置构建环境变量
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 安装必要的系统构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 同步依赖（排除开发依赖）
RUN uv sync --frozen --no-dev --no-install-project

# ----- 最终运行镜像 -----
FROM python:3.12.9-slim

WORKDIR /app

# 设置运行环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# 安装运行时必要的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制虚环境
COPY --from=builder /app/.venv /app/.venv

# 复制源代码和相关文件
COPY . .

# 预先创建需要映射的目录
RUN mkdir -p config data logs plugins

# 启动命令
CMD ["python", "main.py"]
