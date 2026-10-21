from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from core import views as core_views

admin.site.site_header = "Scoutly admin"
admin.site.site_title = "Scoutly admin"

urlpatterns = [
    path(settings.ADMIN_PATH, admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("seo.urls")),
    path("api/", include("workspace.urls")),
    # everything else is the static site (index.html, feature pages, app pages, assets)
    re_path(r"^(?P<path>.*)$", core_views.site),
]
