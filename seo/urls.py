from django.urls import path

from . import views

urlpatterns = [
    path("audit", views.audit),
    path("pagespeed", views.pagespeed),
    path("health", views.health),
    *[path(name, views.tool(name)) for name in views.TOOLS],
    path("backlinks", views.backlinks),
    path("provider/status", views.provider_status),
    path("provider/usage", views.provider_usage),
    *[path(f"provider/{name}", views.provider_lookup(name)) for name in views.PROVIDER],
    path("provider/keywords", views.provider_keywords),
    path("provider/clear-cache", views.provider_clear_cache),
]
