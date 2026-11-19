"""
One place for every LLM call. LiteLLM runs in-process and talks to AWS Bedrock.

    ask_llm(...)       -> plain text answer
    ask_llm_json(...)  -> answer parsed as a JSON dict
    embed_text(...)    -> embedding vector

Every completion is cached in Redis under llm:{sha256(model + system + prompt)}.
"""

import json
import logging
import re

import litellm

from app.services.cache import cache_get, cache_set, make_key
from app.settings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, LLM_CACHE_TTL_SECONDS, PROMPTS

log = logging.getLogger(__name__)
litellm.suppress_debug_info = True


def persona(name: str) -> str:
    return PROMPTS["personas"][name]


def prompt(name: str, **values) -> str:
    return PROMPTS["prompts"][name].format(**values)


def ask_llm(model: str, system: str, user_prompt: str,
            max_tokens: int = 1500, use_cache: bool = True) -> str:
    cache_key = make_key("llm", model, system, user_prompt)
    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            log.info("LLM cache hit (%s)", model)
            return cached

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    answer = response.choices[0].message.content or ""

    if use_cache:
        cache_set(cache_key, answer, LLM_CACHE_TTL_SECONDS)
    return answer


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```json fences or add a sentence. Pull out the {...} part.

    Invoice text often has line breaks or tabs inside a value (multi-line addresses,
    item descriptions). The model copies them raw into JSON strings, which strict
    json.loads rejects with "Invalid control character". strict=False accepts them.
    """
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in model output: {text[:200]}")
    json_str = cleaned[start:end + 1]
    # drop control characters that are never meaningful (keeps \t \n \r)
    json_str = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", json_str)
    return json.loads(json_str, strict=False)


def ask_llm_json(model: str, system: str, user_prompt: str, max_tokens: int = 3000) -> dict:
    raw = ask_llm(model, system, user_prompt, max_tokens=max_tokens)
    return extract_json(raw)


def embed_text(text: str) -> list[float]:
    # Titan Text Embeddings v1 returns 1536 dimensions.
    response = litellm.embedding(model=EMBEDDING_MODEL, input=[text])
    vector = response.data[0]["embedding"]
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding size {len(vector)} does not match EMBEDDING_DIMENSIONS={EMBEDDING_DIMENSIONS}"
        )
    return vector