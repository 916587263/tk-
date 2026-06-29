#!/usr/bin/env python3
"""
TikTok Analyzer — 一键启动器
"""
import sys as _sys

# ── 第 1 行可执行代码: 直接用 stderr.write 输出, 绕过所有 print/import 失败 ──
_sys.stderr.write("[LAUNCHER] module top reached\n")
_sys.stderr.flush()

# ── 确保 stdout 能输出 (某些环境 stdout 可能被关闭) ──
try:
    print("[LAUNCHER] print() to stdout works", flush=True)
except Exception:
    _sys.stderr.write("[LAUNCHER] print() failed, using stderr fallback\n")

import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
_sys.path.insert(0, str(PROJECT_ROOT))

_sys.stderr.write(f"[LAUNCHER] project_root={PROJECT_ROOT}\n")
_sys.stderr.write(f"[LAUNCHER] sys.path[0]={_sys.path[0]}\n")
_sys.stderr.flush()

# ── 逐模块导入, 每个失败立即打印到 stderr ──
_import_ok = True

_sys.stderr.write("[LAUNCHER] importing launcher.config...\n")
_sys.stderr.flush()
try:
    from launcher.config import load_config
    _sys.stderr.write("[LAUNCHER]   launcher.config OK\n")
except BaseException as e:
    _sys.stderr.write(f"[LAUNCHER]   launcher.config FAILED: {e}\n")
    traceback.print_exc(file=_sys.stderr)
    _import_ok = False

_sys.stderr.write("[LAUNCHER] importing launcher.logging_setup...\n")
_sys.stderr.flush()
try:
    from launcher.logging_setup import init_logging, get_logger
    _sys.stderr.write("[LAUNCHER]   launcher.logging_setup OK\n")
except BaseException as e:
    _sys.stderr.write(f"[LAUNCHER]   launcher.logging_setup FAILED: {e}\n")
    traceback.print_exc(file=_sys.stderr)
    _import_ok = False

_sys.stderr.write("[LAUNCHER] importing launcher.health...\n")
_sys.stderr.flush()
try:
    from launcher.health import (
        run_health_checks,
        resolve_browser_paths,
        find_browser_executable,
        HealthReport,
    )
    _sys.stderr.write("[LAUNCHER]   launcher.health OK\n")
except BaseException as e:
    _sys.stderr.write(f"[LAUNCHER]   launcher.health FAILED: {e}\n")
    traceback.print_exc(file=_sys.stderr)
    _import_ok = False

_sys.stderr.write("[LAUNCHER] importing launcher.browser.manager...\n")
_sys.stderr.flush()
try:
    from launcher.browser.manager import create_manager
    _sys.stderr.write("[LAUNCHER]   launcher.browser.manager OK\n")
except BaseException as e:
    _sys.stderr.write(f"[LAUNCHER]   launcher.browser.manager FAILED: {e}\n")
    traceback.print_exc(file=_sys.stderr)
    _import_ok = False

_sys.stderr.write("[LAUNCHER] importing launcher.runner...\n")
_sys.stderr.flush()
try:
    from launcher.runner import AppRunner
    _sys.stderr.write("[LAUNCHER]   launcher.runner OK\n")
except BaseException as e:
    _sys.stderr.write(f"[LAUNCHER]   launcher.runner FAILED: {e}\n")
    traceback.print_exc(file=_sys.stderr)
    _import_ok = False

if not _import_ok:
    _sys.stderr.write("\n[LAUNCHER] FATAL: one or more imports failed (see above)\n")
    _sys.stderr.write("[LAUNCHER] Fix: pip install -r requirements.txt\n")
    _sys.stderr.flush()
    _sys.exit(1)

_sys.stderr.write("[LAUNCHER] all imports OK, defining helper classes...\n")
_sys.stderr.flush()


# ═══════════════════════════════════════════════════════════════
# 终端样式
# ═══════════════════════════════════════════════════════════════

class Term:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def ok(cls, s: str) -> str:   return f"{cls.GREEN}{s}{cls.RESET}"
    @classmethod
    def warn(cls, s: str) -> str: return f"{cls.YELLOW}{s}{cls.RESET}"
    @classmethod
    def err(cls, s: str) -> str:  return f"{cls.RED}{s}{cls.RESET}"
    @classmethod
    def info(cls, s: str) -> str: return f"{cls.CYAN}{s}{cls.RESET}"
    @classmethod
    def bold(cls, s: str) -> str: return f"{cls.BOLD}{s}{cls.RESET}"


