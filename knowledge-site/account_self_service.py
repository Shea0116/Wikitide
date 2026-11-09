"""Self-service password, one-use recovery codes and per-session sign-out."""
import secrets
import time
from fastapi import HTTPException,Request
from fastapi.responses import RedirectResponse
from accounts import audit,bounded_body
from storage import connect,create_session,digest,password_hash,password_matches


def register_self_service(app,db_path,api):
    with connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS recovery_codes(
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL,PRIMARY KEY(user_id,code_hash))''')

    def authenticated(db,request,form=None):
        user,csrf=api['current_user'](db,request)
        if not user: raise HTTPException(401,'请先登录')
        if form is not None: api['require_csrf'](form,csrf)
        return user,csrf

    def settings_page(request,db,user,csrf,*,message='',codes=None):
        sessions=db.execute('''SELECT s.token_hash,s.expires_at,d.label,d.created_at,d.last_seen
            FROM sessions s LEFT JOIN session_devices d ON d.token_hash=s.token_hash
            WHERE s.user_id=? AND s.expires_at>? ORDER BY coalesce(d.last_seen,0) DESC''',(user['id'],int(time.time()))).fetchall()
        count=db.execute('SELECT count(*) FROM recovery_codes WHERE user_id=?',(user['id'],)).fetchone()[0]
        return api['page'](request,'account_settings.html',user=user,csrf=csrf,categories=api['categories'](db,user),sessions=sessions,
                           current=user['token_hash'],recovery_count=count,message=message,codes=codes)

    @app.get('/account')
    def settings(request:Request):
        with api['database'](db_path) as db:
            user,csrf=api['current_user'](db,request)
            if not user: return RedirectResponse('/login?next=/account',303)
            return settings_page(request,db,user,csrf)

    @app.post('/account/password')
    async def password(request:Request):
        form=api['form_values'](await bounded_body(request))
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,csrf=authenticated(db,request,form)
            if not password_matches(form.get('current_password',''),user['password_hash']): raise HTTPException(400,'当前密码不正确')
            if form.get('password')!=form.get('confirm'): raise HTTPException(400,'两次新密码不一致')
            try: hashed=password_hash(form.get('password',''))
            except ValueError as exc: raise HTTPException(400,str(exc)) from exc
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?',(hashed,user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            db.execute('DELETE FROM recovery_codes WHERE user_id=?',(user['id'],))
            audit(db,user['id'],'account.password_changed',user['username'],'自行修改密码并撤销会话与旧恢复码')
        response=RedirectResponse('/login',303);response.delete_cookie('kb_session');return response

    @app.post('/account/recovery-codes')
    async def codes(request:Request):
        form=api['form_values'](await bounded_body(request))
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,csrf=authenticated(db,request,form)
            if not password_matches(form.get('current_password',''),user['password_hash']): raise HTTPException(400,'当前密码不正确')
            values=[secrets.token_urlsafe(24) for _ in range(8)]
            db.execute('DELETE FROM recovery_codes WHERE user_id=?',(user['id'],))
            db.executemany('INSERT INTO recovery_codes VALUES(?,?,?)',[(user['id'],digest(value),int(time.time())) for value in values])
            audit(db,user['id'],'account.recovery_codes_rotated',user['username'],'生成 8 个一次性恢复码，旧码已失效')
            return settings_page(request,db,user,csrf,message='恢复码仅展示这一次，请保存在安全位置。旧恢复码已失效。',codes=values)

    @app.post('/account/sessions/revoke')
    async def revoke(request:Request):
        form=api['form_values'](await bounded_body(request))
        with api['database'](db_path) as db:
            user,_=authenticated(db,request,form)
            db.execute('DELETE FROM sessions WHERE token_hash=? AND user_id=?',(form.get('session',''),user['id']))
        response=RedirectResponse('/account',303)
        if form.get('session')==user['token_hash']: response.delete_cookie('kb_session')
        return response

    def recovery_page(request,token,*,error='',success=False,status=200):
        return api['page'](request,'account_recovery.html',status_code=status,user=None,csrf=token,categories=[],error=error,success=success)

    @app.get('/recover')
    def recover_page(request:Request):
        token=secrets.token_urlsafe(24);response=recovery_page(request,token)
        response.set_cookie('kb_recovery_csrf',token,max_age=600,httponly=True,secure=app.state.cookie_secure,samesite='lax');return response

    @app.post('/recover')
    async def recover(request:Request):
        form=api['form_values'](await bounded_body(request));csrf=request.cookies.get('kb_recovery_csrf')
        api['require_csrf'](form,csrf)
        key=digest('recover:'+(request.client.host if request.client else '')+':'+form.get('username','')[:64].lower())
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');now=int(time.time())
            if db.execute('SELECT count(*) FROM login_attempts WHERE key=? AND happened_at>?',(key,now-900)).fetchone()[0]>=5:
                return recovery_page(request,csrf,error='尝试次数过多，请 15 分钟后重试。',status=429)
            db.execute('INSERT INTO login_attempts VALUES(?,?)',(key,now))
            user=db.execute('SELECT * FROM users WHERE username=? AND active=1',(form.get('username','').strip(),)).fetchone()
            code=form.get('code','')
            valid=db.execute('SELECT 1 FROM recovery_codes WHERE user_id=? AND code_hash=?',(user['id'],digest(code))).fetchone() if user and len(code)<=100 else None
            if not valid: return recovery_page(request,csrf,error='用户名或恢复码无效。',status=400)
            if form.get('password')!=form.get('confirm'): return recovery_page(request,csrf,error='两次新密码不一致。',status=400)
            try: hashed=password_hash(form.get('password',''))
            except ValueError as exc: return recovery_page(request,csrf,error=str(exc),status=400)
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?',(hashed,user['id']))
            db.execute('DELETE FROM recovery_codes WHERE user_id=?',(user['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            audit(db,user['id'],'account.recovered',user['username'],'使用恢复码重置密码，撤销全部恢复码与会话')
        response=recovery_page(request,csrf,success=True);response.delete_cookie('kb_session');response.delete_cookie('kb_recovery_csrf');return response
