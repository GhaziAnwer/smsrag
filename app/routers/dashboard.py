# app/routers/dashboard.py - RAG Analytics Dashboard API
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi import Path as PathParam
from typing import Optional, List, Dict, Any
import sqlite3
import json
from datetime import datetime, timedelta
from loguru import logger
from app.auth import authenticate

router = APIRouter()

# Auto-detect query logs database path
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # Go up to project root
QUERY_LOGS_DB = os.getenv("QUERY_LOGS_DB", str(PROJECT_ROOT / "query_logs.db"))

@router.get("/test")
async def test_endpoint():
    """Test endpoint without authentication"""
    return {"status": "ok", "message": "Dashboard API is working"}

@router.get("/test-auth")
async def test_auth(user: str = Depends(authenticate)):
    """Test endpoint with authentication"""
    return {"status": "ok", "user": user, "message": "Authentication working"}

def get_db():
    """Get database connection"""
    try:
        conn = sqlite3.connect(QUERY_LOGS_DB)
        conn.row_factory = sqlite3.Row
        # Test connection
        conn.execute("SELECT 1").fetchone()
        return conn
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/overview", summary="Dashboard Overview Metrics")
async def get_overview(
    client_id: Optional[str] = Query(None, description="Filter by client ID"),
    days: int = Query(30, ge=1, le=365, description="Number of days to include in metrics")
):
    """Get dashboard overview metrics including query statistics and performance."""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        where_clause = "WHERE timestamp >= ?"
        params = [since_date.isoformat()]

        if client_id:
            where_clause += " AND user_org = ?"
            params.append(client_id)

        # Total queries
        total_queries = conn.execute(
            f"SELECT COUNT(*) FROM query_logs {where_clause}", params
        ).fetchone()[0]

        # Average quality (top reference score)
        avg_quality = conn.execute(
            f"SELECT AVG(COALESCE(top_reference_score, 0)) FROM query_logs {where_clause}", params
        ).fetchone()[0] or 0

        # Average response time
        avg_response_time = conn.execute(
            f"SELECT AVG(total_time_ms) FROM query_logs {where_clause}", params
        ).fetchone()[0] or 0

        # Active clients (all time, exclude None values)
        active_clients = conn.execute(
            "SELECT COUNT(DISTINCT user_org) FROM query_logs WHERE user_org IS NOT NULL"
        ).fetchone()[0]

        conn.close()

        return {
            "total_queries": total_queries,
            "avg_quality": round(avg_quality, 3),
            "avg_response_time": round(avg_response_time, 1),
            "active_clients": active_clients,
            "total_clients": active_clients,
            "total_documents": 0,
            "active_clients_with_docs": active_clients
        }
    except Exception as e:
        return {
            "error": str(e),
            "total_queries": 0,
            "avg_quality": 0,
            "avg_response_time": 0,
            "active_clients": 0,
            "total_clients": 0,
            "total_documents": 0,
            "active_clients_with_docs": 0
        }

