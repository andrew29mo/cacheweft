"""Fail-closed real-model record; inference and cache memory scopes remain separate."""
import copy
import html
import json
import math
from pathlib import Path
from tools.report import INPUTS
from tools.deployment_report import EXTRA,TESTS

FILES=INPUTS|EXTRA|{'examples/'+n for n in ('__init__.py','model.json','requirements.txt','prepare.py','origin.py','demo.py','infer.py','wan.py','report.py','validate.py')}
MODEL=json.loads(Path(__file__).with_name('model.json').read_text())
INPUT_SHA='c83ddde4b251e6fa2366412601f133aec1e80e2b33ea31236716c2eeef541d9d'
STAGES={'initial','create','dependencies','upload','install','provenance','native-provenance','native','native-model','native-wan','linux','linux-read','linux-model','linux-model-read','delete','final'}

def need(value,why):
    if not value:raise ValueError(why)

def seconds(x):need(type(x) in (int,float) and math.isfinite(x) and x>0,'Invalid measured duration')

def memory(m):
    need(m['pid']>0 and m['uid']>0 and m['daemon_pgid']==m['observer_pgid'],'RSS process binding')
    need(m['samples']>0 and 0<m['sampled_min_bytes']<=m['sampled_max_bytes'] and m['requested_interval_seconds']==.05,'RSS')
    need('daemon PID' in m['scope'] and 'inference' in m['scope'],'RSS scope')

def disk(d):
    need(d['root_regular_unique_inode_logical_bytes']>=0 and d['root_regular_unique_inode_allocated_bytes']>=0,'Disk')

def read(r):need(r['cli_exit']==0 and r['size']==MODEL['size'] and r['sha256']==MODEL['sha256'],'Wrong CLI output');seconds(r['seconds'])

def clean(c):need(c==dict(exit_code=0,socket_removed=True,ready_removed=True),'Daemon cleanup')

def inference(i,names):
    need(i['onnxruntime']=='1.31.0' and i['numpy']=='1.26.4' and i['input_shape']==[1,3,224,224] and i['input_sha256']==INPUT_SHA,'Reference tool/input')
    need([c['mode'] for c in i['cases']]==['reference',*names],'Inference cohort')
    base=i['cases'][0]['output_sha256']
    for c in i['cases']:
        need(c['model_sha256']==MODEL['sha256'] and c['output_shape']==[1,1000] and c['finite'] is True and c['providers']==['CPUExecutionProvider'],'Wrong model/output')
        need(c['output_sha256']==base and c['max_abs_error']==0 and c['rtol']==1e-5 and c['atol']==1e-6,'Inference mismatch')
        seconds(c['session_load_seconds']);need(len(c['run_seconds'])==5,'Inference timing count')
        for s in c['run_seconds']:seconds(s)

def success(f):
    seconds(f['seconds']);final=f['final'];need(final['request']['state']=='SUCCEEDED' and final['available'] is True and final['request']['deadline']==f['accepted_deadline'] and final['object']['digest']==MODEL['sha256'],'Unverified success/extended deadline')

