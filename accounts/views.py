"""Account endpoints under /api/auth/. Session cookies; every change needs the CSRF token
(GET /api/auth/csrf sets the cookie, then send it back as the X-CSRFToken header)."""
from django.contrib.auth import authenticate, login, logout, password_validation, update_session_auth_hash
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie

from core import ratelimit
from core.api import ApiError, api, body, text
from core.db import atomic

from .models import User

LOGIN_FAILS, LOGIN_WINDOW = 8, 900  # failed logins allowed per email and IP, per 15 minutes


def _email(data, key="email"):
    email = text(data, key, required=True, max_len=254, label="your email address").lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ApiError("That email address doesn't look right.", field=key)
    return email


def _check_password(pw, user=None, field="password"):
    if not isinstance(pw, str) or not pw:
        raise ApiError("Add a password.", field=field)
    try:
        password_validation.validate_password(pw, user)
    except ValidationError as e:
        raise ApiError(" ".join(e.messages), field=field)


@ensure_csrf_cookie
@api(["GET"])
def csrf(request):
    return {"csrfToken": get_token(request)}


@api(["GET", "PATCH", "DELETE"])
def me(request):
    user = request.user
    if request.method == "GET":
        return {"user": user.to_dict() if user.is_authenticated else None}
    if not user.is_authenticated:
        raise ApiError("Log in to use this.", status=401, auth=True)
    data = body(request)
    if request.method == "DELETE":
        if not user.check_password(data.get("password") or ""):
            raise ApiError("That password isn't right.", status=403, field="password")
        logout(request)
        user.delete()  # sites, audits, lists, snapshots, campaigns and saved state go with it
        return {"ok": True}
    if "name" in data:
        user.name = text(data, "name", required=True, max_len=150, label="your name")
    if "email" in data:
        email = _email(data)
        if email != user.email and User.objects.filter(email=email).exists():
            raise ApiError("Another account already uses that email address.", status=409, field="email")
        user.email = email
    user.save()
    return {"user": user.to_dict()}


@api(["POST"])
def signup(request):
    ratelimit.limit(request, "signup")
    data = body(request)
    name = text(data, "name", required=True, max_len=150, label="your name")
    email = _email(data)
    password = data.get("password")
    _check_password(password, User(email=email, name=name))
    taken = ApiError("An account with that email already exists. Log in instead.", status=409, field="email")
    if User.objects.filter(email=email).exists():
        raise taken
    try:
        with atomic():
            user = User.objects.create_user(email=email, password=password, name=name)
    except IntegrityError:  # signed up a moment ago in another tab: the unique email index caught it
        raise taken
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return {"user": user.to_dict()}, 201


@api(["POST"])
def login_view(request):
    data = body(request)
    email = _email(data)
    key = f"login:{email}:{ratelimit.client_ip(request)}"
    # only failed attempts count towards the lockout
    if ratelimit.count(key, LOGIN_WINDOW) >= LOGIN_FAILS:
        raise ApiError("Too many failed attempts. Wait 15 minutes and try again.", status=429)
    user = authenticate(request, email=email, password=data.get("password") or "")
    if user is None:
        ratelimit.hit(key, LOGIN_FAILS, LOGIN_WINDOW)
        raise ApiError("That email and password don't match an account.", status=401, field="password")
    login(request, user)
    return {"user": user.to_dict()}


@api(["POST"])
def logout_view(request):
    logout(request)
    return {"ok": True}


@api(["POST"], login=True)
def password(request):
    data = body(request)
    user = request.user
    if not user.check_password(data.get("current") or ""):
        raise ApiError("Your current password isn't right.", status=403, field="current")
    _check_password(data.get("new"), user, field="new")
    user.set_password(data["new"])
    user.save()
    update_session_auth_hash(request, user)  # stay logged in on this device
    return {"ok": True}
