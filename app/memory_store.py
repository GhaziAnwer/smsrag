# app/memory_store.py

import datetime
import os
import logging
from pathlib import Path
from typing import List, Dict, Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Boolean, func, select
)
from sqlalchemy.orm import declarative_base, sessionmaker

__all__ = ["Message", "MemoryStore",
           "store_message", "fetch_all", "recent_turns",
           "clear_conversation", "get_conversation_list",
           "get_conversation_stats", "cleanup_old_conversations",
           "search_conversations", "get_store"]

# -------------------------------------------------------------------
# Database setup
# -------------------------------------------------------------------

DEFAULT_DB = "chat_history.db"
DB_PATH = os.getenv("CHAT_DB_PATH", DEFAULT_DB)

def _ensure_db_path(db_path: str) -> str:
    p = Path(db_path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)

DB_PATH = _ensure_db_path(DB_PATH)

# check_same_thread=False → safe for FastAPI workers/threads
engine = create_engine(
    f"sqlite:///{DB_PATH}", echo=False, future=True,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
Base = declarative_base()


# -------------------------------------------------------------------
# Model
# -------------------------------------------------------------------
class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String(128), index=True)  # a bit larger for "client:uuid"
    role = Column(String(16))
    content = Column(Text)
    ts = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    persistent = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)


