# 后端服务镜像: Python 3.11 精简版
ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}
USER root

# 设置工作目录
WORKDIR /app

# 1. 先复制依赖清单并安装 (利用 Docker 分层缓存: 依赖变更频率远低于代码)
COPY requirements.txt .
COPY requirements.lock .
# 公网镜像源偶发慢速读取时重试；仍不在最终镜像保留 pip 缓存。
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 5 --timeout 120 -r requirements.txt && pip check

# 2. 复制应用代码
COPY app ./app
COPY evals ./evals
COPY run.py .
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts
# 手工维护的旅游知识随镜像发布；运行数据通过 Compose 绑定到宿主机持久化。
COPY data/knowledge ./data/knowledge

# 3. 非 root 用户运行 (安全最佳实践: 即使被攻破也无法读写宿主系统)
RUN (id -u appuser >/dev/null 2>&1 || useradd -m appuser) \
    && mkdir -p logs data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

# 生产模式启动: 先迁移数据库，再以非 reload 模式启动服务。
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.api.main:app --host 0.0.0.0 --port 9000"]
