"""Reproducible chunking + official v4 embeddings + exact pgvector cosine retrieval.

Small corpus: exact search needs no approximate index infrastructure or HNSW tuning.
"""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy import func
from backend.db import SessionLocal
from backend.ai_models import KnowledgeChunk,EmbeddingCache
from backend.ai_config import AIError,EMBED_MODEL,DIMENSION
from backend.models import utcnow

ROOT=Path(__file__).resolve().parents[1]


def corpus():
    metadata={s['id']:s for s in json.loads((ROOT/'knowledge/sources.json').read_text())}
    files=sorted((ROOT/'knowledge/documents').glob('*.md'))
    raw=[(p.name,p.read_text(encoding='utf-8')) for p in files]
    version=hashlib.sha256(json.dumps({'sources':metadata,'files':raw},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    chunks=[]
    for filename,text in raw:
        import re
        match=re.match(r'<!-- source_id: ([a-z0-9-]+) -->',text)
        if not match or match[1] not in metadata:raise AIError('knowledge_source_metadata_invalid')
        source=metadata[match[1]];parts=re.split(r'\n(?=##? )',text[match.end():].strip())
        for number,part in enumerate(parts):
            part=part.strip()
            if not part:continue
            if len(part)>1800:raise AIError('knowledge_chunk_too_large')
            content_hash=hashlib.sha256(part.encode()).hexdigest()
            chunks.append({'id':source['id']+'-'+str(number)+'-'+content_hash[:12],
                'document_id':source['id'],'title':source['title'],'source_url':source['url'],
                'synthetic':source['synthetic'],'source_version':source['version'],'content_hash':content_hash,
                'corpus_version':version,'text':part})
    return version,chunks


def cache_id(text,mode):
    return hashlib.sha256((EMBED_MODEL+str(DIMENSION)+mode+text).encode()).hexdigest()


def embedding(text,gateway,operation,key,purpose):
    digest=cache_id(text,gateway.mode)
    from backend.service import locked
    with locked(-2,'embedding-'+digest) as db:
        cached=db.get(EmbeddingCache,digest)
        if cached:return list(cached.vector),cached.call_id,True
        vectors,call_id=gateway.embed([text],key,operation,purpose)
        db.add(EmbeddingCache(id=digest,model=EMBED_MODEL,dimension=DIMENSION,mode=gateway.mode,
            vector=vectors[0],call_id=call_id));db.commit()
        return vectors[0],call_id,False


def index(gateway):
    version,chunks=corpus();added=reused=0
    from backend.billable_bounds import embedding_maximum_nano
    gateway.ledger.check_batch(len(chunks)*embedding_maximum_nano())
    for chunk in chunks:
        with SessionLocal() as db:
            prior=db.get(KnowledgeChunk,chunk['id'])
            if prior and prior.corpus_version==version and prior.embedding_mode==gateway.mode:
                reused+=1;continue
        vector,call_id,cached=embedding(chunk['text'],gateway,'kb-'+version[:32],
            'kb-'+chunk['content_hash']+'-'+gateway.mode,'knowledge_embedding')
        with SessionLocal() as db:
            row=db.get(KnowledgeChunk,chunk['id'])
            if row is None:row=KnowledgeChunk(id=chunk['id']);db.add(row)
            for field,value in chunk.items():setattr(row,field,value)
            row.embedding_model=EMBED_MODEL;row.embedding_dim=DIMENSION;row.embedding_mode=gateway.mode;row.embedding=vector
            row.indexed_at=utcnow()
            db.commit();added+=1
    return {'corpus_version':version,'chunks':len(chunks),'indexed':added,'reused':reused,'mode':gateway.mode,'completed_at_utc':utcnow().isoformat()}


def retrieve(query,gateway,operation,key,top_k=3,min_similarity=0.40):
    version,expected_chunks=corpus()
    with SessionLocal() as db:
        count=db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.corpus_version==version,
            KnowledgeChunk.embedding_mode==gateway.mode))
    if count!=len(expected_chunks):raise AIError('knowledge_index_not_ready')
    vector,call_id,cached=embedding(query,gateway,operation,key,'query_embedding')
    with SessionLocal() as db:
        distance=KnowledgeChunk.embedding.cosine_distance(vector)
        rows=db.execute(select(KnowledgeChunk,distance.label('distance')).where(
            KnowledgeChunk.corpus_version==version,KnowledgeChunk.embedding_mode==gateway.mode,
            KnowledgeChunk.embedding_model==EMBED_MODEL,KnowledgeChunk.embedding_dim==DIMENSION).order_by(distance).limit(min(top_k,4))).all()
        results=[]
        for row,dist in rows:
            similarity=1-float(dist)
            if similarity<min_similarity:continue
            results.append({'id':row.id,'document_id':row.document_id,'title':row.title,'url':row.source_url,
                'synthetic':row.synthetic,'version':row.source_version,'text':row.text,'similarity':round(similarity,4)})
    return {'chunks':results,'call_id':call_id,'cached_embedding':cached,'corpus_version':version}
