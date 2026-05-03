# django-dashboard

Self-contained Django app: declarative time-series metrics + JSON API + an
admin-styled HTML page with date-range / granularity / user filters.

Install from PyPI (or directly from this Git repo), add to `INSTALLED_APPS`,
point a URL prefix at it, write a metrics module — done.

## Install

Install directly from Git:

```bash
pip install git+https://github.com/icrazymob/django-dashboard.git
```

Pin to a tag or commit for reproducible builds:

```bash
pip install git+https://github.com/icrazymob/django-dashboard.git@v0.1.0
```

Or via `requirements.txt`:

```
django-dashboard @ git+https://github.com/icrazymob/django-dashboard.git@main
```

Or in `pyproject.toml` (PEP 508):

```toml
dependencies = [
    "django-dashboard @ git+https://github.com/icrazymob/django-dashboard.git@main",
]
```

The package name on PyPI/Git is `django-dashboard`; the Python import name is
`dashboard`.

## Wire it up

1. Add to `INSTALLED_APPS`:

   ```python
   # settings.py
   INSTALLED_APPS = [
       # ...
       'dashboard',
   ]
   ```

2. Mount the URLs at any prefix:

   ```python
   # urls.py
   from django.urls import include, path

   urlpatterns = [
       path('admin/dashboard/', include('dashboard.urls')),
       # ...
   ]
   ```

3. Tell the dashboard where your metrics live:

   ```python
   # settings.py
   DASHBOARD_METRICS_MODULES = ['myproject.dashboard_metrics']
   # OR omit it — every INSTALLED_APP is autodiscovered for a
   # `dashboard_metrics` submodule (Django's standard pattern).
   ```

4. Optional: pin the card order on the page.

   ```python
   # settings.py
   DASHBOARD_LAYOUT = ['signups', 'revenue', 'churn', ...]
   # When unset, every registered metric is shown in registration order.
   ```

5. Optional: also embed the dashboard above the standard `/admin/` index.

   ```python
   # urls.py
   from django.contrib import admin
   admin.site.index_template = 'admin/dashboard_index.html'
   ```

That's it. Visit `/admin/dashboard/` (or wherever you mounted it).
Auth: `@staff_member_required` is enforced on every endpoint.

## Writing a metric

```python
# myproject/dashboard_metrics.py
from dashboard.core import Metric, registry


@registry.register
class SignupsMetric(Metric):
    key = 'signups'                # used in the API URL and registry lookup
    title = 'New signups'          # shown on the card

    def build_queryset(self, mreq):
        from myproject.users.models import User
        return User.objects.all()
```

That's the whole metric. The framework handles date-range filtering,
granularity bucketing (hour/day/week/month/year), user filtering, and JSON
serialisation.

### Common knobs

```python
class RevenueMetric(Metric):
    key = 'revenue'
    title = 'Revenue'

    timestamp_field = 'paid_at'    # default 'created_at'
    user_filter_paths = ('user__',)  # fallback path for ?filter=… resolution

    def build_queryset(self, mreq):
        return Payment.objects.filter(status='paid')

    def get_aggregates(self):       # default: {'count': Count('id')}
        return {'count': Sum('amount')}
```

### Series breakdown (one dataset per group)

```python
class RevenueByPlanMetric(Metric):
    key = 'revenue_by_plan'
    title = 'Revenue by plan'
    series_field = 'plan_name'      # one Chart.js dataset per distinct value

    def build_queryset(self, mreq):
        return Payment.objects.filter(status='paid')

    def get_aggregates(self):
        return {'count': Sum('amount')}
```

### Custom compute (ratios, sliding windows, multi-query merges)

When the standard `group-by-bucket → aggregate` shape doesn't fit, override
`compute()` directly:

```python
from dashboard.core import Metric, Series, TimeSeries, bucket_keys, bucketed, by_bucket, registry


@registry.register
class ConversionMetric(Metric):
    key = 'conversion'
    title = 'Conversion %'

    def compute(self, mreq):
        users = self.apply_filters(User.objects.all(), mreq)
        all_rows  = bucketed(users, mreq, count=Count('id'))
        paid_rows = bucketed(users.filter(paid=True), mreq, count=Count('id'))

        labels   = bucket_keys(all_rows, paid_rows)
        all_idx  = by_bucket(all_rows)
        paid_idx = by_bucket(paid_rows)
        data = [
            round(paid_idx.get(b, 0) / (all_idx.get(b) or 1) * 100, 2)
            for b in labels
        ]
        return TimeSeries(labels=labels, datasets=[Series(title=self.title, data=data)])
```

`bucketed()`, `bucket_keys()`, `by_bucket()` are convenience helpers that
respect the active granularity from `mreq`.

## API

| URL                                         | Description                |
|---------------------------------------------|----------------------------|
| `GET /api/metrics/`                         | list of available metrics  |
| `GET /api/metrics/<key>/`                   | data for one metric (JSON) |

### Query parameters

- `granularity` = `hour | day | week | month | year`  — defaults to `day`.
- `date_from`, `date_to` = `YYYY-MM-DD`  — both optional.
- `date-range` = `YYYY-MM-DD, YYYY-MM-DD`  — alternative single param.
- `filter` = JSON dict (e.g. `{"user_id": 42}`). Tries direct filter first
  then falls back to `user__<key>=<v>` (configurable per metric via
  `user_filter_paths`).

The HTML page forwards its filter form's parameters to the API as-is, so
date-range / granularity / user filter all "just work" across all charts.
Each chart card additionally has its own granularity dropdown that
overrides the global one for that card alone.

## Front-end

`templates/dashboard/index.html` extends `admin/base_site.html` so it picks
up whatever admin theme the host project ships (Django default, Unfold,
Grappelli, etc.). The reusable chunk lives in
`templates/dashboard/_widgets.html`; if you want the same charts on the
admin index, point `admin.site.index_template` to
`'admin/dashboard_index.html'` (also shipped here) — it extends the
default `admin/index.html` and prepends the dashboard above the app list.

Vendor JS/CSS ship inside `static/dashboard/vendor/` — pinned versions, no
CDN dependency at runtime:

  - `chart.umd.min.js`                       — Chart.js 4.4.7
  - `chartjs-adapter-date-fns.bundle.min.js` — chartjs-adapter-date-fns 3.0.0
  - `air-datepicker.min.{js,css}`            — air-datepicker 3.5.3

Run `manage.py collectstatic` (or rely on `staticfiles` in dev) so the
files are served at `STATIC_URL/dashboard/vendor/…`.

To upgrade, replace the files; versions are documented in this README and
referenced via `{% static %}` in the template.

## Layout

```
django-dashboard/
  pyproject.toml          # packaging metadata
  MANIFEST.in
  LICENSE
  README.md
  dashboard/              # the importable Django app
    __init__.py
    apps.py               # AppConfig — wires up metric loading
    core.py               # Metric, Granularity, MetricRequest, registry, helpers
    views.py              # JSON API + HTML page views
    urls.py               # URL routes
    templates/dashboard/index.html
    templates/dashboard/_widgets.html
    templates/admin/dashboard_index.html
    static/dashboard/vendor/
      chart.umd.min.js
      chartjs-adapter-date-fns.bundle.min.js
      air-datepicker.min.{js,css}
```

## License

MIT — see [LICENSE](LICENSE).
