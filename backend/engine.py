"""Local log index and timezone-aware query engine. Source access is read-only."""
from __future__ import annotations
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo
from .legacy_reader import parse_minute, parse_record, parse_zone, index_coverage
from .quota import parse_quota, deduplicate_quota, query_quota
from .logfiles import compressed_lines, log_paths, zstd_executable
from .legacy_intervals import snapshot as legacy_snapshot, build_intervals, allocated, overlaps, instant as parse_instant

UTC=timezone.utc
FIELDS=['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens']

class QueryError(ValueError):
    pass

class Engine:
    def __init__(self, root: Path, database: Path):
        self.root=root.expanduser().resolve(); self.database=database
        database.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock(); self.rows=[]; self.quota_rows=[]; self.legacy_rows=[]; self.status={}; self.generation=''
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,inode TEXT,size INTEGER,mtime INTEGER,prefix TEXT,checkpoint TEXT,offset INTEGER,state TEXT);
            CREATE TABLE IF NOT EXISTS occurrences(file TEXT,response TEXT,data TEXT,PRIMARY KEY(file,response));
            CREATE INDEX IF NOT EXISTS response_idx ON occurrences(response);
            CREATE TABLE IF NOT EXISTS quota_occurrences(file TEXT,snapshot TEXT,data TEXT,PRIMARY KEY(file,snapshot));
            CREATE TABLE IF NOT EXISTS legacy_snapshots(file TEXT,snapshot TEXT,data TEXT,PRIMARY KEY(file,snapshot));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);''')

    def connect(self):
        return sqlite3.connect(self.database,timeout=10)

    def _read_file(self,path,c,stats):
        st=path.stat(); key=str(path); identity=f'{st.st_dev}:{st.st_ino}'
        compressed=path.name.endswith('.jsonl.zst')
        previous=c.execute('SELECT inode,size,mtime,prefix,checkpoint,offset,state FROM files WHERE path=?',(key,)).fetchone()
        with ExitStack() as resources:
            stream=resources.enter_context(path.open('rb'))
            # A short file's signature must not grow during append.
            prefix=hashlib.sha256(stream.read(min(st.st_size,2048))).hexdigest()
            reset=not previous
            if previous:
                old_inode,old_size,old_mtime,old_prefix,checkpoint,offset,state_json=previous
                parser_current=json.loads(state_json).get('parser_version')==6
                if parser_current and identity==old_inode and st.st_size==old_size and st.st_mtime_ns==old_mtime:
                    stats['unchanged_files']+=1; return
                reset=compressed or not parser_current or identity!=old_inode or st.st_size<old_size or st.st_size==old_size
                if not reset and old_size>=2048 and prefix!=old_prefix: reset=True
                if not reset:
                    stream.seek(max(0,offset-256)); check=hashlib.sha256(stream.read(min(256,offset))).hexdigest()
                    if check!=checkpoint:reset=True
            if reset:
                c.execute('DELETE FROM occurrences WHERE file=?',(key,)); offset=0
                c.execute('DELETE FROM quota_occurrences WHERE file=?',(key,))
                c.execute('DELETE FROM legacy_snapshots WHERE file=?',(key,))
                state={'thread_id':None,'created_ms':None,'inherited_counter':False,'legacy_initial_inherited':None,'parser_version':6,'contexts':{},'current':None,'defaults':{},'pending':None,'line':0,'invalid':0,'response_records':0,'legacy_counts':0,'legacy_first':None,'legacy_latest':None}
                stats['reparsed_files']+=1
            else:
                state=json.loads(previous[6]); stats['appended_files']+=1
            if compressed:
                lines=resources.enter_context(compressed_lines(path)); complete=0; bytes_read=st.st_size
            else:
                stream.seek(offset); payload=stream.read(st.st_size-offset)
                complete=payload.rfind(b'\n')+1; bytes_read=len(payload)
                tail=payload[complete:]
                if tail.strip(): stats['partial_tails']+=1
                lines=payload[:complete].splitlines()
            for raw in lines:
                state['line']+=1
                interesting=any(t in raw for t in [b'"session_meta"',b'"token_usage_record"',b'"turn_context"',b'"thread_settings_applied"',b'"configuration_update"',b'"compacted"',b'"rate_limits"',b'"token_count"'])
                if not interesting and not state.get('pending'):continue
                try:event=json.loads(raw)
                except (ValueError,UnicodeDecodeError):
                    state['invalid']+=1; state['pending']=None; continue
                if not isinstance(event,dict):continue
                kind=event.get('type'); p=event.get('payload') or {}
                if kind=='session_meta' and isinstance(p,dict):
                    if isinstance(p.get('id'),str):state['thread_id']=p['id']
                    state['created_ms']=parse_instant(p.get('timestamp') or event.get('timestamp'))
                    state['inherited_counter']=bool(p.get('parent_thread_id'))
                if kind=='event_msg' and isinstance(p,dict) and p.get('type')=='token_count':
                    info=p.get('info')
                    if isinstance(info,dict) and isinstance(info.get('total_token_usage'),dict):
                        state['legacy_counts']+=1
                        try:
                            instant=datetime.fromisoformat(event['timestamp'].replace('Z','+00:00'))
                            if instant.tzinfo is not None:
                                stamp=instant.astimezone(UTC).isoformat()
                                state['legacy_first']=min(state['legacy_first'] or stamp,stamp)
                                state['legacy_latest']=max(state['legacy_latest'] or stamp,stamp)
                        except (ValueError,KeyError,TypeError,AttributeError):pass
                    if not state['response_records']:
                        point=legacy_snapshot(event,state.get('thread_id'),state.get('created_ms'),state.get('inherited_counter',False))
                        if point:
                            if state.get('legacy_initial_inherited') is None:state['legacy_initial_inherited']=point['inherited_counter']
                            point['inherited_counter']=state['legacy_initial_inherited']
                            c.execute('INSERT OR REPLACE INTO legacy_snapshots VALUES(?,?,?)',(key,point['snapshot_id'],json.dumps(point)))
                    for point in parse_quota(event):
                        c.execute('INSERT OR REPLACE INTO quota_occurrences VALUES(?,?,?)',(key,point['snapshot_id'],json.dumps(point)))
                    continue
                if state.get('pending') and kind=='compacted':
                    row=c.execute('SELECT data FROM occurrences WHERE file=? AND response=?',(key,state['pending'])).fetchone()
                    if row:
                        obj=json.loads(row[0]); obj['compaction']=True
                        c.execute('UPDATE occurrences SET data=? WHERE file=? AND response=?',(json.dumps(obj),key,state['pending']))
                state['pending']=None
                if not isinstance(p,dict):continue
                if kind=='turn_context':
                    tid=p.get('turn_id'); settings=(p.get('collaboration_mode') or {}).get('settings') or {}
                    if tid:
                        state['current']=tid
                        state['contexts'][tid]={'model':p.get('model') or settings.get('model') or 'unknown','effort':p.get('effort') or settings.get('reasoning_effort') or 'unknown'}
                elif kind=='event_msg' and p.get('type')=='thread_settings_applied':
                    s=p.get('thread_settings') or {}; entry={}
                    if s.get('model'):entry['model']=s['model']
                    if s.get('reasoning_effort'):entry['effort']=s['reasoning_effort']
                    state['defaults'].update(entry)
                    current=state.get('current')
                    if current and current in state['contexts']:state['contexts'][current].update(entry)
                elif kind=='response_item' and p.get('type')=='configuration_update':
                    effort=(p.get('reasoning') or {}).get('effort'); current=state.get('current')
                    if effort and current in state['contexts']:state['contexts'][current]['effort']=effort
                elif kind=='token_usage_record':
                    try:record=parse_record(p,event.get('timestamp'))
                    except ValueError:state['invalid']+=1; continue
                    if not isinstance(p.get('thread_id'),str):state['invalid']+=1;continue
                    state['response_records']+=1
                    ctx=state['contexts'].get(p.get('turn_id'),{})
                    obj={'response_id':record.response_id,'timestamp':record.timestamp.isoformat(), 'timestamp_ms':int(record.timestamp.timestamp()*1000),'thread_id':p['thread_id'],'turn_id':p.get('turn_id'), 'model':ctx.get('model','unknown'),'effort':ctx.get('effort','unknown'),'compaction':False,'cache_known':'cached_input_tokens' in p['usage'],**record.values}
                    existing=c.execute('SELECT data FROM occurrences WHERE file=? AND response=?',(key,record.response_id)).fetchone()
                    if existing:
                        old=json.loads(existing[0])
                        if any(old.get(k)!=obj.get(k) for k in ['timestamp_ms','thread_id','turn_id',*FIELDS]):obj['conflict']=True
                        elif old.get('conflict'):obj['conflict']=True
                        else:obj['compaction']=old.get('compaction',False)
                    c.execute('INSERT OR REPLACE INTO occurrences VALUES(?,?,?)',(key,record.response_id,json.dumps(obj)))
                    state['pending']=record.response_id
            if compressed:
                # Exit the decoder here to verify its return code before any
                # file checkpoint is committed; a damaged frame rolls back.
                resources.close()
                current=path.stat()
                if (current.st_ino,current.st_size,current.st_mtime_ns)!=(st.st_ino,st.st_size,st.st_mtime_ns):
                    raise OSError('压缩日志在读取时发生变化')
                with path.open('rb') as compressed_stream:
                    compressed_stream.seek(max(0,st.st_size-256))
                    checkpoint=hashlib.sha256(compressed_stream.read(min(256,st.st_size))).hexdigest()
                offset=st.st_size
            else:
                offset+=complete
                stream.seek(max(0,offset-256)); checkpoint=hashlib.sha256(stream.read(min(256,offset))).hexdigest()
            c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',(key,identity,st.st_size,st.st_mtime_ns,prefix,checkpoint,offset,json.dumps(state)))
            stats['bytes_read']+=bytes_read;stats['invalid_records']+=state['invalid']

    def refresh(self):
        with self.lock:
            began=time.monotonic(); stats=Counter(); paths=log_paths(self.root)
            with self.connect() as c:
                active={str(p) for p in paths}
                for (old,) in c.execute('SELECT path FROM files').fetchall():
                    if old not in active:
                        c.execute('DELETE FROM occurrences WHERE file=?',(old,));c.execute('DELETE FROM quota_occurrences WHERE file=?',(old,));c.execute('DELETE FROM legacy_snapshots WHERE file=?',(old,));c.execute('DELETE FROM files WHERE path=?',(old,));stats['removed_files']+=1
                for path in paths:
                    c.execute('SAVEPOINT read_rollout')
                    try:
                        self._read_file(path,c,stats)
                        c.execute('RELEASE read_rollout')
                    except OSError:
                        c.execute('ROLLBACK TO read_rollout');c.execute('RELEASE read_rollout')
                        stats['unreadable_files']+=1
                allrows=[json.loads(r[0]) for r in c.execute('SELECT data FROM occurrences')]
                legacy_points=[json.loads(r[0]) for r in c.execute('SELECT data FROM legacy_snapshots')]
                quota_rows,quota_conflicts=deduplicate_quota([json.loads(r[0]) for r in c.execute('SELECT data FROM quota_occurrences')])
                file_states=[json.loads(s[0]) for s in c.execute('SELECT state FROM files')]
                stats['invalid_records']=sum(s.get('invalid',0) for s in file_states)
                legacy_only=[s for s in file_states if s.get('legacy_counts') and not s.get('response_records')]
            dedup={}; conflicts=set()
            for row in allrows:
                rid=row['response_id'];old=dedup.get(rid)
                if row.get('conflict'):
                    conflicts.add(rid);dedup.pop(rid,None);continue
                if rid in conflicts:continue
                if old is None:dedup[rid]=row;continue
                signature=lambda r:(r['timestamp_ms'],r['thread_id'],r['turn_id'],*(r[k] for k in FIELDS))
                if signature(old)!=signature(row):conflicts.add(rid);dedup.pop(rid);continue
                stats['duplicates']+=1
                old['compaction']=old['compaction'] or row['compaction']
                for dimension in ['model','effort']:
                    if old[dimension]=='unknown':old[dimension]=row[dimension]
                    elif row[dimension]!='unknown' and row[dimension]!=old[dimension]:old[dimension]='unknown';stats['attribution_conflicts']+=1
            rows=sorted(dedup.values(),key=lambda r:(r['timestamp_ms'],r['response_id']))
            legacy_rows,legacy_conflicts=build_intervals(legacy_points,rows)
            titles={}
            db=self.root/'state_5.sqlite'
            if db.exists():
                try:
                    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=2) as c:
                        c.execute('PRAGMA query_only=ON')
                        for rid,title,source in c.execute('SELECT id,title,source FROM threads'):
                            titles[rid]={'title':title or rid[-8:],'kind':'子会话 / 审核' if 'subagent' in source else '会话'}
                except sqlite3.Error:stats['title_index_unavailable']+=1
            # The app's local name index holds friendly names; SQLite may retain the original prompt.
            names=self.root/'session_index.jsonl'
            if names.exists():
                try:
                    for line in names.open():
                        try:item=json.loads(line)
                        except ValueError:continue
                        title=item.get('thread_name')
                        if item.get('id') and isinstance(title,str) and title.strip():
                            previous=titles.get(item['id'],{'kind':'会话'})
                            titles[item['id']]={**previous,'title':title.strip()}
                except OSError:stats['title_index_unavailable']+=1
            for key,item in titles.items():
                title=item.get('title','')
                if len(title)>150 or title.startswith('# Files mentioned'):
                    if '## My request:' in title:title=title.split('## My request:',1)[1].strip()
                    item['title']=title[:100] if title else '会话 '+key[-8:]
            for row in [*rows,*legacy_rows]:
                meta=titles.get(row['thread_id'],{'title':'会话 '+row['thread_id'][-8:],'kind':'会话'})
                row['title']=meta['title'];row['kind']=meta['kind']
            self.rows=rows; self.quota_rows=quota_rows; self.legacy_rows=legacy_rows; self.generation='v-'+hashlib.sha256(json.dumps([rows,quota_rows,legacy_rows],sort_keys=True).encode()).hexdigest()[:16]
            self.status={'generation':self.generation,'updated_at':datetime.now(UTC).isoformat(),'record_count':len(rows),'file_count':len(paths),'first':rows[0]['timestamp'] if rows else None,'latest':rows[-1]['timestamp'] if rows else None,'conflicting_responses':len(conflicts),'unknown_model':sum(r['model']=='unknown' for r in rows),'unknown_effort':sum(r['effort']=='unknown' for r in rows),'index_coverage':index_coverage(self.root),'scan':dict(stats),'scan_seconds':round(time.monotonic()-began,3)}
            self.status.update(legacy_interval_count=len(legacy_rows),legacy_estimated_tokens=sum(r['total_tokens'] for r in legacy_rows),legacy_conflicts=legacy_conflicts)
            self.status.update(quota_record_count=len(quota_rows),quota_latest=quota_rows[-1]['timestamp'] if quota_rows else None,quota_conflicts=quota_conflicts)
            self.status.update(quota_first=quota_rows[0]['timestamp'] if quota_rows else None,
                               compressed_file_count=sum(p.name.endswith('.jsonl.zst') for p in paths),zstd_available=bool(zstd_executable()),
                               legacy_only_files=len(legacy_only),
                               legacy_first=min((s['legacy_first'] for s in legacy_only if s.get('legacy_first')),default=None),
                               legacy_latest=max((s['legacy_latest'] for s in legacy_only if s.get('legacy_latest')),default=None))
            return self.status.copy()

    def snapshot(self):
        with self.lock:return {'status':self.status.copy(),'rows':list(self.rows),'quota_rows':list(self.quota_rows),'legacy_rows':list(self.legacy_rows)}

    def query(self,body):
        with self.lock:
            if body.get('generation') and body['generation']!=self.generation:raise QueryError('日志索引已更新，请重新载入页面。')
            zone=parse_zone(body.get('timezone','Asia/Shanghai'))
            start=parse_minute(body['start'].replace('T',' '),zone);end=parse_minute(body['end'].replace('T',' '),zone)
            if end<=start:raise QueryError('结束时间必须晚于开始时间。')
            gran=body.get('granularity','6h')
            if gran not in ['1h','6h','12h','day']:raise QueryError('不支持这个分组粒度。')
            scoped=[r for r in self.rows if start.timestamp()*1000<=r['timestamp_ms']<end.timestamp()*1000 and (body.get('include_compaction',True) or not r['compaction'])]
            start_ms=int(start.timestamp()*1000);end_ms=int(end.timestamp()*1000)
            legacy=[r for r in self.legacy_rows if overlaps(r,start_ms,end_ms)]
            options={}
            for r in scoped:
                if r['total_tokens']<=0:continue
                item=options.setdefault(r['thread_id'],{'id':r['thread_id'],'title':r['title'],'kind':r['kind'],'tokens':0,'responses':0})
                item['tokens']+=r['total_tokens'];item['responses']+=1
            for r in legacy:
                amount=allocated(r,start_ms,end_ms)
                if amount<=0:continue
                item=options.setdefault(r['thread_id'],{'id':r['thread_id'],'title':r['title'],'kind':r['kind'],'tokens':0,'responses':0})
                item['tokens']+=amount;item['estimated_tokens']=item.get('estimated_tokens',0)+amount
            selection=body.get('sessions',{'mode':'all'});mode=selection.get('mode','all')
            if mode not in ['all','explicit']:raise QueryError('无效的会话选择。')
            supplied=set(selection.get('ids',[]));selected=set(options) if mode=='all' else supplied&set(options)
            scoped=[r for r in scoped if r['thread_id'] in selected]
            legacy=[r for r in legacy if r['thread_id'] in selected and allocated(r,start_ms,end_ms)>0]
            bins=make_bins(start,end,zone,gran)
            models={};totals=Counter()
            import bisect
            edges=[b['start_ms'] for b in bins]
            for r in scoped:
                model=models.get(r['model'])
                if model is None:
                    model={'name':r['model'],'tokens':0,'responses':0,'efforts':Counter(),'buckets':[{'id':b['id'],'tokens':0,'responses':0,'efforts':Counter(),'cached':0,'uncached':0,'cache_unknown':0,'output':0} for b in bins],'response_ids':[]}
                    models[r['model']]=model
                model['tokens']+=r['total_tokens'];model['responses']+=1;model['efforts'][r['effort']]+=r['total_tokens'];model['response_ids'].append(r['response_id'])
                i=bisect.bisect_right(edges,r['timestamp_ms'])-1
                if i<0 or i>=len(bins):continue
                bucket=model['buckets'][i];bucket['tokens']+=r['total_tokens'];bucket['responses']+=1;bucket['efforts'][r['effort']]+=r['total_tokens'];bucket['output']+=r['output_tokens']
                if r['cache_known']:bucket['cached']+=r['cached_input_tokens'];bucket['uncached']+=r['input_tokens']-r['cached_input_tokens']
                else:bucket['cache_unknown']+=r['input_tokens']
                for key in FIELDS:totals[key]+=r[key]
            recorded_tokens=totals['total_tokens']
            total_buckets=[{'id':b['id'],'tokens':0,'recorded':0,'estimated':0,'responses':0} for b in bins]
            for model in models.values():
                for bucket,value in zip(total_buckets,model['buckets']):
                    bucket['recorded']+=value['tokens'];bucket['responses']+=value['responses']
            for row in legacy:
                first=max(0,bisect.bisect_right(edges,row['start_ms'])-1)
                for i in range(first,len(bins)):
                    b=bins[i]
                    if b['start_ms']>row['end_ms']:break
                    total_buckets[i]['estimated']+=allocated(row,b['start_ms'],b['end_ms'])
            for bucket in total_buckets:bucket['tokens']=bucket['recorded']+bucket['estimated']
            estimated_tokens=sum(b['estimated'] for b in total_buckets)
            totals['total_tokens']+=estimated_tokens
            totals['recorded_tokens']=recorded_tokens;totals['estimated_tokens']=estimated_tokens
            warnings=[]
            if end>datetime.now(UTC):warnings.append('区间包含未来时间，只统计已写入的记录。')
            if self.status.get('index_coverage',{}).get('missing_log_files'):warnings.append('部分历史日志缺失，当前结果不代表完整账户用量。')
            for model in models.values():
                model['efforts']=dict(model['efforts'])
                for b in model['buckets']:b['efforts']=dict(b['efforts'])
            return {'generation':self.generation,'start':start.isoformat(),'end':end.isoformat(),'timezone':zone.key,'bins':bins,'models':sorted(models.values(),key=lambda m:-m['tokens']),'totals':dict(totals),'response_count':len(scoped),'selected_ids':sorted(selected),'removed_ids':sorted(supplied-set(options)) if mode=='explicit' else [],'options':sorted(options.values(),key=lambda x:-x['tokens']),'warnings':warnings,'status':self.status.copy(),
                    'total_buckets':total_buckets,'legacy_intervals':[dict(r,selected_tokens=allocated(r,start_ms,end_ms)) for r in legacy],
                    'quota':query_quota(self.quota_rows,start_ms,end_ms)}

def make_bins(start,end,zone,gran):
    local_start=start.astimezone(zone);local_end=end.astimezone(zone)
    days=(local_end.date()-local_start.date()).days
    if days>3700:raise QueryError('一次最多查询约 10 年，请缩小时间范围。')
    hours=24 if gran=='day' else int(gran[:-1]);boundaries=set()
    day=local_start.date()-timedelta(days=1)
    for d in range(days+4):
        date=day+timedelta(days=d)
        for hour in range(0,24,hours):
            naive=datetime.combine(date,datetime.min.time()).replace(hour=hour)
            for fold in [0,1]:
                point=naive.replace(tzinfo=zone,fold=fold).astimezone(UTC)
                if point.astimezone(zone).replace(tzinfo=None)==naive:boundaries.add(point)
    points=sorted(boundaries);result=[]
    for a,b in zip(points,points[1:]):
        x=max(a,start);y=min(b,end)
        if x>=y:continue
        label=a.astimezone(zone).strftime('%m/%d' if gran=='day' else '%m/%d %H:%M')
        result.append({'id':a.isoformat(),'start_ms':int(x.timestamp()*1000),'end_ms':int(y.timestamp()*1000),'start':x.astimezone(zone).isoformat(),'end':y.astimezone(zone).isoformat(),'label':label,'offset':a.astimezone(zone).strftime('%z'),'partial':x!=a or y!=b})
    if len(result)>10000:raise QueryError('时间段超过 10,000 个，请缩小范围或选择更粗粒度。')
    return result
