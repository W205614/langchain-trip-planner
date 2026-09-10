"""Process-local serialization for the supported single-worker deployment."""
from threading import RLock
from functools import wraps

knowledge_lock = RLock()
rag_lock = RLock()


def knowledge_serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with knowledge_lock:
            return fn(*args, **kwargs)
    return wrapped
