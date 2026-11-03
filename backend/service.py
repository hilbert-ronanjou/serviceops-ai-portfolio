import hashlib
import json
import uuid
from contextlib import contextmanager
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from backend.db import engine
from backend.models import Operation, Audit
from backend.adapter import Adapter, Upstream

adapter = Adapter()


@contextmanager
def locked(user_id, key):
    """Session advisory lock spans ledger commits + upstream writes on this connection.

    Cross worker/process exclusion; released by PostgreSQL on process/connection death.
    Each side effect is preceded by a durable intent, so death cannot trigger a second create.
    """
    lock_id = int.from_bytes(hashlib.sha256(f'{user_id}:{key}'.encode()).digest()[:8], 'big', signed=True)
    with engine.connect() as conn:
        conn.execute(text('SELECT pg_advisory_lock(:key)'), {'key': lock_id})
        conn.commit()
        db = Session(bind=conn, expire_on_commit=False)
        try:
            yield db
        finally:
            db.close()
            conn.rollback()
            conn.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_id})
            conn.commit()


def repair(db, row, user):
    try:
        adapter.bind(row.native_id, user.glpi_user_id)
        adapter.read(row.native_id, user.glpi_user_id)
        row.phase, row.error_code = 'complete', None
        db.commit()
    except Upstream as exc:
        row.phase, row.error_code = 'repair_pending', exc.code
        db.commit()
        raise


def create(user, payload, key, request_id):
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with locked(user.id, key) as db:
        row = db.scalar(select(Operation).where(Operation.user_id == user.id, Operation.request_key == key))
        replay = row is not None
        if row and row.request_hash != fingerprint:
            raise HTTPException(409, 'idempotency_conflict')
        if not row:
            row = Operation(id=str(uuid.uuid4()), user_id=user.id, request_key=key,
                            request_hash=fingerprint, phase='creating', payload=payload)
            db.add(row)
            db.commit()
            try:
                row.native_id = adapter.create(payload, row.id)
                row.phase = 'repair_pending'
                db.commit()
            except Upstream as exc:
                row.phase, row.error_code = 'uncertain', exc.code
                db.commit()
                raise
        if row.phase in ('creating', 'uncertain'):
            raise HTTPException(409, 'create_uncertain_reconciliation_required')
        if row.phase == 'repair_pending':
            repair(db, row, user)
        result = adapter.read(row.native_id, user.glpi_user_id)
        db.add(Audit(request_id=request_id, operation_id=row.id, user_id=user.id,
                     action='create_ticket', outcome='replay' if replay else 'verified'))
        db.commit()
        return {'id': row.id, 'request_key': key, 'phase': row.phase, **result}, replay


def owned(db, user, operation_id):
    row = db.scalar(select(Operation).where(Operation.id == operation_id, Operation.user_id == user.id))
    if not row:
        raise HTTPException(404, 'ticket_not_found')
    return row


def view(row, user, detail=False):
    if row.phase != 'complete':
        # Incomplete state is observable to its owner; never implies a successful ticket.
        return {'id': row.id, 'request_key': row.request_key, 'phase': row.phase,
                'error_code': row.error_code, 'title': row.payload['title']}
    result = {'id': row.id, 'request_key': row.request_key, 'phase': row.phase,
              **adapter.read(row.native_id, user.glpi_user_id)}
    if detail:
        result['replies'] = adapter.replies(row.native_id)
    return result
