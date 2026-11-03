"""M2 state, knowledge and one cumulative cost ledger. No new business authority."""
from sqlalchemy import BigInteger, String, Integer, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint, CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from backend.models import Base, utcnow


class BudgetAccount(Base):
    __tablename__='ai_budget_accounts'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    spent_nano: Mapped[int]=mapped_column(BigInteger,default=0)
    reserved_nano: Mapped[int]=mapped_column(BigInteger,default=0)
    status: Mapped[str]=mapped_column(String(40),default='open')
    __table_args__=(CheckConstraint('spent_nano >= 0 AND reserved_nano >= 0'),)


class ModelCall(Base):
    __tablename__='ai_model_calls'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    budget_id: Mapped[str]=mapped_column(ForeignKey('ai_budget_accounts.id'))
    request_key: Mapped[str]=mapped_column(String(160),unique=True)
    operation_id: Mapped[str]=mapped_column(String(64))
    purpose: Mapped[str]=mapped_column(String(40))
    model: Mapped[str]=mapped_column(String(50))
    region: Mapped[str]=mapped_column(String(30),default='cn-beijing')
    price_version: Mapped[str]=mapped_column(String(50))
    mode: Mapped[str]=mapped_column(String(30))
    input_hash: Mapped[str]=mapped_column(String(64))
    input_bound: Mapped[int]=mapped_column(Integer)
    output_bound: Mapped[int]=mapped_column(Integer)
    reservation_nano: Mapped[int]=mapped_column(BigInteger)
    # Nullable preserves every historical record. New live sends require a bound
    # proof and the DB fence; this is NOT vendor Usage or a new budget ledger.
    max_cost_proof: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    cost_nano: Mapped[int|None]=mapped_column(BigInteger,nullable=True)
    input_tokens: Mapped[int|None]=mapped_column(Integer,nullable=True)
    output_tokens: Mapped[int|None]=mapped_column(Integer,nullable=True)
    usage: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    result: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    status: Mapped[str]=mapped_column(String(40))
    error_code: Mapped[str|None]=mapped_column(String(60),nullable=True)
    elapsed_ms: Mapped[int]=mapped_column(Integer,default=0)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    settled_at: Mapped[object|None]=mapped_column(DateTime(timezone=True),nullable=True)


class UnknownDisposition(Base):
    """Additional conservative reserve, never supplier Usage or settlement."""
    __tablename__='ai_unknown_dispositions'
    call_id: Mapped[str]=mapped_column(ForeignKey('ai_model_calls.id'),primary_key=True)
    budget_id: Mapped[str]=mapped_column(ForeignKey('ai_budget_accounts.id'))
    original_reservation_nano: Mapped[int]=mapped_column(BigInteger)
    upper_nano: Mapped[int]=mapped_column(BigInteger)
    additional_nano: Mapped[int]=mapped_column(BigInteger)
    proof_hash: Mapped[str]=mapped_column(String(64))
    authorization_hash: Mapped[str]=mapped_column(String(64))
    evidence: Mapped[dict]=mapped_column(JSON)
    supplier_settlement: Mapped[str]=mapped_column(String(20),default='PENDING')
    run_id: Mapped[str]=mapped_column(String(100),unique=True)
    batch_phase: Mapped[str]=mapped_column(String(20),default='unclaimed')
    batch_ceiling_nano: Mapped[int]=mapped_column(BigInteger)
    chat_attempts: Mapped[int]=mapped_column(Integer,default=0)
    embedding_attempts: Mapped[int]=mapped_column(Integer,default=0)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint('additional_nano >= 0 AND upper_nano = original_reservation_nano + additional_nano'),)


