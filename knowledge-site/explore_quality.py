"""Answer feedback and aggregate quality/latency metrics without sharing conversations."""
import json
import os
import statistics
import time
from fastapi import HTTPException, Request
from storage import connect


def initialize_quality(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS explore_feedback(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      message_id INTEGER NOT NULL REFERENCES explore_messages(id) ON DELETE CASCADE,
      rating TEXT NOT NULL,comment TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL,
      PRIMARY KEY(user_id,message_id));
    CREATE TABLE IF NOT EXISTS explore_metrics(
      id INTEGER PRIMARY KEY,created_at INTEGER NOT NULL,status TEXT NOT NULL,model TEXT NOT NULL,
      duration_ms INTEGER NOT NULL,retrieval_ms INTEGER NOT NULL,generation_ms INTEGER NOT NULL,
      first_token_ms INTEGER,source_count INTEGER NOT NULL,used_count INTEGER NOT NULL,
      prompt_tokens INTEGER,completion_tokens INTEGER,cost REAL);
    CREATE INDEX IF NOT EXISTS ix_explore_metrics_created ON explore_metrics(created_at);
    ''')


def record_metric(db, *, status, model, started, retrieval_ms=0,generation_ms=0,first_token_ms=None,source_count=0,used_count=0,usage=None):
    usage=usage or {}
    prompt=usage.get('prompt_tokens'); completion=usage.get('completion_tokens')
    prompt=prompt if type(prompt) is int and prompt>=0 else None
    completion=completion if type(completion) is int and completion>=0 else None
    cost=None
    try:
        input_price=float(os.environ['KB_AI_INPUT_PRICE_PER_MILLION']); output_price=float(os.environ['KB_AI_OUTPUT_PRICE_PER_MILLION'])
        if prompt is not None and completion is not None and input_price>=0 and output_price>=0:
            cost=(prompt*input_price+completion*output_price)/1000000
    except (KeyError,ValueError): pass
    db.execute('''INSERT INTO explore_metrics(created_at,status,model,duration_ms,retrieval_ms,generation_ms,first_token_ms,source_count,used_count,prompt_tokens,completion_tokens,cost)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(int(time.time()),status,str(model)[:100],round((time.perf_counter()-started)*1000),retrieval_ms,generation_ms,first_token_ms,source_count,used_count,prompt,completion,cost))
    db.execute('DELETE FROM explore_metrics WHERE created_at<?',(int(time.time())-90*86400,))


def register_quality(app,db_path,api):
    with connect(db_path) as db: initialize_quality(db)

    @app.post('/api/explore/messages/{message_id}/feedback')
    async def feedback(request:Request,message_id:int):
        raw=await request.body()
        if len(raw)>8192: raise HTTPException(413)
        try: data=json.loads(raw)
        except ValueError: raise HTTPException(400)
        if not isinstance(data,dict) or data.get('rating') not in {'helpful','unsupported','outdated','incorrect'} or not isinstance(data.get('comment',''),str) or len(data.get('comment',''))>1000: raise HTTPException(400,'反馈格式无效')
        with api['database'](db_path) as db:
            user,csrf=api['current_user'](db,request)
            if not user: raise HTTPException(401)
            api['require_csrf']({'csrf':request.headers.get('X-CSRF-Token','')},csrf)
            message=db.execute("SELECT m.sources FROM explore_messages m JOIN explore_sessions s ON s.id=m.session_id WHERE m.id=? AND m.role='assistant' AND s.user_id=?",(message_id,user['id'])).fetchone()
            if not message: raise HTTPException(404)
            from explore import sources_valid
            if not sources_valid(db,json.loads(message['sources']),user_id=user['id']): raise HTTPException(409,'引用资料已变化，请重新提问后评价')
            db.execute('INSERT OR REPLACE INTO explore_feedback VALUES(?,?,?,?,?)',(user['id'],message_id,data['rating'],data.get('comment',''),int(time.time())))
        return {'ok':True}

    @app.get('/admin/explore-quality')
    def quality(request:Request):
        with api['database'](db_path) as db:
            user,csrf=api['current_user'](db,request);api['require_admin'](user)
            metrics=db.execute('SELECT * FROM explore_metrics WHERE created_at>=? ORDER BY id DESC LIMIT 10000',(int(time.time())-30*86400,)).fetchall()
            durations=sorted(row['duration_ms'] for row in metrics)
            feedback=[dict(row) for row in db.execute('SELECT rating,count(*) AS total FROM explore_feedback WHERE updated_at>=? GROUP BY rating',(int(time.time())-30*86400,))]
            summary=dict(count=len(metrics),success=sum(row['status']=='success' for row in metrics),p50=round(statistics.median(durations)) if durations else None,p95=durations[min(len(durations)-1,int(len(durations)*.95))] if durations else None,
                         prompt=sum(row['prompt_tokens'] or 0 for row in metrics),completion=sum(row['completion_tokens'] or 0 for row in metrics),metered=sum(row['prompt_tokens'] is not None for row in metrics),cost=sum(row['cost'] or 0 for row in metrics),priced=sum(row['cost'] is not None for row in metrics))
            return api['page'](request,'explore_quality.html',user=user,csrf=csrf,categories=api['categories'](db,user),summary=summary,feedback=feedback)
