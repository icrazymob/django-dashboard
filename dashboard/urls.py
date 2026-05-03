"""URL routes — mount at any prefix from your project's root URLconf.

Example::

    path('admin/dashboard/', include('dashboard.urls')),
"""
from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard_index, name='index'),
    path('api/metrics/', views.metric_list, name='metric_list'),
    path('api/metrics/<slug:key>/', views.metric_data, name='metric_data'),
    path('api/filter-options/', views.filter_options, name='filter_options'),
]
