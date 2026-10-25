"""Fresh no-host-mount VM, installed runtime, real faults and normal reboot."""
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
from .report import verify,controls,render

ROOT=Path(__file__).resolve().parents[1]


def manifest():
    paths=[ROOT/'pyproject.toml']
    for folder in ('cacheweft','tools','tests'):
        paths.extend(sorted((ROOT/folder).glob('*.py')))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    p=argparse.ArgumentParser();p.add_argument('--colima-home',required=True);p.add_argument('--toolchain',required=True)
    p.add_argument('--profile',required=True);p.add_argument('--wheel',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    if not a.profile.startswith('cw-exp-') or not all(c.isalnum() or c=='-' for c in a.profile):p.error('Dedicated cw-exp-* profile required')
    home=Path(a.colima_home).resolve();tool=Path(a.toolchain).resolve();colima=tool/'colima';wheel=Path(a.wheel).resolve()
    output=Path(a.output).resolve();output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):p.error('Fresh output required')
    logs=output/'private-logs';logs.mkdir();source=manifest()
    with zipfile.ZipFile(wheel) as z:
        payload={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('cacheweft/') and n.endswith('.py')}
    if payload!={k:v for k,v in source.items() if k.startswith('cacheweft/')}:p.error('Wheel/source mismatch')
    env=dict(os.environ,COLIMA_HOME=str(home),PATH=str(tool)+os.pathsep+os.environ['PATH'])
    v=dict(contract='cacheweft-milestone1-review-v5',passed=False,recorded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
           source=source,wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),stages=[],host_mounts='none',
           limits=dict(power_loss_tested=False,wan_tested=False,deployed_service_tested=True,systemd_tested=False,long_term_tested=False))
    def persist():
        (output/'evidence.json').write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
        (output/'report.html').write_text(render(v))
    def run(name,args,timeout=60,stdin=None):
        started=time.monotonic();proc=subprocess.Popen(list(map(str,args)),cwd=ROOT,env=env,stdin=subprocess.PIPE if stdin is not None else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        timed=False
        try:stdout,stderr=proc.communicate(stdin,timeout=timeout)
        except BaseException:
            timed=True;os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate(timeout=5)
        (logs/(name+'.log')).write_text(stdout+stderr)
        v['stages'].append(dict(name=name,exit_code=proc.returncode,timed_out=timed,seconds=time.monotonic()-started));persist()
        if timed or proc.returncode:raise RuntimeError(name+' failed or timed out')
        return stdout
    def ssh(name,args,**kw):return run(name,[colima,'--profile',a.profile,'ssh','--',*args],**kw)
    def guest(name,command,**kw):return ssh(name,['sudo','sh','-c','cd /opt/cacheweft/source && '+command],**kw)
    initial=[];created=False;disk=home/'_lima/_disks'/('colima-'+a.profile)
    try:
        initial=[json.loads(x) for x in run('initial',[colima,'list','--json']).splitlines()]
        if disk.exists() or any(x['name']==a.profile for x in initial):raise RuntimeError('Existing profile refused')
        created=True
        run('create',[colima,'start',a.profile,'--cpu','2','--memory','2','--disk','15','--mount','none','--activate=false','--ssh-config=false','--hostname','cacheweft-exp','--dns-host','cacheweft-exp=127.0.0.1','--dns-host','lima-colima-'+a.profile+'=127.0.0.1'],timeout=300)
        ssh('dependencies',['sudo','sh','-c','apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv openssl util-linux iproute2'],timeout=300)
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:gz') as tar:
            for name in source:tar.add(ROOT/name,arcname='source/'+name,recursive=False)
            tar.add(wheel,arcname=wheel.name,recursive=False)
        code='import sys,base64,io,tarfile,pathlib;p=pathlib.Path("/opt/cacheweft");p.mkdir();tarfile.open(fileobj=io.BytesIO(base64.b64decode(sys.stdin.read()))).extractall(p,filter="data")'
        ssh('upload',['sudo','python3','-c',code],stdin=base64.b64encode(buf.getvalue()).decode())
        guest('install','python3 -m venv /opt/cacheweft/venv && /opt/cacheweft/venv/bin/pip install --no-index --no-deps /opt/cacheweft/'+wheel.name+' && mv cacheweft /opt/cacheweft/retired-runtime')
        code='import cacheweft,hashlib,pathlib,json;root=pathlib.Path(cacheweft.__file__).parent;print(json.dumps({"cacheweft/"+p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.py")}))'
        v['installed_modules']=json.loads(guest('provenance',"/opt/cacheweft/venv/bin/python -c '"+code+"'"))
        if v['installed_modules']!=payload:raise RuntimeError('Installed runtime mismatch')
        run('native',[sys.executable,'-m','tools.unit','--output',output/'native.json'],timeout=120)
        v['native']=json.loads((output/'native.json').read_text())
        run('native-cases',[sys.executable,'-m','tools.cases','--output',output/'native-cases.json'],timeout=120)
        v['native_cases']=json.loads((output/'native-cases.json').read_text())
        run('native-service',[sys.executable,'-m','tools.milestone','cases','--output',output/'native-service.json'],timeout=60)
        v['native_service']=json.loads((output/'native-service.json').read_text())
        run('native-review',[sys.executable,'-m','tools.review_cases','--output',output/'native-review.json'],timeout=60)
        v['native_review']=json.loads((output/'native-review.json').read_text())
        guest('linux','/opt/cacheweft/venv/bin/python -m tools.unit --output /opt/cacheweft/linux.json',timeout=120)
        v['linux']=json.loads(ssh('linux-read',['sudo','cat','/opt/cacheweft/linux.json']))
        guest('linux-cases','/opt/cacheweft/venv/bin/python -m tools.cases --storage --output /opt/cacheweft/linux-cases.json',timeout=120)
        v['linux_cases']=json.loads(ssh('linux-cases-read',['sudo','cat','/opt/cacheweft/linux-cases.json']))
        guest('linux-service','/opt/cacheweft/venv/bin/python -m tools.milestone cases --output /opt/cacheweft/service.json',timeout=60)
        v['linux_service']=json.loads(ssh('linux-service-read',['sudo','cat','/opt/cacheweft/service.json']))
        guest('linux-review','/opt/cacheweft/venv/bin/python -m tools.review_cases --output /opt/cacheweft/review.json',timeout=60)
        v['linux_review']=json.loads(ssh('linux-review-read',['sudo','cat','/opt/cacheweft/review.json']))
        guest('reboot-prepare','/opt/cacheweft/venv/bin/python -m tools.reboot prepare',timeout=40)
        prepared=json.loads(ssh('reboot-prepared-read',['sudo','cat','/opt/cacheweft/reboot/prepared.json']))
        ssh('schedule-reboot',['sudo','systemd-run','--unit=cacheweft-test-reboot','--on-active=2s','--timer-property=AccuracySec=1s','/usr/bin/systemctl','reboot'])
        until=time.monotonic()+120;changed=False
        while time.monotonic()<until:
            try:
                r=subprocess.run([str(colima),'--profile',a.profile,'ssh','--','cat','/proc/sys/kernel/random/boot_id'],env=env,capture_output=True,text=True,timeout=5)
                if r.returncode==0 and len(r.stdout.strip())==36 and r.stdout.strip()!=prepared['boot_id']:changed=True;break
            except subprocess.TimeoutExpired:pass
            time.sleep(1)
        if not changed:raise TimeoutError('Boot identity did not change')
        guest('reboot-finish','/opt/cacheweft/venv/bin/python -m tools.reboot finish',timeout=40)
        v['reboot']=json.loads(ssh('reboot-finished-read',['sudo','cat','/opt/cacheweft/reboot/finished.json']))
        guest('service-boot-prepare','/opt/cacheweft/venv/bin/python -m tools.milestone prepare',timeout=40)
        prepared=json.loads(ssh('service-boot-prepared-read',['sudo','cat','/opt/cacheweft/milestone-boot/prepared.json']))
        ssh('service-schedule-reboot',['sudo','systemd-run','--unit=cacheweft-m1-reboot','--on-active=2s','--timer-property=AccuracySec=1s','/usr/bin/systemctl','reboot'])
        until=time.monotonic()+120;changed=False
        while time.monotonic()<until:
            try:
                r=subprocess.run([str(colima),'--profile',a.profile,'ssh','--','cat','/proc/sys/kernel/random/boot_id'],env=env,capture_output=True,text=True,timeout=5)
                if r.returncode==0 and len(r.stdout.strip())==36 and r.stdout.strip()!=prepared['boot_id']:changed=True;break
            except subprocess.TimeoutExpired:pass
            time.sleep(1)
        if not changed:raise TimeoutError('Service boot identity did not change')
        guest('service-boot-finish','/opt/cacheweft/venv/bin/python -m tools.milestone finish',timeout=40)
        v['service_reboot']=json.loads(ssh('service-boot-finished-read',['sudo','cat','/opt/cacheweft/milestone-boot/finished.json']))
        v['passed']=True
    except BaseException as exc:
        v['error']=type(exc).__name__+': '+str(exc).replace(str(ROOT),'<checkout>')
    finally:
        if created:
            try:
                run('delete',[colima,'delete',a.profile,'--data','--force'],timeout=120)
                if disk.exists():run('delete-disk',['env','LIMA_HOME='+str(home/'_lima'),tool/'limactl','--tty=false','disk','delete','colima-'+a.profile],timeout=30)
                final=[json.loads(x) for x in run('final',[colima,'list','--json']).splitlines()]
                v['cleanup']=dict(vm_deleted=not any(x['name']==a.profile for x in final),disk_removed=not disk.exists(),
                                  preexisting_unchanged=all(any(x['name']==y['name'] and x['status']==y['status'] for x in final) for y in initial))
            except BaseException as exc:v.update(passed=False,cleanup_error=type(exc).__name__)
        v['source_unchanged']=manifest()==source
        if v['passed']:
            try:verify(v);v['controls']=controls(v)
            except Exception as exc:v.update(passed=False,verification_error=str(exc))
        persist()
    print(json.dumps({k:v.get(k) for k in ('passed','error','verification_error','cleanup_error')}))
    raise SystemExit(0 if v['passed'] else 1)

if __name__=='__main__':main()
