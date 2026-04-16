"""
SQLite Query Logger for RAG System
Logs all user queries, rewrites, answers, and performance metrics
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class QueryLogger:
    """
    Comprehensive query logging system for RAG analytics and monitoring.
    Stores all query interactions in SQLite database.
    """
    
    def __init__(self, db_path: str = "query_logs.db"):
        self.db_path = Path(db_path)
        self._init_database()
    
    def _init_database(self):
        """Initialize database schema"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Main query logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS query_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    
                    -- Client/User Information
                    client_id TEXT NOT NULL,
                    user_org TEXT,
                    index_name TEXT,
                    conversation_id TEXT,
                    
                    -- Query Information
                    original_query TEXT NOT NULL,
                    enhanced_query TEXT,
                    was_rewritten BOOLEAN DEFAULT 0,
                    
                    -- Answer Information
                    answer TEXT NOT NULL,
                    answer_length INTEGER,
                    
                    -- Retrieval Metrics
                    chunks_retrieved INTEGER DEFAULT 0,
                    chunks_reranked INTEGER DEFAULT 0,
                    chunks_used INTEGER DEFAULT 0,
                    reranker_enabled BOOLEAN DEFAULT 0,
                    
                    -- Top Reference Info (for quick access)
                    top_reference_title TEXT,
                    top_reference_score REAL,
                    top_reference_url TEXT,
                    
                    -- Performance Metrics
                    retrieval_time_ms INTEGER,
                    reranking_time_ms INTEGER,
                    synthesis_time_ms INTEGER,
                    total_time_ms INTEGER,
                    
                    -- Success/Error Tracking
                    status TEXT DEFAULT 'success',
                    error_message TEXT,
                    
                    -- Phase 1 & 2: Advanced RAG Features
                    query_intent TEXT,
                    is_compound BOOLEAN DEFAULT 0,
                    is_followup BOOLEAN DEFAULT 0,
                    confidence_score REAL,
                    entities_detected BOOLEAN DEFAULT 0,
                    
                    -- Additional Metadata
                    metadata JSON
                )
            """)
            
            # Add new columns to existing tables (if they don't exist)
            try:
                cursor.execute("ALTER TABLE query_logs ADD COLUMN query_intent TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                cursor.execute("ALTER TABLE query_logs ADD COLUMN is_compound BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            
            try:
                cursor.execute("ALTER TABLE query_logs ADD COLUMN is_followup BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            
            try:
                cursor.execute("ALTER TABLE query_logs ADD COLUMN confidence_score REAL")
            except sqlite3.OperationalError:
                pass
            
            try:
                cursor.execute("ALTER TABLE query_logs ADD COLUMN entities_detected BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            
            # References table (stores all references for each query)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS query_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_log_id INTEGER NOT NULL,
                    reference_rank INTEGER NOT NULL,
                    
                    -- Reference Details
                    title TEXT,
                    breadcrumb TEXT,
                    url TEXT,
                    score REAL,
                    
                    -- Metadata
                    viq_codes TEXT,
                    tags TEXT,
                    text_snippet TEXT,
                    
                    FOREIGN KEY (query_log_id) REFERENCES query_logs(id) ON DELETE CASCADE
                )
            """)
            
            # User feedback table (for future ratings/feedback)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS query_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_log_id INTEGER NOT NULL,
                    feedback_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    
                    -- Feedback
                    rating INTEGER,  -- 1-5 stars
                    helpful BOOLEAN,
                    feedback_text TEXT,
                    
                    FOREIGN KEY (query_log_id) REFERENCES query_logs(id) ON DELETE CASCADE
                )
            """)
            
            # Create indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_logs_timestamp 
                ON query_logs(timestamp)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_logs_client 
                ON query_logs(client_id, timestamp)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_logs_status 
                ON query_logs(status)
            """)
            
            conn.commit()
            logger.info(f"[QUERY_LOGGER] Database initialized at {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        try:
            yield conn
        finally:
            conn.close()
    
    def log_query(
        self,
        # Required fields
        client_id: str,
        original_query: str,
        answer: str,
        
        # Optional user/org info
        user_org: Optional[str] = None,
        index_name: Optional[str] = None,
        conversation_id: Optional[str] = None,
        
        # Query rewriting
        enhanced_query: Optional[str] = None,
        was_rewritten: bool = False,
        
        # References
        references: Optional[List[Dict[str, Any]]] = None,
        
        # Metrics
        chunks_retrieved: int = 0,
        chunks_reranked: int = 0,
        chunks_used: int = 0,
        reranker_enabled: bool = False,
        
        # Performance timings (in milliseconds)
        retrieval_time_ms: Optional[int] = None,
        reranking_time_ms: Optional[int] = None,
        synthesis_time_ms: Optional[int] = None,
        total_time_ms: Optional[int] = None,
        
        # Error tracking
        status: str = "success",
        error_message: Optional[str] = None,
        
        # Phase 1 & 2: Advanced RAG Features
        query_intent: Optional[str] = None,
        is_compound: Optional[bool] = None,
        is_followup: Optional[bool] = None,
        confidence_score: Optional[float] = None,
        entities_detected: Optional[bool] = None,
        
        # Additional metadata
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Log a query interaction to the database.
        
        Returns:
            query_log_id: ID of the inserted log entry
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Extract top reference info
                top_ref_title = None
                top_ref_score = None
                top_ref_url = None
                
                if references and len(references) > 0:
                    top_ref = references[0]
                    top_ref_title = top_ref.get("title")
                    top_ref_score = top_ref.get("score")
                    top_ref_url = top_ref.get("url")
                
                # Insert main query log
                cursor.execute("""
                    INSERT INTO query_logs (
                        client_id, user_org, index_name, conversation_id,
                        original_query, enhanced_query, was_rewritten,
                        answer, answer_length,
                        chunks_retrieved, chunks_reranked, chunks_used, reranker_enabled,
                        top_reference_title, top_reference_score, top_reference_url,
                        retrieval_time_ms, reranking_time_ms, synthesis_time_ms, total_time_ms,
                        status, error_message,
                        query_intent, is_compound, is_followup, confidence_score, entities_detected,
                        metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    client_id, user_org, index_name, conversation_id,
                    original_query, enhanced_query, was_rewritten,
                    answer, len(answer) if answer else 0,
                    chunks_retrieved, chunks_reranked, chunks_used, reranker_enabled,
                    top_ref_title, top_ref_score, top_ref_url,
                    retrieval_time_ms, reranking_time_ms, synthesis_time_ms, total_time_ms,
                    status, error_message,
                    query_intent, is_compound, is_followup, confidence_score, entities_detected,
                    json.dumps(metadata) if metadata else None
                ))
                
                query_log_id = cursor.lastrowid
                
                # Insert references
                if references:
                    for rank, ref in enumerate(references, 1):
                        # Convert lists to JSON strings for storage
                        viq_codes = json.dumps(ref.get("viq", [])) if ref.get("viq") else None
                        tags = json.dumps(ref.get("tags", [])) if ref.get("tags") else None
                        text_snippet = ref.get("text", "")[:500] if ref.get("text") else None
                        
                        cursor.execute("""
                            INSERT INTO query_references (
                                query_log_id, reference_rank,
                                title, breadcrumb, url, score,
                                viq_codes, tags, text_snippet
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            query_log_id, rank,
                            ref.get("title"), ref.get("breadcrumb"), ref.get("url"), ref.get("score"),
                            viq_codes, tags, text_snippet
                        ))
                
                conn.commit()
                logger.info(f"[QUERY_LOGGER] Logged query {query_log_id} for client {client_id}")
                return query_log_id
                
        except Exception as e:
            logger.error(f"[QUERY_LOGGER] Failed to log query: {e}")
            return -1
    
    def add_feedback(self, query_log_id: int, rating: int = None, helpful: bool = None, feedback_text: str = None):
        """Add user feedback for a query"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO query_feedback (query_log_id, rating, helpful, feedback_text)
                    VALUES (?, ?, ?, ?)
                """, (query_log_id, rating, helpful, feedback_text))
                conn.commit()
                logger.info(f"[QUERY_LOGGER] Added feedback for query {query_log_id}")
        except Exception as e:
            logger.error(f"[QUERY_LOGGER] Failed to add feedback: {e}")
    
    def get_recent_queries(self, client_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get recent queries, optionally filtered by client"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if client_id:
                    cursor.execute("""
                        SELECT * FROM query_logs 
                        WHERE client_id = ?
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    """, (client_id, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM query_logs 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    """, (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[QUERY_LOGGER] Failed to get recent queries: {e}")
            return []
    
    def get_query_analytics(self, client_id: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
        """Get analytics for queries"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                base_where = f"WHERE client_id = '{client_id}'" if client_id else ""
                date_filter = f"{' AND' if base_where else 'WHERE'} timestamp >= datetime('now', '-{days} days')"
                
                # Total queries
                cursor.execute(f"SELECT COUNT(*) as total FROM query_logs {base_where} {date_filter}")
                total_queries = cursor.fetchone()["total"]
                
                # Success rate
                cursor.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful
                    FROM query_logs {base_where} {date_filter}
                """)
                result = cursor.fetchone()
                success_rate = (result["successful"] / result["total"] * 100) if result["total"] > 0 else 0
                
                # Rewrite rate
                cursor.execute(f"""
                    SELECT 
                        SUM(CASE WHEN was_rewritten = 1 THEN 1 ELSE 0 END) as rewritten,
                        COUNT(*) as total
                    FROM query_logs {base_where} {date_filter}
                """)
                result = cursor.fetchone()
                rewrite_rate = (result["rewritten"] / result["total"] * 100) if result["total"] > 0 else 0
                
                # Phase 1 & 2 Analytics
                cursor.execute(f"""
                    SELECT 
                        SUM(CASE WHEN is_compound = 1 THEN 1 ELSE 0 END) as compound_queries,
                        SUM(CASE WHEN is_followup = 1 THEN 1 ELSE 0 END) as followup_queries,
                        SUM(CASE WHEN entities_detected = 1 THEN 1 ELSE 0 END) as queries_with_entities,
                        AVG(confidence_score) as avg_confidence
                    FROM query_logs {base_where} {date_filter}
                """)
                phase_stats = dict(cursor.fetchone())
                
                # Average performance
                cursor.execute(f"""
                    SELECT 
                        AVG(total_time_ms) as avg_total_time,
                        AVG(retrieval_time_ms) as avg_retrieval_time,
                        AVG(reranking_time_ms) as avg_reranking_time,
                        AVG(synthesis_time_ms) as avg_synthesis_time,
                        AVG(chunks_retrieved) as avg_chunks_retrieved
                    FROM query_logs {base_where} {date_filter}
                """)
                perf = dict(cursor.fetchone())
                
                return {
                    "total_queries": total_queries,
                    "success_rate": round(success_rate, 2),
                    "rewrite_rate": round(rewrite_rate, 2),
                    "compound_rate": round((phase_stats["compound_queries"] / total_queries * 100) if total_queries > 0 else 0, 2),
                    "followup_rate": round((phase_stats["followup_queries"] / total_queries * 100) if total_queries > 0 else 0, 2),
                    "entity_detection_rate": round((phase_stats["queries_with_entities"] / total_queries * 100) if total_queries > 0 else 0, 2),
                    "avg_confidence": round(phase_stats["avg_confidence"] or 0, 2),
                    "performance": perf,
                    "period_days": days
                }
        except Exception as e:
            logger.error(f"[QUERY_LOGGER] Failed to get analytics: {e}")
            return {}
    
    def export_to_csv(self, output_path: str, client_id: Optional[str] = None):
        """Export query logs to CSV"""
        try:
            import csv
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if client_id:
                    cursor.execute("""
                        SELECT * FROM query_logs WHERE client_id = ? ORDER BY timestamp
                    """, (client_id,))
                else:
                    cursor.execute("SELECT * FROM query_logs ORDER BY timestamp")
                
                rows = cursor.fetchall()
                
                if rows:
                    with open(output_path, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                        writer.writeheader()
                        writer.writerows([dict(row) for row in rows])
                    
                    logger.info(f"[QUERY_LOGGER] Exported {len(rows)} queries to {output_path}")
                    return len(rows)
                return 0
        except Exception as e:
            logger.error(f"[QUERY_LOGGER] Failed to export to CSV: {e}")
            return 0


# Singleton instance
_query_logger = None

def get_query_logger(db_path: str = "query_logs.db") -> QueryLogger:
    """Get or create the global query logger instance"""
    global _query_logger
    if _query_logger is None:
        _query_logger = QueryLogger(db_path)
    return _query_logger