class HistoricalUnknownAcceptance(Base):
    """Owner decision, NOT Usage/settlement/extra reserve or a live authorization.

    Separate from UnknownDisposition: that historical journal requires a proven
    monetary upper bound. Putting an unproven amount there would falsify it.
    """
    __tablename__='ai_historical_unknown_acceptances'
    call_id: Mapped[str]=mapped_column(ForeignKey('ai_model_calls.id'),primary_key=True)
    budget_id: Mapped[str]=mapped_column(ForeignKey('ai_budget_accounts.id'))
    identity_sha256: Mapped[str]=mapped_column(String(64))
    decision_sha256: Mapped[str]=mapped_column(String(64))
    prior_cover_nano: Mapped[int]=mapped_column(BigInteger)
    evidence: Mapped[dict]=mapped_column(JSON)
    classification: Mapped[str]=mapped_column(String(50),default='historical_unknown_owner_accepted')
    supplier_settlement: Mapped[str]=mapped_column(String(20),default='PENDING')
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint("budget_id='M2-20261014' AND call_id IN ('8fe869b9-349e-4330-bab9-7b9a7e9810f8','30604883-dab3-468a-9507-9b58aa886d45') AND classification='historical_unknown_owner_accepted' AND supplier_settlement='PENDING' AND prior_cover_nano>=0"),)


class Conversation(Base):
    __tablename__='agent_conversations'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    state: Mapped[dict]=mapped_column(JSON,default=dict)
    phase: Mapped[str]=mapped_column(String(30),default='ready')
    turn_count: Mapped[int]=mapped_column(Integer,default=0)
    pending_summary: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    ticket_id: Mapped[str|None]=mapped_column(String(36),nullable=True)
    expires_at: Mapped[object]=mapped_column(DateTime(timezone=True))
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)


class AgentTurn(Base):
    __tablename__='agent_turns'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    conversation_id: Mapped[str]=mapped_column(ForeignKey('agent_conversations.id'))
    client_key: Mapped[str]=mapped_column(String(100))
    input_hash: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(30))
    result: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error_code: Mapped[str|None]=mapped_column(String(60),nullable=True)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('conversation_id','client_key'),)


class AgentEvent(Base):
    __tablename__='agent_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    conversation_id: Mapped[str]=mapped_column(String(36))
    turn_id: Mapped[str]=mapped_column(String(36))
    request_id: Mapped[str]=mapped_column(String(36))
    node: Mapped[str]=mapped_column(String(40))
    outcome: Mapped[str]=mapped_column(String(60))
    call_id: Mapped[str|None]=mapped_column(String(36),nullable=True)
    elapsed_ms: Mapped[int]=mapped_column(Integer,default=0)
    # Optional, allowlisted trace metadata only; never prompts, reasoning or credentials.
    trace_data: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)


class KnowledgeChunk(Base):
    __tablename__='knowledge_chunks'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    document_id: Mapped[str]=mapped_column(String(60))
    title: Mapped[str]=mapped_column(String(180))
    source_url: Mapped[str|None]=mapped_column(String(500),nullable=True)
    synthetic: Mapped[bool]=mapped_column(Boolean)
    source_version: Mapped[str]=mapped_column(String(100))
    content_hash: Mapped[str]=mapped_column(String(64))
    corpus_version: Mapped[str]=mapped_column(String(64))
    text: Mapped[str]=mapped_column(Text)
    embedding_model: Mapped[str]=mapped_column(String(50))
    embedding_dim: Mapped[int]=mapped_column(Integer)
    embedding: Mapped[list|None]=mapped_column(Vector(1024),nullable=True)
    embedding_mode: Mapped[str]=mapped_column(String(30))
    # Preserve physical column for additive migration compatibility; records last materialization.
    indexed_at: Mapped[object]=mapped_column('created_at',DateTime(timezone=True),default=utcnow)


class EmbeddingCache(Base):
    __tablename__='embedding_cache'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    model: Mapped[str]=mapped_column(String(50))
    dimension: Mapped[int]=mapped_column(Integer)
    mode: Mapped[str]=mapped_column(String(30))
    vector: Mapped[list]=mapped_column(Vector(1024))
    call_id: Mapped[str]=mapped_column(String(36))
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=utcnow)