_SEP = "=" * 56


def _print_health_report(report):
    print()
    print(Term.bold("── 健康检查 ──"))
    print()
    for r in report.results:
        icon = Term.ok(f"[{r.status_icon()}]") if r.passed else Term.err(f"[{r.status_icon()}]")
        severity_tag = Term.warn("[WARN]") if r.severity == "warning" else ""
        print(f"  {icon} {r.name}: {r.message} {severity_tag}")
        if r.fix_hint:
            print(f"     {Term.info('→ ' + r.fix_hint)}")


def _print_health_summary(report):
    if report.all_pass:
        print()
        print(f"  {Term.ok('✓ 健康检查全部通过')}  ({len(report.results)} 项)")
        print()
        return True
    fatal = report.fatal_count
    warn = report.warning_count
    print()
    print(f"  {Term.err(f'✗ 发现 {fatal} 个阻断问题')}" +
          (f", {Term.warn(f'{warn} 个警告')}" if warn else ""))
    print()
    return False


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    _sys.stderr.write("[LAUNCHER] main() entered\n")
    _sys.stderr.flush()

    import argparse

    parser = argparse.ArgumentParser(description="TikTok Analyzer — 一键启动器")
    parser.add_argument("--debug", action="store_true", help="详细调试输出")
    parser.add_argument("--no-wait", action="store_true", help="启动后不阻塞")
    parser.add_argument("--skip-checks", action="store_true", help="跳过健康检查")
    args = parser.parse_args()

    _sys.stderr.write(f"[LAUNCHER] args parsed: debug={args.debug}, no_wait={args.no_wait}, skip_checks={args.skip_checks}\n")
    _sys.stderr.flush()

    # ── 横幅 ──
    print()
    print(Term.bold(_SEP))
    print(Term.bold("  TikTok Analyzer — 外贸对标视频发现系统"))
    print(Term.bold("  Launcher v2.0"))
    print(Term.bold(_SEP))

    # ── 加载配置 ──
    _sys.stderr.write("[LAUNCHER] loading config...\n")
    _sys.stderr.flush()
    try:
        cfg = load_config(project_root=PROJECT_ROOT)
    except Exception as e:
        print(f"\n  {Term.err('✗ 配置加载失败:')} {e}")
        traceback.print_exc()
        return 1
    _sys.stderr.write(f"[LAUNCHER] config loaded: browser={cfg.browser.preferred}\n")
    _sys.stderr.flush()

    # ── 初始化日志 ──
    _sys.stderr.write("[LAUNCHER] initializing logging...\n")
    _sys.stderr.flush()
    try:
        log = init_logging(PROJECT_ROOT, cfg.logging)
    except Exception as e:
        print(f"\n  {Term.err('✗ 日志初始化失败:')} {e}")
        traceback.print_exc()
        return 1
    log.info("启动器 v2.0 — 项目: %s", PROJECT_ROOT)
    _sys.stderr.write("[LAUNCHER] logging ready\n")
    _sys.stderr.flush()

    # ── 解析浏览器路径 ──
    _sys.stderr.write("[LAUNCHER] resolving browser paths...\n")
    _sys.stderr.flush()
    try:
        browser_paths, browser_name = resolve_browser_paths(cfg.browser.preferred)
    except Exception as e:
        log.error("浏览器路径解析失败: %s", e)
        print(f"  {Term.err('✗ 浏览器路径解析失败:')} {e}")
        traceback.print_exc()
        return 1
    _sys.stderr.write(f"[LAUNCHER] browser: {browser_name}, {len(browser_paths)} paths\n")
    _sys.stderr.flush()

    # ── 健康检查 ──
    if not args.skip_checks:
        _sys.stderr.write("[LAUNCHER] running health checks...\n")
        _sys.stderr.flush()
        try:
            report = run_health_checks(
                project_root=PROJECT_ROOT,
                browser_paths=browser_paths,
                browser_name=browser_name,
                required_packages=cfg.health.required_packages,
                optional_packages=cfg.health.optional_packages,
                min_python=cfg.health.min_python,
                cdp_host=cfg.browser.debug_host,
                cdp_port=cfg.browser.debug_port,
            )
        except Exception as e:
            log.error("健康检查失败: %s", e)
            print(f"  {Term.err('✗ 健康检查异常:')} {e}")
            traceback.print_exc()
            return 1

        _print_health_report(report)
        if not _print_health_summary(report):
            print(f"  {Term.warn('提示: 使用 --skip-checks 跳过检查 (不推荐)')}")
            print()
            return 1
    else:
        log.warning("跳过健康检查 (--skip-checks)")

    # ── 浏览器管理 ──
    _sys.stderr.write("[LAUNCHER] creating browser manager...\n")
    _sys.stderr.flush()
    print(Term.bold("── 浏览器管理 ──"))
    print()

    browser_log = get_logger("browser")

    try:
        browser_manager = create_manager(
            preferred=cfg.browser.preferred,
            config=cfg.browser,
            project_root=PROJECT_ROOT,
            logger=browser_log,
        )
    except Exception as e:
        log.error("create_manager 异常: %s", e)
        print(f"  {Term.err('✗ 浏览器管理器创建失败:')} {e}")
        traceback.print_exc()
        return 1

    if browser_manager is None:
        exe = find_browser_executable(browser_paths)
        if exe is None:
            print(f"  {Term.err('✗ 未找到支持的浏览器 (Edge/Chrome)')}")
            print(f"  {Term.info('→ 安装 Edge: https://www.microsoft.com/edge')}")
        else:
            print(f"  {Term.err('✗ 浏览器管理器初始化失败 (找到浏览器但 create_manager 返回 None)')}")
            print(f"  {Term.info('→ 浏览器: ' + str(exe))}")
        return 1

    _sys.stderr.write(f"[LAUNCHER] browser manager created: {type(browser_manager).__name__}\n")
    _sys.stderr.flush()

    try:
        ok, msg = browser_manager.ensure_ready()
    except Exception as e:
        log.error("ensure_ready 异常: %s", e)
        print(f"  {Term.err('✗ 浏览器就绪检测异常:')} {e}")
        traceback.print_exc()
        return 1

    if not ok:
        log.error("浏览器准备失败: %s", msg)
        print(f"  {Term.err('✗ 浏览器未就绪:')} {msg}")
        return 1

    log.info("浏览器就绪: %s", msg)

    # ── Flask ──
    _sys.stderr.write("[LAUNCHER] starting Flask...\n")
    _sys.stderr.flush()
    print(Term.bold("── 启动 Web 应用 ──"))
    print()

    runner = AppRunner(PROJECT_ROOT, cfg.app, get_logger("runner"))

    try:
        runner.start()
    except Exception as e:
        log.exception("Flask 启动失败")
        print(f"  {Term.err('✗ 无法启动 Flask:')} {e}")
        traceback.print_exc()
        browser_manager.shutdown()
        return 1

    _sys.stderr.write("[LAUNCHER] Flask started\n")
    _sys.stderr.flush()
    runner.open_browser()

    # ── 就绪 ──
    print()
    print(Term.bold(_SEP))
    print(Term.bold(f"  🚀 一切就绪!"))
    print(Term.bold(f"  Web 界面: {runner.url}"))
    print(Term.bold(f"  CDP 端口: {cfg.browser.debug_port}"))
    if args.no_wait:
        print(Term.bold(f"  模式: --no-wait"))
    else:
        print(Term.bold(f"  按 Ctrl+C 退出"))
    print(Term.bold(_SEP))
    print()
    print(f"  {Term.info('日志: ' + str(PROJECT_ROOT / cfg.logging.log_dir / cfg.logging.log_filename))}")
    print()

    if args.no_wait:
        runner.shutdown()
        return 0

    try:
        runner.wait()
    except KeyboardInterrupt:
        log.info("用户按 Ctrl+C")
    finally:
        runner.shutdown()
        if not cfg.app.keep_browser_on_exit:
            browser_manager.shutdown()

    print(f"  {Term.info('✓ 已退出')}")
    print()
    return 0


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _sys.stderr.write("[LAUNCHER] entering main()...\n")
    _sys.stderr.flush()
    try:
        _sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _sys.stderr.write("\n[LAUNCHER] FATAL: unhandled exception\n")
        traceback.print_exc(file=_sys.stderr)
        _sys.stderr.flush()
        _sys.exit(1)
