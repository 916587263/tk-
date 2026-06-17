"""TikTok 竞争对手分析系统 - 断点续爬模块"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime

from .logger import setup_logger

logger = setup_logger("checkpoint")

CHECKPOINT_DIR = Path(__file__).parent.parent / "checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True)

class CheckpointManager:
    """SQLite 断点管理器，记录每个任务的抓取进度"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.db_path = CHECKPOINT_DIR / f"{task_id}.db"
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS progress (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        stage TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        detail TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scraped_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        item_type TEXT NOT NULL,
                        item_key TEXT NOT NULL UNIQUE,
                        data TEXT,
                        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def mark_stage(self, stage: str, status: str = "completed", detail: str = ""):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO progress (id, stage, status, detail, updated_at) "
                    "VALUES ((SELECT id FROM progress WHERE stage = ?), ?, ?, ?, CURRENT_TIMESTAMP)",
                    (stage, stage, status, detail)
                )
                conn.commit()
            finally:
                conn.close()
        logger.debug("[%s] 阶段 %s -> %s", self.task_id, stage, status)

    def get_stage(self, stage: str) -> Optional[str]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT status FROM progress WHERE stage = ?", (stage,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def is_completed(self, item_type: str, item_key: str) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM scraped_items WHERE item_type = ? AND item_key = ?",
                (item_type, item_key)
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def mark_scraped(self, item_type: str, item_key: str, data: dict = None):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO scraped_items (item_type, item_key, data) VALUES (?, ?, ?)",
                    (item_type, item_key, json.dumps(data, ensure_ascii=False) if data else None)
                )
                conn.commit()
            finally:
                conn.close()

    def get_scraped_data(self, item_type: str, item_key: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT data FROM scraped_items WHERE item_type = ? AND item_key = ?",
                (item_type, item_key)
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row and row[0] else None

    def get_progress_summary(self) -> dict:
        conn = self._get_conn()
        try:
            stages = conn.execute("SELECT stage, status, detail FROM progress").fetchall()
            counts = conn.execute(
                "SELECT item_type, COUNT(*) FROM scraped_items GROUP BY item_type"
            ).fetchall()
        finally:
            conn.close()
        return {
            "stages": {s: {"status": st, "detail": d} for s, st, d in stages},
            "counts": dict(counts)
        }

    def clear(self):
        with self._lock:
            try:
                self.db_path.unlink(missing_ok=True)
            except PermissionError:
                import time
                time.sleep(0.1)
                self.db_path.unlink(missing_ok=True)
