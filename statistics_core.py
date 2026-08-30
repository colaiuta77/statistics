# -*- coding: utf-8 -*-
"""Core aggregation/runtime helpers for the local BookOasis statistics plugin."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

LARGEST_ITEMS_LIMIT = 100
TOKEN_DISTRIBUTION_LIMIT = 60


def normalize_token(value):
    return " ".join(str(value or "").strip().split())


def split_tokens(value):
    """Split BookOasis comma/semicolon/pipe metadata while preserving first label casing."""
    result = []
    seen = set()
    for raw in re.split(r"[,;|]+", str(value or "")):
        label = normalize_token(raw)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(label)
    return result


def _sorted_counter(counter, labels, limit=None):
    rows = sorted(counter.items(), key=lambda item: (-int(item[1]), labels[item[0]].casefold()))
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    return [{"label": labels[key], "count": int(count)} for key, count in rows]


def build_genre_statistics(rows, top_limit=20, chord_limit=12):
    """Build token distribution and weighted co-occurrence from GROUP BY genre rows."""
    token_counts = Counter()
    pair_counts = Counter()
    labels = {}

    for row in rows or []:
        weight = max(0, int(row.get("count") or 0))
        if not weight:
            continue
        tokens = split_tokens(row.get("genre"))
        keyed = []
        for label in tokens:
            key = label.casefold()
            labels.setdefault(key, label)
            token_counts[key] += weight
            keyed.append(key)
        if chord_limit:
            for left, right in combinations(sorted(set(keyed)), 2):
                pair_counts[(left, right)] += weight

    distribution = _sorted_counter(token_counts, labels, top_limit)
    chord_keys = [row["label"].casefold() for row in _sorted_counter(token_counts, labels, chord_limit)]
    chord_set = set(chord_keys)
    links = []
    for (left, right), value in sorted(
        pair_counts.items(),
        key=lambda item: (-int(item[1]), labels[item[0][0]].casefold(), labels[item[0][1]].casefold()),
    ):
        if left not in chord_set or right not in chord_set:
            continue
        links.append({"source": labels[left], "target": labels[right], "value": int(value)})

    return {
        "distribution": distribution,
        "genre_count": len(token_counts),
        "chord": {
            "nodes": [{"name": labels[key], "value": int(token_counts[key])} for key in chord_keys],
            "links": links,
        },
    }


def build_metadata_score_distribution(rows):
    """Convert 0..10 populated-field counts into stable 0..100 dashboard buckets."""
    buckets = [
        ("0–19", 0),
        ("20–39", 0),
        ("40–59", 0),
        ("60–79", 0),
        ("80–100", 0),
    ]
    counts = [0, 0, 0, 0, 0]
    for row in rows or []:
        filled = max(0, min(10, int(row.get("filled_count") or 0)))
        count = max(0, int(row.get("count") or 0))
        if filled <= 1:
            idx = 0
        elif filled <= 3:
            idx = 1
        elif filled <= 5:
            idx = 2
        elif filled <= 7:
            idx = 3
        else:
            idx = 4
        counts[idx] += count
    return [{"label": label, "count": counts[idx]} for idx, (label, _) in enumerate(buckets)]


class SnapshotStore:
    """Small persistent store for the last successful statistics snapshot."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        return conn

    def load(self):
        with self._lock:
            try:
                conn = self._connect()
                try:
                    row = conn.execute("SELECT payload FROM snapshot WHERE id = 1").fetchone()
                finally:
                    conn.close()
            except Exception:
                return None
        if not row:
            return None
        try:
            payload = json.loads(row[0])
            return payload if isinstance(payload, dict) else None
        except (TypeError, ValueError):
            return None

    def save(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("snapshot payload must be a dict")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO snapshot (id, payload, updated_at) VALUES (1, ?, ?)",
                    (encoded, updated_at),
                )
                conn.commit()
            finally:
                conn.close()


