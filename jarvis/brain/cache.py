"""Caches that are safe to have.

Every entry is scoped to ``(user, session)`` unless it is declared ``shared`` — and only data with
no personal content may be shared (model capability metadata, tool schemas, the stable system
prompt's token count). A lookup from another session simply misses.

Every entry expires. What is cached, and for how long:

    stable system segments / tool schemas   shared, 24 h (invalidated by content hash anyway)
    model capability metadata               shared, 24 h
    deterministic parse results             per session, 10 min
    research results                        per session, by freshness: 15 min for "today/latest",
                                            6 h otherwise — always with the retrieval time
    embeddings of unchanged local files     shared by content hash, 7 days

Refused outright: anything whose key or value looks like a password, OTP, token or card number
(``privacy.find_secrets``), screenshots, approval answers, and private message bodies — callers
declare ``kind`` and the refusal is by kind as well as by content.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from .privacy import find_secrets

NEVER = frozenset({"screenshot", "approval", "message_body", "password", "otp", "token", "email_body"})
TTL = {"system": 86400, "schema": 86400, "capability": 86400, "parse": 600, "research_fresh": 900,
       "research": 6 * 3600, "embedding": 7 * 86400}


def content_hash(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ScopedCache:
    def __init__(self, max_entries: int = 512, clock=time.time):
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.clock = clock
        self.refused = 0

    @staticmethod
    def _key(kind: str, key: str, user: str, session: str, shared: bool) -> tuple:
        return (kind, key) if shared else (kind, key, user or "owner", session or "local")

    def put(self, kind: str, key: str, value: Any, *, user: str = "owner", session: str = "local",
            shared: bool = False, ttl: Optional[float] = None) -> bool:
        if kind in NEVER:
            self.refused += 1
            return False
        text = key + " " + (value if isinstance(value, str) else json.dumps(value, default=str))
        if find_secrets(text):
            self.refused += 1
            return False
        expires = self.clock() + (ttl if ttl is not None else TTL.get(kind, 600))
        with self._lock:
            k = self._key(kind, key, user, session, shared)
            self._data[k] = (expires, value)
            self._data.move_to_end(k)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return True

    def get(self, kind: str, key: str, *, user: str = "owner", session: str = "local",
            shared: bool = False) -> Any:
        with self._lock:
            k = self._key(kind, key, user, session, shared)
            row = self._data.get(k)
            if not row:
                return None
            if row[0] < self.clock():
                self._data.pop(k, None)
                return None
            self._data.move_to_end(k)
            return row[1]

    def forget_session(self, session: str) -> None:
        with self._lock:
            for k in [k for k in self._data if len(k) == 4 and k[3] == session]:
                self._data.pop(k, None)

    def __len__(self) -> int:
        return len(self._data)


CACHE = ScopedCache()
