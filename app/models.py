from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from enum import Enum


class AskRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    index_name: Optional[str] = None

    # <-- important: let the UI pass conversation_id (for history)
    conversation_id: Optional[str] = None

    # allow future-safe extra keys from the UI without 422
    model_config = ConfigDict(extra="ignore")


class RefItem(BaseModel):
    title: Optional[str] = None
    breadcrumb: Optional[str] = None
    url: Optional[str] = None
    score: Optional[float] = None
    viq: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class AskResponse(BaseModel):
    answer: str
    references: List[RefItem] = []
    meta: Dict[str, Any] = {}


# ── Feedback Models ──────────────────────────────────────────

class FeedbackType(str, Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"

class FeedbackRequest(BaseModel):
    conversation_id: str
    client_id: str
    question: str
    answer: str
    feedback_type: FeedbackType
    comment: Optional[str] = None
    user_id: Optional[str] = None

class FeedbackResponse(BaseModel):
    success: bool
    message: str
    feedback_id: Optional[int] = None

class FeedbackItem(BaseModel):
    id: int
    conversation_id: str
    client_id: str
    question: str
    answer: str
    feedback_type: FeedbackType
    comment: Optional[str]
    user_id: Optional[str]
    created_at: datetime

class FeedbackStats(BaseModel):
    total_feedback: int
    thumbs_up_count: int
    thumbs_down_count: int
    thumbs_up_percentage: float
    thumbs_down_percentage: float
    recent_feedback: List[FeedbackItem]

class FeedbackDashboardResponse(BaseModel):
    stats: FeedbackStats
    feedback_items: List[FeedbackItem]
    total_pages: int
    current_page: int
