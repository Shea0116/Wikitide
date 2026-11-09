"""Evaluate public-note retrieval against an explicit JSON question set, without calling an LLM."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from storage import connect
from explore_search import ExploreIndex, initialize_explore, public_path


def evaluate(db,cases,index=None,k=5):
    index=index or ExploreIndex();results=[]
    for case in cases:
        question=case['question'];expected=set(case['expected_paths'])
        if not isinstance(question,str) or not question.strip() or len(question)>1000 or not expected or not all(isinstance(path,str) and public_path(path) for path in expected):
            raise ValueError('问题集仅允许有效问题和公开笔记路径')
        start=time.perf_counter();matches,mode=index.search(db,question,limit=k)
        paths=[item['path'] for item in matches]
        rank=next((position+1 for position,path in enumerate(paths) if path in expected),None)
        results.append(dict(question=question,hit=bool(rank),recall=len(set(paths)&expected)/len(expected),reciprocal_rank=1/rank if rank else 0,latency_ms=round((time.perf_counter()-start)*1000,2),mode=mode))
    return dict(cases=len(results),k=k,hit_rate=sum(item['hit'] for item in results)/len(results),mean_recall=sum(item['recall'] for item in results)/len(results),mrr=sum(item['reciprocal_rank'] for item in results)/len(results),results=results)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--db',required=True,type=Path);parser.add_argument('--cases',required=True,type=Path);parser.add_argument('--k',type=int,default=5)
    args=parser.parse_args();cases=json.loads(args.cases.read_text())
    if not isinstance(cases,list) or not 1<=len(cases)<=200 or not 1<=args.k<=50: parser.error('问题集 1–200 条，k 为 1–50')
    with connect(args.db) as db:
        initialize_explore(db)
        print(json.dumps(evaluate(db,cases,k=args.k),ensure_ascii=False,indent=2))
