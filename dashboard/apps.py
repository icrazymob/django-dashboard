from importlib import import_module

from django.apps import AppConfig
from django.conf import settings
from django.utils.module_loading import autodiscover_modules


class DashboardConfig(AppConfig):
    name = 'dashboard'
    verbose_name = 'Dashboard'

    def ready(self):
        self._load_metrics()
        self._install_unfold_app_nav()

    def _load_metrics(self):
        """Pull project-specific metric registrations into the global registry.

        Two modes:
          1) settings.DASHBOARD_METRICS_MODULES = ['path.to.module', ...]
             → import each module explicitly (the simple "one place" path)
          2) not set
             → autodiscover a `dashboard_metrics` submodule in every
               INSTALLED_APP (Django's standard pattern, used by admin)
        """
        modules = getattr(settings, 'DASHBOARD_METRICS_MODULES', None)
        if modules is None:
            autodiscover_modules('dashboard_metrics')
        else:
            for mod in modules:
                import_module(mod)

    def _install_unfold_app_nav(self):
        """Auto-append every registered admin app to Unfold's sidebar nav.

        Unfold's `SIDEBAR.navigation` is a static list, so the only way to
        inject the live app list is to wrap `admin.site.get_sidebar_list`.
        Skipped on a vanilla Django admin (no Unfold) — nothing to wrap.
        """
        from django.contrib import admin
        site = admin.site
        if not hasattr(site, 'get_sidebar_list'):
            return
        if getattr(site.get_sidebar_list, '_dashboard_wrapped', False):
            return  # idempotent under runserver autoreload

        original = site.get_sidebar_list

        def get_sidebar_list_with_apps(request):
            results = original(request)
            for app in site.get_app_list(request):
                items = []
                for model in app['models']:
                    url = model.get('admin_url') or model.get('add_url')
                    if not url:
                        continue
                    items.append({
                        'title': model['name'],
                        'link': url,
                        'has_permission': True,
                        'active': request.path.startswith(url),
                    })
                if items:
                    results.append({'title': app['name'], 'items': items})
            return results

        get_sidebar_list_with_apps._dashboard_wrapped = True
        site.get_sidebar_list = get_sidebar_list_with_apps
