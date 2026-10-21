"""The data an app page starts with, embedded in the page as it's served (see core.views.site),
so the page can read the user's saved work straight away instead of waiting for an API call."""
from django.middleware.csrf import get_token

from . import store

# Large keys go only to the pages that use them; the page is told they were left out so it
# never overwrites them with an empty value.
HEAVY = {"scoutly.ranks": {"position-tracking.html", "reports.html"}}


def page_boot(request, page):
    user = request.user
    keys = [k for k in store.all_keys(user) if page in HEAVY.get(k, {page})]
    return {
        "user": user.to_dict(),
        "csrfToken": get_token(request),
        "data": store.load(user, keys),
        "withheld": [k for k, pages in HEAVY.items() if page not in pages],
    }
