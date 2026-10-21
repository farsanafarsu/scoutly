"""Database helpers for MongoDB."""
from contextlib import nullcontext

from bson import ObjectId
from django.db import connection
from django_mongodb_backend import transaction as mongo_transaction


def atomic():
    """A real transaction when MongoDB runs as a replica set or sharded cluster (MongoDB Atlas
    always does); a plain block on a standalone server, which can't run transactions.

    Django's own transaction.atomic() is a no-op on this backend, so use this instead."""
    return mongo_transaction.atomic() if connection.features._supports_transactions else nullcontext()


def is_id(value):
    """Whether value is a valid ObjectId string, i.e. a possible primary key."""
    return ObjectId.is_valid(str(value or ""))
