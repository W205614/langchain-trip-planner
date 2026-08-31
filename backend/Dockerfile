# 后端服务镜像: Python 3.11 精简版
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 1. 先复制依赖清单并安装 (利用 Docker 分层缓存: 依赖变更频率远低于代码)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 复制应用代码
COPY app ./app
COPY evals ./evals
COPY run.py .
COPY alembic.ini .
COPY alembic ./alembic
# 手工维护的旅游知识随镜像发布；运行数据通过 Compose 绑定到宿主机持久化。
COPY data/knowledge ./data/knowledge

# 3. 非 root 用户运行 (安全最佳实践: 即使被攻破也无法读写宿主系统)
RUN useradd -m appuser \
    && mkdir -p logs data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

# 生产模式启动: 先迁移数据库，再以非 reload 模式启动服务。
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.api.main:app --host 0.0.0.0 --port 9000"]
