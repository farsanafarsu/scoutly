"""The audit score, computed the same way audit.html does (without the browser-only PageSpeed checks).

Every category starts at 100 and loses points per issue; issues found on many pages cost more,
and each critical issue also lowers the overall score directly.
"""
CATEGORY_WEIGHTS = {"crawl": 25, "onpage": 20, "content": 12, "links": 13, "internal": 5, "perf": 12, "schema": 7, "ai": 6}
DEDUCTION = {"critical": 45, "warning": 22, "notice": 7}
CRITICAL_PENALTY = 10


def _issue(c):
    return c.get("status") in ("fail", "warn")


def _spread(n):
    return 1.6 if n >= 10 else 1.3 if n >= 3 else 1


def category_score(checks, cat):
    scored = [c for c in checks if c.get("cat") == cat and c.get("status") != "info" and c.get("weight")]
    if not scored:
        return None
    lost = sum(DEDUCTION.get(c.get("sev"), 0) * _spread(c.get("count") or 1) for c in scored if _issue(c))
    return max(0, round(100 - lost))


def overall(checks):
    total = weight = 0
    for cat, w in CATEGORY_WEIGHTS.items():
        s = category_score(checks, cat)
        if s is not None:
            total += w * s
            weight += w
    if not weight:
        return 0
    critical = sum(1 for c in checks if _issue(c) and c.get("sev") == "critical" and c.get("weight"))
    return max(0, round(total / weight - CRITICAL_PENALTY * critical))


def counts(checks):
    out = {"critical": 0, "warning": 0, "notice": 0, "passed": 0}
    for c in checks:
        if _issue(c) and c.get("sev") in out:
            out[c["sev"]] += 1
        elif c.get("status") == "pass":
            out["passed"] += 1
    return out