# -------------------------------------------------------------------
# Store class
# -------------------------------------------------------------------
class MemoryStore:
    """
    Thin wrapper around SQLAlchemy for chat history.
    Exposes the exact methods your code already uses.
    """

    def __init__(self, db_url: Optional[str] = None):
        if db_url:
            db_url = _ensure_db_path(db_url)
            self._engine = create_engine(
                f"sqlite:///{db_url}", echo=False, future=True,
                connect_args={"check_same_thread": False}
            )
            self._Session = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
            Base.metadata.create_all(bind=self._engine)
        else:
            self._engine = engine
            self._Session = SessionLocal

    # ------------- helpers -------------
    @staticmethod
    def _now():
        return datetime.datetime.utcnow()

    # ------------- CRUD -------------
    def store_message(self, role: str, content: str, conversation_id: str, persistent: bool = False):
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Invalid role: {role}")
        if not content or not content.strip():
            raise ValueError("Content cannot be empty")
        if not conversation_id or not conversation_id.strip():
            raise ValueError("Conversation ID cannot be empty")

        try:
            with self._Session() as db:
                db.add(
                    Message(
                        conversation_id=conversation_id.strip(),
                        role=role.strip(),
                        content=content.strip(),
                        persistent=persistent,
                    )
                )
                db.commit()
        except Exception as e:
            logging.error(f"Failed to store message: {e}")
            raise

    def fetch_all(self, conversation_id: str) -> List[Message]:
        if not conversation_id or not conversation_id.strip():
            return []
        try:
            with self._Session() as db:
                stmt = (
                    select(Message)
                    .where(Message.conversation_id == conversation_id.strip())
                    .order_by(Message.id.asc())
                )
                return list(db.execute(stmt).scalars().all())
        except Exception as e:
            logging.error(f"Failed to fetch messages for {conversation_id}: {e}")
            return []

    def recent_turns(self, conversation_id: str, limit: int = 12) -> List[Message]:
        if limit <= 0 or not conversation_id or not conversation_id.strip():
            return []
        try:
            rows = self.fetch_all(conversation_id)
            clean = [r for r in rows if not (r.role == "assistant" and (r.content or "").startswith("[REFS]"))]
            return clean[-limit:]
        except Exception as e:
            logging.error(f"Failed to get recent turns for {conversation_id}: {e}")
            return []

    def clear_conversation(self, conversation_id: str) -> int:
        if not conversation_id or not conversation_id.strip():
            logging.warning("Cannot clear conversation: invalid conversation_id")
            return 0
        try:
            with self._Session() as db:
                deleted_count = (
                    db.query(Message)
                    .filter(Message.conversation_id == conversation_id.strip())
                    .delete()
                )
                db.commit()
                logging.info(f"Cleared {deleted_count} messages from conversation {conversation_id}")
                return deleted_count
        except Exception as e:
            logging.error(f"Failed to clear conversation {conversation_id}: {e}")
            return 0

    # -------- tenant-aware helpers (optional use) --------
    def get_conversation_list(self, tenant_prefix: Optional[str] = None) -> List[Dict]:
        """
        If tenant_prefix is provided (e.g., 'andriki:'), only return rows whose
        conversation_id starts with that prefix. This helps keep tenants isolated
        when the frontend uses IDs like '<client>:<uuid>'.
        """
        try:
            with self._Session() as db:
                q = (
                    db.query(
                        Message.conversation_id,
                        func.count(Message.id).label("message_count"),
                        func.max(Message.ts).label("last_activity"),
                    )
                )
                if tenant_prefix:
                    q = q.filter(Message.conversation_id.like(f"{tenant_prefix}%"))

                rows = (
                    q.group_by(Message.conversation_id)
                     .order_by(func.max(Message.ts).desc())
                     .all()
                )
                return [
                    {
                        "conversation_id": row[0],
                        "message_count": row[1],
                        "last_activity": row[2].isoformat() if row[2] else None,
                    }
                    for row in rows
                ]
        except Exception as e:
            logging.error(f"Failed to get conversation list: {e}")
            return []

    def get_conversation_stats(self, conversation_id: str) -> Optional[Dict]:
        if not conversation_id or not conversation_id.strip():
            return None
        try:
            with self._Session() as db:
                res = (
                    db.query(
                        func.count(Message.id).label("total_messages"),
                        func.min(Message.ts).label("first_message"),
                        func.max(Message.ts).label("last_message"),
                    )
                    .filter(Message.conversation_id == conversation_id.strip())
                    .first()
                )
                if res and res[0] > 0:
                    return {
                        "conversation_id": conversation_id,
                        "total_messages": res[0],
                        "first_message": res[1].isoformat() if res[1] else None,
                        "last_message": res[2].isoformat() if res[2] else None,
                    }
                return None
        except Exception as e:
            logging.error(f"Failed to get stats for {conversation_id}: {e}")
            return None

    def cleanup_old_conversations(self, days_old: int = 30, keep_persistent: bool = True) -> int:
        if days_old <= 0:
            logging.warning("Invalid days_old parameter for cleanup")
            return 0
        try:
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days_old)
            with self._Session() as db:
                q = db.query(Message).filter(Message.ts < cutoff)
                if keep_persistent:
                    q = q.filter(Message.persistent == False)  # noqa: E712
                deleted = q.delete()
                db.commit()
                logging.info(f"Cleaned up {deleted} old messages (>{days_old} days)")
                return deleted
        except Exception as e:
            logging.error(f"Failed to cleanup old conversations: {e}")
            return 0

    def search_conversations(self, search_term: str, limit: int = 50) -> List[Message]:
        if not search_term or not search_term.strip():
            return []
        try:
            with self._Session() as db:
                return (
                    db.query(Message)
                    .filter(Message.content.contains(search_term.strip()))
                    .order_by(Message.ts.desc())
                    .limit(limit)
                    .all()
                )
        except Exception as e:
            logging.error(f"Failed to search conversations: {e}")
            return []


# shared singleton store (for simple imports)
_store_singleton: Optional[MemoryStore] = MemoryStore(DB_PATH)

def get_store() -> MemoryStore:
    return _store_singleton


# -------------------------------------------------------------------
# Backward-compatible module-level functions
# -------------------------------------------------------------------
def store_message(role: str, content: str, conversation_id: str, persistent: bool = False):
    return get_store().store_message(role, content, conversation_id, persistent)

def fetch_all(conversation_id: str):
    return get_store().fetch_all(conversation_id)

def recent_turns(conversation_id: str, limit: int = 12):
    return get_store().recent_turns(conversation_id, limit)

def clear_conversation(conversation_id: str):
    return get_store().clear_conversation(conversation_id)

def get_conversation_list(tenant_prefix: Optional[str] = None):
    return get_store().get_conversation_list(tenant_prefix)

def get_conversation_stats(conversation_id: str):
    return get_store().get_conversation_stats(conversation_id)

def cleanup_old_conversations(days_old: int = 30, keep_persistent: bool = True):
    return get_store().cleanup_old_conversations(days_old, keep_persistent)

def search_conversations(search_term: str, limit: int = 50):
    return get_store().search_conversations(search_term, limit)
