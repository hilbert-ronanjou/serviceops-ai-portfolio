"""Frozen F06/C authority, separate from ITSM and model output."""
from sqlalchemy import String,Integer,DateTime,ForeignKey,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from backend.models import Base,utcnow

class ApproverScope(Base):
    __tablename__='access_approver_scopes'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    resource:Mapped[str]=mapped_column(String(50),primary_key=True)

class AccessRequest(Base):
    __tablename__='access_requests'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('agent_conversations.id'),unique=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    request_key:Mapped[str]=mapped_column(String(100))
    creation_hash:Mapped[str]=mapped_column(String(64))
    resource:Mapped[str]=mapped_column(String(50))
    permission:Mapped[str]=mapped_column(String(20))
    reason:Mapped[str]=mapped_column(String(400))
    operation_id:Mapped[str|None]=mapped_column(ForeignKey('operations.id'),nullable=True)
    version:Mapped[int]=mapped_column(Integer,default=1)
    scope_hash:Mapped[str]=mapped_column(String(64))
    phase:Mapped[str]=mapped_column(String(30),default='pending')
    decision_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    grant_id:Mapped[str]=mapped_column(String(36),unique=True)
    receipt_hash:Mapped[str|None]=mapped_column(String(64),nullable=True)
    error:Mapped[str|None]=mapped_column(String(60),nullable=True)
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    created_at:Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    expires_at:Mapped[object]=mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint('user_id','request_key'),)

class AccessDecision(Base):
    __tablename__='access_decisions'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    request_id:Mapped[str]=mapped_column(ForeignKey('access_requests.id'))
    version:Mapped[int]=mapped_column(Integer)
    scope_hash:Mapped[str]=mapped_column(String(64))
    actor_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    action:Mapped[str]=mapped_column(String(12))
    created_at:Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('request_id','version','action'),)
