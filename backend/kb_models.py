"""Minimal self-reported feedback, never proof of objective problem resolution."""
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from backend.models import Base


class KnowledgeFeedback(Base):
    __tablename__ = 'knowledge_feedback'
    __table_args__ = (Index('ix_knowledge_feedback_updated_at', 'updated_at'),)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    article_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(80))
    resolved: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
