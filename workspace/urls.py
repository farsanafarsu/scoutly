from django.urls import path

from . import views

urlpatterns = [
    path("sites", views.sites),
    path("sites/<str:pk>", views.site_detail),
    path("audits", views.audits),
    path("audits/<str:pk>", views.audit_detail),
    path("keyword-lists", views.keyword_lists),
    path("keyword-lists/<str:pk>", views.keyword_list_detail),
    path("keyword-lists/<str:pk>/keywords", views.keyword_list_items),
    path("rank-snapshots", views.rank_snapshots),
    path("rank-snapshots/<str:day>", views.rank_snapshot_detail),
    path("campaigns", views.campaigns),
    path("campaigns/<str:pk>", views.campaign_detail),
    path("preferences", views.preferences),
    path("state", views.states),
    path("state/<str:key>", views.state_detail),
    path("export", views.export),
    path("store", views.store_all),
    path("store/<str:key>", views.store_key),
]
