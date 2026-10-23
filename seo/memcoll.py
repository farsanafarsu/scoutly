"""A small in-memory stand-in for a pymongo collection, for the tests and for trying the Action Center
locally without MongoDB (SCOUTLY_DEV_MEMORY_DB=1; everything is lost when the server stops).

It supports only what taskstore.py uses: create_index (unique keys are enforced), insert_one, find_one,
find (sort / skip / limit), count_documents, distinct, replace_one and delete_many, with equality,
$in, $nin, $ne, $lt, $lte, $gt, $gte, $exists, $regex, $or and $and filters.
"""
import copy
import re
import threading

try:
    from pymongo.errors import DuplicateKeyError
except ImportError:  # the tests don't need pymongo
    class DuplicateKeyError(Exception):
        pass


class _Result:
    def __init__(self, matched=0, inserted_id=None, deleted=0):
        self.matched_count = self.modified_count = matched
        self.inserted_id = inserted_id
        self.deleted_count = deleted


def _match_value(v, cond):
    if isinstance(v, list) and isinstance(cond, dict) and "$regex" in cond:
        return any(_match_value(x, cond) for x in v)  # like MongoDB, a regex matches any element of an array
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in" and v not in arg and not (isinstance(v, list) and any(x in arg for x in v)):
                return False
            if op == "$nin" and (v in arg or (isinstance(v, list) and any(x in arg for x in v))):
                return False
            if op == "$ne" and v == arg:
                return False
            if op in ("$lt", "$lte", "$gt", "$gte"):
                if v is None or v == "":
                    return False
                if op == "$lt" and not v < arg or op == "$lte" and not v <= arg or op == "$gt" and not v > arg or op == "$gte" and not v >= arg:
                    return False
            if op == "$exists" and (v is not None) != bool(arg):
                return False
            if op == "$regex":
                flags = re.I if "i" in cond.get("$options", "") else 0
                if not isinstance(v, str) or not re.search(arg, v, flags):
                    return False
        return True
    if isinstance(v, list) and not isinstance(cond, list):
        return cond in v
    return v == cond


def matches(doc, flt):
    for k, cond in (flt or {}).items():
        if k == "$or":
            if not any(matches(doc, f) for f in cond):
                return False
        elif k == "$and":
            if not all(matches(doc, f) for f in cond):
                return False
        elif not _match_value(doc.get(k), cond):
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(keys):
            # missing values sort first ascending, like MongoDB
            self._docs.sort(key=lambda x: (x.get(k) is not None, x.get(k) if x.get(k) is not None else 0), reverse=d < 0)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([copy.deepcopy(d) for d in self._docs])


class MemoryCollection:
    def __init__(self, name="memory"):
        self.name = name
        self._docs = {}
        self._unique = []
        self._lock = threading.Lock()

    def create_index(self, keys, unique=False, name=None, **_):
        if unique:
            self._unique.append([k for k, _ in keys])
        return name

    def _check_unique(self, doc, skip_id=None):
        for fields in self._unique:
            key = tuple(doc.get(f) for f in fields)
            for d in self._docs.values():
                if d["_id"] != skip_id and tuple(d.get(f) for f in fields) == key:
                    raise DuplicateKeyError(f"duplicate key {fields}")

    def insert_one(self, doc):
        with self._lock:
            if doc["_id"] in self._docs:
                raise DuplicateKeyError("duplicate _id")
            self._check_unique(doc)
            self._docs[doc["_id"]] = copy.deepcopy(doc)
        return _Result(inserted_id=doc["_id"])

    def find(self, flt=None, projection=None):
        with self._lock:
            docs = [d for d in self._docs.values() if matches(d, flt)]
        if projection:
            keep = {k for k, v in projection.items() if v}
            docs = [{k: v for k, v in d.items() if k in keep or k == "_id"} for d in docs]
        return _Cursor(docs)

    def find_one(self, flt=None):
        for d in self.find(flt):
            return d
        return None

    def count_documents(self, flt=None):
        with self._lock:
            return sum(1 for d in self._docs.values() if matches(d, flt))

    def distinct(self, field, flt=None):
        out = []
        for d in self.find(flt):
            if d.get(field) not in out and d.get(field) is not None:
                out.append(d.get(field))
        return out

    def replace_one(self, flt, doc, upsert=False):
        with self._lock:
            for d in self._docs.values():
                if matches(d, flt):
                    self._check_unique(doc, skip_id=d["_id"])
                    self._docs[d["_id"]] = copy.deepcopy(doc)
                    return _Result(matched=1)
            if upsert:
                self._check_unique(doc)
                self._docs[doc["_id"]] = copy.deepcopy(doc)
        return _Result(matched=0)

    def delete_many(self, flt):
        with self._lock:
            ids = [i for i, d in self._docs.items() if matches(d, flt)]
            for i in ids:
                del self._docs[i]
        return _Result(deleted=len(ids))
