"""Semantic cache implementations for the runtime gateway."""
from __future__ import annotations

import copy
import time

from extensions.runtime import CacheLookupResult, GatewayResponse


class NoOpSemanticCache:
    def get(self, key: str) -> CacheLookupResult:
        return CacheLookupResult(hit=False, response=None)

    def put(self, key: str, response: GatewayResponse, ttl_seconds: int = 0) -> None:
        return None


class InMemorySemanticCache:
    def __init__(self):
        self._entries: dict[str, tuple[float, GatewayResponse]] = {}

    def get(self, key: str) -> CacheLookupResult:
        entry = self._entries.get(key)
        if entry is None:
            return CacheLookupResult(hit=False, response=None)
        expires_at, response = entry
        if expires_at and expires_at < time.time():
            self._entries.pop(key, None)
            return CacheLookupResult(hit=False, response=None)
        return CacheLookupResult(hit=True, response=copy.deepcopy(response))

    def put(self, key: str, response: GatewayResponse, ttl_seconds: int = 0) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0
        self._entries[key] = (expires_at, copy.deepcopy(response))
