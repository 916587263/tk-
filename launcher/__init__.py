"""
TikTok Analyzer — 一键启动子系统

职责边界:
  - 环境健康检查 (Python / 依赖 / 浏览器 / CDP 端口)
  - 浏览器生命周期管理 (启动 / 复用 / 关闭)
  - 应用进程管理 (Flask 子进程 + 优雅关闭)

入口:
  python launcher.py          # 交互模式
  python -m launcher          # 包模式 (等价)
  start.bat / start.sh        # 系统壳脚本

设计原则:
  - SOLID: 浏览器适配器模式实现 OCP (对扩展开放, 对修改关闭)
  - 依赖注入: 无模块级全局状态, 所有依赖通过参数传递
  - 防御性: 每个组件返回结构化结果, 调用方显式处理失败路径
  - 可测试: 纯函数 + 接口抽象, 方便 mock 和 pytest
"""

__version__ = "2.0.0"
