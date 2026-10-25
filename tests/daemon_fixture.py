"""Actual daemon process and CLI observer, not a replacement runtime."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from cacheweft.cli import call
from cacheweft.service import token
from .fixture import DATA


def require(value,message):
    if not value:raise RuntimeError(message)


class Daemon:
    def __init__(self,work,origin,reuse=False):
        self.work=Path(work);self.work.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.root=self.work/'cache';self.socket=self.root/'control.sock';self.configuration=self.work/'config.json'
        self.origin=origin;self.proc=None;self.logs=[]
        self.digest=hashlib.sha256(DATA).hexdigest()
        self.value=dict(revision=1,origin=origin.origin,address='127.0.0.1',cafile=origin.cert,
                        entries=[dict(id='fixture-v1',url=origin.url,size=len(DATA),sha256=self.digest)])
        self.uid=65534 if sys.platform=='linux' and os.geteuid()==0 else os.geteuid()
        if self.uid!=os.geteuid():os.chown(self.work,self.uid,self.uid)
        if not reuse:self.write()

    def write(self):
        self.configuration.write_text(json.dumps(self.value));os.chmod(self.configuration,0o600)
        if self.uid!=os.geteuid():os.chown(self.configuration,self.uid,self.uid)

    def argv(self):
        args=[sys.executable,'-m','cacheweft.cli','serve','--root',str(self.root),'--config',str(self.configuration)]
        if self.uid!=os.geteuid():args=['setpriv','--reuid='+str(self.uid),'--regid='+str(self.uid),'--clear-groups','--no-new-privs',*args]
        return args

    def start(self):
        log=open(self.work/('daemon-'+str(len(self.logs))+'.log'),'wb');self.logs.append(log.name)
        self.proc=subprocess.Popen(self.argv(),stdout=log,stderr=log,start_new_session=True);log.close()
        until=time.monotonic()+5
        while time.monotonic()<until:
            if self.proc.poll() is not None:raise RuntimeError('Daemon exited: '+Path(self.logs[-1]).read_text())
            try:return self.api('info')
            except (OSError,RuntimeError):time.sleep(.02)
        raise TimeoutError('Daemon not usable')

    def api(self,op,**fields):return call(self.socket,dict(op=op,**fields))
    def submit(self,r,seconds=3):return self.api('fetch',id=r,artifact='fixture-v1',seconds=seconds,epoch=self.api('info')['epoch'])
    def terminal(self,r,timeout=5):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            v=self.api('status',id=r)
            if v['request']['state']!='WAITING':return v
            time.sleep(.01)
        raise TimeoutError('No terminal request result')

    def cli(self,*args,check=True):
        r=subprocess.run([sys.executable,'-m','cacheweft.cli','--socket',str(self.socket),*args],capture_output=True,text=True,timeout=5)
        if check and r.returncode:raise RuntimeError('CLI failed: '+r.stderr)
        return r

    def stop(self,crash=False):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid,signal.SIGKILL if crash else signal.SIGTERM)
            try:self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid,signal.SIGKILL);self.proc.wait(2);raise
            expected=-signal.SIGKILL if crash else 0
            require(self.proc.returncode==expected,'Daemon exit code wrong')

    def identity(self):
        if sys.platform!='linux':
            rss=int(subprocess.check_output(['ps','-o','rss=','-p',str(self.proc.pid)]).strip())*1024
            uid=int(subprocess.check_output(['ps','-o','uid=','-p',str(self.proc.pid)]).strip())
            require(uid==self.uid and uid!=0,'Not unprivileged')
            return dict(uid=uid,rss_bytes=rss,pid=self.proc.pid,scope='daemon PID current RSS via ps')
        values={}
        for line in Path('/proc/'+str(self.proc.pid)+'/status').read_text().splitlines():
            if line.split(':')[0] in ('Uid','CapEff','NoNewPrivs','VmRSS','FDSize'):values[line.split(':')[0]]=line.split(':',1)[1].strip()
        values['pid']=self.proc.pid
        values['fd_count']=len(list(Path('/proc/'+str(self.proc.pid)+'/fd').iterdir()))
        values['rss_bytes']=int(values['VmRSS'].split()[0])*1024
        require([int(x) for x in values['Uid'].split()]==[self.uid]*4 and self.uid!=0,'Not unprivileged')
        require(int(values['CapEff'],16)==0,'Unexpected capability')
        require('cacheweft.cli' in Path('/proc/'+str(self.proc.pid)+'/cmdline').read_bytes().decode(),'Wrong process')
        return values
