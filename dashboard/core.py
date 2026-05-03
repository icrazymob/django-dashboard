"""Core abstractions for the dashboard.

A ``Metric`` is a declarative time-series definition: a queryset, a timestamp
field, an aggregate (or several), and optional series-breakdown configuration.
The framework handles the common concerns — date-range filtering, granularity
bucketing, user filtering, JSON serialisation — so adding a new chart is
typically a 5-line subclass.

For metrics whose computation does not fit the standard "group-by-bucket +
aggregate" shape (e.g. ratios across two queries, sliding-window joins),
override ``compute(mreq)`` directly and return a ``TimeSeries``.
"""
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, ClassVar, Iterable, Optional

from django.core.exceptions import FieldError
from django.db.models import Count, QuerySet
from django.db.models.functions import TruncDay, TruncHour, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone


# ─── Granularity ────────────────────────────────────────────────────────────

class Granularity(str, Enum):
    HOUR = 'hour'
    DAY = 'day'
    WEEK = 'week'
    MONTH = 'month'
    YEAR = 'year'

    @property
    def trunc_class(self):
        return _TRUNC[self]

    @property
    def time_unit(self) -> str:
        """Chart.js time-axis ``unit`` matching this granularity."""
        return _TIME_UNIT[self]

    @classmethod
    def parse(cls, raw):
        if not raw:
            return cls.DAY
        try:
            return cls(str(raw).lower())
        except ValueError:
            return cls.DAY


_TRUNC = {
    Granularity.HOUR: TruncHour,
    Granularity.DAY: TruncDay,
    Granularity.WEEK: TruncWeek,
    Granularity.MONTH: TruncMonth,
    Granularity.YEAR: TruncYear,
}

_TIME_UNIT = {
    Granularity.HOUR: 'hour',
    Granularity.DAY: 'day',
    Granularity.WEEK: 'week',
    Granularity.MONTH: 'month',
    Granularity.YEAR: 'year',
}


# ─── Request parsing ────────────────────────────────────────────────────────

DEFAULT_LOOKBACK_DAYS = 90


@dataclass
class MetricRequest:
    """Parsed query parameters that shape every metric computation."""
    granularity: Granularity = Granularity.DAY
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    extra_filters: dict = field(default_factory=dict)

    @classmethod
    def from_http(cls, request) -> 'MetricRequest':
        gran = Granularity.parse(request.GET.get('granularity'))
        date_from, date_to = cls._parse_range(request)
        return cls(
            granularity=gran,
            date_from=date_from,
            date_to=date_to,
            extra_filters=cls._parse_filter(request),
        )

    @staticmethod
    def _parse_range(request):
        date_from = _parse_date(request.GET.get('date_from'))
        date_to = _parse_date(request.GET.get('date_to'))
        if not (date_from or date_to):
            legacy = request.GET.get('date-range')
            if legacy:
                parts = [p.strip() for p in legacy.split(',') if p.strip()]
                if parts:
                    date_from = _parse_date(parts[0])
                if len(parts) >= 2:
                    date_to = _parse_date(parts[1])
        if not date_from and not date_to:
            date_from = timezone.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        if date_to is not None:
            # Inclusive end-of-day
            date_to = date_to + timedelta(days=1) - timedelta(microseconds=1)
        return date_from, date_to

    @staticmethod
    def _parse_filter(request):
        raw = request.GET.get('filter')
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}


def _parse_date(s):
    if not s:
        return None
    try:
        d = date.fromisoformat(str(s)[:10])
        return timezone.make_aware(datetime(d.year, d.month, d.day))
    except (ValueError, TypeError):
        return None


# ─── Output shape ───────────────────────────────────────────────────────────

@dataclass
class Series:
    title: str
    data: list

    def to_dict(self):
        return {'title': self.title, 'data': self.data}


@dataclass
class TimeSeries:
    labels: list
    datasets: list

    def to_dict(self, visual_type: str = 'bar'):
        return {
            'visual_type': visual_type,
            'labels': list(self.labels),
            'datasets': [s.to_dict() for s in self.datasets],
        }


# ─── Metric base ────────────────────────────────────────────────────────────

