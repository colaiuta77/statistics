# -*- coding: utf-8 -*-
"""BookOasis local library statistics plugin."""

import os

from plugins.metadata.base import BaseMetadataProvider

from .statistics_core import SnapshotStore, StatisticsAggregator, StatisticsRuntime

SELF_ID = "statistics"
PLUGIN_VERSION = "1.0.0"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_PLUGIN_DIR, "..", "..", "data", SELF_ID))
_STORE = SnapshotStore(os.path.join(_DATA_DIR, "statistics.db"))
_RUNTIME = StatisticsRuntime(store=_STORE, periodic_seconds=6 * 60 * 60)


def _aggregate_general():
    from services.plugin_db_gateway import PluginDatabaseGateway

    gateway = PluginDatabaseGateway("general")
    return StatisticsAggregator(gateway).aggregate()


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
        "sessions": ["general"],
    }
    update_manifest = None

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "통계 플러그인은 메타데이터 적용 기능을 제공하지 않습니다."

    def start_background_service(self, db_type):
        if str(db_type or "general").strip().lower() != "general":
            return None
        _RUNTIME.start(_aggregate_general, initial_delay=3)
        return None

    def on_scan_new_books_detected(self, db_type, payload):
        if str(db_type or "general").strip().lower() != "general":
            return {"success": True, "skipped": True, "message": "general DB만 지원합니다."}
        _RUNTIME.request_refresh(delay=45, debounce=True)
        return {
            "success": True,
            "scheduled": True,
            "message": "통계 재집계를 예약했습니다.",
        }

    def get_dashboard_data(self, db_type, limit=10):
        if str(db_type or "general").strip().lower() != "general":
            return {"success": False, "error": "general DB만 지원합니다."}
        return {"success": True, **_RUNTIME.get_state()}

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id != "statistics_rpc":
            return {"success": False, "error": "지원하지 않는 통계 요청입니다."}
        if str(db_type or "general").strip().lower() != "general":
            return {"success": False, "error": "general DB만 지원합니다."}

        context = context or {}
        op = str(context.get("op") or "snapshot").strip().lower()
        if op in {"snapshot", "status"}:
            return {"success": True, **_RUNTIME.get_state()}
        if op == "refresh":
            accepted = _RUNTIME.request_refresh(delay=0, debounce=False)
            return {
                "success": True,
                "accepted": bool(accepted),
                "message": "통계 재집계를 요청했습니다.",
                **_RUNTIME.get_state(),
            }
        return {"success": False, "error": f"지원하지 않는 작업입니다: {op}"}
