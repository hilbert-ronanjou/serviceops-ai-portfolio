"""Scenario-B notification intent, not a second ticket authority or generic bus."""
from sqlalchemy import String,Integer,DateTime,ForeignKey,JSON,UniqueConstraint,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from backend.models import Base,utcnow

class TicketEvent(Base):
    __tablename__='ticket_events'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    source:Mapped[str]=mapped_column(String(60))
    fingerprint:Mapped[str]=mapped_column(String(64),unique=True)
    body_sha256:Mapped[str]=mapped_column(String(64))
    signed_at:Mapped[int]=mapped_column(Integer)
    payload:Mapped[dict]=mapped_column(JSON)
    ticket_id:Mapped[int]=mapped_column(Integer)
    operation_id:Mapped[str|None]=mapped_column(ForeignKey('operations.id'),nullable=True)
    notice_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    state:Mapped[str]=mapped_column(String(30))
    reason:Mapped[str|None]=mapped_column(String(60),nullable=True)
    payload_status_mismatch:Mapped[bool]=mapped_column(Boolean,default=False)
    trail:Mapped[list]=mapped_column(JSON,default=list)
    created_at:Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)

class TicketEventCursor(Base):
    __tablename__='ticket_event_cursors'
    source:Mapped[str]=mapped_column(String(60),primary_key=True)
    ticket_id:Mapped[int]=mapped_column(Integer,primary_key=True)
    revision:Mapped[str]=mapped_column(String(40))
    status:Mapped[int]=mapped_column(Integer)
    notice_id:Mapped[str]=mapped_column(String(36))

class TicketNotice(Base):
    __tablename__='ticket_notices'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    event_id:Mapped[str]=mapped_column(ForeignKey('ticket_events.id'))
    source:Mapped[str]=mapped_column(String(60))
    ticket_id:Mapped[int]=mapped_column(Integer)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    revision:Mapped[str]=mapped_column(String(40))
    status:Mapped[int]=mapped_column(Integer)
    payload_hash:Mapped[str]=mapped_column(String(64))
    state:Mapped[str]=mapped_column(String(30))
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    reason:Mapped[str|None]=mapped_column(String(60),nullable=True)
    receipt:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    trail:Mapped[list]=mapped_column(JSON,default=list)
    created_at:Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('source','ticket_id','revision','status'),)
