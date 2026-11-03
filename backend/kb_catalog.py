"""Bounded employee publication catalogue. No model, embeddings, URL fetch or RAG fallback."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / 'knowledge' / 'employee'
ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
ARTICLE_FIELDS = {'id', 'title', 'keywords', 'body', 'applicability', 'steps',
                  'precautions', 'not_applicable', 'verification', 'sources', 'version', 'synthetic', 'audience'}


class CatalogUnavailable(Exception):
    pass


def article_hash(article):
    return hashlib.sha256(json.dumps(article, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def text(value, maximum):
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum


def texts(values, count, maximum):
    return isinstance(values, list) and 0 < len(values) <= count and all(text(v, maximum) for v in values)


def valid_article(a):
    if not isinstance(a, dict) or set(a) != ARTICLE_FIELDS:
        return False
    if (not isinstance(a['id'], str) or not ID.fullmatch(a['id']) or
            a['synthetic'] is not False or a['audience'] != 'employees' or
            not text(a['title'], 160) or not text(a['version'], 80) or
            not text(a['body'], 10000) or not text(a['applicability'], 2000) or
            not text(a['not_applicable'], 2000) or not text(a['verification'], 2000) or
            not texts(a['keywords'], 20, 80) or not texts(a['steps'], 20, 1500) or
            not texts(a['precautions'], 10, 1500)):
        return False
    sources = a['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
        return False
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'title', 'url', 'nature'} or not text(source['title'], 200) or not text(source['url'], 1000) or source['nature'] not in ('official-paraphrase','human-reviewed-internal'):
            return False
        try:
            url = urlsplit(source['url'])
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443):
                return False
        except ValueError:
            return False
    return True


def read_json(name):
    path = ROOT / name
    if path.stat().st_size > 1_000_000:
        raise CatalogUnavailable()
    return json.loads(path.read_text(encoding='utf-8'))


def publication_matches(approval, article):
    """Integrity gate, not identity proof: an authorized human must verify the evidence."""
    try:
        when = datetime.fromisoformat(approval['approved_at'])
        evidence_id = approval['evidence_id']
        if (approval['decision'] != 'publish' or approval['article_id'] != article['id'] or
                approval['article_sha256'] != article_hash(article) or
                not text(approval['human_reviewer'], 120) or
                when.tzinfo is None or when > datetime.now(timezone.utc) or
                not isinstance(evidence_id, str) or not ID.fullmatch(evidence_id)):
            return False
        evidence = ROOT / 'reviews' / (evidence_id + '.txt')
        # Never follow an evidence link outside this fixed public review directory.
        if evidence.resolve().parent != (ROOT / 'reviews').resolve() or not 1 <= evidence.stat().st_size <= 50000:
            return False
        raw = evidence.read_bytes()
        return bool(raw.decode('utf-8').strip()) and hashlib.sha256(raw).hexdigest() == approval['evidence_sha256']
    except (KeyError, TypeError, ValueError, OSError):
        return False


def published():
    try:
        from backend.kb_local_demo import load
        demo = load(article_hash, valid_article)
        if demo is not None:
            return [demo]
        articles, approvals = read_json('articles.json'), read_json('approvals.json')
        if not isinstance(articles, list) or len(articles) > 50 or not isinstance(approvals, list) or len(approvals) > 100:
            raise CatalogUnavailable()
        ids = [a.get('id') for a in articles if isinstance(a, dict)]
        if len(ids) != len(articles) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise CatalogUnavailable()
        result = []
        for a in articles:
            if valid_article(a):
                matches = [p for p in approvals if isinstance(p, dict) and p.get('article_id') == a['id']]
                if len(matches) == 1 and publication_matches(matches[0], a):
                    result.append({**a, 'content_hash': article_hash(a)})
        return result
    except (OSError, ValueError, TypeError, RecursionError):
        raise CatalogUnavailable() from None


def search(query):
    terms = query.casefold().split()
    if len(query) > 160 or len(terms) > 8:
        raise ValueError('invalid_query')
    articles = published()
    hits = []
    for article in articles:
        title = article['title'].casefold()
        keywords = ' '.join(article['keywords']).casefold()
        body = '\n'.join([article['body'], article['applicability'], *article['steps'], *article['precautions'],article['not_applicable'],article['verification']]).casefold()
        if all(term in title or term in keywords or term in body for term in terms):
            score = sum(4 * (term in title) + 2 * (term in keywords) + (term in body) for term in terms)
            hits.append((score, article))
    hits.sort(key=lambda pair: (-pair[0], pair[1]['id']))
    match_kind = 'literal' if hits else 'none'
    if not hits:
        # Mixed Chinese/Latin sentences are not a continuous title/body phrase.
        # Fall back only to whole technical terms explicitly in public keywords;
        # this is related reading, never an inferred answer or a synonym model.
        technical_terms = set(re.findall(r'(?<![a-z0-9-])[a-z][a-z0-9-]*', query.casefold()))
        for article in articles:
            keywords = {k.casefold() for k in article['keywords']
                        if re.fullmatch(r'[a-z][a-z0-9-]{1,31}', k.casefold())}
            common = technical_terms & keywords
            if common:
                hits.append((len(common), article))
        hits.sort(key=lambda pair: (-pair[0], pair[1]['id']))
        if hits:
            match_kind = 'related'
    return {'articles': [{k: a[k] for k in ('id', 'title', 'version', 'content_hash')} for _, a in hits[:20]],
            'published_count': len(articles), 'has_more': len(hits) > 20, 'match_kind': match_kind}
