# app/routers/feedback.py - Feedback API endpoints
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from loguru import logger
import math

from app.models import (
    FeedbackRequest,
    FeedbackResponse,
    FeedbackDashboardResponse,
    FeedbackType
)
from app.services.feedback_service import get_feedback_service

router = APIRouter()

@router.post("/submit", response_model=FeedbackResponse)
async def submit_feedback(feedback: FeedbackRequest):
    """
    Submit user feedback for a question-answer pair

    - **thumbs_up**: User found the answer helpful
    - **thumbs_down**: User found the answer unhelpful (comment optional)
    """
    try:
        logger.info(f"[FEEDBACK] Submitting {feedback.feedback_type} feedback for client {feedback.client_id}")

        feedback_service = get_feedback_service()
        feedback_id = feedback_service.submit_feedback(feedback)

        return FeedbackResponse(
            success=True,
            message="Feedback submitted successfully",
            feedback_id=feedback_id
        )

    except Exception as e:
        logger.error(f"[FEEDBACK] Failed to submit feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {str(e)}")

@router.get("/dashboard", response_model=FeedbackDashboardResponse)
async def get_feedback_dashboard(
    client_id: Optional[str] = Query(None, description="Filter by client ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    feedback_type: Optional[FeedbackType] = Query(None, description="Filter by feedback type")
):
    """
    Get feedback dashboard with statistics and paginated feedback items
    """
    try:
        logger.info(f"[FEEDBACK] Getting dashboard for client {client_id}, page {page}")

        feedback_service = get_feedback_service()

        # Get statistics
        stats = feedback_service.get_feedback_stats(client_id)

        # Get paginated feedback items
        feedback_items, total_count = feedback_service.get_feedback_items(
            client_id=client_id,
            page=page,
            page_size=page_size,
            feedback_type=feedback_type
        )

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

        return FeedbackDashboardResponse(
            stats=stats,
            feedback_items=feedback_items,
            total_pages=total_pages,
            current_page=page
        )

    except Exception as e:
        logger.error(f"[FEEDBACK] Failed to get dashboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard: {str(e)}")

@router.get("/stats")
async def get_feedback_stats(
    client_id: Optional[str] = Query(None, description="Filter by client ID")
):
    """
    Get feedback statistics only (lightweight endpoint)
    """
    try:
        feedback_service = get_feedback_service()
        stats = feedback_service.get_feedback_stats(client_id)
        return stats

    except Exception as e:
        logger.error(f"[FEEDBACK] Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

@router.get("/health")
async def feedback_health():
    """Health check for feedback service"""
    try:
        feedback_service = get_feedback_service()
        # Simple test query
        stats = feedback_service.get_feedback_stats()
        return {
            "status": "healthy",
            "total_feedback": stats.total_feedback,
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"[FEEDBACK] Health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Feedback service unhealthy: {str(e)}")
