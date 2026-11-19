"""
Qdrant helper - one collection holding invoice text chunks and report text chunks.

Payload stored with every vector:
    invoice_id, chunk_text, source_type ("invoice" | "report"), vendor, language
"""

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, FilterSelector, MatchValue,
    PayloadSchemaType, PointStruct, VectorParams,
)

from app.settings import EMBEDDING_DIMENSIONS, QDRANT_COLLECTION, QDRANT_URL

qdrant = QdrantClient(url=QDRANT_URL, timeout=10)


def ensure_collection() -> None:
    if qdrant.collection_exists(QDRANT_COLLECTION):
        return
    qdrant.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
    )
    # payload indexes make the invoice / source filters fast
    for field in ("invoice_id", "source_type", "vendor"):
        qdrant.create_payload_index(QDRANT_COLLECTION, field, PayloadSchemaType.KEYWORD)


def recreate_collection() -> None:
    if qdrant.collection_exists(QDRANT_COLLECTION):
        qdrant.delete_collection(QDRANT_COLLECTION)
    ensure_collection()


def count_points() -> int:
    return qdrant.count(QDRANT_COLLECTION).count


def delete_chunks(invoice_id: str, source_type: str) -> None:
    """Remove old chunks before re-indexing, so a re-validated report replaces the old one."""
    qdrant.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=FilterSelector(filter=Filter(must=[
            FieldCondition(key="invoice_id", match=MatchValue(value=invoice_id)),
            FieldCondition(key="source_type", match=MatchValue(value=source_type)),
        ])),
    )


def upsert_chunks(invoice_id: str, source_type: str, chunks: list[str],
                  vectors: list[list[float]], vendor: str, language: str) -> None:
    points = []
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}/{source_type}/{index}"))
        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "invoice_id": invoice_id,
                "chunk_text": chunk,
                "source_type": source_type,
                "vendor": vendor or "unknown",
                "language": language or "unknown",
            },
        ))
    if points:
        qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)


def search(vector: list[float], top_k: int, invoice_id: str | None = None) -> list[dict]:
    query_filter = None
    if invoice_id:
        query_filter = Filter(must=[
            FieldCondition(key="invoice_id", match=MatchValue(value=invoice_id))
        ])

    hits = qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    ).points
    return [{"score": hit.score, **hit.payload} for hit in hits]


def is_healthy() -> bool:
    try:
        qdrant.get_collections()
        return True
    except Exception:
        return False
