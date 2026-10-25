"""Real core measurements and Linux storage faults; fixtures own only temp paths."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import tempfile
import time

from cacheweft import Cache,Spec,CacheError
from cacheweft.http import HTTPS
from tests.fixture import Origin,DATA


def require(value,message):
    if not value:raise RuntimeError(message)


def rss():
    if platform.system()=='Linux':
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return int(line.split()[1])*1024
    # macOS has no /proc; ru_maxrss is peak, separately labelled.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def measure(root):
    origin=Origin(root/'tls');digest=hashlib.sha256(DATA).hexdigest()
    spec=Spec('fixture',origin.url,len(DATA),digest)
    trials=[]
    try:
        for mode in ('cold','resume','coalesced'):
            origin.mode='drop' if mode=='resume' else ('hold' if mode=='coalesced' else 'normal')
            origin.calls.clear();origin.sent=0;origin.arrived.clear();origin.release.clear()
            c=Cache(root/mode,[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert))
            try:
                start=time.monotonic();c.submit('r','fixture')
                waiting=[]
                if mode=='coalesced':
                    require(origin.arrived.wait(3),'coalesced origin barrier not reached')
                    for i in range(1,4):c.submit('r'+str(i),'fixture')
                    waiting=[c.status(r)['request'] for r in ('r','r1','r2','r3')]
                    require(all(x['state']=='WAITING' for x in waiting) and len({x['generation'] for x in waiting})==1,'Not four concurrent subscribers')
                    origin.release.set()
                require(c.wait('r')['request']['state']=='SUCCEEDED','download failed')
                outcomes=[c.status('r')['request']]
                if mode=='coalesced':
                    for i in range(1,4):
                        outcome=c.wait('r'+str(i))['request'];require(outcome['state']=='SUCCEEDED','subscriber failed');outcomes.append(outcome)
                download=time.monotonic()-start
                read_start=time.monotonic()
                with c.read(digest) as f:content=f.read()
                read_seconds=time.monotonic()-read_start
                require(content==DATA,'wrong content')
                trials.append(dict(mode=mode,request_count=4 if mode=='coalesced' else 1,
                                   size=len(DATA),download_seconds=download,read_seconds=read_seconds,
                                   actual_origin_requests=len(origin.calls),origin_payload_bytes=origin.sent,
                                   request_ranges=[x['range'] for x in origin.calls],correct=True,
                                   subscribers_before_release=waiting,subscriber_outcomes=outcomes,
                                   process_rss_or_peak_bytes=rss(),usage=c.usage(),
                                   root_allocated_bytes=sum(p.stat().st_blocks*512 for p in c.root.rglob('*') if p.is_file())))
            finally:origin.release.set();c.close()
    finally:origin.close()
    return dict(environment=platform.system(),memory_scope='whole observer process including TLS fixture and core',
                memory_kind='current_RSS' if platform.system()=='Linux' else 'peak_RSS',
                performance_scope='local TLS fixture; single observation per mode, no WAN/speedup claim',trials=trials)


def storage(root):
    require(platform.system()=='Linux' and os.geteuid()==0,'Isolated Linux root required')
    mount=root/'volume';mount.mkdir();observations=[]
    origin=Origin(root/'fault-tls');spec=Spec('fixture',origin.url,len(DATA),hashlib.sha256(DATA).hexdigest())
    def command(*args):subprocess.run(args,check=True,capture_output=True,timeout=20)
    command('mount','-t','tmpfs','-o','size=8m','cacheweft-test',str(mount))
    c=None;readonly_bind=False
    try:
        for mode in ('enospc','readonly'):
            events=[];origin.mode='hold';origin.arrived.clear();origin.release.clear()
            c=Cache(mount/mode,[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),hook=lambda p,i:events.append(dict(point=p,**i)))
            c.submit('r','fixture');require(origin.arrived.wait(3),'request not held')
            if mode=='enospc':
                with open(mount/'filler','wb',buffering=0) as f:
                    while os.statvfs(mount).f_bavail*os.statvfs(mount).f_frsize > 32768:f.write(b'x'*4096)
            else:
                command('mount','--bind',str(mount),str(mount))
                command('mount','-o','remount,bind,ro',str(mount));readonly_bind=True
            origin.release.set();c.futures[spec.sha256].result(10)
            require(c.degraded,'storage failure did not retire authority')
            require(not c.status('r')['available'],'storage failure published')
            rejected=False
            try:c.submit('new','fixture')
            except CacheError:rejected=True
            require(rejected,'admission accepted in degraded state')
            failures=[e for e in events if e['point']=='worker_exit']
            require(failures,'no actual failure recorded')
            c.close();c=None
            if mode=='readonly':
                command('umount',str(mount));readonly_bind=False
            else:(mount/'filler').unlink()
            origin.mode='normal'
            c=Cache(mount/mode,[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert))
            c.resume();require(c.wait('r')['request']['state']=='SUCCEEDED','repair did not recover')
            with c.read(spec.sha256) as f:require(f.read()==DATA,'repair content wrong')
            observations.append(dict(mode=mode,actual_errors=failures,rejected=True,published_before_repair=False,repair_correct=True,
                                     injection='read-only bind mount' if mode=='readonly' else 'filled 8MiB tmpfs leaving 32KiB free'))
            c.close();c=None
        # Real SQLITE_FULL via page cap, not a mocked exception.
        c=Cache(mount/'sqlfull',[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),db_pages=8,records=1000)
        c.submit('r','fixture');c.wait('r');full=None;count=0
        for i in range(500):
            try:c.submit('request_'+str(i).zfill(50),'fixture');count+=1
            except sqlite3.Error as exc:full=getattr(exc,'sqlite_errorcode',None);break
        require(full==sqlite3.SQLITE_FULL and c.degraded,'actual SQLite FULL not observed')
        rejected=False
        try:c.submit('blocked_after_full','fixture')
        except CacheError:rejected=True
        require(rejected,'SQLite FULL admitted new request')
        observations.append(dict(mode='sqlite_full_admission',sqlite_errorcode=full,successful_extra_requests=count,degraded=True,rejected=True,
                                 scope='request catalog admission after an already READY artifact'))
        c.close();c=None
        c=Cache(mount/'sqlfull',[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),db_pages=64,records=1000)
        require(c.submit('after_repair','fixture')['available'],'SQLite capacity repair failed')
        observations[-1]['repair_correct']=True
        c.close();c=None
        # Force actual SQLite FULL at the production publish-intent transaction.
        # A fault-only trigger allocates beyond the fixed page limit; no mocked exception.
        events=[];origin.mode='hold';origin.arrived.clear();origin.release.clear();origin.calls.clear()
        c=Cache(mount/'sqlfull-publish',[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),db_pages=16,
                hook=lambda p,i:events.append(dict(point=p,**i)))
        c.db.executescript("CREATE TABLE pressure(data BLOB); CREATE TRIGGER publish_pressure BEFORE UPDATE OF state ON objects WHEN NEW.state='PUBLISHING' BEGIN INSERT INTO pressure VALUES(zeroblob(131072)); END;")
        c.submit('pending','fixture');require(origin.arrived.wait(3),'publish FULL barrier not reached');origin.release.set()
        c.futures[spec.sha256].result(10)
        errors=[x for x in events if x['point']=='worker_exit'];status=c.status('pending')
        require(c.degraded and errors and errors[-1]['sqlite_errorcode']==sqlite3.SQLITE_FULL,'publish transaction did not get FULL')
        require(status['object']['state']=='VERIFYING' and status['request']['state']=='WAITING' and not status['available'],'FULL falsely published')
        require(not any((c.root/'objects').iterdir()),'FULL created ready target')
        rejected=False
        try:c.submit('blocked_publish','fixture')
        except CacheError:rejected=True
        require(rejected,'publish FULL admitted work')
        path=c.root;c.close();c=None
        db=sqlite3.connect(path/'catalog.db');db.execute('DROP TRIGGER publish_pressure');db.commit();db.close()
        origin.mode='416';origin.calls.clear()
        c=Cache(path,[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),db_pages=64)
        c.resume();require(c.wait('pending')['request']['state']=='SUCCEEDED','pending FULL repair failed')
        with c.read(spec.sha256) as f:require(f.read()==DATA,'pending FULL repair content wrong')
        require(not origin.calls,'complete checkpoint unexpectedly used origin')
        observations.append(dict(mode='sqlite_full_publish',actual_errors=errors,degraded=True,rejected=True,
                                 state_before_repair=dict(object=status['object']['state'],request=status['request']['state']),
                                 published_before_repair=False,repair_correct=True,repair_origin_requests=0,
                                 injection='SQLite allocation trigger at publish intent under 16-page cap; trigger removed offline'))
    finally:
        if c:c.close()
        origin.close()
        if readonly_bind:command('umount',str(mount))
        command('umount',str(mount))
    return dict(cases=observations,mount_removed=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--storage',action='store_true');a=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);v={'measurements':measure(root)}
        if a.storage:v['storage']=storage(root)
    Path(a.output).write_text(json.dumps(v,indent=2)+'\n')

if __name__=='__main__':main()
