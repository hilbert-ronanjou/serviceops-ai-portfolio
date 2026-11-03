"""Official PostgreSQL LangGraph saver. Setup is an explicit migration step."""
from contextlib import contextmanager
from langgraph.checkpoint.postgres import PostgresSaver
from backend.config import settings


@contextmanager
def saver():
    dsn=settings.database_url.replace('postgresql+psycopg://','postgresql://',1)
    with PostgresSaver.from_conn_string(dsn) as checkpointer:
        yield checkpointer


if __name__=='__main__':
    with saver() as checkpointer:checkpointer.setup()
    print('LangGraph PostgreSQL checkpoint schema ready; no model call.')