class StatisticsRuntime:
    """Single-process background refresher with stale-while-revalidate semantics."""

    def __init__(self, store, aggregate_callable=None, periodic_seconds=21600):
        self.store = store
        self.aggregate_callable = aggregate_callable
        self.periodic_seconds = max(60, int(periodic_seconds or 21600))
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._schedule_lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self._next_due = None
        self._snapshot = self.store.load()
        self._status = "idle" if self._snapshot else "waiting"
        self._last_error = ""
        self._last_attempt_at = ""

    def configure(self, aggregate_callable):
        if aggregate_callable is not None:
            self.aggregate_callable = aggregate_callable

    def get_state(self):
        with self._state_lock:
            return {
                "status": self._status,
                "last_error": self._last_error,
                "last_attempt_at": self._last_attempt_at,
                "snapshot": self._snapshot,
                "refresh_scheduled": self._next_due is not None,
            }

    def refresh_once(self):
        if not callable(self.aggregate_callable):
            with self._state_lock:
                self._status = "error"
                self._last_error = "statistics aggregator is not configured"
            return False
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            with self._state_lock:
                self._status = "refreshing"
                self._last_error = ""
                self._last_attempt_at = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                payload = self.aggregate_callable()
                if not isinstance(payload, dict):
                    raise ValueError("statistics aggregator returned invalid payload")
                payload.setdefault(
                    "generated_at",
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                )
                self.store.save(payload)
                with self._state_lock:
                    self._snapshot = payload
                    self._status = "idle"
                    self._last_error = ""
                return True
            except Exception as error:
                with self._state_lock:
                    self._status = "error"
                    self._last_error = str(error)[:1000]
                print(f"[Statistics] refresh failed: {error}")
                return False
        finally:
            self._refresh_lock.release()

    def request_refresh(self, delay=0, debounce=False):
        now = time.monotonic()
        due = now + max(0.0, float(delay or 0))
        with self._schedule_lock:
            if self._next_due is None:
                self._next_due = due
            elif debounce:
                self._next_due = max(self._next_due, due)
            else:
                self._next_due = min(self._next_due, due)
        self._wake.set()
        return True

    def start(self, aggregate_callable=None, initial_delay=3):
        self.configure(aggregate_callable)
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            thread = threading.Thread(
                target=self._worker,
                name="bookoasis-statistics",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        self.request_refresh(delay=initial_delay)
        return True

    def _take_due(self):
        with self._schedule_lock:
            due = self._next_due
            if due is not None and due <= time.monotonic():
                self._next_due = None
                return True
        return False

    def _seconds_until_due(self, periodic_due):
        now = time.monotonic()
        candidates = [max(0.0, periodic_due - now)]
        with self._schedule_lock:
            if self._next_due is not None:
                candidates.append(max(0.0, self._next_due - now))
        return min(candidates + [60.0])

    def _worker(self):
        periodic_due = time.monotonic() + self.periodic_seconds
        while True:
            if self._take_due() or time.monotonic() >= periodic_due:
                self.refresh_once()
                periodic_due = time.monotonic() + self.periodic_seconds
                continue
            self._wake.wait(self._seconds_until_due(periodic_due))
            self._wake.clear()


_METADATA_FIELDS = [
    ("author", "저자"),
    ("publisher", "출판사"),
    ("summary", "소개"),
    ("genre", "장르"),
    ("tags", "태그"),
    ("isbn", "ISBN"),
    ("cover", "표지"),
    ("release_date", "출간일"),
    ("total_pages", "페이지"),
    ("file_size", "파일 크기"),
]
_METADATA_MISSING_KEYS = [
    "summary",
    "tags",
    "isbn",
    "release_date",
    "total_pages",
    "publisher",
    "author",
    "cover",
]
_PAGE_BUCKET_ORDER = ["1–200", "201–400", "401–800", "801–1200", "1201+"]
_YEAR_RE = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|21\d{2})(?!\d)")


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_year(value):
    match = _YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _group_by_library(rows):
    grouped = defaultdict(list)
    for row in rows or []:
        grouped[str(_int(row.get("library_id")))].append(dict(row))
    return grouped


def _combine_label_rows(rows, value_key="count", label_key="label", output_value_key=None):
    output_value_key = output_value_key or value_key
    counter = Counter()
    labels = {}
    for row in rows or []:
        label = normalize_token(row.get(label_key)) or "기타"
        key = label.casefold()
        labels.setdefault(key, label)
        counter[key] += _int(row.get(value_key))
    return [
        {"label": labels[key], output_value_key: int(value)}
        for key, value in sorted(counter.items(), key=lambda item: (-int(item[1]), labels[item[0]].casefold()))
    ]


def _combine_period_rows(rows):
    counter = Counter()
    for row in rows or []:
        period = str(row.get("period") or "").strip()
        if period:
            counter[period] += _int(row.get("count"))
    return [{"period": period, "count": int(counter[period])} for period in sorted(counter)]


def _combine_format_period_rows(rows):
    counter = Counter()
    labels = {}
    for row in rows or []:
        period = str(row.get("period") or "").strip()
        label = normalize_token(row.get("label")) or "기타"
        if not period:
            continue
        key = label.casefold()
        labels.setdefault(key, label)
        counter[(period, key)] += _int(row.get("count"))
    return [
        {"period": period, "label": labels[key], "count": int(count)}
        for (period, key), count in sorted(counter.items(), key=lambda item: (item[0][0], labels[item[0][1]].casefold()))
    ]


