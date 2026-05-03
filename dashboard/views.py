"""HTTP views — page + JSON API.

URLs are mounted from ``dashboard/urls.py``; the parent project
includes them at any prefix it likes (typically ``/admin/dashboard/``).
"""
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.module_loading import import_string

from .core import MetricRequest, registry


def _ordered_metrics():
    """Metrics in display order — ``settings.DASHBOARD_LAYOUT`` if set,
    otherwise every registered metric in registration order."""
    layout = getattr(settings, 'DASHBOARD_LAYOUT', None)
    if layout is None:
        return registry.all()
    return [m for m in (registry.get(k) for k in layout) if m is not None]


@staff_member_required
def dashboard_index(request):
    return render(request, 'dashboard/index.html')


@staff_member_required
def metric_list(request):
    """Layout-aware metric directory consumed by the page JS to build cards."""
    return JsonResponse({
        'metrics': [
            {'key': m.key, 'title': m.title, 'visual_type': m.visual_type}
            for m in _ordered_metrics()
        ],
    })


@staff_member_required
def metric_data(request, key):
    metric = registry.get(key)
    if metric is None:
        return JsonResponse({'error': f'unknown metric: {key}'}, status=404)
    mreq = MetricRequest.from_http(request)
    payload = metric.compute(mreq).to_dict(visual_type=metric.visual_type)
    payload['title'] = metric.title
    return JsonResponse(payload)


@staff_member_required
def filter_options(request):
    """Pluggable dropdown options for the filter bar.

    The dashboard package is project-agnostic, so the actual list of UTM
    sources / countries / plans / etc. comes from a project-supplied
    callable referenced by ``settings.DASHBOARD_FILTER_OPTIONS``::

        DASHBOARD_FILTER_OPTIONS = 'myproject.dashboard_metrics.filter_options'

    The callable receives the request and returns a dict shaped like::

        {
          "groups": [
            {
              "key": "utm_source",
              "label": "UTM Source",
              "options": [
                {"label": "google", "filter": {"utm_source": "google"}},
                ...
              ]
            },
            ...
          ]
        }

    Each option carries the JSON ``filter`` fragment to merge into the
    main filter input when selected. Returns an empty list of groups when
    no callable is configured — the dropdowns simply don't render.
    """
    callback_path = getattr(settings, 'DASHBOARD_FILTER_OPTIONS', None)
    if not callback_path:
        return JsonResponse({'groups': []})
    callback = import_string(callback_path)
    return JsonResponse(callback(request))
