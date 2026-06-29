"""
浏览器适配器包

设计模式: Adapter Pattern + Strategy Pattern

   BrowserAdapter (ABC)
   ├── EdgeAdapter      — Microsoft Edge
   ├── ChromeAdapter    — Google Chrome
   └── (未来可扩展)
       ├── BraveAdapter
       ├── ChromiumAdapter
       └── RemoteBrowserAdapter

用法:
  from launcher.browser.manager import BrowserManager

  mgr = BrowserManager(adapter, config, project_root, logger)
  ok, msg = mgr.ensure_ready()
"""

from .base import BrowserAdapter
from .edge import EdgeAdapter
from .chrome import ChromeAdapter
from .manager import BrowserManager

__all__ = [
    "BrowserAdapter",
    "EdgeAdapter",
    "ChromeAdapter",
    "BrowserManager",
]
