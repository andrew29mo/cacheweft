"""Official installed systemd service: documented CLI workflow, fixed soak and real full disk."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import sqlite3
import statistics
import subprocess
import threading
import time
from cacheweft.cli import call
from .origin import Origin,BODIES

ROOT=Path('/var/lib/cacheweft');SOCKET=ROOT/'control.sock';UNIT='cacheweft.service'
CONTRACT=json.loads(Path(__file__).with_name('contract.json').read_text())
DIGEST={k:hashlib.sha256(v).hexdigest() for k,v in BODIES.items()}


def require(v,message):
    if not v:raise RuntimeError(message)

def command(*args,check=True,timeout=35):
    r=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    if check and r.returncode:raise RuntimeError('Command failed: '+' '.join(map(str,args))+'; '+r.stderr)
    return r

def props():return dict(line.split('=',1) for line in command('systemctl','show',UNIT,'--property=MainPID,InvocationID,NRestarts,ActiveState,SubState,Result').stdout.splitlines())
def api(op,**kw):return call(SOCKET,dict(op=op,**kw))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def ledger():
    with sqlite3.connect('file:'+str(ROOT/'catalog.db')+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT * FROM requests ORDER BY id').fetchall()
        return dict(count=len(rows),sha256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())

def footprint():
    out=dict(payload_logical=0,payload_allocated=0,metadata_logical=0,metadata_allocated=0);seen=set()
    for p in ROOT.rglob('*'):
        if not p.is_file():continue

        try:st=p.stat()
        except FileNotFoundError:continue  # Concurrent publication/journal unlink; sampled footprint is not atomic.
        key=(st.st_dev,st.st_ino)
        if key in seen:continue
        seen.add(key);prefix='payload' if p.parent.name in ('objects','staging') else 'metadata'
        out[prefix+'_logical']+=st.st_size;out[prefix+'_allocated']+=st.st_blocks*512
    return out

class Observer:
    def __init__(self,output):
        self.output=Path(output);self.value=dict(contract=CONTRACT,failures=[],commands=[],cycles=[],samples=[]);self.phase='setup';self.uid=None;self.quiet=False
    def persist(self):self.output.write_text(json.dumps(self.value,indent=2,allow_nan=False)+'\n')
    def cli(self,*args,negative=None):
        argv=['runuser','-u','cacheweft','--','/opt/cacheweft/venv/bin/cacheweft','--socket',str(SOCKET),*map(str,args)]
        start=time.monotonic();r=command(*argv,check=False,timeout=12)
        if negative or not self.quiet or r.returncode:
            self.value['commands'].append(dict(phase=self.phase,args=list(map(str,args)),exit_code=r.returncode,stderr=r.stderr,seconds=time.monotonic()-start,expected_rejection=negative))
        if negative:require(r.returncode!=0 and negative in r.stderr,'Expected refusal missing: '+r.stderr);return r
        if r.returncode:
            self.persist();raise RuntimeError('Official CLI failed: '+r.stderr)
        return json.loads(r.stdout)
    def fetch(self,r,name='a',seconds=5):return self.cli('fetch',r,name,'--seconds',seconds)
    def terminal(self,r,expected='SUCCEEDED'):
        until=time.monotonic()+8
        while time.monotonic()<until:
            v=self.cli('status',r)
            if v['request']['state']!='WAITING':require(v['request']['state']==expected,'Wrong terminal state: '+str(v['request']));return v
            time.sleep(.01)
        raise TimeoutError('Request did not terminate')
    def read(self,name):
        out=self.workspace/'observed.bin';self.cli('read',DIGEST[name],'--output',out);require(sha(out)==DIGEST[name],'CLI returned wrong bytes');out.unlink()
    def gc(self):
        plan=self.cli('gc-plan');p=self.workspace/'observer-plan.json';p.write_text(json.dumps(plan));os.chown(p,self.uid,self.uid);p.chmod(0o600)
        try:return self.cli('gc-run','--plan',p)
        finally:p.unlink()
    def settled(self):
        until=time.monotonic()+3
        while time.monotonic()<until:
            if api('info')['usage']['payload_bytes']==0:return
            time.sleep(.01)
        raise TimeoutError('GC did not reclaim payload')
    def workflow(self,origin):
        self.phase='runbook'
        self.fetch('book_a');self.terminal('book_a');self.read('a');self.cli('pin',DIGEST['a'],'--value','true','--request','book_a')
        before=ledger();self.fetch_refusal('book_quota','c','Capacity: explicit GC required')
        require(ledger()==before and sha(ROOT/'objects'/DIGEST['a'])==DIGEST['a'] and self.cli('gc-plan')==[],'Quota refusal deleted pinned data or inserted request')
        self.cli('pin',DIGEST['a'],'--value','false','--request','book_a');self.gc()
        self.fetch('book_c','c');self.terminal('book_c');self.read('c');self.gc()
        held={}
        for name in ('a','b'):
            arrived,_=origin.mode(name,'hold');held[name]=arrived;self.fetch('book_hold_'+name,name);require(arrived.wait(3),'No held transport')
        before=ledger();self.fetch_refusal('book_jobs','c','Outstanding worker budget full');require(ledger()==before,'Rejected job was persisted')
        for name in ('a','b'):
            require(self.cli('cancel','book_hold_'+name)=='CANCELLED','Full worker budget blocked cancellation');origin.release(name)
        origin.idle();self.gc();self.settled()
        for name in ('a','b'):origin.mode(name,'normal')
        self.value['runbook']=dict(quota_refused_without_deletion=True,request_slot_refused=True,cancellation_under_full_worker_budget=True,read_sha256=DIGEST,ledger=ledger());self.persist()
    def fetch_refusal(self,r,name,reason):return self.cli('fetch',r,name,'--seconds','5',negative=reason)
    def cycle(self,i,origin):
        prefix='s'+str(i)+'_';self.fetch(prefix+'ok');self.terminal(prefix+'ok');self.read('a')
        self.cli('pin',DIGEST['a'],'--value','true','--request',prefix+'ok');require(self.cli('gc-plan')==[],'Pinned entry was eligible')
        self.cli('pin',DIGEST['a'],'--value','false','--request',prefix+'ok');self.gc()
        arrived,_=origin.mode('a','prefix');accepted=self.fetch(prefix+'old',seconds=.2);require(arrived.wait(3),'No actual partial stream')
        timed=self.terminal(prefix+'old','TIMED_OUT');require(timed['object']['offset']>0 and timed['request']['deadline']==accepted['request']['deadline'],'Timeout lacked checkpoint or extended deadline')
        origin.release('a');origin.idle();origin.mode('a','normal')
        self.cli('resume',prefix+'new','a','--source',prefix+'old','--seconds','5');self.terminal(prefix+'new');self.read('a');self.gc()
        arrived,_=origin.mode('b','hold');self.fetch(prefix+'cancel','b');require(arrived.wait(3),'Cancel did not reach source')
        require(self.cli('cancel',prefix+'cancel')=='CANCELLED','Cancellation failed');self.terminal(prefix+'cancel','CANCELLED')
        origin.release('b');origin.idle();origin.mode('b','normal');self.gc();self.settled()
        return dict(index=i,requests=4,partial_offset=timed['object']['offset'],old_deadline_unchanged=True,states=['SUCCEEDED','TIMED_OUT','SUCCEEDED','CANCELLED'])
    def soak(self,origin):
        self.phase='soak';self.quiet=True;before=props();epoch=api('info')['epoch'];pid=int(before['MainPID']);start=time.monotonic();done=threading.Event();errors=[]
        def sample():
            while not done.is_set():
                try:
                    raw=dict(line.split(':',1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())
                    ticks=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
                    self.value['samples'].append(dict(seconds=time.monotonic()-start,pid=pid,start_ticks=ticks,rss=int(raw['VmRSS'].split()[0])*1024,
                        fd=len(list(Path(f'/proc/{pid}/fd').iterdir())),threads=int(raw['Threads']),disk=footprint()))
                except Exception as exc:errors.append(type(exc).__name__)
                done.wait(CONTRACT['sample_interval_seconds'])
        thread=threading.Thread(target=sample);thread.start();history=ledger()
        try:
            for i in range(CONTRACT['cycles']):
                target=start+i*CONTRACT['period_seconds'];time.sleep(max(0,target-time.monotonic()))
                lag=time.monotonic()-target;require(lag<=CONTRACT['max_schedule_lag_seconds'],'Scheduled workload fell behind')
                cycle=self.cycle(i,origin);cycle.update(start_lag_seconds=lag,finished_seconds=time.monotonic()-start);self.value['cycles'].append(cycle);self.persist()
            time.sleep(max(0,start+CONTRACT['duration_seconds']-time.monotonic()))
        finally:done.set();thread.join(3);self.quiet=False
        require(not thread.is_alive() and not errors,'Resource sampler failed: '+str(errors));after=props();samples=self.value['samples']
        require(before['MainPID']==after['MainPID'] and before['InvocationID']==after['InvocationID'] and before['NRestarts']==after['NRestarts']=='0' and api('info')['epoch']==epoch,'Restart concealed growth')
        require(len({s['start_ticks'] for s in samples})==1 and len(samples)>=600,'Incomplete process observation')
        def med(field,window):return statistics.median(s[field] for s in samples if window[0]<=s['seconds']<window[1])
        early={k:med(k,CONTRACT['early_window']) for k in ('rss','fd','threads')};late={k:med(k,CONTRACT['late_window']) for k in early}
        windows=[med('rss',(t,t+30)) for t in range(30,180,30)];streak=peak=0
        for a,b in zip(windows,windows[1:]):streak=streak+1 if b-a>CONTRACT['rss_window_step_bytes'] else 0;peak=max(peak,streak)
        for field,absolute,growth in [('rss','rss_absolute_bytes','rss_median_growth_bytes'),('fd','fd_absolute','fd_median_growth'),('threads','threads_absolute','threads_median_growth')]:
            require(max(s[field] for s in samples)<=CONTRACT[absolute] and late[field]-early[field]<=CONTRACT[growth],field+' exceeded fixed resource bound')
        require(peak<=CONTRACT['rss_max_growing_steps'],'RSS kept growing across observation windows')
        require(max(s['disk']['metadata_allocated'] for s in samples)<=CONTRACT['metadata_allocated_bytes'],'Metadata exceeded bounded allocation')
        require(ledger()['count']-history['count']==4*CONTRACT['cycles'],'Lost/extra request history')
        self.value['soak']=dict(seconds=time.monotonic()-start,pid=pid,epoch=epoch,before=before,after=after,early_medians=early,late_medians=late,rss_window_medians=windows,
            largest_growing_streak=peak,history_before=history,history_after=ledger(),sampler_errors=errors,cycles=CONTRACT['cycles']);self.persist()
    def history_full(self,configuration):
        self.phase='history_full';self.fetch('history_anchor');self.terminal('history_anchor');self.cli('pin',DIGEST['a'],'--value','true','--request','history_anchor')
        for i in range(CONTRACT['records']-ledger()['count']):self.fetch('fill_'+str(i))
        full=ledger();require(full['count']==CONTRACT['records'],'Did not actually fill history')
        self.fetch_refusal('over_history','b','Request history full');require(ledger()==full,'History rejection altered ledger')
        self.read('a');require(self.gc()==[] and ledger()==full,'GC evicted pin or reclaimed history')
        protected=sha(ROOT/'objects'/DIGEST['a'])
        self.cli('pin',DIGEST['a'],'--value','false','--request','history_anchor');removed=self.gc()
        require(removed==[DIGEST['a']] and not (ROOT/'objects'/DIGEST['a']).exists() and ledger()==full,'Actual eviction reclaimed history or failed')
        historical=self.cli('status','history_anchor');require(historical['request']['state']=='SUCCEEDED' and historical['available'] is False,'Eviction changed immutable request result')
        self.fetch_refusal('over_after_gc','b','Request history full')
        old=api('info')['epoch'];command('systemctl','stop',UNIT);command('systemctl','start',UNIT)
        require(ledger()==full,'Restart reset history');self.fetch_refusal('over_after_restart','b','Request history full')
        command('systemctl','stop',UNIT);configuration['records']=CONTRACT['repaired_records'];write_config(configuration);command('systemctl','start',UNIT)
        require(ledger()==full,'Capacity increase discarded history')
        new_epoch=api('info')['epoch'];require(new_epoch!=old,'Repair retained epoch')
        refused=False
        try:api('fetch',id='stale_history',artifact='b',seconds=5,epoch=old)
        except Exception as exc:
            require('Stale epoch' in str(exc),'Wrong stale refusal');refused=True
        require(refused,'Old operation accepted');self.fetch('after_capacity_increase','b');self.terminal('after_capacity_increase');self.read('b')
        self.value['history_full']=dict(full=full,gc_history_count=full['count'],after_restart=full,repair_limit=configuration['records'],old_epoch_rejected=True,after_repair=ledger(),pinned_sha256_before_explicit_eviction=protected,actual_eviction=True,historical_result='SUCCEEDED',historical_available=False);self.persist()
    def disk_full(self,origin):
        self.phase='disk_full';command('systemctl','stop',UNIT);underlying=sha(ROOT/'catalog.db')
        mount=Path('/mnt/cw-closeout');mount.mkdir();command('mount','-t','tmpfs','-o','size=8m','cacheweft-closeout',mount)
        bound=False;trace=None;log=mount/'trace-unused';record={};self.value['disk_full']=record
        try:
            backing=mount/'cache';backing.mkdir(mode=0o700);os.chown(backing,self.uid,self.uid)
            command('mount','--bind',backing,ROOT);bound=True;command('systemctl','start',UNIT)
            self.fetch('disk_anchor');self.terminal('disk_anchor');self.cli('pin',DIGEST['a'],'--value','true','--request','disk_anchor')
            preserved=sha(ROOT/'objects'/DIGEST['a']);pid=int(props()['MainPID'])
            log=Path('/opt/cacheweft/disk-trace.log')
            trace=subprocess.Popen(['strace','-f','-qq','-yy','-e','trace=write,pwrite64,fsync,fdatasync,openat','-p',str(pid),'-o',str(log)],stderr=subprocess.PIPE)
            until=time.monotonic()+3
            while time.monotonic()<until:
                tracer=next(line for line in Path(f'/proc/{pid}/status').read_text().splitlines() if line.startswith('TracerPid:'))
                if int(tracer.split()[1])==trace.pid:break
                time.sleep(.01)
            require(int(tracer.split()[1])==trace.pid,'Disk tracer did not attach')
            arrived,_=origin.mode('b','gated');receipt=self.fetch('disk_pending','b');require(arrived.wait(3),'Fault request did not reach body gate')
            until=time.monotonic()+2
            while time.monotonic()<until:
                pending=self.cli('status','disk_pending')
                if pending['object']['offset']==262144:break
                time.sleep(.01)
            require(pending['object']['offset']==262144 and pending['request']['state']=='WAITING','No committed checkpoint before disk fault')
            with (mount/'filler').open('wb',buffering=0) as f:
                while os.statvfs(mount).f_bavail*os.statvfs(mount).f_frsize>CONTRACT['disk_fault_free_bytes']:f.write(b'x'*4096)
                os.fsync(f.fileno())
            free=os.statvfs(mount).f_bavail*os.statvfs(mount).f_frsize
            require(free==CONTRACT['disk_fault_free_bytes'],'Wrong actual free-space target')
            origin.release('b');until=time.monotonic()+5
            while time.monotonic()<until:
                info=api('info')
                if info['degraded']:break
                time.sleep(.01)
            record.update(free_bytes_at_injection=free,formal_service_pid=pid,degraded=info['degraded'],receipt_state=receipt['request']['state'])
            require(info['degraded'] is True,'Actual disk pressure did not degrade service')
            failed=self.cli('status','disk_pending');require(failed['request']['state']!='SUCCEEDED' and failed['available'] is False and not (ROOT/'objects'/DIGEST['b']).exists(),'Full disk falsely succeeded')
            self.fetch_refusal('during_full','c','Authority unavailable');self.cli('read',DIGEST['b'],'--output',self.workspace/'denied.bin',negative='Authority unavailable')
            require(not (self.workspace/'denied.bin').exists() and sha(ROOT/'objects'/DIGEST['a'])==preserved,'Full disk deleted protected data or created read output')
            trace.send_signal(signal.SIGINT);_,stderr=trace.communicate(timeout=5)
            require(trace.returncode in (0,-signal.SIGINT),'Tracer failed: '+stderr.decode())
            lines=[line for line in log.read_text().splitlines() if 'ENOSPC' in line and '/staging/' in line]
            require(lines,'No actual formal payload-write ENOSPC witness')
            old_epoch=info['epoch'];command('systemctl','stop',UNIT);(mount/'filler').unlink();command('systemctl','start',UNIT)
            origin.mode('b','normal')
            source=self.cli('status','disk_pending');require(source['request']['state']=='INTERRUPTED' and source['request']['deadline']==receipt['request']['deadline'],'Repair revived old request or deadline')
            rejected=False
            try:api('fetch',id='stale_disk',artifact='b',seconds=5,epoch=old_epoch)
            except Exception as exc:require('Stale epoch' in str(exc),'Wrong stale disk rejection');rejected=True
            require(rejected,'Stale disk operation accepted')
            self.cli('resume','disk_repair','b','--source','disk_pending','--seconds','5');self.terminal('disk_repair');self.read('b')
            require(sha(ROOT/'objects'/DIGEST['a'])==preserved,'Repair lost pinned data')
            record=dict(tmpfs_bytes=CONTRACT['disk_fault_tmpfs_bytes'],free_bytes_at_injection=free,receipt_state=receipt['request']['state'],state_under_fault=failed['request']['state'],
                no_ready_target=True,mutation_refused=True,protected_sha256=preserved,formal_service_pid=pid,tracer_attached=True,enospc_payload_syscalls=lines,checkpoint_before_fault=262144,
                repair_old_state=source['request']['state'],deadline_unchanged=True,stale_epoch_rejected=True,new_request_state='SUCCEEDED',read_sha256=DIGEST['b'])
        finally:
            if trace and trace.poll() is None:trace.send_signal(signal.SIGINT);trace.communicate(timeout=5)
            if log.exists():
                raw=log.read_text();record['observed_io_errors']=[line for line in raw.splitlines() if 'ENOSPC' in line or 'EIO' in line];record['trace_tail']=raw[-24000:]
            command('systemctl','stop',UNIT,check=False)
            if bound:command('umount',ROOT)
            command('umount',mount);mount.rmdir()
        require(sha(ROOT/'catalog.db')==underlying,'Fault fixture modified the original catalog')
        record.update(mounts_removed=True,original_catalog_unchanged=True);self.value['disk_full']=record;self.persist()

def write_config(configuration):
    path=Path('/etc/cacheweft/config.json');path.write_text(json.dumps(configuration));uid=pwd.getpwnam('cacheweft').pw_uid
    path.chmod(0o600);os.chown(path,uid,uid)

def run(wheel,output):
    base=Path('/opt/cacheweft/closeout');base.mkdir();origin=Origin(base/'tls');observer=Observer(output);started=False
    configuration=dict(revision=1,origin=origin.origin,address='127.0.0.1',cafile='/etc/cacheweft/ca.pem',budget=CONTRACT['payload_budget_bytes'],records=CONTRACT['records'],jobs=CONTRACT['jobs'],db_pages=1024,
        entries=[dict(id=k,url=origin.origin+'/'+k+'.bin',size=len(v),sha256=DIGEST[k]) for k,v in BODIES.items()])
    try:
        paths=['/opt/cacheweft/venv','/etc/systemd/system/cacheweft.service','/etc/cacheweft/config.json','/var/lib/cacheweft']
        absent={p:not Path(p).exists() for p in paths}
        try:pwd.getpwnam('cacheweft');account_absent=False
        except KeyError:account_absent=True
        require(all(absent.values()) and account_absent,'Production installation was pre-populated')
        observer.value['clean_install']=dict(absent_paths=absent,account_absent=account_absent,observer_venv_separate=True)
        Path('/etc/cacheweft').mkdir(exist_ok=True);Path('/etc/cacheweft/ca.pem').write_bytes(Path(origin.cert).read_bytes())
        config=base/'config.json';config.write_text(json.dumps(configuration));config.chmod(0o600)
        command('sh','deploy/install.sh',wheel,config);observer.uid=pwd.getpwnam('cacheweft').pw_uid
        observer.workspace=Path('/var/tmp/cacheweft-observer');observer.workspace.mkdir(mode=0o700);os.chown(observer.workspace,observer.uid,observer.uid)
        require(command('systemctl','is-enabled',UNIT,check=False).stdout.strip()=='disabled','Installer unexpectedly enabled boot service')
        command('systemd-analyze','verify','/etc/systemd/system/cacheweft.service');command('systemctl','start',UNIT);started=True
        from tools.deployment import identity
        observer.value['installed_identity']=identity();observer.value['installed_unit_sha256']=sha('/etc/systemd/system/cacheweft.service')
        code='import cacheweft,hashlib,pathlib,json;p=pathlib.Path(cacheweft.__file__).parent;print(json.dumps({"cacheweft/"+f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob("*.py")}))'
        observer.value['installed_modules']=json.loads(command('/opt/cacheweft/venv/bin/python','-c',code).stdout)
        permission={p:dict(uid=Path(p).stat().st_uid,mode=Path(p).stat().st_mode&0o777) for p in ('/opt/cacheweft/venv/bin/cacheweft','/etc/cacheweft/config.json','/var/lib/cacheweft')}
        require(permission['/opt/cacheweft/venv/bin/cacheweft']['uid']==0 and permission['/etc/cacheweft/config.json']==dict(uid=observer.uid,mode=0o600) and permission['/var/lib/cacheweft']==dict(uid=observer.uid,mode=0o700),'Installed ownership or modes wrong')
        observer.value['clean_install']['permissions']=permission
        require(Path('/etc/systemd/system/cacheweft.service').read_bytes()==Path('deploy/cacheweft.service').read_bytes(),'Wrong installed unit')
        observer.cli('info');observer.workflow(origin);observer.soak(origin);observer.history_full(configuration);observer.disk_full(origin)
    except BaseException as exc:
        observer.value['failures'].append(dict(phase=observer.phase,error=type(exc).__name__+': '+str(exc)));observer.persist();raise
    finally:
        command('systemctl','stop',UNIT,check=False);origin.close()
        p=props();observer.value['cleanup']=dict(main_pid=p['MainPID'],active_state=p['ActiveState'],socket_removed=not SOCKET.exists(),ready_removed=not (ROOT/'ready.json').exists(),origin_handlers=origin.active)
        observer.persist()
    require(observer.value['cleanup']==dict(main_pid='0',active_state='inactive',socket_removed=True,ready_removed=True,origin_handlers=0),'Final cleanup failed')
    return observer.value

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--wheel',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.wheel,a.output)
