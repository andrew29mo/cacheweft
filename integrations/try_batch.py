"""Native integration pilot with existing unmodified tiny-batch-service; no performance claim."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from examples.origin import Origin

ROOT=Path(__file__).resolve().parents[1]

def require(value,message):
    if not value:raise RuntimeError(message)

def run(args):
    cli=str(Path(args.cacheweft).absolute());consumer=Path(args.consumer).resolve();python=consumer/'.venv/bin/python';service=consumer/'.venv/bin/tiny-batch-service'
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=consumer,text=True).strip()
    require(not subprocess.check_output(['git','status','--porcelain'],cwd=consumer,text=True),'Consumer repository has user changes; do not silently use a different version')
    with tempfile.TemporaryDirectory(prefix='cw-app-',dir='/tmp') as tmp:
        base=Path(tmp);generated=base/'generated';record=dict(scope='Native local application integration pilot; controlled loopback TLS origin, not external user adoption or a performance/accuracy benchmark',consumer=dict(repository='https://github.com/andrew29mo/tiny-batch-service',commit=commit),cases=[],failures=[])
        record['platform']=dict(system=platform.system(),machine=platform.machine(),observer_python=platform.python_version())
        record['adapter_sources']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'integrations/launch_batch.py',ROOT/'integrations/try_batch.py')}
        installed_python=Path(cli).parent/'python'
        code='import cacheweft,pathlib,hashlib,json;p=pathlib.Path(cacheweft.__file__).parent;print(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob("*.py")}))'
        installed=json.loads(subprocess.check_output([str(installed_python),'-c',code],cwd='/tmp',text=True))
        expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'cacheweft').glob('*.py')}
        require(installed==expected,'Installed Cacheweft runtime differs from current source')
        record['cache_runtime_modules']=installed
        origin=None;daemon=None;services=[];log_files=[]
        def cache(*parts):
            r=subprocess.run([cli,'--socket',str(base/'cache/control.sock'),*map(str,parts)],capture_output=True,text=True,timeout=12)
            require(r.returncode==0,'Cache CLI failed: '+r.stderr);return json.loads(r.stdout)
        def start_cache():
            log=open(base/('cache-'+str(len(log_files))+'.log'),'wb');log_files.append(log.name)
            proc=subprocess.Popen([cli,'serve','--root',str(base/'cache'),'--config',str(base/'config.json')],stdout=log,stderr=log,start_new_session=True);log.close()
            until=time.monotonic()+5
            while time.monotonic()<until:
                require(proc.poll() is None,'Cache daemon exited')
                try:return proc,cache('info')
                except (OSError,RuntimeError):time.sleep(.02)
            stop(proc);raise TimeoutError('Cache daemon not ready')
        def stop(proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait(3);raise
            if proc:require(proc.returncode==0,'Unexpected process exit')
        def http(port,path,body=None):
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            with opener.open(request,timeout=5) as response:return json.loads(response.read())
        try:
            # Run the consumer's official producer, exporting only into this task-owned temp root.
            code='import importlib.util,sys,json;from pathlib import Path;p=Path(sys.argv[1]);s=importlib.util.spec_from_file_location("make_model",p/"tools/make_model.py");m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps(m.prepare(Path(sys.argv[2]))))'
            production=subprocess.run([str(python),'-c',code,str(consumer),str(generated)],capture_output=True,text=True,timeout=40)
            require(production.returncode==0,'Official model preparation failed: '+production.stderr)
            manifest=json.loads(production.stdout);body=(generated/'model.onnx').read_bytes();digest=hashlib.sha256(body).hexdigest();require(digest==manifest['model_sha256'],'Model/manifest mismatch')
            # Confirm actual CLI imports the unchanged consumer source at the recorded commit.
            code='import tiny_batch_service,pathlib,hashlib,json;p=pathlib.Path(tiny_batch_service.__file__).parent;print(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob("*.py")}))'
            modules=json.loads(subprocess.check_output([str(python),'-c',code],cwd='/tmp',text=True))
            for name,dig in modules.items():require(hashlib.sha256(subprocess.check_output(['git','show',commit+':src/tiny_batch_service/'+name],cwd=consumer)).hexdigest()==dig,'Installed consumer differs from Git commit')
            record['consumer'].update(modules=modules,model_sha256=digest,model_bytes=len(body),fixture_sha256=manifest['fixture_sha256'],producer_sha256=hashlib.sha256((consumer/'tools/make_model.py').read_bytes()).hexdigest())
            # The consumer environment is the only process that imports NumPy/reference arrays.
            code='import numpy as n,json,sys;x=n.load(sys.argv[1],allow_pickle=False);ids=[0,1,2,3,4,5,len(x["inputs"])-1];print(json.dumps({"rows":ids,"inputs":x["inputs"][ids].tolist(),"reference":x["reference"][ids].tolist()}))'
            fixture=json.loads(subprocess.check_output([str(python),'-c',code,str(generated/'fixture.npz')],text=True));origin=Origin(base/'tls',body)
            config=dict(revision=1,origin=origin.origin,address='127.0.0.1',cafile=origin.cert,entries=[dict(id='digits-v1',url=origin.url,size=len(body),sha256=digest)])
            (base/'config.json').write_text(json.dumps(config));(base/'config.json').chmod(0o600)
            daemon,first_info=start_cache();record['cache_initial_epoch']=first_info['epoch']
            def launch(mode,request_id,expected_digest,positive=True):
                listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
                path=base/(mode+'.log');log=open(path,'wb')
                argv=[sys.executable,str(ROOT/'integrations/launch_batch.py'),'--cacheweft',cli,'--socket',str(base/'cache/control.sock'),'--artifact','digits-v1','--digest',expected_digest,'--request-id',request_id,'--model-dir',str(base/mode),'--service',str(service),'--seconds','5','--socket-fd',str(listener.fileno())]
                proc=subprocess.Popen(argv,stdout=log,stderr=log,pass_fds=(listener.fileno(),),start_new_session=True);services.append(proc);log.close();listener.close()
                if not positive:
                    proc.wait(8);require(proc.returncode!=0,'Invalid preparation started the consumer')
                    return dict(mode=mode,exit_code=proc.returncode,consumer_not_started='verified_model_ready' not in path.read_text(),error=path.read_text().strip().splitlines()[-1])
                until=time.monotonic()+10;health=None
                while time.monotonic()<until:
                    require(proc.poll() is None,'Consumer exited: '+path.read_text())
                    try:
                        health=http(port,'/health')
                        if health['ready']:break
                    except (OSError,ValueError):time.sleep(.03)
                require(health and health['ready'] and health['worker']['loaded_model_sha256']==digest,'Wrong consumer model or not ready')
                def infer(i):return http(port,'/v1/infer',dict(id=mode+'_'+str(i),input=fixture['inputs'][i],timeout_ms=2000))
                with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:responses=list(pool.map(infer,range(7)))
                error=0
                for i,response in enumerate(responses):
                    require(response['http_status']==200 and response['id']==mode+'_'+str(i),'Request failed or reordered')
                    actual=response['probabilities'];expected=fixture['reference'][i]
                    require(len(actual)==len(expected)==10,'Wrong output shape')
                    for a,b in zip(actual,expected):require(abs(a-b)<=1e-5+1e-4*abs(b),'HTTP probabilities differ from independent reference');error=max(error,abs(a-b))
                stats=http(port,'/v1/stats');require(stats['occupancy']==0,'Physical compute did not drain')
                worker=health['worker']['pid'];stop(proc)
                until=time.monotonic()+3
                while time.monotonic()<until:
                    try:os.kill(worker,0)
                    except ProcessLookupError:break
                    time.sleep(.03)
                else:raise RuntimeError('Consumer worker survived shutdown')
                return dict(mode=mode,request_id=request_id,http_successes=7,test_rows=fixture['rows'],max_abs_probability_error=error,rtol=1e-4,atol=1e-5,loaded_model_sha256=health['worker']['loaded_model_sha256'],worker_pid=worker,service_exit=proc.returncode,worker_cleaned=True)
            cold=launch('cold','boot_cold',digest);record['cases'].append(cold);cold_status=cache('status','boot_cold');require(len(origin.calls)==1 and cold_status['object']['pin']==1,'Cold did not fetch/pin model')
            before=cold_status['object'];origin.close();origin=None
            stop(daemon);daemon,new_info=start_cache();require(new_info['epoch']!=first_info['epoch'],'No actual cache restart')
            offline=launch('offline','boot_offline',digest);after=cache('status','boot_offline')['object']
            require(before['attempts']==after['attempts'] and before['wire']==after['wire'],'Offline startup transferred bytes')
            offline.update(origin_stopped=True,cache_restarted=True,transfer_attempts_delta=0,accepted_body_bytes_delta=0);record['cases'].append(offline)
            wrong=launch('wrong_digest','boot_wrong','0'*64,False);require(wrong['consumer_not_started'] and 'digest differs' in wrong['error'],'Wrong digest failed for another reason');record['cases'].append(wrong)
            cache('pin',digest,'--value','false','--request','boot_offline');plan=cache('gc-plan');(base/'gc.json').write_text(json.dumps(plan));cache('gc-run','--plan',base/'gc.json')
            stale=launch('old_id_after_gc','boot_cold',digest,False);require(stale['consumer_not_started'] and 'content is unavailable' in stale['error'],'Historical success did not fail closed');record['cases'].append(stale)
            stop(daemon);require(not (base/'cache/control.sock').exists() and not (base/'cache/ready.json').exists(),'Cache IPC cleanup failed')
            record.update(passed=True,cache_final_epoch=new_info['epoch'],cleanup=dict(cache_exit=daemon.returncode,socket_removed=True,ready_removed=True,consumer_processes_exited=True),
                limits=dict(native_only=True,vm_tested=False,wan_origin_tested=False,accuracy_benchmark=False,performance_benchmark=False,external_users=False))
        except Exception as exc:
            record.update(passed=False);record['failures'].append(type(exc).__name__+': '+str(exc).replace(str(ROOT),'<cache-repository>').replace(str(consumer),'<consumer-repository>'));raise
        finally:
            errors=[]
            for proc in services:
                if proc.poll() is None:
                    try:stop(proc)
                    except Exception as exc:errors.append(type(exc).__name__)
            if daemon and daemon.poll() is None:
                try:stop(daemon)
                except Exception as exc:errors.append(type(exc).__name__)
            if origin:origin.close()
            if errors:record.update(passed=False);record['failures']+=errors
            Path(args.output).write_text(json.dumps(record,indent=2)+'\n')
    return record

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cacheweft',required=True);p.add_argument('--consumer',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    v=run(a);print(json.dumps(dict(passed=v['passed'],cases=len(v['cases']))));raise SystemExit(0 if v['passed'] else 1)
