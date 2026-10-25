"""Formal installed daemon/CLI paths with a real pre-trained model; no cache substitute."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time

from cacheweft.cli import call
from cacheweft import CacheError
from tests.daemon_fixture import Daemon,require
from .origin import Origin

MODEL=json.loads(Path(__file__).with_name('model.json').read_text())

class ModelDaemon(Daemon):
    def __init__(self,work,origin,cli):
        self.executable=str(Path(cli).resolve());super().__init__(work,origin)
        self.digest=MODEL['sha256'];self.value['entries']=[dict(id=MODEL['id'],url=origin.url,size=MODEL['size'],sha256=self.digest)]
        self.value['attempts']=6;self.write()
    def prefix(self):
        return ['setpriv','--reuid='+str(self.uid),'--regid='+str(self.uid),'--clear-groups','--no-new-privs'] if self.uid!=os.geteuid() else []
    def argv(self):return [*self.prefix(),self.executable,'serve','--root',str(self.root),'--config',str(self.configuration)]
    def start(self):
        # All children share the observer's group so the outer timeout kills the entire experiment.
        log=open(self.work/('daemon-'+str(len(self.logs))+'.log'),'wb');self.logs.append(log.name)
        try:self.proc=subprocess.Popen(self.argv(),cwd=self.work,stdout=log,stderr=log)
        finally:log.close()
        require(os.getpgid(self.proc.pid)==os.getpgrp(),'Daemon escaped observer group')
        until=time.monotonic()+5
        while time.monotonic()<until:
            if self.proc.poll() is not None:raise RuntimeError('Installed daemon exited: '+Path(self.logs[-1]).read_text())
            try:return self.api('info')
            except (OSError,CacheError):time.sleep(.02)
        raise TimeoutError('Installed daemon not ready')
    def stop(self,crash=False):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL if crash else signal.SIGTERM)
            try:self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill();self.proc.wait(2);raise
            require(self.proc.returncode==(-signal.SIGKILL if crash else 0),'Unexpected installed daemon exit')
    def cli(self,*args,check=True):
        r=subprocess.run([*self.prefix(),self.executable,'--socket',str(self.socket),*map(str,args)],cwd=self.work,capture_output=True,text=True,timeout=15)
        if check and r.returncode:raise RuntimeError('Official CLI failed: '+r.stderr)
        return r

class Samples:
    def __init__(self,daemon):self.daemon=daemon;self.values=[];self.errors=[];self.event=threading.Event();self.thread=threading.Thread(target=self.run)
    def sample(self):
        pid=self.daemon.proc.pid
        if sys.platform=='linux':
            raw={k:v.strip() for k,v in (line.split(':',1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())}
            require([int(i) for i in raw['Uid'].split()]==[self.daemon.uid]*4 and int(raw['CapEff'],16)==0 and raw['NoNewPrivs']=='1','Daemon privilege wrong')
            return int(raw['VmRSS'].split()[0])*1024
        r=subprocess.run(['ps','-o','rss=,uid=','-p',str(pid)],capture_output=True,text=True,check=True,timeout=2)
        rss,uid=map(int,r.stdout.split());require(uid==self.daemon.uid and uid!=0,'Daemon privilege wrong');return rss*1024
    def run(self):
        while not self.event.is_set():
            try:self.values.append(self.sample())
            except Exception as exc:self.errors.append(type(exc).__name__)
            self.event.wait(.05)
    def start(self):self.thread.start();return self
    def stop(self):
        self.event.set();self.thread.join(3)
        require(not self.thread.is_alive() and self.values and not self.errors,'RSS sampling incomplete')
        return dict(pid=self.daemon.proc.pid,uid=self.daemon.uid,daemon_pgid=os.getpgid(self.daemon.proc.pid),observer_pgid=os.getpgrp(),sampled_min_bytes=min(self.values),sampled_max_bytes=max(self.values),samples=len(self.values),requested_interval_seconds=.05,
                    scope='daemon PID current RSS samples only; observer, inference and VM/host memory excluded',platform=platform.system())

def disk(root):
    seen=set();logical=allocated=0
    for p in root.rglob('*'):
        if p.is_file():
            st=p.stat();key=(st.st_dev,st.st_ino)
            if key not in seen:seen.add(key);logical+=st.st_size;allocated+=st.st_blocks*512
    return dict(root_regular_unique_inode_logical_bytes=logical,root_regular_unique_inode_allocated_bytes=allocated,
                scope='cache root only, payload plus catalog and metadata; reference/read outputs and inference environment excluded')

def usage(d):return {k:v for k,v in d.api('info')['usage'].items() if k!='free_bytes'}

def wait(d,request,timeout=25):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        v=d.api('status',id=request)
        if v['request']['state']!='WAITING':return v
        time.sleep(.01)
    raise TimeoutError('No terminal request')

def fetch(d,request):
    start=time.monotonic();accepted=json.loads(d.cli('fetch',request,MODEL['id'],'--seconds','20').stdout);v=wait(d,request)
    require(v['request']['state']=='SUCCEEDED' and v['available'],'No verified success')
    return dict(seconds=time.monotonic()-start,accepted_deadline=accepted['request']['deadline'],final=v)

def read(d,name):
    path=d.work/(name+'.onnx');start=time.monotonic();r=d.cli('read',MODEL['sha256'],'--output',path)
    elapsed=time.monotonic()-start;require(hashlib.sha256(path.read_bytes()).hexdigest()==MODEL['sha256'] and path.stat().st_size==MODEL['size'],'CLI output differs')
    return path,dict(seconds=elapsed,cli_exit=r.returncode,size=path.stat().st_size,sha256=MODEL['sha256'])

def stop(d):
    d.stop();require(not d.socket.exists() and not (d.root/'ready.json').exists(),'Socket/hint leaked')
    return dict(exit_code=d.proc.returncode,socket_removed=True,ready_removed=True)

class ConnectTrace:
    def __init__(self,pid,path):self.pid=pid;self.path=path;self.proc=None
    def start(self):
        if sys.platform!='linux' or os.geteuid()!=0:return
        self.proc=subprocess.Popen(['strace','-f','-qq','-e','trace=connect','-o',str(self.path),'-p',str(self.pid)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        until=time.monotonic()+3
        while time.monotonic()<until:
            value=next(x for x in Path(f'/proc/{self.pid}/status').read_text().splitlines() if x.startswith('TracerPid:'))
            if int(value.split()[1])==self.proc.pid:return
            if self.proc.poll() is not None:break
            time.sleep(.01)
        self.proc.send_signal(signal.SIGINT);self.proc.communicate(timeout=5)
        raise RuntimeError('No actual syscall tracer attachment')
    def stop(self):
        if self.proc is None:return dict(tested=False,scope='Native macOS: closed origin and unchanged formal transfer counters only; no syscall trace')
        self.proc.send_signal(signal.SIGINT)
        _,stderr=self.proc.communicate(timeout=5)
        require(self.proc.returncode in (0,-signal.SIGINT),'Tracer exit: '+stderr.decode())
        text=self.path.read_text();require('connect(' not in text,'Offline cache access attempted a connection')
        return dict(tested=True,tracer_attached=True,daemon_pid=self.pid,tracer_pid=self.proc.pid,connect_syscalls=0,trace_sha256=hashlib.sha256(text.encode()).hexdigest(),tracer_exit=self.proc.returncode,
                    scope='Linux strace attached to formal daemon and its threads before offline fetch through CLI read')

def controlled(base,reference,cli,inference):
    base=Path(base).resolve();base.mkdir();os.chmod(base,0o755)
    body=Path(reference).read_bytes();require(len(body)==MODEL['size'] and hashlib.sha256(body).hexdigest()==MODEL['sha256'],'Reference differs from trust lock')
    origin=Origin(base/'tls',body);daemons=[];samplers=[];paths={};cases=[];closed=False
    def begin(name):
        d=ModelDaemon(base/name,origin,cli);daemons.append(d);d.start();sampler=Samples(d).start();samplers.append(sampler);return d,sampler
    try:
        d,s=begin('cold');f=fetch(d,'cold');path,r=read(d,'cold');paths['cold']=str(path)
        cold=dict(mode='cold',fetch=f,read=r,server_requests=list(origin.calls),server_body_write_bytes=origin.sent,
                  disk=disk(d.root),usage=usage(d),memory=s.stop(),cleanup=stop(d));cases.append(cold)
        origin.calls.clear();origin.sent=0;origin.mode='partial_hold';origin.arrived.clear();origin.release.clear()
        resumed,s=begin('resumed');old=json.loads(resumed.cli('fetch','old',MODEL['id'],'--seconds','20').stdout)
        require(origin.arrived.wait(3),'No actual held prefix')
        until=time.monotonic()+1.5
        while time.monotonic()<until:
            partial=resumed.api('status',id='old')
            if partial['object']['offset']==262144:break
            time.sleep(.01)
        require(partial['request']['state']=='WAITING' and partial['object']['offset']==262144,'No complete intended partial checkpoint')
        before_memory=s.stop();resumed.stop(crash=True)
        with sqlite3.connect('file:'+str(resumed.root/'catalog.db')+'?mode=ro',uri=True) as db:
            before=dict(zip(('state','deadline','epoch'),db.execute("SELECT state,deadline,epoch FROM requests WHERE id='old'").fetchone()))
        require(before['state']=='WAITING' and before['deadline']==old['request']['deadline'],'No retained journal')
        origin.release.set();origin.mode='normal';start=time.monotonic();new_info=resumed.start();restart_seconds=time.monotonic()-start
        recovered=resumed.api('status',id='old');require(recovered['request']['state']=='INTERRUPTED' and recovered['request']['deadline']==before['deadline'] and new_info['epoch']!=old['epoch'],'Old authority revived')
        require(len(origin.calls)==1,'Automatic network continuation')
        stale=False
        try:resumed.api('fetch',id='stale',artifact=MODEL['id'],seconds=3,epoch=old['epoch'])
        except CacheError:stale=True
        require(stale,'Stale epoch accepted')
        sampler=Samples(resumed).start();samplers.append(sampler);start=time.monotonic()
        resumed.cli('resume','new',MODEL['id'],'--source','old','--seconds','20');final=wait(resumed,'new')
        resume_seconds=time.monotonic()-start;require(final['request']['state']=='SUCCEEDED','Resume failed')
        path,r=read(resumed,'resumed');paths['resumed']=str(path)
        require(len(origin.calls)==2 and origin.calls[1]['range']=='bytes=262144-' and origin.calls[1]['if_range']==origin.etag and origin.calls[1]['status']==206,'Not a real strong-validator range resume')
        require(origin.calls[1]['successful_body_write_bytes']==MODEL['size']-262144 and origin.sent==MODEL['size'],'Resume did not save body bytes')
        cases.append(dict(mode='resumed',before_crash=before,checkpoint=partial['object']['offset'],crash_exit=-9,
            recovered_old=recovered['request'],old_epoch_rejected=stale,no_automatic_get=True,restart_seconds=restart_seconds,
            resume_fetch_seconds=resume_seconds,final=final,read=r,server_requests=list(origin.calls),server_body_write_bytes=origin.sent,
            memory_before_crash=before_memory,memory=sampler.stop(),disk=disk(resumed.root),usage=usage(resumed),cleanup=stop(resumed)))
        # The origin is fully shut before the cold cache is restarted for offline access.
        calls=len(origin.calls);origin.close();closed=True
        try:
            with socket.create_connection(('127.0.0.1',origin.server.server_port),timeout=.3):raise RuntimeError('Origin still reachable')
        except ConnectionRefusedError:pass
        d.start();sampler=Samples(d).start();samplers.append(sampler)
        before=d.api('status',id='cold')['object'];trace=ConnectTrace(d.proc.pid,base/'offline.trace');trace.start()
        try:
            f=fetch(d,'offline');path,r=read(d,'offline');paths['offline']=str(path)
        finally:traced=trace.stop()
        after=d.api('status',id='offline')['object']
        require(before['attempts']==after['attempts'] and before['wire']==after['wire'] and len(origin.calls)==calls,'Offline access used transfer budget')
        cases.append(dict(mode='offline',fetch=f,read=r,origin_closed=True,origin_connection_refused=True,transfer_attempts_delta=after['attempts']-before['attempts'],
            accepted_body_bytes_delta=after['wire']-before['wire'],trace=traced,memory=sampler.stop(),disk=disk(d.root),usage=usage(d),cleanup=stop(d)))
        # Independent root and real TLS source with one altered byte, same advertised size/digest.
        corrupt=Origin(base/'corrupt-tls',body);corrupt.mode='corrupt'
        try:
            bad=ModelDaemon(base/'corrupt',corrupt,cli);bad.value['attempts']=1;bad.write();daemons.append(bad);bad.start();sampler=Samples(bad).start();samplers.append(sampler)
            bad.cli('fetch','bad',MODEL['id'],'--seconds','20');failure=wait(bad,'bad')
            output=bad.work/'denied.onnx';rejected=bad.cli('read',MODEL['sha256'],'--output',output,check=False)
            published=(bad.root/'objects'/MODEL['sha256']).exists()
            stage=bad.root/'staging'/failure['object']['generation']
            staged_sha=hashlib.sha256(stage.read_bytes()).hexdigest()
            altered_sha=hashlib.sha256(bytes([body[0]^1])+body[1:]).hexdigest()
            require(failure['object']['offset']==failure['object']['wire']==stage.stat().st_size==MODEL['size'] and failure['object']['attempts']==1 and staged_sha==altered_sha and staged_sha!=MODEL['sha256'],'Corrupt test failed before full-body integrity verification')
            require(failure['request']['state']=='FAILED' and not failure['available'] and rejected.returncode!=0 and not output.exists() and not published,'Corrupt bytes published')
            cases.append(dict(mode='corrupt',state=failure['request']['state'],available=False,read_exit=rejected.returncode,
                output_exists=False,published=False,received_body_bytes=failure['object']['offset'],accepted_body_bytes=failure['object']['wire'],staged_sha256=staged_sha,attempts=failure['object']['attempts'],server_body_write_bytes=corrupt.sent,memory=sampler.stop(),disk=disk(bad.root),cleanup=stop(bad)))
        finally:corrupt.close()
        inference_result=base/'inference.json'
        # Invoke script by path; this process has no Cacheweft runtime dependencies installed.
        subprocess.run([str(inference),str(Path(__file__).with_name('infer.py')),'--reference',str(Path(reference).resolve()),'--paths',json.dumps(paths),'--output',str(inference_result)],check=True,capture_output=True,text=True,timeout=40)
        return dict(platform=platform.system(),reference_sha256=MODEL['sha256'],cases=cases,inference=json.loads(inference_result.read_text()),
                    sample_scope='Controlled loopback TLS; model bytes independently provisioned from pinned upstream; no WAN speedup claim')
    finally:
        errors=[]
        for s in samplers:
            try:
                if s.thread.is_alive():s.stop()
            except Exception as exc:errors.append(type(exc).__name__)
        for d in daemons:
            try:d.stop()
            except Exception as exc:errors.append(type(exc).__name__)
        try:
            if not closed:origin.close()
        finally:
            if errors:raise RuntimeError('Model experiment cleanup failed: '+','.join(errors))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',required=True);p.add_argument('--reference',required=True);p.add_argument('--cli',required=True);p.add_argument('--inference-python',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    v=controlled(a.work,a.reference,a.cli,a.inference_python);Path(a.output).write_text(json.dumps(v,indent=2)+'\n')
