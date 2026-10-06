from __future__ import annotations
from ._client import bearer, model_config, remote, request_json


def _headers() -> dict[str, str]:
    return bearer("NORM_VANE_TOKEN")


def vane_status() -> dict:
    """Check the configured remote Vane bridge and report its retrieval/embedding status."""
    r = remote("vane")
    if not r["enabled"]:
        return {"enabled": False, "configured": bool(r["host"]), "endpoint": r["base"]}
    if not r["host"]: raise RuntimeError("Vane is enabled but remote_agents.vane_host is blank")
    return {"enabled": True, "endpoint": r["base"], "health": request_json(r["base"] + "/health", headers=_headers(), timeout=10)}


def vane_collections() -> dict:
    """List collections exposed by the configured remote Vane bridge."""
    r = remote("vane")
    if not r["enabled"] or not r["host"]: raise RuntimeError("Vane remote integration is not enabled/configured")
    return {"collections": request_json(r["base"] + "/collections", headers=_headers(), timeout=20)}


def vane_search(query: str, collection: str = "web", top_k: int = 10, mode: str = "hybrid") -> dict:
    """Run bounded BM25/vector/hybrid retrieval through the remote Vane bridge; embedding is performed by its configured shared embedding model."""
    r = remote("vane")
    if not r["enabled"] or not r["host"]: raise RuntimeError("Vane remote integration is not enabled/configured")
    mode = mode.strip().lower()
    if mode not in {"text", "vector", "hybrid"}: raise ValueError("mode must be text, vector, or hybrid")
    body = {"collection": collection, "query": query, "top_k": max(1, min(int(top_k), 100)), "mode": mode}
    return request_json(r["base"] + "/search", method="POST", body=body, headers=_headers(), timeout=120)


def vane_add(documents: list[dict], collection: str = "web", flush: bool = True) -> dict:
    """Upsert a bounded batch of documents into remote Vane; the bridge obtains embeddings and persists the index."""
    r = remote("vane")
    if not r["enabled"] or not r["host"]: raise RuntimeError("Vane remote integration is not enabled/configured")
    if not isinstance(documents, list) or not documents: raise ValueError("documents must be a non-empty list")
    if len(documents) > 500: raise ValueError("vane_add accepts at most 500 documents per call")
    return request_json(r["base"] + "/add", method="POST", body={"collection": collection, "documents": documents, "flush": bool(flush)}, headers=_headers(), timeout=600)


def vane_delete(ids: list[str], collection: str = "web", flush: bool = True) -> dict:
    """Delete document IDs from a remote Vane collection."""
    r = remote("vane")
    if not r["enabled"] or not r["host"]: raise RuntimeError("Vane remote integration is not enabled/configured")
    clean = [str(x) for x in ids if str(x).strip()]
    if not clean: raise ValueError("ids must contain at least one id")
    return request_json(r["base"] + "/delete", method="POST", body={"collection": collection, "ids": clean, "flush": bool(flush)}, headers=_headers(), timeout=120)
