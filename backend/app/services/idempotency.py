"""进程内幂等执行器。

默认不引入 Redis，因此本地开发可直接使用。生产多实例时可改为 Redis 等共享实现；
接口语义与 Idempotency-Key 保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    fingerprint: str
    ready: Event = field(default_factory=Event)
    created_at: float = field(default_factory=monotonic)
    value: T | None = None
    error: BaseException | None = None


class InMemoryIdempotencyStore:
    """同一用户同一 key 在 TTL 内只执行一次，并复用首次结果。"""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[tuple[int, str], _Entry] = {}
        self._lock = Lock()

    def execute(self, user_id: int, key: str | None, fingerprint: str, action: Callable[[], T]) -> tuple[T, bool]:
        if not key:
            return action(), False
        identity = (user_id, key)
        with self._lock:
            now = monotonic()
            self._entries = {
                item_key: item for item_key, item in self._entries.items()
                if now - item.created_at <= self.ttl_seconds
            }
            entry = self._entries.get(identity)
            if entry is None:
                entry = _Entry(fingerprint=fingerprint)
                self._entries[identity] = entry
                owner = True
            elif entry.fingerprint != fingerprint:
                raise ValueError("同一个 Idempotency-Key 不能用于不同的请求内容")
            else:
                owner = False

        if not owner:
            entry.ready.wait()
            if entry.error:
                raise entry.error
            return entry.value, True

        try:
            entry.value = action()
            return entry.value, False
        except BaseException as exc:
            entry.error = exc
            raise
        finally:
            entry.ready.set()


idempotency_store = InMemoryIdempotencyStore()
