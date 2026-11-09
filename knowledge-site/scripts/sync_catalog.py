"""Maintain only public catalog entries after successful enhanced sync."""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from prepare_content import resolve_public
from sync_local import atomic_write, digest


def update_public_catalog(root, synchronized):
    script=root/'SCHEMA/wiki.py'; catalog_path=root/'categories.json'
    if not script.is_file(): return None
    original=catalog_path.read_text(encoding='utf-8') if catalog_path.is_file() else ''
    data=json.loads(original) if original else {
        'name':'Private','slug':'private','path':'/Private','documents':[],
        'children':[{'name':name,'slug':name.lower(),'path':name,'documents':[],'children':[]} for name in ('Backend','Frontend')],
        'stats':{'total_documents':0,'total_size':0,'generated_at':''},
    }
    if data.get('path')!='/Private' or {row.get('path') for row in data.get('children',[])}!={'Backend','Frontend'}:
        raise ValueError('Public catalog shape invalid; refusing to rewrite')
    nodes={}; documents={}; identities={}

    def identity(name):
        info=(root/name).stat()
        return (name.casefold(),info.st_dev,info.st_ino)

    def walk(node,ancestors,labels):
        current=ancestors+[node['path']]
        trail=labels+([] if node['path']=='/Private' else [node['name']])
        nodes[' / '.join(trail)]=node
        retained=[]
        for doc in node['documents']:
            try:
                target=resolve_public(root,doc['path'],current)
            except FileNotFoundError:
                # Prune obsolete index entries, never source files or website history.
                continue
            name=target.relative_to(root).as_posix()
            key=identity(name)
            if key in identities: continue
            identities[key]=name
            retained.append(doc)
            documents[name]=(node,doc)
        node['documents']=retained
        for child in node.get('children',[]): walk(child,current,trail)
    walk(data,[],[])
    for name,record in synchronized.items():
        file=root/name
        if name.split('/',1)[0] not in {'Backend','Frontend'} or not file.is_file(): continue
        key=identity(name)
        if key in identities and identities[key]!=name: continue
        # Do not publish notes that the owner has made private on the website.
        if record.get('visibility')!='public': continue
        category=record['category'].split(' / ')
        if category[0]!=name.split('/',1)[0]:
            # Keep unusual website categories out of the public navigation.
            continue
        parent=nodes[category[0]]
        for index,label in enumerate(category[1:],start=1):
            key=' / '.join(category[:index+1])
            if key not in nodes:
                node={'name':label,'slug':'sync-'+digest(key)[:12],'path':file.parent.relative_to(root).as_posix(),'documents':[],'children':[]}
                parent.setdefault('children',[]).append(node); nodes[key]=node
            parent=nodes[key]
        if name in documents:
            old,doc=documents[name]
            if old is not parent:
                old['documents'].remove(doc); parent['documents'].append(doc); doc['path']=name
        else:
            doc={'slug':'note-'+digest(name)[:12],'title':file.stem,'path':name,'summary':'','size':0}
            parent['documents'].append(doc); documents[name]=(parent,doc)
            identities[key]=name
        doc['title']=file.stem
        doc['summary']=record['summary'].strip() or f'围绕{file.stem}的学习笔记。'

    # Refresh the documented non-whitespace character counts without editing note bodies.
    total=0
    for name,(_node,doc) in documents.items():
        doc['size']=sum(not char.isspace() for char in (root/name).read_text(encoding='utf-8'))
        total+=doc['size']
    data['stats']['total_documents']=len(documents); data['stats']['total_size']=total
    candidate=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    spec=importlib.util.spec_from_file_location('wikitide_public_catalog',script)
    wiki=importlib.util.module_from_spec(spec); spec.loader.exec_module(wiki)
    wiki.ROOT=root; wiki.CATEGORIES=catalog_path; wiki.CATALOG=root/'WIKI/分类目录.md'
    errors,_,records,indexed=wiki.inspect(data)
    if errors or wiki.check_public_links(exclude_catalog=True): raise ValueError('Public catalog checks failed; existing catalog retained')
    expected=wiki.render(records,len(indexed))
    old_wiki=wiki.CATALOG.read_text(encoding='utf-8') if wiki.CATALOG.is_file() else ''
    if old_wiki and not old_wiki.startswith(wiki.MARKER): raise ValueError('Refusing to overwrite a manually maintained Wiki catalog')
    if candidate!=original:
        data['stats']['generated_at']=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        candidate=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
        # Retain a recoverable metadata copy in the private sync workspace.
        if original:
            backup=root/'knowledge-site/work/catalog-before-sync.json'
            atomic_write(backup,original)
            backup.chmod(0o600)
        atomic_write(catalog_path,candidate,expected_sha=digest(original) if original else '')
    if expected!=old_wiki:
        atomic_write(wiki.CATALOG,expected,expected_sha=digest(old_wiki) if old_wiki else '')
        result=subprocess.run([sys.executable,str(script),'check'],cwd=root,capture_output=True,text=True)
        if result.returncode: raise ValueError('Public Wiki check failed after rebuild')
    # Return the catalog actually written, so generated metadata is not mistaken for a local edit.
    from sync_v2 import catalog_records
    return catalog_records(root)