def _release_statistics(rows):
    years = Counter()
    for row in rows or []:
        year = _extract_year(row.get("release_date"))
        if year is not None:
            years[year] += _int(row.get("count"))
    timeline = [{"year": year, "count": int(years[year])} for year in sorted(years)]
    decades = Counter()
    for year, count in years.items():
        decades[(year // 10) * 10] += count
    decade_rows = [
        {"label": f"{decade}s", "count": int(decades[decade])}
        for decade in sorted(decades)
    ]
    return {
        "timeline": timeline,
        "decades": decade_rows,
        "min_year": min(years) if years else None,
        "max_year": max(years) if years else None,
    }


def _metadata_completeness(rows, fields=None):
    fields = fields or _METADATA_FIELDS
    total = sum(_int(row.get("total")) for row in rows or [])
    result = []
    for key, label in fields:
        present = sum(_int(row.get(key)) for row in rows or [])
        percent = round((present / total) * 100, 1) if total else 0.0
        result.append({"key": key, "label": label, "present": present, "total": total, "percent": percent})
    return result


def build_metadata_missing(rows, fields=None, missing_keys=None):
    fields = fields or _METADATA_FIELDS
    missing_keys = missing_keys or _METADATA_MISSING_KEYS
    total = sum(_int(row.get("total")) for row in rows or [])
    labels = dict(fields)
    order = {key: index for index, key in enumerate(missing_keys)}
    result = []
    for key in missing_keys:
        present = sum(_int(row.get(key)) for row in rows or [])
        missing = max(0, total - present)
        if missing > 0:
            result.append({"key": key, "label": labels.get(key, key), "count": missing})
    result.sort(key=lambda row: (-_int(row.get("count")), order.get(row.get("key"), 999)))
    return result


def _metadata_heatmap(libraries, metadata_rows, fields=None):
    fields = fields or _METADATA_FIELDS
    grouped = _group_by_library(metadata_rows)
    lib_rows = []
    values = []
    for library in libraries:
        lib_id = str(_int(library.get("id")))
        rows = grouped.get(lib_id, [])
        total = sum(_int(row.get("total")) for row in rows)
        lib_rows.append({"id": _int(library.get("id")), "name": str(library.get("name") or f"#{lib_id}")})
        for field_idx, (key, _label) in enumerate(fields):
            present = sum(_int(row.get(key)) for row in rows)
            percent = round((present / total) * 100, 1) if total else 0.0
            values.append([field_idx, len(lib_rows) - 1, percent])
    return {
        "fields": [{"key": key, "label": label} for key, label in fields],
        "libraries": lib_rows,
        "values": values,
    }


class StatisticsAggregator:
    """Generate all chart scopes using grouped BookOasis queries only."""

    def __init__(self, gateway, session_type="general"):
        self.gateway = gateway
        self.session_type = str(session_type or "general")
        self.engine = str(getattr(gateway, "_engine", os.environ.get("DB_ENGINE", "sqlite")) or "sqlite").lower()

    @staticmethod
    def _active_where():
        return "COALESCE(is_deleted, 0) = 0"

    def _month_expr(self, column):
        if self.engine == "mariadb":
            return f"DATE_FORMAT({column}, '%%Y-%%m')"
        return f"strftime('%Y-%m', {column})"

    def _fetch_all(self, marker, sql):
        return self.gateway.fetch_all(f"/*statistics:{marker}*/\n{sql}") or []

    def _fetch_one(self, marker, sql):
        return self.gateway.fetch_one(f"/*statistics:{marker}*/\n{sql}") or {}

    def _top_rows(self, field, marker, limit=15):
        where = self._active_where()
        global_rows = self._fetch_all(
            f"{marker}_all",
            f"""
            SELECT TRIM({field}) AS label, COUNT(*) AS count
            FROM books
            WHERE {where} AND TRIM(COALESCE({field}, '')) <> ''
            GROUP BY TRIM({field})
            ORDER BY count DESC, label
            LIMIT {int(limit)}
            """,
        )
        per_library = self._fetch_all(
            f"{marker}_by_library",
            f"""
            SELECT library_id, label, count
            FROM (
                SELECT grouped.*,
                       ROW_NUMBER() OVER (PARTITION BY library_id ORDER BY count DESC, label) AS rn
                FROM (
                    SELECT library_id, TRIM({field}) AS label, COUNT(*) AS count
                    FROM books
                    WHERE {where} AND TRIM(COALESCE({field}, '')) <> ''
                    GROUP BY library_id, TRIM({field})
                ) grouped
            ) ranked
            WHERE rn <= {int(limit)}
            ORDER BY library_id, rn
            """,
        )
        return global_rows, _group_by_library(per_library)

    def aggregate(self):
        started = time.monotonic()
        where = self._active_where()
        libraries = [
            {
                "id": _int(row.get("id")),
                "name": str(row.get("name") or ""),
                "color": str(row.get("color") or ""),
                "icon": str(row.get("icon") or ""),
                "last_scanned_at": str(row.get("last_scanned_at") or ""),
            }
            for row in self._fetch_all(
                "libraries",
                "SELECT id, name, color, icon, last_scanned_at FROM libraries ORDER BY sort_order, name, id",
            )
        ]

        summary_by_library = self._fetch_all(
            "summary_by_library",
            f"""
            SELECT library_id,
                   COUNT(*) AS book_count,
                   COUNT(DISTINCT NULLIF(TRIM(author), '')) AS author_count,
                   COUNT(DISTINCT NULLIF(TRIM(series_name), '')) AS series_count,
                   COUNT(DISTINCT NULLIF(TRIM(publisher), '')) AS publisher_count,
                   COALESCE(SUM(COALESCE(file_size, 0)), 0) AS storage_bytes
            FROM books
            WHERE {where}
            GROUP BY library_id
            """,
        )
        summary_all = self._fetch_one(
            "summary_all",
            f"""
            SELECT COUNT(*) AS book_count,
                   COUNT(DISTINCT NULLIF(TRIM(author), '')) AS author_count,
                   COUNT(DISTINCT NULLIF(TRIM(series_name), '')) AS series_count,
                   COUNT(DISTINCT NULLIF(TRIM(publisher), '')) AS publisher_count,
                   COALESCE(SUM(COALESCE(file_size, 0)), 0) AS storage_bytes
            FROM books
            WHERE {where}
            """,
        )
        formats = self._fetch_all(
            "formats",
            f"""
            SELECT library_id, LOWER(TRIM(COALESCE(file_format, 'unknown'))) AS label,
                   COUNT(*) AS count, COALESCE(SUM(COALESCE(file_size, 0)), 0) AS bytes
            FROM books
            WHERE {where}
            GROUP BY library_id, LOWER(TRIM(COALESCE(file_format, 'unknown')))
            """,
        )
        genres = self._fetch_all(
            "genres",
            f"""
            SELECT library_id, genre, COUNT(*) AS count
            FROM books
            WHERE {where} AND TRIM(COALESCE(genre, '')) <> ''
            GROUP BY library_id, genre
            """,
        )
        tags = self._fetch_all(
            "tags",
            f"""
            SELECT library_id, tags AS genre, COUNT(*) AS count
            FROM books
            WHERE {where} AND TRIM(COALESCE(tags, '')) <> ''
            GROUP BY library_id, tags
            """,
        )

        metadata_fields_sql = {
            "author": "TRIM(COALESCE(author, '')) <> ''",
            "publisher": "TRIM(COALESCE(publisher, '')) <> ''",
            "summary": "TRIM(COALESCE(summary, '')) <> ''",
            "genre": "TRIM(COALESCE(genre, '')) <> ''",
            "tags": "TRIM(COALESCE(tags, '')) <> ''",
            "isbn": "TRIM(COALESCE(isbn, '')) <> ''",
            "cover": "TRIM(COALESCE(cover_image, '')) <> ''",
            "release_date": "TRIM(COALESCE(release_date, '')) <> ''",
            "total_pages": "COALESCE(total_pages, 0) > 0",
            "file_size": "COALESCE(file_size, 0) > 0",
        }
        metadata_select = ",\n".join(
            f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS {key}"
            for key, condition in metadata_fields_sql.items()
        )
        metadata = self._fetch_all(
            "metadata",
            f"""
            SELECT library_id, COUNT(*) AS total,
                   {metadata_select}
            FROM books
            WHERE {where}
            GROUP BY library_id
            """,
        )
        filled_expr = " + ".join(
            f"CASE WHEN {condition} THEN 1 ELSE 0 END"
            for condition in metadata_fields_sql.values()
        )
        scores = self._fetch_all(
            "scores",
            f"""
            SELECT library_id, filled_count, COUNT(*) AS count
            FROM (
                SELECT library_id, ({filled_expr}) AS filled_count
                FROM books
                WHERE {where}
            ) scored
            GROUP BY library_id, filled_count
            ORDER BY library_id, filled_count
            """,
        )

        month_expr = self._month_expr("created_at")
        created_month = self._fetch_all(
            "created_month",
            f"""
            SELECT library_id, {month_expr} AS period, COUNT(*) AS count
            FROM books
            WHERE {where} AND created_at IS NOT NULL
            GROUP BY library_id, {month_expr}
            ORDER BY period
            """,
        )
        format_month = self._fetch_all(
            "format_month",
            f"""
            SELECT library_id, {month_expr} AS period,
                   LOWER(TRIM(COALESCE(file_format, 'unknown'))) AS label,
                   COUNT(*) AS count
            FROM books
            WHERE {where} AND created_at IS NOT NULL
            GROUP BY library_id, {month_expr}, LOWER(TRIM(COALESCE(file_format, 'unknown')))
            ORDER BY period, label
            """,
        )
        release_dates = self._fetch_all(
            "release_dates",
            f"""
            SELECT library_id, release_date, COUNT(*) AS count
            FROM books
            WHERE {where} AND TRIM(COALESCE(release_date, '')) <> ''
            GROUP BY library_id, release_date
            """,
        )
        page_buckets = self._fetch_all(
            "page_buckets",
            f"""
            SELECT library_id,
                   CASE
                     WHEN total_pages <= 200 THEN '1–200'
                     WHEN total_pages <= 400 THEN '201–400'
                     WHEN total_pages <= 800 THEN '401–800'
                     WHEN total_pages <= 1200 THEN '801–1200'
                     ELSE '1201+'
                   END AS label,
                   COUNT(*) AS count
            FROM books
            WHERE {where} AND COALESCE(total_pages, 0) > 0
            GROUP BY library_id, label
            """,
        )

        top_authors_all, top_authors_by_lib = self._top_rows("author", "top_authors")
        top_series_all, top_series_by_lib = self._top_rows("series_name", "top_series")
        top_publishers_all, top_publishers_by_lib = self._top_rows("publisher", "top_publishers")

        largest_all = self._fetch_all(
            "largest_all",
            f"""
            SELECT id, library_id, title, series_name, file_format, file_size AS bytes
            FROM books
            WHERE {where} AND COALESCE(file_size, 0) > 0
            ORDER BY file_size DESC, id DESC
            LIMIT {LARGEST_ITEMS_LIMIT}
            """,
        )
        largest_by_library = self._fetch_all(
            "largest_by_library",
            f"""
            SELECT id, library_id, title, series_name, file_format, bytes
            FROM (
                SELECT id, library_id, title, series_name, file_format, file_size AS bytes,
                       ROW_NUMBER() OVER (PARTITION BY library_id ORDER BY file_size DESC, id DESC) AS rn
                FROM books
                WHERE {where} AND COALESCE(file_size, 0) > 0
            ) ranked
            WHERE rn <= {LARGEST_ITEMS_LIMIT}
            ORDER BY library_id, rn
            """,
        )

        grouped = {
            "summary": _group_by_library(summary_by_library),
            "formats": _group_by_library(formats),
            "genres": _group_by_library(genres),
            "tags": _group_by_library(tags),
            "metadata": _group_by_library(metadata),
            "scores": _group_by_library(scores),
            "created_month": _group_by_library(created_month),
            "format_month": _group_by_library(format_month),
            "release_dates": _group_by_library(release_dates),
            "page_buckets": _group_by_library(page_buckets),
            "largest": _group_by_library(largest_by_library),
        }

        all_rows = {
            key: [row for rows in value.values() for row in rows]
            for key, value in grouped.items()
            if key != "summary"
        }
        heatmap = _metadata_heatmap(libraries, metadata)
        current_year = str(datetime.now().year)

        def build_scope(scope_id):
            is_all = scope_id == "all"
            if is_all:
                summary_source = summary_all
                format_rows = all_rows.get("formats", [])
                genre_rows = all_rows.get("genres", [])
                tag_rows = all_rows.get("tags", [])
                metadata_rows = all_rows.get("metadata", [])
                score_rows = all_rows.get("scores", [])
                created_rows = all_rows.get("created_month", [])
                format_month_rows = all_rows.get("format_month", [])
                release_rows = all_rows.get("release_dates", [])
                page_rows = all_rows.get("page_buckets", [])
                largest_rows = largest_all
                top_authors = top_authors_all
                top_series = top_series_all
                top_publishers = top_publishers_all
                selected_libraries = libraries
            else:
                summary_source = (grouped["summary"].get(scope_id) or [{}])[0]
                format_rows = grouped["formats"].get(scope_id, [])
                genre_rows = grouped["genres"].get(scope_id, [])
                tag_rows = grouped["tags"].get(scope_id, [])
                metadata_rows = grouped["metadata"].get(scope_id, [])
                score_rows = grouped["scores"].get(scope_id, [])
                created_rows = grouped["created_month"].get(scope_id, [])
                format_month_rows = grouped["format_month"].get(scope_id, [])
                release_rows = grouped["release_dates"].get(scope_id, [])
                page_rows = grouped["page_buckets"].get(scope_id, [])
                largest_rows = grouped["largest"].get(scope_id, [])
                top_authors = top_authors_by_lib.get(scope_id, [])
                top_series = top_series_by_lib.get(scope_id, [])
                top_publishers = top_publishers_by_lib.get(scope_id, [])
                selected_libraries = [row for row in libraries if str(row["id"]) == scope_id]

            genre_stats = build_genre_statistics(genre_rows, top_limit=TOKEN_DISTRIBUTION_LIMIT, chord_limit=12)
            tag_stats = build_genre_statistics(tag_rows, top_limit=TOKEN_DISTRIBUTION_LIMIT, chord_limit=0)
            release_stats = _release_statistics(release_rows)
            books_added = _combine_period_rows(created_rows)[-60:]
            format_over_time = _combine_format_period_rows(format_month_rows)
            periods = sorted({row["period"] for row in format_over_time})[-60:]
            period_set = set(periods)
            format_over_time = [row for row in format_over_time if row["period"] in period_set]
            page_counts = Counter()
            for row in page_rows:
                page_counts[str(row.get("label") or "")] += _int(row.get("count"))

            summary = {
                "book_count": _int(summary_source.get("book_count")),
                "author_count": _int(summary_source.get("author_count")),
                "series_count": _int(summary_source.get("series_count")),
                "publisher_count": _int(summary_source.get("publisher_count")),
                "storage_bytes": _int(summary_source.get("storage_bytes")),
                "genre_count": _int(genre_stats.get("genre_count")),
                "library_count": len(selected_libraries),
                "publication_year_min": release_stats["min_year"],
                "publication_year_max": release_stats["max_year"],
                "added_this_year": sum(
                    _int(row.get("count")) for row in books_added if str(row.get("period") or "").startswith(current_year)
                ),
            }
            completeness = _metadata_completeness(metadata_rows)
            return {
                "summary": summary,
                "format_distribution": _combine_label_rows(format_rows, "count")[:15],
                "storage_by_format": _combine_label_rows(format_rows, "bytes", output_value_key="bytes")[:15],
                "genre_distribution": genre_stats["distribution"],
                "tag_distribution": tag_stats["distribution"],
                "genre_cooccurrence": genre_stats["chord"],
                "top_authors": [{"label": str(row.get("label") or ""), "count": _int(row.get("count"))} for row in top_authors],
                "top_series": [{"label": str(row.get("label") or ""), "count": _int(row.get("count"))} for row in top_series],
                "top_publishers": [{"label": str(row.get("label") or ""), "count": _int(row.get("count"))} for row in top_publishers],
                "metadata_completeness": completeness,
                "metadata_score_distribution": build_metadata_score_distribution(score_rows),
                "metadata_missing": build_metadata_missing(metadata_rows),
                "library_metadata_completeness": _metadata_heatmap(selected_libraries, metadata_rows) if not is_all else heatmap,
                "books_added_over_time": books_added,
                "format_share_over_time": format_over_time,
                "publication_decade": release_stats["decades"],
                "publication_year_timeline": release_stats["timeline"],
                "page_count_distribution": [
                    {"label": label, "count": int(page_counts.get(label, 0))} for label in _PAGE_BUCKET_ORDER
                ],
                "largest_books": [
                    {
                        "id": _int(row.get("id")),
                        "title": str(row.get("title") or row.get("series_name") or ""),
                        "format": str(row.get("file_format") or ""),
                        "bytes": _int(row.get("bytes")),
                    }
                    for row in largest_rows
                ],
            }

        scopes = {"all": build_scope("all")}
        for library in libraries:
            scopes[str(library["id"])] = build_scope(str(library["id"]))

        return {
            "version": 2,
            "session_type": self.session_type,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "generation_ms": round((time.monotonic() - started) * 1000),
            "engine": self.engine,
            "libraries": libraries,
            "scopes": scopes,
        }


_MEDIA_SCHEMAS = {
    "audiobook": {
        "item_table": "audiobooks",
        "child_table": "audiobook_tracks",
        "child_fk": "audiobook_id",
        "item_select": """
            id, library_id, title, author, publisher, code, poster,
            premiered AS release_date, description, folder_name,
            total_duration AS duration_seconds, total_tracks AS child_count,
            file_type, web_id, created_at,
            '' AS genre, '' AS backdrop
        """,
        "metadata_fields": [
            ("author", "저자"),
            ("publisher", "출판사"),
            ("description", "소개"),
            ("poster", "표지"),
            ("release_date", "출시일"),
            ("duration_seconds", "재생시간"),
            ("child_count", "트랙"),
            ("file_type", "파일 유형"),
            ("code", "코드"),
            ("file_size", "파일 크기"),
        ],
        "count_buckets": [(1, "1"), (5, "2–5"), (10, "6–10"), (20, "11–20")],
    },
    "video": {
        "item_table": "videos",
        "child_table": "video_episodes",
        "child_fk": "video_id",
        "item_select": """
            id, library_id, title, '' AS author, '' AS publisher, '' AS code,
            poster, premiered AS release_date, description, folder_name,
            total_duration AS duration_seconds, total_episodes AS child_count,
            '' AS file_type, web_id, created_at, genres AS genre, backdrop
        """,
        "metadata_fields": [
            ("genre", "장르"),
            ("poster", "포스터"),
            ("backdrop", "배경 이미지"),
            ("release_date", "출시일"),
            ("description", "소개"),
            ("duration_seconds", "재생시간"),
            ("child_count", "에피소드"),
            ("web_id", "웹 ID"),
            ("folder_name", "폴더명"),
            ("file_size", "파일 크기"),
        ],
        "count_buckets": [(1, "1"), (5, "2–5"), (10, "6–10"), (20, "11–20")],
    },
}


def _month_key(value):
    match = re.search(r"(?<!\d)(\d{4})[-/.](\d{1,2})(?!\d)", str(value or ""))
    if not match:
        return ""
    month = int(match.group(2))
    return f"{match.group(1)}-{month:02d}" if 1 <= month <= 12 else ""


def _top_item_rows(items, field, limit=15):
    counter = Counter()
    labels = {}
    for item in items:
        label = normalize_token(item.get(field))
        if not label:
            continue
        key = label.casefold()
        labels.setdefault(key, label)
        counter[key] += 1
    return [
        {"label": labels[key], "count": int(count)}
        for key, count in sorted(counter.items(), key=lambda row: (-row[1], labels[row[0]].casefold()))[:limit]
    ]


class MediaStatisticsAggregator:
    """Aggregate audiobook/video statistics through their native parent and child tables."""

    def __init__(self, gateway, session_type):
        if session_type not in _MEDIA_SCHEMAS:
            raise ValueError(f"unsupported media statistics session: {session_type}")
        self.gateway = gateway
        self.session_type = session_type
        self.schema = _MEDIA_SCHEMAS[session_type]
        self.engine = str(getattr(gateway, "_engine", os.environ.get("DB_ENGINE", "sqlite")) or "sqlite").lower()

    def _fetch_all(self, marker, sql):
        return self.gateway.fetch_all(f"/*statistics:{self.session_type}:{marker}*/\n{sql}") or []

    def _load_libraries(self):
        return [
            {
                "id": _int(row.get("id")),
                "name": str(row.get("name") or ""),
                "color": str(row.get("color") or ""),
                "icon": str(row.get("icon") or ""),
                "last_scanned_at": str(row.get("last_scanned_at") or ""),
            }
            for row in self._fetch_all(
                "libraries",
                "SELECT id, name, color, icon, last_scanned_at FROM libraries ORDER BY sort_order, name, id",
            )
        ]

    def _load_items(self):
        table = self.schema["item_table"]
        return self._fetch_all(
            "items",
            f"""
            SELECT {self.schema['item_select']}
            FROM {table}
            WHERE COALESCE(is_deleted, 0) = 0
            """,
        )

    def _load_files(self):
        item_table = self.schema["item_table"]
        child_table = self.schema["child_table"]
        child_fk = self.schema["child_fk"]
        return self._fetch_all(
            "files",
            f"""
            SELECT child.{child_fk} AS item_id,
                   LOWER(TRIM(COALESCE(child.format, 'unknown'))) AS label,
                   COUNT(*) AS count,
                   COALESCE(SUM(COALESCE(child.file_size, 0)), 0) AS bytes
            FROM {child_table} child
            JOIN {item_table} item ON item.id = child.{child_fk}
            WHERE COALESCE(item.is_deleted, 0) = 0
            GROUP BY child.{child_fk}, LOWER(TRIM(COALESCE(child.format, 'unknown')))
            """,
        )

    def _metadata_rows(self, libraries, items, bytes_by_item, count_by_item):
        fields = self.schema["metadata_fields"]
        rows = []
        for library in libraries:
            library_id = _int(library.get("id"))
            selected = [item for item in items if _int(item.get("library_id")) == library_id]
            row = {"library_id": library_id, "total": len(selected)}
            for key, _label in fields:
                if key == "file_size":
                    row[key] = sum(1 for item in selected if bytes_by_item.get(_int(item.get("id")), 0) > 0)
                elif key == "child_count":
                    row[key] = sum(1 for item in selected if count_by_item.get(_int(item.get("id")), 0) > 0)
                elif key == "duration_seconds":
                    row[key] = sum(1 for item in selected if float(item.get(key) or 0) > 0)
                else:
                    row[key] = sum(1 for item in selected if normalize_token(item.get(key)))
            rows.append(row)
        return rows

    def _score_rows(self, items, bytes_by_item, count_by_item):
        fields = self.schema["metadata_fields"]
        counter = Counter()
        for item in items:
            filled = 0
            for key, _label in fields:
                if key == "file_size":
                    present = bytes_by_item.get(_int(item.get("id")), 0) > 0
                elif key == "child_count":
                    present = count_by_item.get(_int(item.get("id")), 0) > 0
                elif key == "duration_seconds":
                    present = float(item.get(key) or 0) > 0
                else:
                    present = bool(normalize_token(item.get(key)))
                filled += int(present)
            counter[(_int(item.get("library_id")), filled)] += 1
        return [
            {"library_id": library_id, "filled_count": filled, "count": count}
            for (library_id, filled), count in sorted(counter.items())
        ]

    def _count_distribution(self, items, count_by_item):
        labels = [label for _limit, label in self.schema["count_buckets"]] + ["21+"]
        counts = Counter()
        for item in items:
            value = count_by_item.get(_int(item.get("id")), 0)
            if value <= 0:
                continue
            label = "21+"
            for limit, candidate in self.schema["count_buckets"]:
                if value <= limit:
                    label = candidate
                    break
            counts[label] += 1
        return [{"label": label, "count": int(counts[label])} for label in labels]

    def aggregate(self):
        started = time.monotonic()
        libraries = self._load_libraries()
        # ponytail: media parent rows are loaded once; move scope grouping into SQL if catalogs reach book-scale size.
        items = [dict(row) for row in self._load_items()]
        file_rows = [dict(row) for row in self._load_files()]
        item_by_id = {_int(item.get("id")): item for item in items}
        bytes_by_item = Counter()
        count_by_item = Counter()
        for row in file_rows:
            item_id = _int(row.get("item_id"))
            bytes_by_item[item_id] += _int(row.get("bytes"))
            count_by_item[item_id] += _int(row.get("count"))

        metadata = self._metadata_rows(libraries, items, bytes_by_item, count_by_item)
        scores = self._score_rows(items, bytes_by_item, count_by_item)
        metadata_by_library = _group_by_library(metadata)
        scores_by_library = _group_by_library(scores)
        fields = self.schema["metadata_fields"]
        current_year = str(datetime.now().year)

        def build_scope(scope_id):
            is_all = scope_id == "all"
            selected_items = items if is_all else [item for item in items if str(_int(item.get("library_id"))) == scope_id]
            selected_ids = {_int(item.get("id")) for item in selected_items}
            selected_files = [row for row in file_rows if _int(row.get("item_id")) in selected_ids]
            selected_libraries = libraries if is_all else [row for row in libraries if str(row["id"]) == scope_id]
            metadata_rows = metadata if is_all else metadata_by_library.get(scope_id, [])
            score_rows = scores if is_all else scores_by_library.get(scope_id, [])

            genre_stats = build_genre_statistics(
                [{"genre": item.get("genre"), "count": 1} for item in selected_items],
                top_limit=TOKEN_DISTRIBUTION_LIMIT,
                chord_limit=12,
            )
            release_stats = _release_statistics(
                [{"release_date": item.get("release_date"), "count": 1} for item in selected_items]
            )
            added_counter = Counter(_month_key(item.get("created_at")) for item in selected_items)
            added_counter.pop("", None)
            books_added = [{"period": key, "count": int(added_counter[key])} for key in sorted(added_counter)][-60:]

            format_month = []
            for row in selected_files:
                item = item_by_id.get(_int(row.get("item_id")), {})
                period = _month_key(item.get("created_at"))
                if period:
                    format_month.append({"period": period, "label": row.get("label"), "count": row.get("count")})
            format_over_time = _combine_format_period_rows(format_month)
            periods = sorted({row["period"] for row in format_over_time})[-60:]
            format_over_time = [row for row in format_over_time if row["period"] in set(periods)]

            summary = {
                "item_count": len(selected_items),
                "child_count": sum(_int(row.get("count")) for row in selected_files),
                "author_count": len({normalize_token(item.get("author")).casefold() for item in selected_items if normalize_token(item.get("author"))}),
                "publisher_count": len({normalize_token(item.get("publisher")).casefold() for item in selected_items if normalize_token(item.get("publisher"))}),
                "storage_bytes": sum(_int(row.get("bytes")) for row in selected_files),
                "duration_seconds": round(sum(float(item.get("duration_seconds") or 0) for item in selected_items)),
                "genre_count": _int(genre_stats.get("genre_count")),
                "library_count": len(selected_libraries),
                "publication_year_min": release_stats["min_year"],
                "publication_year_max": release_stats["max_year"],
                "added_this_year": sum(count for period, count in added_counter.items() if period.startswith(current_year)),
            }
            largest = sorted(selected_items, key=lambda item: (-bytes_by_item.get(_int(item.get("id")), 0), -_int(item.get("id"))))[:LARGEST_ITEMS_LIMIT]
            return {
                "summary": summary,
                "format_distribution": _combine_label_rows(selected_files, "count")[:15],
                "storage_by_format": _combine_label_rows(selected_files, "bytes", output_value_key="bytes")[:15],
                "genre_distribution": genre_stats["distribution"],
                "genre_cooccurrence": genre_stats["chord"],
                "top_authors": _top_item_rows(selected_items, "author"),
                "top_series": [],
                "top_publishers": _top_item_rows(selected_items, "publisher"),
                "metadata_completeness": _metadata_completeness(metadata_rows, fields),
                "metadata_score_distribution": build_metadata_score_distribution(score_rows),
                "metadata_missing": build_metadata_missing(metadata_rows, fields, [key for key, _label in fields]),
                "library_metadata_completeness": _metadata_heatmap(selected_libraries, metadata_rows, fields),
                "books_added_over_time": books_added,
                "format_share_over_time": format_over_time,
                "publication_decade": release_stats["decades"],
                "publication_year_timeline": release_stats["timeline"],
                "page_count_distribution": self._count_distribution(selected_items, count_by_item),
                "largest_books": [
                    {
                        "id": _int(item.get("id")),
                        "title": str(item.get("title") or ""),
                        "format": str(next((row.get("label") for row in selected_files if _int(row.get("item_id")) == _int(item.get("id"))), "")),
                        "bytes": int(bytes_by_item.get(_int(item.get("id")), 0)),
                    }
                    for item in largest
                    if bytes_by_item.get(_int(item.get("id")), 0) > 0
                ],
            }

        scopes = {"all": build_scope("all")}
        for library in libraries:
            scopes[str(library["id"])] = build_scope(str(library["id"]))

        return {
            "version": 2,
            "session_type": self.session_type,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "generation_ms": round((time.monotonic() - started) * 1000),
            "engine": self.engine,
            "libraries": libraries,
            "scopes": scopes,
        }
