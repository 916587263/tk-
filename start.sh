#!/usr/bin/env bash
# ============================================================
# TikTok Analyzer — macOS / Linux 一键启动
#
# 用法:
#   chmod +x start.sh
#   ./start.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 检查 Python ──
if ! command -v python3 &>/dev/null; then
    echo ""
    echo " [错误] 未找到 python3"
    echo " 请安装 Python 3.9+: https://www.python.org/downloads/"
    exit 1
fi

# ── 虚拟环境 ──
PYTHON="python3"
if [ -f ".venv/bin/python" ]; then
    echo "[信息] 使用虚拟环境 .venv"
    PYTHON=".venv/bin/python"
fi

# ── 首次: 安装依赖 ──
if [ ! -f ".deps_checked" ]; then
    echo "[信息] 检查 Python 依赖..."
    if ! $PYTHON -c "import flask, playwright, openai, yaml, dotenv" &>/dev/null; then
        echo "[信息] 正在安装依赖 (首次运行)..."
        $PYTHON -m pip install -r requirements.txt
    fi
    touch .deps_checked
fi

# ── Playwright Chromium ──
if ! $PYTHON -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.launch(); p.stop()" &>/dev/null; then
    echo "[信息] 正在安装 Playwright Chromium (首次运行)..."
    $PYTHON -m playwright install chromium
fi

# ── 首次: 创建 .env ──
if [ ! -f ".env" ]; then
    echo "[信息] 未找到 .env, 从模板复制..."
    cp .env.example .env
    echo "[提示] 编辑 .env 填入 API Key (规则引擎模式无需)"
fi

# ── 启动 ──
echo ""
echo "========================================"
echo "  TikTok Analyzer — 一键启动"
echo "========================================"
echo ""
exec $PYTHON launcher.py "$@"
