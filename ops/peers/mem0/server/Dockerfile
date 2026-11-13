# qf-mem0 独立构建（QUOT-569 一键安装）：不再依赖 mem0-dev 基镜像。
# 与 Dockerfile.patch 同源（/app 路径、端口 8000、alembic 由 compose command 执行）。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH= \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY . /app/

RUN pip install --no-cache-dir -q --force-reinstall --no-deps mem0ai==2.2.1 \
 && pip install --no-cache-dir -q -r /app/requirements.txt \
 && python -c "import mem0, sentence_transformers; print('runtime imports ok')"

WORKDIR /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