def verify(v):
    need(v['contract']=='cacheweft-real-model-v1' and v['passed'] is True and set(v['source'])==FILES and v['source_unchanged'] is True,'Source/success')
    runtime={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')}
    need(v['installed_modules']==v['native_installed_modules']==runtime,'Runtime provenance')
    need(v['reference']['model']==MODEL and v['reference']['reference_sha256']==MODEL['sha256'] and v['reference']['reference_size']==MODEL['size'],'Reference provenance')
    expected=f'version https://git-lfs.github.com/spec/v1\noid sha256:{MODEL["sha256"]}\nsize {MODEL["size"]}\n'
    need(v['reference']['pointer']==expected and v['reference']['redirects_allowed'] is False and v['reference']['proxies_used'] is False,'Untrusted bootstrap')
    need(v['reference']['license_sha256']=='cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30' and v['reference']['card_sha256']=='37c9978227b7fc5c481342d81cf8b892febea6c5e342827f8b8247457500312d','License/card changed')
    names=[s['name'] for s in v['stages']];need(STAGES<=set(names) and len(names)==len(set(names)),'Missing stages')
    for s in v['stages']:need(s['exit_code']==0 and s['timed_out'] is False and math.isfinite(s['seconds']) and s['seconds']>=0,'Stage failed')
    for key,platform in [('native','Darwin'),('linux','Linux')]:
        u=v[key];need(u['passed'] is True and u['tests']==71 and u['test_ids']==TESTS and u['platform']==platform and u['failures']==u['errors']==u['skipped']==0 and u['modules']==runtime,'Formal test cohort')
        demo=v[key+'_model'];need(demo['platform']==platform and demo['reference_sha256']==MODEL['sha256'],'Model platform')
        need([c['mode'] for c in demo['cases']]==['cold','resumed','offline','corrupt'],'Model cases')
        cold,resume,offline,bad=demo['cases']
        for c in (cold,resume,offline):
            read(c['read']);memory(c['memory']);disk(c['disk']);clean(c['cleanup'])
            need(c['usage']['payload_bytes']==MODEL['size'] and c['usage']['remaining_reserved']==0 and c['usage']['budget']==16777216,'Payload accounting')
        success(cold['fetch']);success(offline['fetch'])
        need(cold['server_body_write_bytes']==MODEL['size'] and len(cold['server_requests'])==1 and cold['server_requests'][0]['range'] is None,'Not a cold fetch')
        need(resume['checkpoint']==262144 and resume['crash_exit']==-9 and resume['before_crash']['state']=='WAITING' and resume['recovered_old']['state']=='INTERRUPTED' and resume['recovered_old']['deadline']==resume['before_crash']['deadline'] and resume['recovered_old']['epoch']==resume['before_crash']['epoch'],'Restart extended work')
        need(resume['old_epoch_rejected'] is True and resume['no_automatic_get'] is True and resume['final']['request']['state']=='SUCCEEDED' and resume['final']['request']['epoch']!=resume['before_crash']['epoch'],'Stale resume')
        seconds(resume['restart_seconds']);seconds(resume['resume_fetch_seconds']);memory(resume['memory_before_crash'])
        requests=resume['server_requests'];need(len(requests)==2 and requests[1]['range']=='bytes=262144-' and requests[1]['status']==206 and requests[1]['if_range']=='"'+MODEL['sha256']+'"' and requests[1]['successful_body_write_bytes']==MODEL['size']-262144 and resume['server_body_write_bytes']==MODEL['size'],'False resume savings')
        need(offline['origin_closed'] is True and offline['origin_connection_refused'] is True and offline['transfer_attempts_delta']==offline['accepted_body_bytes_delta']==0,'Offline transfer attempted')
        if platform=='Linux':need(offline['trace']['tested'] is True and offline['trace']['tracer_attached'] is True and offline['trace']['daemon_pid']==offline['memory']['pid'] and offline['trace']['tracer_pid']>0 and offline['trace']['connect_syscalls']==0,'No actual offline trace')
        else:need(offline['trace']['tested'] is False,'False macOS syscall trace')
        need(bad['state']=='FAILED' and bad['available'] is False and bad['read_exit']!=0 and bad['published'] is False and bad['output_exists'] is False and bad['server_body_write_bytes']==bad['received_body_bytes']==bad['accepted_body_bytes']==MODEL['size'] and bad['attempts']==1 and bad['staged_sha256']=='44e35082ecf7e2c1efd1bd080934bd73d074c55226ac6b59e18b6f48e1530e14','Corruption accepted or incomplete')
        memory(bad['memory']);disk(bad['disk']);clean(bad['cleanup']);inference(demo['inference'],['cold','resumed','offline'])
    wan=v['wan'];seconds(wan['fetch_seconds']);read(wan['read']);memory(wan['memory']);disk(wan['disk']);clean(wan['cleanup']);inference(wan['inference'],['wan'])
    need(wan['url']==v['reference']['payload_url'] and wan['pinned_address'] in wan['provisioned_addresses'] and wan['final']['request']['state']=='SUCCEEDED' and wan['final']['available'] is True,'No real WAN success')
    need(all(v['cleanup'][k] is True for k in ('vm_deleted','disk_removed','preexisting_unchanged','native_work_removed')),'Cleanup')
    need(v['limits']==dict(accuracy_tested=False,wan_trials=1,syscall_trace_linux_only=True,systemd_tested=False,power_loss_tested=False,long_term_tested=False),'Scope')

def controls(v):
    changes=[lambda x:x['native'].update(skipped=1),lambda x:x['stages'][0].update(timed_out=True),lambda x:x['stages'][0].update(exit_code=1),
        lambda x:x['installed_modules'].clear(),lambda x:x['reference'].update(reference_sha256='0'*64),lambda x:x['reference'].update(pointer='not trusted'),
        lambda x:x['linux_model']['cases'][1].update(checkpoint=0),lambda x:x['native_model']['cases'][1].update(server_body_write_bytes=MODEL['size']*2),
        lambda x:x['linux_model']['cases'][1].update(old_epoch_rejected=False),lambda x:x['linux_model']['cases'][2]['trace'].update(tested=False),
        lambda x:x['native_model']['cases'][2].update(transfer_attempts_delta=1),lambda x:x['linux_model']['cases'][3].update(published=True),
        lambda x:x['native_model']['inference']['cases'][1].update(max_abs_error=1),lambda x:x['wan']['inference'].update(input_sha256='0'*64),
        lambda x:x['wan'].update(fetch_seconds=float('nan')),lambda x:x['cleanup'].update(native_work_removed=False),
        lambda x:x['limits'].update(accuracy_tested=True),lambda x:x['linux']['test_ids'].__setitem__(0,'unrelated.test'),
        lambda x:x['native_model']['cases'][0]['memory'].update(sampled_max_bytes=0),lambda x:x['source'].pop('examples/model.json'),
        lambda x:x['linux_model']['cases'][2]['trace'].update(tracer_attached=False),
        lambda x:x['native_model']['cases'][0]['memory'].update(daemon_pgid=-1),
        lambda x:x['linux_model']['cases'][3].update(received_body_bytes=0),
        lambda x:x['native_model']['cases'][3].update(staged_sha256=MODEL['sha256'])]
    for change in changes:
        bad=copy.deepcopy(v);change(bad)
        try:verify(bad)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupt evidence accepted')
    return dict(rejected=len(changes),all_rejected=True)

def render(v):
    rows=''
    for key,label in [('native_model','Native macOS loopback TLS'),('linux_model','Linux VM loopback TLS')]:
        for c in v.get(key,{}).get('cases',[]):
            elapsed=c.get('fetch',{}).get('seconds',c.get('resume_fetch_seconds'))
            rows+='<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in (label,c['mode'],round(elapsed,4) if elapsed else 'not timed',c.get('server_body_write_bytes','origin offline'),c['memory']['sampled_max_bytes']))+'</tr>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Cacheweft real model</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Cacheweft real-model demonstration</h1><p>Passed: '+str(v.get('passed',False))+'</p><p>Times include CLI startup and status polling. Resumed time excludes the prefix/crash/restart; it is not comparable to a full-download speedup. RSS is sampled daemon memory, excludes inference, and Linux VM is separate from native macOS. Body-write bytes exclude HTTP/TLS framing; offline attempts use formal counters and Linux syscall tracing. Synthetic input verifies model execution, not classification accuracy. WAN is one separate sample; no WAN speedup, power-loss or long-term claim.</p><table><tr><th>Environment</th><th>Mode</th><th>Fetch seconds</th><th>Server successful body-write bytes</th><th>Sampled max daemon RSS bytes</th></tr>'+rows+'</table><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
