# app/state.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from app.config import settings
from app.services import build_retriever_bundle

# Cache: (client_id, index_name) -> bundle
_BUNDLES: Dict[Tuple[str, str], dict] = {}


def _norm(s: str | None, fallback: str) -> str:
    """Lower/strip with fallback."""
    return (s or fallback).strip().lower()


def _clients_base() -> Path:
    """
    Resolve parent folder that contains all client folders.
    """
    base = settings.base_dir  # ← FIXED: removed data_root reference
    p = Path(base).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Clients base directory not found: {p}")
    return p


def _client_root(client_id: str) -> Path:
    root = _clients_base() / client_id
    if not root.exists():
        raise FileNotFoundError(f"Client folder not found: {root}")
    return root


def get_bundle(client_id: str | None = None, index_name: str | None = None) -> dict:
    """
    Build or return a cached retriever bundle for a given tenant/index.

    - client_id: tenant id (folder under CLIENTS_BASE)
    - index_name: logical index name; defaults to client_id
    """
    client = _norm(client_id, settings.default_client_id)
    index = _norm(index_name, client)
    key = (client, index)

    if key in _BUNDLES:
        return _BUNDLES[key]

    root = _client_root(client)
    idx_store = root / "index_store"

    paths = {
        "tenant_root": str(root),
        "chroma_path": str(idx_store / "chroma"),
        "chunks_path": str(idx_store / "chunks.jsonl"),
        "settings_path": str(idx_store / "settings.json"),
        "rules_yaml": str(root / "rules.yaml"),
    }

    bundle = build_retriever_bundle(paths=paths, tenant=client, index=index)
    _BUNDLES[key] = bundle
    return bundle


def reload_bundle(client_id: str, index_name: str | None = None) -> dict:
    """Force rebuild and refresh the cache for a tenant/index."""
    client = _norm(client_id, settings.default_client_id)
    index = _norm(index_name, client)
    key = (client, index)
    if key in _BUNDLES:
        del _BUNDLES[key]
    return get_bundle(client, index)


def clear_cache() -> None:
    """Clear all cached bundles (useful for admin/debug)."""
    _BUNDLES.clear()