"""C uses real PostgresSaver/interrupt; resume data is NOT an approval."""
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt
from backend.db import SessionLocal
from backend.access_models import AccessRequest
from backend import access_service as authority
from backend.models import utcnow
class State(TypedDict):request_id:str
def build(checkpointer):
    def wait(state):
        with SessionLocal() as db:
            row=db.get(AccessRequest,state['request_id'])
            if row.phase=='pending' and row.expires_at<=utcnow():
                row.phase='expired';authority.event(db,row,'approval_gate','approval_expired');db.commit()
            pending=row.phase=='pending';prompt={'kind':'trusted_human_approval_required','request_id':row.id,'version':row.version,'scope_hash':row.scope_hash}
        if pending:interrupt(prompt)
        return {}
    def execute(state):authority.execute(state['request_id']);return {}
    g=StateGraph(State);g.add_node('approval_wait',wait);g.add_node('permission_execute',execute)
    g.add_edge(START,'approval_wait');g.add_edge('approval_wait','permission_execute');g.add_edge('permission_execute',END)
    return g.compile(checkpointer=checkpointer)