class Metric:
    """Declarative time-series metric.

    Subclass and set ``key``, ``title`` (and optionally other class
    attributes) plus override ``build_queryset(mreq)``.

    Class attributes:
      key                 — slug used in API URL and registry lookup
      title               — display name (also default dataset label)
      visual_type         — Chart.js type: 'bar' / 'line' / ...
      timestamp_field     — column to bucket by (default 'created_at')
      user_filter_paths   — prefixes to try for user filters
      series_field        — if set, group by this and emit one dataset per value
      aggregates          — {label: ORM Aggregate}; default Count('id')

    Hooks:
      build_queryset(mreq)        — base queryset (required)
      get_series_label(value)     — pretty label for series_field values
      transform_value(label, v)   — post-aggregate transform (e.g. units→USD)
      compute(mreq) → TimeSeries  — full override for non-standard metrics
    """

    key: ClassVar[str] = ''
    title: ClassVar[str] = ''
    visual_type: ClassVar[str] = 'bar'

    timestamp_field: ClassVar[str] = 'created_at'
    user_filter_paths: ClassVar[tuple] = ('', 'user__')
    aggregates: ClassVar[Optional[dict]] = None
    series_field: ClassVar[Optional[str]] = None

    def build_queryset(self, mreq: MetricRequest) -> QuerySet:
        raise NotImplementedError

    def get_aggregates(self) -> dict:
        return dict(self.aggregates) if self.aggregates else {'count': Count('id')}

    def get_series_label(self, value) -> str:
        return '' if value is None else str(value)

    def transform_value(self, agg_name: str, value):
        return value if value is not None else 0

    def apply_filters(self, qs, mreq: MetricRequest):
        if mreq.extra_filters:
            qs = self._apply_user_filter(qs, mreq.extra_filters)
        if mreq.date_from is not None:
            qs = qs.filter(**{f'{self.timestamp_field}__gte': mreq.date_from})
        if mreq.date_to is not None:
            qs = qs.filter(**{f'{self.timestamp_field}__lte': mreq.date_to})
        return qs

    def _apply_user_filter(self, qs, filters):
        for prefix in self.user_filter_paths:
            try:
                return qs.filter(**{f'{prefix}{k}': v for k, v in filters.items()})
            except FieldError:
                continue
        return qs

    def compute(self, mreq: MetricRequest) -> TimeSeries:
        qs = self.build_queryset(mreq)
        qs = self.apply_filters(qs, mreq)
        bucket = mreq.granularity.trunc_class(self.timestamp_field)
        qs = qs.annotate(_bucket=bucket)
        group_fields = ['_bucket']
        if self.series_field:
            group_fields.append(self.series_field)
        aggs = self.get_aggregates()
        rows = list(qs.values(*group_fields).annotate(**aggs).order_by('_bucket'))
        return self._format(rows, list(aggs.keys()))

    def _format(self, rows, agg_keys):
        labels = sorted({_fmt_label(r['_bucket']) for r in rows if r['_bucket']})
        if self.series_field:
            grouped: dict = {}
            for r in rows:
                series = self.get_series_label(r[self.series_field])
                bucket_label = _fmt_label(r['_bucket'])
                for agg in agg_keys:
                    full = series if len(agg_keys) == 1 else f'{series} · {agg}'
                    grouped.setdefault(full, {})[bucket_label] = self.transform_value(agg, r[agg])
            datasets = [
                Series(title=lbl, data=[grouped[lbl].get(b, 0) for b in labels])
                for lbl in grouped
            ]
        else:
            indexed = {_fmt_label(r['_bucket']): r for r in rows}
            datasets = [
                Series(
                    title=agg if len(agg_keys) > 1 else (self.title or ''),
                    data=[self.transform_value(agg, indexed.get(b, {}).get(agg, 0)) for b in labels],
                )
                for agg in agg_keys
            ]
        return TimeSeries(labels=labels, datasets=datasets)


def _fmt_label(bucket) -> str:
    if bucket is None:
        return ''
    if isinstance(bucket, datetime):
        # Hour-truncated buckets carry a non-zero time component; preserve it
        # so charts can plot at hour resolution. Midnight-aligned datetimes
        # (day/week/month/year) collapse to plain ``YYYY-MM-DD`` as before.
        if bucket.hour or bucket.minute or bucket.second:
            return bucket.replace(microsecond=0).isoformat()
        return bucket.date().isoformat()
    if isinstance(bucket, date):
        return bucket.isoformat()
    return str(bucket)


# ─── Bucket helpers (for custom-compute metrics) ────────────────────────────

def bucket_keys(*row_sets: Iterable) -> list:
    """Sorted union of bucket labels across any number of row sets."""
    seen: set = set()
    for rows in row_sets:
        for r in rows:
            if r.get('_bucket'):
                seen.add(_fmt_label(r['_bucket']))
    return sorted(seen)


def by_bucket(rows, agg_key: str = 'count') -> dict:
    """Index aggregated rows by bucket label."""
    return {_fmt_label(r['_bucket']): r[agg_key] for r in rows if r['_bucket']}


def bucketed(qs, mreq: MetricRequest, *, timestamp_field='created_at',
             extra_group=None, **aggregates) -> list:
    """Quick helper for custom ``compute()`` overrides:

        rows = bucketed(qs, mreq, count=Count('id'))
    """
    bucket = mreq.granularity.trunc_class(timestamp_field)
    qs = qs.annotate(_bucket=bucket)
    group = ['_bucket'] + (extra_group or [])
    return list(qs.values(*group).annotate(**aggregates).order_by('_bucket'))


# ─── Registry ───────────────────────────────────────────────────────────────

class _Registry:
    def __init__(self):
        self._items: dict = {}

    def register(self, metric_cls):
        if not getattr(metric_cls, 'key', None):
            raise ValueError(f'{metric_cls.__name__} must define a non-empty `key`')
        self._items[metric_cls.key] = metric_cls()
        return metric_cls

    def get(self, key: str):
        return self._items.get(key)

    def all(self) -> list:
        return list(self._items.values())


registry = _Registry()
