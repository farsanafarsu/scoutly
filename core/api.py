"""Small JSON API layer shared by every Scoutly endpoint.

Every response is JSON. Failures carry {"error": "..."}; assets/app.js reads that field whatever
the status code, so messages here are written for the person using the page.
"""
import functools
import json
import logging

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

log = logging.getLogger("scoutly")


class ApiError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


def respond(data, status=200):
    return JsonResponse(data, status=status, safe=not isinstance(data, list), json_dumps_params={"ensure_ascii": False})


def api(methods=("GET",), login=False, csrf=True):
    """Decorate a view that returns a dict (or (dict, status)) and may raise ApiError.

    login: answer 401 to anonymous visitors.
    csrf:  False only for the stateless research tools the existing pages POST to without a token;
           anything that reads or changes account data keeps Django's CSRF check.
    """
    methods = tuple(m.upper() for m in methods)

    def wrap(view):
        @functools.wraps(view)
        def inner(request, *args, **kwargs):
            if request.method not in methods:
                return respond({"error": f"Use {' or '.join(methods)} for this endpoint."}, 405)
            if login and not request.user.is_authenticated:
                return respond({"error": "Log in to use this.", "auth": True}, 401)
            try:
                out = view(request, *args, **kwargs)
            except ApiError as e:
                return respond({"error": e.message, **e.extra}, e.status)
            except Exception:
                log.exception("Unhandled error in %s", request.path)
                return respond({"error": "Something went wrong on the server. Please try again."}, 500)
            if isinstance(out, tuple):
                return respond(*out)
            return respond(out)

        return inner if csrf else csrf_exempt(inner)

    return wrap


def body(request):
    """The request's JSON object, or ApiError."""
    try:
        raw = request.body
    except RequestDataTooBig:
        raise ApiError("That request is too large.", status=413)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise ApiError("Send the request body as JSON.")
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.")
    return data


def text(data, key, *, required=False, max_len=500, label=None, multiline=False):
    """A trimmed string field from a JSON body (inner whitespace collapsed unless multiline)."""
    v = data.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if v is not None and not isinstance(v, str):
        raise ApiError(f"{(label or key).capitalize()} should be text.", field=key)
    v = (v or "").strip() if multiline else " ".join((v or "").split())
    if not v:
        if required:
            raise ApiError(f"Add {label or key}.", field=key)
        return ""
    if len(v) > max_len:
        raise ApiError(f"{(label or key).capitalize()} is too long (at most {max_len} characters).", field=key)
    return v


def json_size_ok(value, limit):
    return len(json.dumps(value, ensure_ascii=False).encode()) <= limit


def int_param(request, key, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.GET.get(key, default))))
    except (TypeError, ValueError):
        return default
