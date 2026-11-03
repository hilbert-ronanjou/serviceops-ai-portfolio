"""Session/CSRF protected employee knowledge; no GLPI or Agent writes."""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from backend.auth import authenticate
from backend import kb_catalog, kb_feedback
from backend.tools import safe_text

router = APIRouter(prefix='/api/knowledge')


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content_hash: str = Field(pattern='^[a-f0-9]{64}$')
    resolved: bool


def article_by_id(article_id):
    try:
        return next((a for a in kb_catalog.published() if a['id'] == article_id), None)
    except kb_catalog.CatalogUnavailable:
        raise HTTPException(503, 'knowledge_unavailable') from None


def require_article(article_id):
    if not kb_catalog.ID.fullmatch(article_id):raise HTTPException(404,'knowledge_not_found')
    article = article_by_id(article_id)
    if article is None:
        raise HTTPException(404, 'knowledge_not_found')
    return article


@router.get('')
def search(request: Request, q: str = Query(default='', max_length=160)):
    authenticate(request)
    try:
        return kb_catalog.search(safe_text(q))
    except ValueError:
        raise HTTPException(422, 'invalid_query') from None
    except kb_catalog.CatalogUnavailable:
        raise HTTPException(503, 'knowledge_unavailable') from None


@router.get('/{article_id}')
def detail(article_id: str, request: Request):
    authenticate(request)
    return require_article(article_id)


@router.get('/{article_id}/feedback')
def own_feedback(article_id: str, request: Request):
    user = authenticate(request)
    return kb_feedback.own(user.id, require_article(article_id))


@router.put('/{article_id}/feedback')
def feedback(article_id: str, body: FeedbackBody, request: Request):
    user = authenticate(request, write=True)
    article = require_article(article_id)
    if body.content_hash != article['content_hash']:
        raise HTTPException(409, 'knowledge_version_changed')
    kb_feedback.save(user.id, article, body.resolved)
    return {'resolved': body.resolved, 'self_reported': True}
