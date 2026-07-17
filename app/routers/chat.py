# app/routers/chat.py

from __future__ import annotations

from typing import List, Optional, Any, Dict
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field
from loguru import logger

from app.config import settings
from app.state import get_bundle
from app.memory_store import get_store, Message

router = APIRouter(tags=["chat"])

# ---------- Schemas ----------

class ChatIn(BaseModel):
    question: str = Field(..., min_length=1)
    conversation_id: Optional[str] = Field(
        None,
        description="Client-scoped ID like '<client>:<uuid>'. If not provided, a new one will be returned."
    )
    index_name: Optional[str] = Field(
        None,
        description="Override index name; defaults to client id."
    )
    top_k: Optional[int] = Field(
        None,
        description="Override top_k for retrieval; falls back to bundle settings."
    )

class Source(BaseModel):
    score: Optional[float] = None
    text_preview: Optional[str] = None
    file: Optional[str] = None
    breadcrumb: Optional[str] = None
    section_id: Optional[str] = None
    slug_url: Optional[str] = None
    viq_hints: Optional[List[str]] = None
    domain_tags: Optional[List[str]] = None
    synonym_hits: Optional[List[str]] = None

class ChatOut(BaseModel):
    answer: str
    sources: List[Source] = []
    conversation_id: str


# ---------- Helpers ----------

def _ensure_conversation_id(client: str, conv_id: Optional[str]) -> str:
    # Keep tenant isolation: prefix with client if not already
    if conv_id and conv_id.strip():
        cid = conv_id.strip()
        return cid if cid.lower().startswith(client.lower() + ":") else f"{client}:{cid}"
    import uuid
    return f"{client}:{uuid.uuid4().hex[:12]}"

def _extract_sources(resp: Any) -> List[Source]:
    items: List[Source] = []
    # LlamaIndex Response objects typically expose .source_nodes
    nodes = getattr(resp, "source_nodes", None) or []
    for sn in nodes:
        try:
            score = getattr(sn, "score", None)
            node = getattr(sn, "node", None) or sn
            text = getattr(node, "text", None)
            md: Dict[str, Any] = getattr(node, "metadata", {}) or {}
            items.append(
                Source(
                    score=score,
                    text_preview=(text[:500] + "…") if text and len(text) > 500 else text,
                    file=md.get("file"),
                    breadcrumb=md.get("breadcrumb"),
                    section_id=md.get("section_id"),
                    slug_url=md.get("slug_url"),
                    viq_hints=md.get("viq_hints"),
                    domain_tags=md.get("domain_tags"),
                    synonym_hits=md.get("synonym_hits"),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse source node: {e}")
    return items


# ---------- Routes ----------

@router.post("/{client}/api/chat", response_model=ChatOut)
async def chat(
    client: str = Path(..., description="Client id, e.g. rsms, andriki, oceangold"),
    payload: ChatIn = ...,
):
    """
    Conversational RAG endpoint.
    - Persists turns to SQLite (MemoryStore).
    - Uses same tenant bundle as /{client}/api/ask.
    """
    client = client.strip().lower()
    index_name = (payload.index_name or client).strip().lower()
    conversation_id = _ensure_conversation_id(client, payload.conversation_id)

    # Load tenant bundle
    try:
        bundle = get_bundle(client_id=client, index_name=index_name)
    except Exception as e:
        logger.exception(f"Bundle load failed for {client}/{index_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load index for {client}/{index_name}")

    query_engine = bundle.get("query_engine")
    if query_engine is None:
        raise HTTPException(status_code=500, detail="Query engine not initialized")

    # Store user turn
    store = get_store()
    store.store_message("user", payload.question, conversation_id)

    # Optional: include short conversational history for “conversational RAG”
    # Fetch last 10-12 turns and join as a lightweight context prefix.
    history: List[Message] = store.recent_turns(conversation_id, limit=12)
    history_text = []
    for m in history[-10:]:
        role = m.role.capitalize()
        content = (m.content or "").strip()
        if content:
            history_text.append(f"{role}: {content}")
    history_prefix = "\n".join(history_text[-8:])  # keep it short

    # Build final prompt
    if history_prefix:
        prompt = f"{history_prefix}\n\nUser: {payload.question}"
    else:
        prompt = payload.question

    # Query (async if available)
    try:
        # Prefer async query if supported by the engine
        if hasattr(query_engine, "aquery"):
            resp = await query_engine.aquery(prompt)
        else:
            resp = query_engine.query(prompt)
    except Exception as e:
        logger.exception(f"Query failed: {e}")
        raise HTTPException(status_code=500, detail="Query execution failed")

    answer = str(resp)
    sources = _extract_sources(resp)

    # Store assistant turn
    store.store_message("assistant", answer, conversation_id)

    return ChatOut(answer=answer, sources=sources, conversation_id=conversation_id)


@router.post("/{client}/api/chat/clear")
def chat_clear(
    client: str = Path(..., description="Client id"),
    conversation_id: str = "",
):
    client = client.strip().lower()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")
    # Guard: only allow clearing within the same tenant
    if not conversation_id.lower().startswith(client + ":"):
        raise HTTPException(status_code=400, detail="conversation_id must start with '<client>:'")
    deleted = get_store().clear_conversation(conversation_id)
    return {"cleared": deleted}
