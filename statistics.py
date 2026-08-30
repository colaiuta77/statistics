# -*- coding: utf-8 -*-
"""BookOasis local library statistics plugin."""

import logging
import os
from datetime import date, timedelta

from plugins.metadata.base import BaseMetadataProvider

from .statistics_core import MediaStatisticsAggregator, SnapshotStore, StatisticsAggregator, StatisticsRuntime

SELF_ID = "statistics"
PLUGIN_VERSION = "1.4.1"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_PLUGIN_DIR, "..", "..", "data", SELF_ID))
SUPPORTED_SESSIONS = ("general", "adult", "audiobook", "video")
logger = logging.getLogger(__name__)


def _store_path(db_type):
    filename = "statistics.db" if db_type == "general" else f"statistics-{db_type}.db"
    return os.path.join(_DATA_DIR, filename)


_RUNTIMES = {
    db_type: StatisticsRuntime(
        store=SnapshotStore(_store_path(db_type)),
        periodic_seconds=(6 * 60 * 60) + (index * 5 * 60),
    )
    for index, db_type in enumerate(SUPPORTED_SESSIONS)
}


def _normalize_session(value):
    db_type = str(value or "general").strip().lower()
    return db_type if db_type in SUPPORTED_SESSIONS else None


def _runtime_for(db_type):
    normalized = _normalize_session(db_type)
    return _RUNTIMES.get(normalized) if normalized else None


class StatisticsMetadataProvider(BaseMetadataProvider):
    id = SELF_ID
    name = "통계"
    version = PLUGIN_VERSION
    is_searchable = False
    config_schema = []
    dashboard_widget = None
    category_tab = {
        "title": "통계",
        "icon": "fa-solid fa-chart-pie",
        "order": 82,
        "sessions": "all",
    }
    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/colaiuta77/statistics/main",
        "files": [
            "statistics.py",
            "statistics_core.py",
            "__init__.py",
            "index.html",
            "style.css",
            "script.js",
            "VERSION",
        ],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "통계 플러그인은 메타데이터 적용 기능을 제공하지 않습니다."

    def _aggregate_session(self, db_type):
        gateway = self.get_db_gateway(db_type)
        if db_type in {"general", "adult"}:
            return StatisticsAggregator(gateway, session_type=db_type).aggregate()
        return MediaStatisticsAggregator(gateway, db_type).aggregate()

    def _get_or_start_runtime(self, db_type, initial_delay=0):
        session_type = _normalize_session(db_type)
        if session_type is None:
            return None
        runtime = _RUNTIMES[session_type]
        runtime.start(
            lambda session_type=session_type: self._aggregate_session(session_type),
            initial_delay=initial_delay,
        )
        return runtime

    def start_background_service(self, db_type):
        for index, session_type in enumerate(SUPPORTED_SESSIONS):
            try:
                self._get_or_start_runtime(session_type, initial_delay=3 + (index * 12))
            except Exception:
                logger.exception("통계 세션 백그라운드 서비스 시작 실패: %s", session_type)
        return None

    def on_scan_new_books_detected(self, db_type, payload):
        runtime = _runtime_for(db_type)
        if runtime is None:
            return {"success": False, "error": "지원하지 않는 통계 세션입니다."}
        runtime.request_refresh(delay=45, debounce=True)
        return {
            "success": True,
            "scheduled": True,
            "message": "통계 재집계를 예약했습니다.",
        }

    def get_dashboard_data(self, db_type, limit=10):
        runtime = self._get_or_start_runtime(db_type)
        if runtime is None:
            return {"success": False, "error": "지원하지 않는 통계 세션입니다."}
        return {"success": True, **runtime.get_state()}

    def _reading_calendar(self, db_type, context):
        from flask import has_request_context, session

        # 코어 권한 검사와 동일한 세션 이름만 허용한다.
        if db_type not in ("general", "adult"):
            return {"success": False, "error": "독서 달력은 일반·성인 도서에서만 지원합니다."}
        user_id = session.get("user_id") if has_request_context() else None
        if not user_id:
            return {"success": False, "error": "로그인이 필요합니다."}

        library_id = context.get("library_id", "all")
        if library_id != "all":
            try:
                if type(library_id) not in (str, int) or not str(library_id).isdigit():
                    raise ValueError
                library_id = int(library_id)
                if not 0 < library_id < 2 ** 63:
                    raise ValueError
            except (TypeError, ValueError):
                return {"success": False, "error": "올바르지 않은 보관함입니다."}

        today = date.today()
        start = date(today.year, 1, 1)
        end = date(today.year + 1, 1, 1)
        query = """
            SELECT l.read_date, COUNT(DISTINCT l.book_id) AS book_count
            FROM user_reading_log l
            JOIN books b ON b.id = l.book_id
            WHERE l.user_id = ? AND l.read_date >= ? AND l.read_date <= ?
              AND l.pages_read_delta > 0 AND COALESCE(b.is_deleted, 0) = 0
              AND EXISTS (
                SELECT 1 FROM user_category_permissions p
                WHERE p.user_id = l.user_id AND p.library_id = b.library_id AND p.has_access = 1
              )
        """
        params = [user_id, start.isoformat(), today.isoformat()]
        if library_id != "all":
            query += " AND b.library_id = ?"
            params.append(library_id)
        query += " GROUP BY l.read_date ORDER BY l.read_date"
        rows = self.get_db_gateway(db_type).fetch_all(query, tuple(params))
        counts = {str(row["read_date"]): int(row["book_count"]) for row in rows}
        days = []
        for offset in range((end - start).days):
            day = (start + timedelta(days=offset)).isoformat()
            days.append([day, counts.get(day, 0)])
        return {"success": True, "year": today.year, "days": days}

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id != "statistics_rpc":
            return {"success": False, "error": "지원하지 않는 통계 요청입니다."}
        context = context or {}
        if not isinstance(context, dict):
            return {"success": False, "error": "올바르지 않은 통계 요청입니다."}
        op = str(context.get("op") or "snapshot").strip().lower()
        if op == "reading_calendar":
            return self._reading_calendar(db_type, context)
        runtime = self._get_or_start_runtime(db_type)
        if runtime is None:
            return {"success": False, "error": "지원하지 않는 통계 세션입니다."}

        if op in {"snapshot", "status"}:
            return {"success": True, **runtime.get_state()}
        if op == "refresh":
            accepted = runtime.request_refresh(delay=0, debounce=False)
            return {
                "success": True,
                "accepted": bool(accepted),
                "message": "통계 재집계를 요청했습니다.",
                **runtime.get_state(),
            }
        return {"success": False, "error": f"지원하지 않는 작업입니다: {op}"}
