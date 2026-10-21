from django.urls import path

from . import views

urlpatterns = [
    path("csrf", views.csrf),
    path("me", views.me),
    path("signup", views.signup),
    path("login", views.login_view),
    path("logout", views.logout_view),
    path("password", views.password),
]
