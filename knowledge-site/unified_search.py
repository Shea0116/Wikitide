"""One search UI, with explicit corpus boundaries and chapter links."""
import time
from urllib.parse import urlencode
from fastapi import HTTPException
from features import highlight, like, search_notes
from storage import visible_where


def search_page(request, db, app, api, *, q, category, page_number, scope, mode, days):
    if not 1 <= page_number <= 10000 or len(category)>240 or len(q)>100 or mode not in {'keyword','semantic'} or days not in {0,7,30,90,365}:
        raise HTTPException(400,'搜索条件无效')
    user,csrf=api['current_user'](db,request)
    teams=db.execute('SELECT t.id,t.name FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=? ORDER BY t.name',(user['id'],)).fetchall() if user else []
    allowed={'all','public',*(('private',) if user else ()), *(f"team:{team['id']}" for team in teams)}
    if scope not in allowed: raise HTTPException(404,'搜索范围不可用')
    term=q.strip(); since=int(time.time())-days*86400 if days else 0
    retrieval='关键词检索'; rows=[]; total=0
    if mode=='semantic':
        scope='public'
        if term:
            matches,retrieval_mode=app.state.explore_index.search(db,term,limit=100000,category=category,since=since)
            retrieval='关键词与语义混合检索' if retrieval_mode=='hybrid' else '关键词检索（语义模型未就绪）'
            total=len(matches)
            rows=[dict(item,hit_title=highlight(item['title'],term),snippet=highlight(item['text'][:240],term)) for item in matches[(page_number-1)*20:page_number*20]]
    elif scope.startswith('team:'):
        team_id=int(scope[5:])
        if term:
            pattern='%'+like(term)+'%'
            where="d.team_id=? AND d.updated_at>=? AND (d.title LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\')"
            args=(team_id,since,pattern,pattern)
            total=db.execute(f'SELECT count(*) FROM team_documents d WHERE {where}',args).fetchone()[0]
            for item in db.execute(f'SELECT d.* FROM team_documents d WHERE {where} ORDER BY d.updated_at DESC,d.id DESC LIMIT 20 OFFSET ?',(*args,(page_number-1)*20)):
                start=max(0,item['body'].casefold().find(term.casefold())-60)
                rows.append(dict(item,category=next(team['name'] for team in teams if team['id']==team_id),hit_title=highlight(item['title'],term),snippet=highlight(item['body'][start:start+240],term),url=f"/teams?space={team_id}&document={item['id']}"))
    else:
        rows,total=search_notes(db,user,term,category,page_number,scope,since)
    if mode == 'keyword' and rows:
        column = 'document_id' if scope.startswith('team:') else 'note_id'
        ids = [row['id'] for row in rows]
        placeholders = ','.join('?' for _ in ids)
        pdf_ids = {row[0] for row in db.execute(f"SELECT {column} FROM document_pdfs WHERE note_format='pdf' AND {column} IN ({placeholders})", ids)}
        rows = [dict(row, is_pdf=row['id'] in pdf_ids) for row in rows]
    access,params=visible_where(user)
    options=[row[0] for row in db.execute(f'SELECT DISTINCT category FROM notes WHERE {access} ORDER BY category',params)]
    query=dict(q=term,category=category,scope=scope,mode=mode,days=days)
    return api['page'](request,'search.html',user=user,csrf=csrf,categories=api['categories'](db,user),notes=rows,q=term,total=total,
                      category=category,category_options=options,page_number=page_number,has_more=total>page_number*20,scope=scope,mode=mode,days=days,
                      teams=teams,retrieval=retrieval,previous='?'+urlencode(dict(query,page_number=page_number-1)),following='?'+urlencode(dict(query,page_number=page_number+1)))
