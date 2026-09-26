FROM python:3.12-slim

# 容器内固定监听 8000；宿主机映射端口由 Compose 的 HOST_PORT 控制。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先单独拷贝依赖清单并安装，利用构建层缓存。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝应用代码与测试。
COPY app ./app
COPY tests ./tests

# 构建期冒烟检查：依赖与应用可正常初始化（导入即完成路由注册）。
RUN python -c "import app.main"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % int(os.environ.get('PORT', '8000')), timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
