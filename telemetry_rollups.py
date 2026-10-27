"""Rebuildable daily materialized views. JSONL remains the source of truth."""
import json
import math
from collections import Counter

NAMES=('google','youtube','tiktok','netflix')
KEYS=('local','gateway')+tuple(n+'.'+m for n in NAMES for m in ('dns','tcp','total'))

class Rollups:
    VERSION=5
    def __init__(self,store):
        self.store=store
        self.cache={}

    def read(self,path):
        stat=path.stat();sig=[stat.st_ino,stat.st_size,stat.st_mtime_ns]
        target=self.store.root/'summaries'/(path.stem+'.json')
        doc=self.cache.get(path)
        if doc is None and target.exists():
            try:
                doc=json.loads(target.read_text())
                if not isinstance(doc,dict) or doc.get('version')!=self.VERSION or not {'signature','offset','latest','hours','previous','reports','first_received','last_received','environment'}<=doc.keys():doc=None
            except (OSError,ValueError):doc=None
        if doc and doc['signature']==sig:
            self.cache[path]=doc
            return doc
        incremental=bool(doc and path.suffix=='.jsonl' and doc['signature'][0]==sig[0] and doc['offset']<=stat.st_size and doc['signature'][1]<stat.st_size)
        if incremental:
            with path.open('rb') as handle:
                head=handle.read(min(64,doc['offset'])).hex()
                handle.seek(max(0,doc['offset']-64))
                tail=handle.read(min(64,doc['offset'])).hex()
            incremental=head==doc.get('head') and tail==doc.get('tail')
        if not incremental:doc={'version':self.VERSION,'signature':sig,'offset':0,'latest':None,'hours':{},'previous':{},'reports':0,'first_received':None,'last_received':None,'environment':{}}
        if path.suffix=='.jsonl':
            with path.open('rb') as handle:
                handle.seek(doc['offset'])
                while True:
                    raw=handle.readline()
                    if not raw:break
                    if not raw.endswith(b'\n'):break
                    if raw.strip():
                        try:self.add(doc,json.loads(raw))
                        except (ValueError,UnicodeError) as exc:raise ValueError('File telemetri rusak: '+str(path)) from exc
                    doc['offset']=handle.tell()
        else:
            for row in self.store.read_day(path)['samples']:self.add(doc,row)
            doc['offset']=stat.st_size
        if path.suffix=='.jsonl':
            with path.open('rb') as handle:
                doc['head']=handle.read(min(64,doc['offset'])).hex()
                handle.seek(max(0,doc['offset']-64))
                doc['tail']=handle.read(min(64,doc['offset'])).hex()
        doc['signature']=sig
        self.store.atomic_write(target,doc)
        self.cache[path]=doc
        # Views are much smaller than source reports; bound device/day count.
        if len(self.cache)>256:self.cache.pop(next(iter(self.cache)))
        return doc

    def add(self,doc,row):
        doc['reports']+=1
        doc['first_received']=row['received'] if doc['first_received'] is None else min(doc['first_received'],row['received'])
        doc['last_received']=row['received'] if doc['last_received'] is None else max(doc['last_received'],row['received'])
        if doc['latest'] is None or row['id']>doc['latest']['id']:doc['latest']=row
        self.current_received=row['received']
        d=row.get('details',{})
        env=d.get('environment')
        if env:
            t=row['received'];bucket=doc['environment'].setdefault(str(int(t//3600)*3600),{'samples':0,'failed':0,'sum_t':0,'sum_h':0,'min_c':None,'max_c':None,'events':[]})
            c=env['temperature_c'] if env['sensor_ok'] else None
            h=env['humidity_percent'] if env['sensor_ok'] else None
            bucket['events'].append([t,c,h])
            self.add_environment(bucket,c,h)
        if d.get('features',{}).get('network') is False:return
        received=row['received']
        self.event(doc,received,'local',row.get('local_ms'),row.get('local_ms') is not None,None)
        g=d.get('gateway_ping')
        if g:
            self.event(doc,received-g['age_ms']/1000,'gateway',g['avg_ms'],g['received']>0,[d.get('boot_id'),g['measurement_id'],g['target']])
        for name,r in d.get('services',{}).items():
            if name not in NAMES:continue
            measured=received-r['age_ms']/1000
            signature=[d.get('boot_id'),r['dns_ok'],r['tcp_ok'],r['dns_ms'],r['tcp_ms']]
            for metric,value,ok in [('dns',r['dns_ms'],r['dns_ok']),('tcp',r['tcp_ms'],r['tcp_ok']),('total',r['dns_ms']+(r['tcp_ms'] or 0),r['tcp_ok'])]:
                self.event(doc,measured,name+'.'+metric,value,ok,signature)

    def event(self,doc,t,key,value,ok,signature):
        hour=doc['hours'].setdefault(str(int(t//3600)*3600),{'stats':{},'events':[],'max_received':self.current_received,'min_received':self.current_received})
        hour['max_received']=max(hour['max_received'],self.current_received)
        hour['min_received']=min(hour['min_received'],self.current_received)
        value=round(value,1) if ok and value is not None else None
        hour['events'].append([t,key,value,signature,self.current_received])
        previous=doc['previous'].get(key)
        if signature is not None and previous and previous[0]==signature and abs(t-previous[1])<=2:return
        if signature is not None:doc['previous'][key]=[signature,t]
        stat=hour['stats'].setdefault(key,{'tests':0,'failed':0,'values':{}})
        stat['tests']+=1
        if value is None:stat['failed']+=1
        else:stat['values'][str(value)]=stat['values'].get(str(value),0)+1

    def summary(self,device_ids,start,end):
        totals={};hours={}
        def accumulate(target,key,count,failed,values):
            st=target.setdefault(key,{'tests':0,'failed':0,'values':Counter()})
            st['tests']+=count;st['failed']+=failed
            st['values'].update({float(v):n for v,n in values.items()})
        for device_id in device_ids:
            # Include preceding day: a cached measurement may predate its report.
            previous={}
            for path in self.store.paths(device_id,start-86400):
                day=self.read(path)
                for stamp,hour in sorted(day['hours'].items(),key=lambda v:int(v[0])):
                    stamp=int(stamp)
                    if stamp>end or stamp+3600<=start:continue
                    bucket=hours.setdefault(stamp,{})
                    # Preserve dedup at archive boundaries. Usually no overlap;
                    # replay boundary file's first hour where repeated samples can occur.
                    boundary=any(e[3] is not None and e[1] in previous and e[3]==previous[e[1]][0] and abs(e[0]-previous[e[1]][1])<=2 for e in hour['events'][:20])
                    if stamp>int(start//3600)*3600 and stamp+3600<=end and hour['min_received']>=start and hour['max_received']<=end and not boundary:
                        for key,st in hour['stats'].items():
                            accumulate(totals,key,st['tests'],st['failed'],st['values'])
                            accumulate(bucket,key,st['tests'],st['failed'],st['values'])
                        for e in hour['events'][-20:]:
                            if e[3] is not None:previous[e[1]]=[e[3],e[0]]
                    else:
                        for t,key,value,signature,received in hour['events']:
                            if not start<=t<=end or not start<=received<=end:continue
                            last=previous.get(key)
                            if signature is not None and last and signature==last[0] and abs(t-last[1])<=2:continue
                            if signature is not None:previous[key]=[signature,t]
                            values={} if value is None else {str(value):1}
                            accumulate(totals,key,1,int(value is None),values)
                            accumulate(bucket,key,1,int(value is None),values)
        def finish(st):
            st=st or {'tests':0,'failed':0,'values':{}}
            values=st['values'];n=sum(values.values());rank=math.ceil(n*.95);seen=0;p95=None
            for value,count in sorted(values.items()):
                seen+=count
                if seen>=rank:p95=value;break
            return dict(tests=st['tests'],failed=st['failed'],average_ms=round(sum(v*c for v,c in values.items())/n,1) if n else None,p95_ms=p95,success_percent=round(n/st['tests']*100,1) if st['tests'] else None)
        def fields(stats):return dict(local=finish(stats.get('local')),gateway=finish(stats.get('gateway')),services={n:{m:finish(stats.get(n+'.'+m)) for m in ('dns','tcp','total')} for n in NAMES})
        hourly=[dict(time=stamp*1000,**fields(st)) for stamp,st in sorted(hours.items()) if st]
        def peak(values):
            values=[(t,s) for t,s in values if s['average_ms'] is not None]
            if not values:return None
            t,s=max(values,key=lambda v:v[1]['average_ms'])
            return dict(hour_start_ms=t,average_ms=s['average_ms'],tests=s['tests'])
        result=dict(from_ms=start*1000,to_ms=end*1000,bucket_seconds=3600,hourly=hourly,**fields(totals))
        result['slowest_hours']={k:peak([(h['time'],h[k]) for h in hourly]) for k in ('local','gateway')}
        result['slowest_hours']['services']={n:peak([(h['time'],h['services'][n]['total']) for h in hourly]) for n in NAMES}
        return result

    @staticmethod
    def add_environment(bucket,c,h):
        if c is None:
            bucket['failed']+=1
            return
        bucket['samples']+=1;bucket['sum_t']+=c;bucket['sum_h']+=h
        bucket['min_c']=c if bucket['min_c'] is None else min(bucket['min_c'],c)
        bucket['max_c']=c if bucket['max_c'] is None else max(bucket['max_c'],c)

    def environment(self,device_id,start,end):
        hours={}
        for path in self.store.paths(device_id,start):
            for key,source in self.read(path)['environment'].items():
                stamp=int(key)
                if stamp>end or stamp+3600<=start:continue
                target=hours.setdefault(stamp,{'samples':0,'failed':0,'sum_t':0,'sum_h':0,'min_c':None,'max_c':None})
                if stamp>=start and stamp+3600<=end:
                    target['samples']+=source['samples'];target['failed']+=source['failed']
                    target['sum_t']+=source['sum_t'];target['sum_h']+=source['sum_h']
                    if source['samples']:
                        target['min_c']=source['min_c'] if target['min_c'] is None else min(target['min_c'],source['min_c'])
                        target['max_c']=source['max_c'] if target['max_c'] is None else max(target['max_c'],source['max_c'])
                else:
                    for t,c,h in source['events']:
                        if start<=t<=end:self.add_environment(target,c,h)
        good=[h for h in hours.values() if h['samples']]
        n=sum(h['samples'] for h in good)
        stats=dict(samples=n,failed=sum(h['failed'] for h in hours.values()),min_c=min(h['min_c'] for h in good) if good else None,max_c=max(h['max_c'] for h in good) if good else None,average_c=round(sum(h['sum_t'] for h in good)/n,1) if n else None,average_humidity=round(sum(h['sum_h'] for h in good)/n,1) if n else None)
        hourly=[dict(time=t*1000,temperature_c=round(h['sum_t']/h['samples'],1),humidity_percent=round(h['sum_h']/h['samples'],1),min_c=h['min_c'],max_c=h['max_c'],samples=h['samples']) for t,h in sorted(hours.items()) if h['samples']]
        return stats,hourly
