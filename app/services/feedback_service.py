# app/services/feedback_service.py - Feedback database service
import sqlite3
import threading
from typing import List, Optional, Tuple
from datetime import datetime
from loguru import logger
import os
from app.models import FeedbackRequest, FeedbackItem, FeedbackStats, FeedbackType

class FeedbackService:
    """Service to handle feedback storage and retrieval using SQLite"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Check env var first, then fall back to project root
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            db_path = os.getenv("FEEDBACK_DB_PATH", os.path.join(repo_root, "feedback.db"))

        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        logger.info(f"[FEEDBACK] Database initialized at {self.db_path}")

    def _init_db(self):
        """Initialize feedback database tables"""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        client_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        feedback_type TEXT NOT NULL CHECK(feedback_type IN ('thumbs_up', 'thumbs_down')),
                        comment TEXT,
                        user_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_client_created ON feedback(client_id, created_at DESC);"
                )

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(feedback_type);"
                )

                conn.commit()
            finally:
                conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with proper settings"""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def submit_feedback(self, feedback: FeedbackRequest) -> int:
        """Submit feedback and return feedback ID"""
        try:
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO feedback
                        (conversation_id, client_id, question, answer, feedback_type, comment, user_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            feedback.conversation_id,
                            feedback.client_id,
                            feedback.question,
                            feedback.answer,
                            feedback.feedback_type.value,
                            feedback.comment,
                            feedback.user_id
                        )
                    )
                    conn.commit()
                    feedback_id = cursor.lastrowid
                    logger.info(f"[FEEDBACK] Submitted feedback {feedback_id} for client {feedback.client_id}")
                    return feedback_id
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to submit feedback: {e}")
            raise

    def get_feedback_stats(self, client_id: Optional[str] = None) -> FeedbackStats:
        """Get feedback statistics"""
        try:
            with self._lock:
                conn = self._get_connection()
                try:
                    # Base query conditions
                    where_clause = "WHERE 1=1"
                    params = []

                    if client_id:
                        where_clause += " AND client_id = ?"
                        params.append(client_id)

                    # Get total counts
                    cursor = conn.execute(f"""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN feedback_type = 'thumbs_up' THEN 1 ELSE 0 END) as thumbs_up,
                            SUM(CASE WHEN feedback_type = 'thumbs_down' THEN 1 ELSE 0 END) as thumbs_down
                        FROM feedback {where_clause}
                    """, params)

                    row = cursor.fetchone()
                    total = row[0] or 0
                    thumbs_up = row[1] or 0
                    thumbs_down = row[2] or 0

                    # Calculate percentages
                    thumbs_up_pct = (thumbs_up / total * 100) if total > 0 else 0
                    thumbs_down_pct = (thumbs_down / total * 100) if total > 0 else 0

                    # Get recent feedback (last 10)
                    cursor = conn.execute(f"""
                        SELECT id, conversation_id, client_id, question, answer,
                               feedback_type, comment, user_id, created_at
                        FROM feedback {where_clause}
                        ORDER BY created_at DESC
                        LIMIT 10
                    """, params)

                    recent_feedback = []
                    for row in cursor.fetchall():
                        recent_feedback.append(FeedbackItem(
                            id=row[0],
                            conversation_id=row[1],
                            client_id=row[2],
                            question=row[3],
                            answer=row[4],
                            feedback_type=FeedbackType(row[5]),
                            comment=row[6],
                            user_id=row[7],
                            created_at=datetime.fromisoformat(row[8])
                        ))

                    return FeedbackStats(
                        total_feedback=total,
                        thumbs_up_count=thumbs_up,
                        thumbs_down_count=thumbs_down,
                        thumbs_up_percentage=round(thumbs_up_pct, 1),
                        thumbs_down_percentage=round(thumbs_down_pct, 1),
                        recent_feedback=recent_feedback
                    )

                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to get stats: {e}")
            raise

    def get_feedback_items(
        self,
        client_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        feedback_type: Optional[FeedbackType] = None
    ) -> Tuple[List[FeedbackItem], int]:
        """Get paginated feedback items and total count"""
        try:
            with self._lock:
                conn = self._get_connection()
                try:
                    # Build query conditions
                    where_conditions = []
                    params = []

                    if client_id:
                        where_conditions.append("client_id = ?")
                        params.append(client_id)

                    if feedback_type:
                        where_conditions.append("feedback_type = ?")
                        params.append(feedback_type.value)

                    where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

                    # Get total count
                    cursor = conn.execute(f"SELECT COUNT(*) FROM feedback {where_clause}", params)
                    total_count = cursor.fetchone()[0]

                    # Get paginated items
                    offset = (page - 1) * page_size
                    cursor = conn.execute(f"""
                        SELECT id, conversation_id, client_id, question, answer,
                               feedback_type, comment, user_id, created_at
                        FROM feedback {where_clause}
                        ORDER BY created_at DESC
                        LIMIT ? OFFSET ?
                    """, params + [page_size, offset])

                    feedback_items = []
                    for row in cursor.fetchall():
                        feedback_items.append(FeedbackItem(
                            id=row[0],
                            conversation_id=row[1],
                            client_id=row[2],
                            question=row[3],
                            answer=row[4],
                            feedback_type=FeedbackType(row[5]),
                            comment=row[6],
                            user_id=row[7],
                            created_at=datetime.fromisoformat(row[8])
                        ))

                    return feedback_items, total_count

                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to get feedback items: {e}")
            raise

# Global feedback service instance
_feedback_service = None

def get_feedback_service() -> FeedbackService:
    """Get global feedback service instance"""
    global _feedback_service
    if _feedback_service is None:
        _feedback_service = FeedbackService()
    return _feedback_service
