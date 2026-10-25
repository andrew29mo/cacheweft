"""Observed official daemon deadlines and reboot recovery; root only for VM setup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

from cacheweft import CacheError
from cacheweft.service import token
from tests.daemon_fixture import Daemon,require
from tests.fixture import Origin,DATA

BASE=Path('/opt/cacheweft/milestone-boot')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def rows(root):
    db=sqlite3.connect('file:'+str(root/'catalog.db')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:return dict(requests=[dict(r) for r in db.execute('SELECT * FROM requests ORDER BY id')],objects=[dict(r) for r in db.execute('SELECT * FROM objects')])
    finally:db.close()


def blackhole(base,origin):
    ns='cw-m1-connect';host='cw-m1-host';peer='cw-m1-peer';daemon=None
    def command(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=10).stdout
    require(not Path('/var/run/netns',ns).exists(),'Existing namespace refused')
    result=None
    try:
        command('ip','netns','add',ns)
        command('ip','link','add',host,'type','veth','peer','name',peer)
        command('ip','link','set',peer,'netns',ns)
        command('ip','addr','add','10.231.91.1/24','dev',host);command('ip','link','set',host,'up')
        command('ip','-n',ns,'addr','add','10.231.91.2/24','dev',peer);command('ip','-n',ns,'link','set',peer,'up');command('ip','-n',ns,'link','set','lo','up')
        class Isolated(Daemon):
            def argv(self):return ['ip','netns','exec',ns,*super().argv()]
        daemon=Isolated(base/'blackhole',origin);daemon.value['address']='10.231.91.99';daemon.write()
        daemon.start();identity=daemon.identity()
        isolated=os.stat('/proc/'+str(daemon.proc.pid)+'/ns/net').st_ino==os.stat('/var/run/netns/'+ns).st_ino
        require(isolated,'Daemon not in isolated namespace');origin.calls.clear()
        started=time.monotonic();initial=daemon.submit('connect',.3);final=daemon.terminal('connect',2)
        neighbor=command('ip','-n',ns,'neigh','show','dev',peer)
        require('10.231.91.99' in neighbor,'No actual unresolved-neighbor connect')
        require(final['request']['state']=='TIMED_OUT' and not final['available'] and final['request']['deadline']==initial['request']['deadline'],'Connect deadline failed')
        terminal=time.monotonic()-started;daemon.stop();total=time.monotonic()-started
        require(total<1.5 and not origin.calls,'Connect/cleanup exceeded controlled bound or reached origin')
        result=dict(mode='connect_blackhole',result='TIMED_OUT',deadline_unchanged=True,available=False,namespace_isolated=isolated,
                    unresolved_neighbor=True,origin_requests=0,terminal_seconds=terminal,total_through_stop_seconds=total,
                    identity=identity,exit_code=daemon.proc.returncode)
    finally:
        if daemon:daemon.stop()
        subprocess.run(['ip','link','del',host],capture_output=True,timeout=5)
        subprocess.run(['ip','netns','del',ns],capture_output=True,timeout=5)
    require(not Path('/var/run/netns',ns).exists() and not Path('/sys/class/net',host).exists(),'Network fixture cleanup failed')
    result['network_cleaned']=True
    return result


def cases():
    outcomes=[]
    with tempfile.TemporaryDirectory(prefix='cw-m1-',dir='/tmp') as tmp:
        base=Path(tmp);os.chmod(base,0o755);origin=Origin(base/'tls');daemon=None
        try:
            for mode in ('slow_headers','slow_body','cancel'):
                origin.mode=mode if mode!='cancel' else 'slow_headers';origin.arrived.clear()
                daemon=Daemon(base/mode,origin);daemon.start();identity=daemon.identity()
                start=time.monotonic();initial=daemon.submit('request',.3 if mode!='cancel' else 3)
                require(origin.arrived.wait(2),'No actual slow origin request')
                replay=False
                if mode=='cancel':
                    require(daemon.api('cancel',id='request',epoch=initial['epoch'],token=token(initial))=='CANCELLED','Cancel failed')
                    try:daemon.api('cancel',id='request',epoch=initial['epoch'],token=token(initial))
                    except CacheError:replay=True
                    require(replay,'Stale cancellation accepted')
                final=daemon.terminal('request',2);elapsed=time.monotonic()-start
                require(final['request']['state']==('CANCELLED' if mode=='cancel' else 'TIMED_OUT') and not final['available'],'Late success')
                require(final['request']['deadline']==initial['request']['deadline'],'Old deadline extended')
                require(elapsed<2,'Slow trickle did not terminate within observation bound')
                stop=time.monotonic();daemon.stop();stopped=time.monotonic()-stop
                require(stopped<2 and not daemon.socket.exists() and not (daemon.root/'ready.json').exists(),'Process cleanup failed')
                outcomes.append(dict(mode=mode,result=final['request']['state'],deadline_unchanged=True,available=False,
                                     seconds=elapsed,stop_seconds=stopped,identity=identity,stale_cancel_rejected=replay if mode=='cancel' else None,
                                     exit_code=daemon.proc.returncode,receipt_removed=True))
                daemon=None
            origin.mode='hold';origin.arrived.clear();origin.release.clear();daemon=Daemon(base/'shared',origin);daemon.start()
            old=daemon.submit('short',.25);require(origin.arrived.wait(2),'No shared origin request');daemon.submit('long',3)
            require(daemon.terminal('short')['request']['state']=='TIMED_OUT','Short request extended')
            origin.release.set();require(daemon.terminal('long')['request']['state']=='SUCCEEDED','Long request failed')
            after=daemon.api('status',id='short')
            require(after['request']['deadline']==old['request']['deadline'] and after['request']['state']=='TIMED_OUT','Old result changed')
            outcomes.append(dict(mode='shared_deadlines',old_state='TIMED_OUT',new_state='SUCCEEDED',old_deadline_unchanged=True))
            daemon.stop();daemon=None
            if sys.platform=='linux' and os.geteuid()==0:outcomes.append(blackhole(base,origin))
        finally:
            origin.release.set()
            if daemon:daemon.stop()
            origin.close()
    return dict(cases=outcomes,cleanup=True,scope='actual daemon subprocess; 2s assertion is a controlled observation bound, not a production SLA')


def prepare():
    BASE.mkdir(mode=0o755);origin=Origin(BASE/'tls');origin.mode='partial_hold';daemon=Daemon(BASE/'user',origin)
    try:
        info=daemon.start();identity=daemon.identity();daemon.submit('old_request',30)
        require(origin.arrived.wait(2),'No partial transfer')
        until=time.monotonic()+3
        while time.monotonic()<until:
            state=daemon.api('status',id='old_request')
            if 65536<=state['object']['offset']<len(DATA):break
            time.sleep(.01)
        require(65536<=state['object']['offset']<len(DATA),'No partial checkpoint')
        daemon.stop(crash=True)
        observed=rows(daemon.root)
        require(observed['requests'][0]['state']=='WAITING','Shutdown cleaned old request')
        result=dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),epoch=info['epoch'],identity=identity,
                    catalog_sha256=sha(daemon.root/'catalog.db'),rows=observed,port=origin.server.server_port,
                    stage_sha256=[sha(p) for p in (daemon.root/'staging').iterdir()],crash_exit=daemon.proc.returncode)
        (BASE/'prepared.json').write_text(json.dumps(result,indent=2)+'\n');return result
    finally:
        origin.release.set();daemon.stop(crash=True);origin.close()


def finish():
    before=json.loads((BASE/'prepared.json').read_text());root=BASE/'user/cache'
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();hash_before=sha(root/'catalog.db');states=rows(root)
    stage_hash=[sha(p) for p in (root/'staging').iterdir()]
    require(boot!=before['boot_id'] and hash_before==before['catalog_sha256'] and states==before['rows'] and stage_hash==before['stage_sha256'],'State changed before recovery')
    origin=Origin(BASE/'tls',port=before['port'],reuse_certificate=True);daemon=Daemon(BASE/'user',origin,reuse=True)
    try:
        info=daemon.start();identity=daemon.identity();status=daemon.api('status',id='old_request')
        require(info['epoch']!=before['epoch'] and status['request']['state']=='INTERRUPTED' and status['object']['state']=='PAUSED','Old request revived')
        deadline=before['rows']['requests'][0]['deadline']
        require(status['request']['deadline']==deadline and not origin.calls,'Restart extended deadline or used origin')
        stale=False
        try:daemon.api('fetch',id='stale',artifact='fixture-v1',seconds=3,epoch=before['epoch'])
        except CacheError:stale=True
        require(stale,'Old epoch accepted')
        stale_token=dict(epoch=before['epoch'],generation=before['rows']['requests'][0]['generation'],revision=before['rows']['requests'][0]['revision'])
        rejected=False
        try:daemon.api('resume',id='bad_resume',source='old_request',artifact='fixture-v1',seconds=3,epoch=info['epoch'],token=stale_token)
        except CacheError:rejected=True
        require(rejected,'Old token accepted')
        daemon.api('resume',id='fresh_request',source='old_request',artifact='fixture-v1',seconds=3,epoch=info['epoch'],token=token(status))
        fresh=daemon.terminal('fresh_request');old=daemon.api('status',id='old_request')
        require(fresh['request']['state']=='SUCCEEDED' and old['request']['state']=='INTERRUPTED' and old['request']['deadline']==deadline,'Resume changed historical result')
        output=BASE/'result';r=daemon.cli('read',daemon.digest,'--output',str(output));require(output.read_bytes()==DATA,'CLI content wrong')
        console=Path(sys.executable).with_name('cacheweft')
        help_run=subprocess.run([str(console),'--help'],capture_output=True,timeout=5)
        require(help_run.returncode==0,'Installed entrypoint failed')
        result=dict(before=before,boot_id_after=boot,catalog_sha256_before_recovery=hash_before,rows_before_recovery=states,
                    stage_sha256_before_recovery=stage_hash,identity_after=identity,epoch_after=info['epoch'],
                    old_before_resume=status['request'],old_after_resume=old['request'],fresh=fresh['request'],
                    no_automatic_origin_request=True,old_epoch_rejected=stale,old_token_rejected=rejected,
                    resume_ranges=[x['range'] for x in origin.calls],read_sha256=sha(output),cli_read_exit=r.returncode,
                    installed_entrypoint_exit=help_run.returncode,normal_reboot=True,power_loss_tested=False)
    finally:daemon.stop();origin.close()
    result['process_cleaned']=daemon.proc.returncode==0 and not daemon.socket.exists() and not (daemon.root/'ready.json').exists()
    (BASE/'finished.json').write_text(json.dumps(result,indent=2)+'\n');return result


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['cases','prepare','finish']);p.add_argument('--output');a=p.parse_args()
    value={'cases':cases,'prepare':prepare,'finish':finish}[a.action]()
    if a.output:Path(a.output).write_text(json.dumps(value,indent=2)+'\n')
    else:print(json.dumps(value))

if __name__=='__main__':main()
