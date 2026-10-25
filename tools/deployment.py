"""Root VM observer of the installed unit; no replacement cache implementation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess
import time
from cacheweft.cli import call
from cacheweft.core import CacheError
from cacheweft.service import token
from tests.daemon_fixture import require
from tests.fixture import Origin,DATA

UNIT='cacheweft.service'
ROOT=Path('/var/lib/cacheweft')
SOCKET=ROOT/'control.sock'

def command(*args,check=True):
    result=subprocess.run(args,capture_output=True,text=True,timeout=35)
    if check and result.returncode:raise RuntimeError('Command failed: '+' '.join(args)+'; '+result.stderr.strip())
    return result

def properties():
    return dict(line.split('=',1) for line in command('systemctl','show',UNIT,'--property=MainPID,InvocationID,NRestarts,Result,ActiveState,SubState,ControlGroup,User,Group,Restart,RestartUSec,StartLimitBurst,StartLimitIntervalUSec,TimeoutStopUSec,KillMode,NoNewPrivileges,CapabilityBoundingSet,FragmentPath').stdout.splitlines())

def api(op,**kw):return call(SOCKET,dict(op=op,**kw))

def until(fn,seconds=8):
    deadline=time.monotonic()+seconds;error=None
    while time.monotonic()<deadline:
        try:
            v=fn()
            if v:return v
        except (OSError,CacheError,sqlite3.Error) as exc:error=exc
        time.sleep(.02)
    raise TimeoutError('Observation failed: '+str(error))

def identity():
    p=properties();pid=int(p['MainPID']);lines=Path(f'/proc/{pid}/status').read_text().splitlines()
    status={k:v.strip() for k,v in (line.split(':',1) for line in lines)}
    uid=pwd.getpwnam('cacheweft').pw_uid
    require([int(x) for x in status['Uid'].split()]==[uid]*4 and uid!=0,'Root service')
    require(int(status['CapEff'],16)==0 and status['NoNewPrivs']=='1','Unexpected privilege')
    require('/opt/cacheweft/venv/bin/cacheweft' in Path(f'/proc/{pid}/cmdline').read_bytes().decode(),'Not installed entrypoint')
    return dict(pid=pid,uid=uid,cap_eff=status['CapEff'],no_new_privs=status['NoNewPrivs'],
                rss_bytes=int(status['VmRSS'].split()[0])*1024,peak_rss_bytes=int(status['VmHWM'].split()[0])*1024,
                fd_count=len(list(Path(f'/proc/{pid}/fd').iterdir())),properties=p)

def run(wheel,output):
    base=Path('/opt/cacheweft/deployment');base.mkdir();origin=Origin(base/'tls')
    config=base/'config.json';digest=hashlib.sha256(DATA).hexdigest()
    ca=Path('/etc/cacheweft');ca.mkdir(exist_ok=True);(ca/'ca.pem').write_bytes(Path(origin.cert).read_bytes())
    value=dict(revision=1,origin=origin.origin,address='127.0.0.1',cafile=str(ca/'ca.pem'),entries=[dict(id='fixture-v1',url=origin.url,size=len(DATA),sha256=digest)])
    config.write_text(json.dumps(value));os.chmod(config,0o600)
    results={}
    def persist():Path(output).write_text(json.dumps(results,indent=2)+'\n')
    try:
        command('sh','deploy/install.sh',wheel,str(config))
        require(command('systemctl','is-active',UNIT,check=False).returncode!=0,'Installer auto-started')
        require(command('systemctl','is-enabled',UNIT,check=False).stdout.strip()=='disabled','Installer auto-enabled')
        require(Path('/etc/systemd/system/cacheweft.service').read_bytes()==Path('deploy/cacheweft.service').read_bytes(),'Different unit installed')
        command('systemd-analyze','verify','/etc/systemd/system/cacheweft.service')
        started=time.monotonic();command('systemctl','start',UNIT);info=api('info');initial=identity()
        require(properties()['SubState']=='running','Not ready')
        require(Path('/proc/'+str(initial['pid'])+'/root/var/lib/cacheweft').exists(),'Missing state directory')
        results['start']=dict(seconds=time.monotonic()-started,identity=initial,manifest_revision=info['manifest_revision'],epoch=info['epoch'])
        persist()
        origin.mode='partial_hold';origin.arrived.clear()
        old=api('fetch',id='old',artifact='fixture-v1',seconds=30,epoch=info['epoch'])
        require(origin.arrived.wait(3),'No actual partial download')
        def partial():
            r=api('status',id='old');return r if r['object']['offset']>=65536 else None
        before=until(partial)
        lock=ROOT/'writer.lock'
        # Cache lock filename is stable and remains present after normal stops.
        locks=list(ROOT.glob('*.lock'));require(len(locks)==1,'Missing stable writer lock');lock=locks[0];inode=lock.stat().st_ino
        started=time.monotonic();command('systemctl','kill','--kill-whom=main','--signal=SIGKILL',UNIT)
        with sqlite3.connect('file:'+str(ROOT/'catalog.db')+'?mode=ro',uri=True) as db:
            saved=db.execute("SELECT state,deadline,epoch FROM requests WHERE id='old'").fetchone()
        require(saved[0]=='WAITING' and saved[1]==old['request']['deadline'],'No retained pending journal witness')
        def recovered():
            p=properties()
            if p['InvocationID']==initial['properties']['InvocationID'] or p['SubState']!='running':return None
            return api('info')
        fresh=until(recovered);elapsed=time.monotonic()-started;after=identity();status=api('status',id='old')
        require(status['request']['state']=='INTERRUPTED' and status['request']['deadline']==old['request']['deadline'] and not status['available'],'Old request revived')
        require(fresh['epoch']!=info['epoch'] and after['pid']!=initial['pid'],'No actual takeover')
        require(not Path('/proc/'+str(initial['pid'])).exists(),'Old process survived')
        require(len(origin.calls)==1,'Automatic download on recovery')
        refused=0
        for q in [dict(op='fetch',id='stale',artifact='fixture-v1',seconds=3,epoch=info['epoch']),dict(op='cancel',id='old',token=token(old),epoch=fresh['epoch'])]:
            try:call(SOCKET,q)
            except CacheError:refused+=1
        require(refused==2,'Stale operation accepted')
        origin.release.set();origin.mode='normal'
        api('resume',id='fresh',source='old',artifact='fixture-v1',seconds=5,epoch=fresh['epoch'],token=token(status))
        completed=until(lambda:(s if (s:=api('status',id='fresh'))['request']['state']=='SUCCEEDED' else None))
        uid=pwd.getpwnam('cacheweft').pw_uid
        # CLI executes as the service account and writes within its owned state directory.
        out=ROOT/'observed.bin'
        cli=command('setpriv','--reuid='+str(uid),'--regid='+str(uid),'--clear-groups','/opt/cacheweft/venv/bin/cacheweft','--socket',str(SOCKET),'read',digest,'--output',str(out))
        require(out.read_bytes()==DATA,'CLI bytes wrong');out.unlink()
        results['crash_takeover']=dict(seconds_until_ready_rpc=elapsed,old_identity=initial,new_identity=after,
            journal_state_after_kill=saved[0],old_state=status['request']['state'],deadline_unchanged=True,no_automatic_get=True,
            stale_rejections=refused,resume_state=completed['request']['state'],read_sha256=digest,cli_exit=cli.returncode,
            lock_inode_unchanged=lock.stat().st_ino==inode)
        persist()
        # Stop during a real in-flight request, after reclaiming the completed object via API.
        api('gc-run',plan=api('gc-plan'),epoch=fresh['epoch']);origin.mode='slow_headers';origin.arrived.clear()
        api('fetch',id='stop',artifact='fixture-v1',seconds=10,epoch=fresh['epoch']);require(origin.arrived.wait(3),'No in-flight stop')
        started=time.monotonic();command('systemctl','stop',UNIT);stopped=properties()
        require(stopped['MainPID']=='0' and stopped['ActiveState']=='inactive' and stopped['Result']=='success','Stop failed')
        require(not SOCKET.exists() and not (ROOT/'ready.json').exists() and not Path('/proc/'+str(after['pid'])).exists(),'Stop leaked service')
        group=Path('/sys/fs/cgroup'+after['properties']['ControlGroup'])
        require(not group.exists() or not (group/'cgroup.procs').read_text().strip(),'Cgroup still occupied')
        require(lock.stat().st_ino==inode,'Lock replaced')
        results['stop']=dict(seconds=time.monotonic()-started,properties=stopped,process_gone=True,cgroup_empty=True,socket_removed=True,ready_removed=True,lock_retained=True)
        persist()
        # Invalid config deterministically exercises the shipped restart policy.
        target=Path('/etc/cacheweft/config.json');target.write_text('{}')
        failed_start=command('systemctl','start',UNIT,check=False)
        results['invalid_start']=dict(exit_code=failed_start.returncode,stderr=failed_start.stderr)
        results['limit_samples']=[]
        def limit_sample():
            p=properties()
            if not results['limit_samples'] or p!=results['limit_samples'][-1]:results['limit_samples'].append(p);persist()
            if p['ActiveState']!='failed' or p['MainPID']!='0':return None
            records=[json.loads(line) for line in command('journalctl','-u',UNIT,'--no-pager','-o','json').stdout.splitlines()]
            hits=[r for r in records if r.get('_PID')=='1' and r.get('MESSAGE')==UNIT+': Start request repeated too quickly.']
            failures=[r for r in records if r.get('MESSAGE')=='CacheError: Invalid config fields']
            if not hits:return None
            require(len(failures)==3 and len({r['_PID'] for r in failures})==3,'Not three actual failed processes')
            results['limit_witness']=dict(manager_events=hits,failed_client_pids=[int(r['_PID']) for r in failures])
            return p
        limited=until(limit_sample,10)
        stable_start=time.monotonic();time.sleep(2.2);stable=properties()
        require(stable==limited,'Unexpected restart after limit hit')
        results['limit_witness']['stable_seconds']=time.monotonic()-stable_start
        results['limit_witness']['stable_properties']=stable
        require(limited['MainPID']=='0' and limited['ActiveState']=='failed','Restart limit not enforced')
        require(not SOCKET.exists() and not (ROOT/'ready.json').exists(),'Failed startup left ready')
        config_bytes=config.read_bytes();target.write_bytes(config_bytes)
        command('systemctl','reset-failed',UNIT);command('systemctl','start',UNIT);repaired=api('info');repair_identity=identity()
        require(repaired['epoch']!=fresh['epoch'] and api('status',id='stop')['request']['state']=='INTERRUPTED','Repair revived work')
        require(api('status',id='old')['request']['state']=='INTERRUPTED','Repair changed old result')
        results['restart_limit']=dict(properties=limited,no_ready=True,repaired_identity=repair_identity,old_request_state='INTERRUPTED',interrupted_stop_request=True,explicit_reset_failed=True)
        persist()
    finally:
        results['last_properties']=properties()
        results['service_journal']=command('journalctl','-u',UNIT,'--no-pager','-n','80',check=False).stdout
        persist()
        origin.release.set();command('systemctl','stop',UNIT,check=False);origin.close()
    p=properties();require(p['MainPID']=='0' and not SOCKET.exists() and not (ROOT/'ready.json').exists(),'Final cleanup failed')
    results['final_cleanup']=dict(main_pid=0,socket_removed=True,ready_removed=True)
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--wheel',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    result=run(a.wheel,a.output);Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
