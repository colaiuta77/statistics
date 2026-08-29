# -*- coding: utf-8 -*-
"""BookOasis local library statistics plugin."""

import os

from plugins.metadata.base import BaseMetadataProvider

from .statistics_core import MediaStatisticsAggregator, SnapshotStore, StatisticsAggregator, StatisticsRuntime

SELF_ID = "statistics"
PLUGIN_VERSION = "1.1.0"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_PLUGIN_DIR, "..", "..", "data", SELF_ID))
SUPPORTED_SESSIONS = ("general", "adult", "audiobook", "video")


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
    update_manifest = None

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "통계 플러그인은 메타데이터 적용 기능을 제공하지 않습니다."

    def _aggregate_session(self, db_type):
        gateway = self.get_db_gateway(db_type)
        if db_type in {"general", "adult"}:
            return StatisticsAggregator(gateway, session_type=db_type).aggregate()
        return MediaStatisticsAggregator(gateway, db_type).aggregate()

    def start_background_service(self, db_type):
        for index, session_type in enumerate(SUPPORTED_SESSIONS):
            _RUNTIMES[session_type].start(
                lambda session_type=session_type: self._aggregate_session(session_type),
                initial_delay=3 + (index * 12),
            )
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
        runtime = _runtime_for(db_type)
        if runtime is None:
            return {"success": False, "error": "지원하지 않는 통계 세션입니다."}
        return {"success": True, **runtime.get_state()}

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id != "statistics_rpc":
            return {"success": False, "error": "지원하지 않는 통계 요청입니다."}
        runtime = _runtime_for(db_type)
        if runtime is None:
            return {"success": False, "error": "지원하지 않는 통계 세션입니다."}

        context = context or {}
        op = str(context.get("op") or "snapshot").strip().lower()
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
