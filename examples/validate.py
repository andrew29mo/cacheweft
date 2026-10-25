"""Dedicated no-host-mount Linux VM; installed artifact cache plus separate inference."""
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from .report import FILES,verify,controls,render

ROOT=Path(__file__).resolve().parents[1]

def manifest():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sorted(FILES)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--colima-home');p.add_argument('--toolchain');p.add_argument('--profile');p.add_argument('--wheel');p.add_argument('--output');p.add_argument('--reference',required=True);p.add_argument('--provenance',required=True);p.add_argument('--runtime-cli',required=True);p.add_argument('--inference-python',required=True);a=p.parse_args()
    if not all((a.colima_home,a.toolchain,a.profile,a.wheel,a.output)):p.error('VM arguments required')
    if not a.profile.startswith('cw-exp-') or not all(c.isalnum() or c=='-' for c in a.profile):p.error('Dedicated cw-exp-* profile required')
    tool=Path(a.toolchain).resolve();home=Path(a.colima_home).resolve();wheel=Path(a.wheel).resolve();output=Path(a.output).resolve();output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):p.error('Fresh output required')
    reference=Path(a.reference).resolve();provenance=json.loads(Path(a.provenance).read_text());runtime_cli=Path(a.runtime_cli).absolute();inference_python=Path(a.inference_python).absolute()
    import tempfile
    temp=tempfile.TemporaryDirectory(prefix='cw-model-',dir='/tmp');work=Path(temp.name);work.chmod(0o755)
    logs=output/'private-logs';logs.mkdir();source=manifest();colima=tool/'colima'
    with zipfile.ZipFile(wheel) as z:runtime={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('cacheweft/') and n.endswith('.py')}
    if runtime!={k:h for k,h in source.items() if k.startswith('cacheweft/')}:p.error('Wheel/source mismatch')
    env=dict(os.environ,COLIMA_HOME=str(home),PATH=str(tool)+os.pathsep+os.environ['PATH'])
    v=dict(contract='cacheweft-real-model-v2',passed=False,recorded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source=source,wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),stages=[],host_mounts='none',reference=provenance,limits=dict(accuracy_tested=False,wan_trials=1,syscall_trace_linux_only=True,systemd_tested=False,power_loss_tested=False,long_term_tested=False))
    def persist():
        (output/'evidence.json').write_text(json.dumps(v,indent=2,allow_nan=False)+'\n');(output/'report.html').write_text(render(v))
    def run(name,args,timeout=60,stdin=None,cwd=ROOT):
        start=time.monotonic();proc=subprocess.Popen(list(map(str,args)),cwd=cwd,env=env,stdin=subprocess.PIPE if stdin is not None else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True);timed=False
        try:stdout,stderr=proc.communicate(stdin,timeout=timeout)
        except subprocess.TimeoutExpired:
            timed=True;os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate(timeout=5)
        except BaseException:
            os.killpg(proc.pid,signal.SIGKILL);proc.communicate(timeout=5);raise
        (logs/(name+'.log')).write_text(stdout+stderr)
        v['stages'].append(dict(name=name,exit_code=proc.returncode,timed_out=timed,seconds=time.monotonic()-start));persist()
        if timed or proc.returncode:raise RuntimeError(name+' failed or timed out')
        return stdout
    def ssh(name,args,**kw):return run(name,[colima,'--profile',a.profile,'ssh','--',*args],**kw)
    def guest(name,cmd,**kw):return ssh(name,['sudo','sh','-c','cd /opt/cacheweft/source && '+cmd],**kw)
    created=False;initial=[];disk=home/'_lima/_disks'/('colima-'+a.profile)
    try:
        initial=[json.loads(x) for x in run('initial',[colima,'list','--json']).splitlines()]
        if disk.exists() or any(x['name']==a.profile for x in initial):raise RuntimeError('Existing profile refused')
        created=True
        run('create',[colima,'start',a.profile,'--cpu','2','--memory','2','--disk','15','--mount','none','--activate=false','--ssh-config=false','--hostname','cacheweft-exp','--dns-host','cacheweft-exp=127.0.0.1','--dns-host','lima-colima-'+a.profile+'=127.0.0.1'],timeout=300)
        ssh('dependencies',['sudo','sh','-c','apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv openssl util-linux iproute2 strace'],timeout=300)
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:gz') as tar:
            for name in source:tar.add(ROOT/name,arcname='source/'+name,recursive=False)
            tar.add(wheel,arcname=wheel.name,recursive=False)
            tar.add(reference,arcname="reference.onnx",recursive=False)
        code='import sys,base64,io,tarfile,pathlib;p=pathlib.Path("/opt/cacheweft");p.mkdir();tarfile.open(fileobj=io.BytesIO(base64.b64decode(sys.stdin.read()))).extractall(p,filter="data")'
        ssh('upload',['sudo','python3','-c',code],stdin=base64.b64encode(buf.getvalue()).decode())
        guest('install','python3 -m venv /opt/cacheweft/venv && /opt/cacheweft/venv/bin/pip install --no-index --no-deps /opt/cacheweft/'+wheel.name+' && mv cacheweft /opt/cacheweft/retired-runtime && python3 -m venv /opt/cacheweft/inference && /opt/cacheweft/inference/bin/pip install --only-binary=:all: -r examples/requirements.txt',timeout=180)
        code='import cacheweft,hashlib,pathlib,json,importlib.util;assert importlib.util.find_spec("onnxruntime") is None;assert importlib.util.find_spec("numpy") is None;p=pathlib.Path(cacheweft.__file__).parent;print(json.dumps({"cacheweft/"+f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob("*.py")}))'
        v['installed_modules']=json.loads(guest('provenance',"/opt/cacheweft/venv/bin/python -c '"+code+"'"))
        if v['installed_modules']!=runtime:raise RuntimeError('Installed mismatch')
        v['native_installed_modules']=json.loads(run('native-provenance',[runtime_cli.parent/'python','-c',code],cwd=work))
        run('native',[sys.executable,'-m','tools.deployment_validate','--unit-output',output/'native.json'],timeout=120)
        v['native']=json.loads((output/'native.json').read_text())
        run('native-model',[sys.executable,'-m','examples.demo','--work',work/'cases','--reference',reference,'--cli',runtime_cli,'--inference-python',inference_python,'--output',output/'native-model.json'],timeout=90)
        v['native_model']=json.loads((output/'native-model.json').read_text())
        run('native-wan',[sys.executable,'-m','examples.wan','--reference',reference,'--cli',runtime_cli,'--inference-python',inference_python,'--output',output/'wan.json'],timeout=180)
        v['wan']=json.loads((output/'wan.json').read_text())
        guest('linux','/opt/cacheweft/venv/bin/python -m tools.deployment_validate --unit-output /opt/cacheweft/linux.json',timeout=120)
        v['linux']=json.loads(ssh('linux-read',['sudo','cat','/opt/cacheweft/linux.json']))
        guest('linux-model','/opt/cacheweft/venv/bin/python -m examples.demo --work /tmp/cw-model-cases --reference /opt/cacheweft/reference.onnx --cli /opt/cacheweft/venv/bin/cacheweft --inference-python /opt/cacheweft/inference/bin/python --output /opt/cacheweft/model.json',timeout=90)
        v['linux_model']=json.loads(ssh('linux-model-read',['sudo','cat','/opt/cacheweft/model.json']))
        v['passed']=True
    except BaseException as exc:v['error']=type(exc).__name__+': '+str(exc).replace(str(ROOT),'<checkout>')
    finally:
        if created:
            try:
                run('delete',[colima,'delete',a.profile,'--data','--force'],timeout=120)
                if disk.exists():run('delete-disk',['env','LIMA_HOME='+str(home/'_lima'),tool/'limactl','--tty=false','disk','delete','colima-'+a.profile],timeout=30)
                final=[json.loads(x) for x in run('final',[colima,'list','--json']).splitlines()]
                v['cleanup']=dict(vm_deleted=not any(x['name']==a.profile for x in final),disk_removed=not disk.exists(),preexisting_unchanged=all(any(x['name']==y['name'] and x['status']==y['status'] for x in final) for y in initial))
            except BaseException as exc:v.update(passed=False,cleanup_error=type(exc).__name__)
        temp.cleanup()
        v.setdefault('cleanup',{})['native_work_removed']=not work.exists()
        v['source_unchanged']=manifest()==source
        if v['passed']:
            try:verify(v);v['controls']=controls(v)
            except Exception as exc:v.update(passed=False,verification_error=str(exc))
        persist()
    print(json.dumps({k:v.get(k) for k in ('passed','error','verification_error','cleanup_error')}));raise SystemExit(0 if v['passed'] else 1)

if __name__=='__main__':main()
