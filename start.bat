@echo off
:: ============================================================
:: TikTok Analyzer — Windows 一键启动
:: 双击此文件即可启动完整分析环境
:: 无 Python 则自动引导安装, 无依赖则自动安装
:: ============================================================
title TikTok Analyzer Launcher

cd /d "%~dp0"

:: ── 检查 Python ──
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [错误] 未找到 Python
    echo  Python 3.9+ 需要手动安装:
    echo    https://www.python.org/downloads/
    echo  安装时请勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:: ── 虚拟环境 (可选) ──
if exist ".venv\Scripts\python.exe" (
    echo [信息] 使用虚拟环境 .venv
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

:: ── 首次: 安装依赖 ──
if not exist ".deps_checked" (
    echo [信息] 检查 Python 依赖...
    %PYTHON% -c "import flask, playwright, openai, yaml, dotenv" >nul 2>&1
    if %errorlevel% neq 0 (
        echo [信息] 正在安装依赖 (首次运行)...
        %PYTHON% -m pip install -r requirements.txt
        if %errorlevel% neq 0 (
            echo [错误] 依赖安装失败
            echo 请手动执行: %PYTHON% -m pip install -r requirements.txt
            pause
            exit /b 1
        )
    )
    type nul > .deps_checked
)

:: ── Playwright Chromium (首次) ──
%PYTHON% -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.launch(); p.stop()" >nul 2>&1
if %errorlevel% neq 0 (
    echo [信息] 正在安装 Playwright Chromium (首次运行, 约 300MB)...
    %PYTHON% -m playwright install chromium
)

:: ── 首次: 创建 .env ──
if not exist ".env" (
    echo [信息] 未找到 .env, 从模板复制...
    copy .env.example .env >nul 2>&1
    echo [提示] 编辑 .env 填入 API Key (规则引擎模式无需)
)

:: ── 启动 ──
echo.
echo ========================================
echo   TikTok Analyzer — 一键启动
echo ========================================
echo.
%PYTHON% launcher.py %*
pause
