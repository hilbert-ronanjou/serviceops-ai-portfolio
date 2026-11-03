"""One current choice per employee/article content; atomic upsert across workers."""
from datetime import timedelta
from sqlalchemy import delete, select
from backend.db import SessionLocal
from backend.models import utcnow
from backend.kb_models import KnowledgeFeedback as Feedback

RETENTION_DAYS = 30


def cutoff():
    return utcnow() - timedelta(days=RETENTION_DAYS)


def purge_expired(db):
    return db.execute(delete(Feedback).where(Feedback.updated_at <= cutoff())).rowcount


def save(user_id, article, resolved):
    with SessionLocal() as db:
        purge_expired(db)
        # Production is PostgreSQL; SQLite supports isolated contract tests only.
        if db.bind.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        elif db.bind.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError('Unsupported feedback persistence')
        statement = insert(Feedback).values(user_id=user_id, article_id=article['id'],
            content_hash=article['content_hash'], version=article['version'], resolved=resolved, updated_at=utcnow())
        statement = statement.on_conflict_do_update(
            index_elements=['user_id', 'article_id', 'content_hash'],
            set_={'resolved': statement.excluded.resolved, 'updated_at': statement.excluded.updated_at},
            where=Feedback.resolved != statement.excluded.resolved)
        db.execute(statement)
        db.commit()


def own(user_id, article):
    with SessionLocal() as db:
        row = db.scalar(select(Feedback).where(Feedback.user_id == user_id,
            Feedback.article_id == article['id'], Feedback.content_hash == article['content_hash'],
            Feedback.updated_at > cutoff()))
        return {'resolved': row.resolved if row else None}