@router.get("/queries", summary="Get Query Logs")
async def get_queries(
    client_id: Optional[str] = Query(None, description="Filter by client ID"),
    query_type: Optional[str] = Query(None, description="Filter by query intent type"),
    search: Optional[str] = Query(None, description="Search in query text"),
    days: Optional[int] = Query(None, ge=1, le=365, description="Filter by days"),
    limit: int = Query(50, ge=1, le=1000, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """Get paginated query logs with filtering options."""
    try:
        conn = get_db()

        where_conditions = []
        params = []

        if days is not None:
            since_date = datetime.now() - timedelta(days=days)
            where_conditions.append("timestamp >= ?")
            params.append(since_date.isoformat())

        if client_id:
            where_conditions.append("user_org = ?")
            params.append(client_id)

        if query_type:
            where_conditions.append("query_intent = ?")
            params.append(query_type)

        if search:
            where_conditions.append("original_query LIKE ?")
            params.append(f"%{search}%")

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        # Get total count
        total = conn.execute(
            f"SELECT COUNT(*) FROM query_logs {where_clause}", params
        ).fetchone()[0]

        # Get queries
        queries = conn.execute(f"""
            SELECT id, original_query, query_intent, user_org, timestamp,
                   total_time_ms, top_reference_score, is_compound, confidence_score
            FROM query_logs {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        conn.close()

        return {
            "queries": [dict(q) for q in queries],
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        return {
            "error": str(e),
            "queries": [],
            "total": 0,
            "limit": limit,
            "offset": offset
        }

@router.get("/queries/{query_id}", summary="Get Query Details")
async def get_query_detail(
    query_id: int = PathParam(..., description="Query ID")
):
    """Get detailed information for a specific query including Q&A details."""
    try:
        conn = get_db()
        query = conn.execute(
            "SELECT * FROM query_logs WHERE id = ?", [query_id]
        ).fetchone()

        if not query:
            raise HTTPException(status_code=404, detail="Query not found")

        # Convert to dict and add formatted data
        result = dict(query)

        # Format timestamp
        if result.get('timestamp'):
            try:
                dt = datetime.fromisoformat(result['timestamp'])
                result['formatted_timestamp'] = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                result['formatted_timestamp'] = result['timestamp']

        # Parse JSON fields safely
        json_fields = ['entities_detected', 'performance_metrics']
        for field in json_fields:
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except:
                    pass

        conn.close()
        return result
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "query_id": query_id}

@router.get("/clients-overview")
async def get_clients_overview():
    """Get client overview from query logs"""
    try:
        conn = get_db()

        # Get per-client stats from query logs
        clients = conn.execute("""
            SELECT
                user_org as client_id,
                COUNT(*) as total_queries,
                AVG(total_time_ms) as avg_response_time,
                AVG(COALESCE(top_reference_score, 0)) as avg_quality,
                MAX(timestamp) as last_query
            FROM query_logs
            WHERE user_org IS NOT NULL
            GROUP BY user_org
            ORDER BY total_queries DESC
        """).fetchall()

        # Get recent activity per client
        enhanced_clients = []
        for client in clients:
            client_id = client['client_id']
            recent_queries = conn.execute(
                "SELECT COUNT(*) FROM query_logs WHERE user_org = ? AND timestamp >= datetime('now', '-7 days')",
                [client_id]
            ).fetchone()[0]

            enhanced_clients.append({
                "client_id": client_id,
                "status": "active",
                "statistics": {
                    "document_count": 0,
                    "index_health": "unknown"
                },
                "query_stats": {
                    "total_queries": client['total_queries'],
                    "avg_response_time": round(client['avg_response_time'] or 0, 1),
                    "recent_queries_7d": recent_queries
                }
            })

        conn.close()

        return {
            "clients": enhanced_clients,
            "summary": {
                "total_clients": len(enhanced_clients),
                "active_clients": len(enhanced_clients),
                "total_documents": 0,
                "total_queries": sum(c["query_stats"]["total_queries"] for c in enhanced_clients)
            }
        }
    except Exception as e:
        logger.error(f"Error getting clients overview: {e}")
        return {"error": str(e), "clients": [], "summary": {}}

@router.get("/qa-details")
async def get_qa_details(
    client_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100)
):
    """Get recent Q&A pairs with full details"""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        where_clause = "WHERE timestamp >= ?"
        params = [since_date.isoformat()]

        if client_id:
            where_clause += " AND user_org = ?"
            params.append(client_id)

        qa_pairs = conn.execute(f"""
            SELECT
                id,
                original_query as question,
                answer,
                user_org as client_id,
                timestamp,
                total_time_ms,
                top_reference_score,
                query_intent,
                is_compound,
                confidence_score,
                chunks_retrieved,
                chunks_used
            FROM query_logs {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """, params + [limit]).fetchall()

        # Format the results
        formatted_pairs = []
        for row in qa_pairs:
            qa_dict = dict(row)

            # Format timestamp
            if qa_dict.get('timestamp'):
                try:
                    dt = datetime.fromisoformat(qa_dict['timestamp'])
                    qa_dict['formatted_timestamp'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                    qa_dict['time_ago'] = _time_ago(dt)
                except:
                    qa_dict['formatted_timestamp'] = qa_dict['timestamp']
                    qa_dict['time_ago'] = 'Unknown'

            # Truncate long text for preview
            if qa_dict.get('question') and len(qa_dict['question']) > 100:
                qa_dict['question_preview'] = qa_dict['question'][:100] + '...'
            else:
                qa_dict['question_preview'] = qa_dict.get('question', '')

            if qa_dict.get('answer') and len(qa_dict['answer']) > 200:
                qa_dict['answer_preview'] = qa_dict['answer'][:200] + '...'
            else:
                qa_dict['answer_preview'] = qa_dict.get('answer', '')

            # Add quality indicators
            score = qa_dict.get('top_reference_score', 0) or 0
            if score >= 0.8:
                qa_dict['quality'] = 'High'
                qa_dict['quality_color'] = 'green'
            elif score >= 0.6:
                qa_dict['quality'] = 'Medium'
                qa_dict['quality_color'] = 'orange'
            else:
                qa_dict['quality'] = 'Low'
                qa_dict['quality_color'] = 'red'

            formatted_pairs.append(qa_dict)

        conn.close()

        return {
            "qa_pairs": formatted_pairs,
            "total_count": len(formatted_pairs),
            "days_range": days
        }
    except Exception as e:
        return {
            "error": str(e),
            "qa_pairs": [],
            "total_count": 0,
            "days_range": days
        }

@router.get("/performance/timeline")
async def get_performance_timeline(
    client_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90)
):
    """Get daily performance timeline"""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        where_clause = "WHERE timestamp >= ?"
        params = [since_date.isoformat()]

        if client_id:
            where_clause += " AND user_org = ?"
            params.append(client_id)

        timeline = conn.execute(f"""
            SELECT
                DATE(timestamp) as date,
                COUNT(*) as query_count,
                AVG(total_time_ms) as avg_response_time,
                AVG(COALESCE(top_reference_score, 0)) as avg_quality
            FROM query_logs {where_clause}
            GROUP BY DATE(timestamp)
            ORDER BY date
        """, params).fetchall()

        conn.close()
        return [dict(row) for row in timeline]
    except Exception as e:
        return {"error": str(e), "timeline": []}

@router.get("/clients")
async def get_client_stats(
    days: int = Query(30, ge=1, le=365)
):
    """Get per-client statistics"""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        clients = conn.execute("""
            SELECT
                user_org as client_id,
                COUNT(*) as query_count,
                AVG(total_time_ms) as avg_response_time,
                AVG(COALESCE(top_reference_score, 0)) as avg_quality
            FROM query_logs
            WHERE timestamp >= ?
            GROUP BY user_org
            ORDER BY query_count DESC
        """, [since_date.isoformat()]).fetchall()

        conn.close()
        return [dict(row) for row in clients]
    except Exception as e:
        return {"error": str(e), "clients": []}

@router.get("/query-types")
async def get_query_types(
    client_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365)
):
    """Get query type distribution"""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        where_clause = "WHERE timestamp >= ?"
        params = [since_date.isoformat()]

        if client_id:
            where_clause += " AND user_org = ?"
            params.append(client_id)

        types = conn.execute(f"""
            SELECT
                COALESCE(query_intent, 'unknown') as query_type,
                COUNT(*) as count
            FROM query_logs {where_clause}
            GROUP BY query_intent
            ORDER BY count DESC
        """, params).fetchall()

        conn.close()
        return [dict(row) for row in types]
    except Exception as e:
        return {"error": str(e), "types": []}

def _time_ago(dt):
    """Helper function to calculate time ago"""
    now = datetime.now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=None)
    if now.tzinfo is None:
        now = now.replace(tzinfo=None)

    diff = now - dt

    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    else:
        return "Just now"

@router.get("/export")
async def export_data(
    client_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    format: str = Query("json", regex="^(json|csv)$")
):
    """Export query data"""
    try:
        conn = get_db()
        since_date = datetime.now() - timedelta(days=days)

        where_clause = "WHERE timestamp >= ?"
        params = [since_date.isoformat()]

        if client_id:
            where_clause += " AND user_org = ?"
            params.append(client_id)

        queries = conn.execute(f"""
            SELECT * FROM query_logs {where_clause}
            ORDER BY timestamp DESC
        """, params).fetchall()

        data = [dict(row) for row in queries]

        if format == "csv":
            import csv
            import io
            output = io.StringIO()
            if data:
                writer = csv.DictWriter(output, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
            return {"data": output.getvalue(), "format": "csv"}

        conn.close()
        return {"data": data, "format": "json"}
    except Exception as e:
        return {"error": str(e), "data": [], "format": format}
