# ============================================================
# TikTok 外贸行业对标视频发现系统 — Docker 镜像
# 可选：用于团队统一环境或 CI 测试
# ============================================================
# 使用方式:
#   docker build -t tiktok-analyzer .
#   docker run -p 5000:5000 --env-file .env tiktok-analyzer
# ============================================================

FROM python:3.11-slim

# 系统依赖：Playwright 需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright Chromium（含依赖）
RUN playwright install --with-deps chromium

# 复制项目
COPY . .

# 创建运行时目录
RUN mkdir -p data checkpoints logs cookies browser_profile cache

EXPOSE 5000

# 启动 Web 服务
CMD ["python", "app.py"]
