"""
Redis cache - disposable by design.

Only values that can be recomputed are stored here, always with an expiry:
    llm:{sha256(prompt)}      -> cached model response (translations included)
    answer:{sha256(question)} -> cached final RAG answer for a repeated question

If Redis is flushed or briefly unavailable, nothing breaks: we just call the LLM again.
"""

import hashlib
import json
import logging

import redis

from app.settings import REDIS_URL

log = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)


def make_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def cache_get(key: str):
    try:
        value = redis_client.get(key)
        return json.loads(value) if value else None
    except redis.RedisError as error:
        log.warning("Redis read failed (continuing without cache): %s", error)
        return None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except redis.RedisError as error:
        log.warning("Redis write failed (continuing without cache): %s", error)
