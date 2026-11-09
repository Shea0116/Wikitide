"""Numeric-only daily provider budget; reservations also survive uncertain calls."""
import os
import secrets
import time
from fastapi import HTTPException
from agent_context import estimate_tokens


def daily_limit():
    try: return max(1000,min(100000000,int(os.getenv('KB_AGENT_DAILY_TOKENS','256000'))))
    except ValueError: return 256000


def daily_used(db,uid,now):
    direct=db.execute('SELECT coalesce(sum(coalesce(charged_tokens,reserved_tokens)),0) FROM agent_usage_reservations WHERE user_id=? AND created_at>?',(uid,now-86400)).fetchone()[0]
    runs=db.execute('SELECT coalesce(sum(b.max_tokens),0) FROM agent_runs r JOIN agent_run_budget b ON b.run_id=r.id WHERE r.user_id=? AND r.created_at>?',(uid,now-86400)).fetchone()[0]
    return direct+runs


def reserve(database,path,uid,messages,output_limit,kind):
    amount=estimate_tokens(messages)+output_limit;now=int(time.time());rid=secrets.token_urlsafe(24)
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if daily_used(db,uid,now)+amount>daily_limit():
            raise HTTPException(429,'账户每日生成 token 预留额度已用完，请明天重试。')
        db.execute('INSERT INTO agent_usage_reservations(id,user_id,kind,reserved_tokens,created_at) VALUES(?,?,?,?,?)',(rid,uid,kind,amount,now))
        db.execute('DELETE FROM agent_usage_reservations WHERE created_at<?',(now-90*86400,))
    return rid


def settle(database,path,rid,usage):
    if not rid:return
    count=usage.get('total_tokens')
    if type(count) is not int or count<0:
        a,b=usage.get('prompt_tokens'),usage.get('completion_tokens')
        count=a+b if type(a) is int and a>=0 and type(b) is int and b>=0 else None
    with database(path) as db:
        db.execute('UPDATE agent_usage_reservations SET charged_tokens=?,status=? WHERE id=?',
                   (count,'reported' if count is not None else 'estimated',rid))
