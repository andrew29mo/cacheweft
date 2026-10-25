"""Fixed pre-measurement closeout contract and rejection controls."""
import copy
import html
import json
import math
from pathlib import Path
import statistics
from tools.report import INPUTS
from tools.deployment_report import EXTRA,TESTS as PRIOR_TESTS

POLICY=json.loads(Path(__file__).with_name('contract.json').read_text())
FILES=INPUTS|EXTRA|{'closeout/'+n for n in ('__init__.py','contract.json','origin.py','scenario.py','report.py','validate.py')}
TESTS=sorted((*PRIOR_TESTS,*('tests.test_core.CoreTests.'+n for n in ('test_completed_generation_leases_are_reclaimed','test_retired_callback_preserves_new_generation_lease','test_retired_callback_preserves_same_generation_resume'))))
STAGES={'initial','create','dependencies','upload','install','provenance','native','linux','linux-read','closeout','closeout-read','delete','final'}

def need(value,message):
    if not value:raise ValueError(message)

def verify(v):
    need(v['contract']=='cacheweft-closeout-v2' and v['passed'] is True and set(v['source'])==FILES and v['source_unchanged'] is True,'Source or result')
    runtime={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')};need(v['installed_modules']==v['observer_modules']==runtime,'Installed runtime differs')
    names=[s['name'] for s in v['stages']];need(STAGES<=set(names) and len(names)==len(set(names)),'Missing stages')
    for s in v['stages']:need(s['exit_code']==0 and s['timed_out'] is False and math.isfinite(s['seconds']) and s['seconds']>=0,'Failed stage')
    for key,platform in [('native','Darwin'),('linux','Linux')]:
        t=v[key];need(t['passed'] is True and t['platform']==platform and t['tests']==74 and t['test_ids']==TESTS and t['failures']==t['errors']==t['skipped']==0 and t['modules']==runtime,'Incorrect test cohort')
    c=v['closeout'];need(c['contract']==POLICY and c['failures']==[],'Policy changed or failures hidden')
    fresh=c['clean_install'];need(fresh['account_absent'] is True and fresh['observer_venv_separate'] is True and fresh['absent_paths']==dict.fromkeys(['/opt/cacheweft/venv','/etc/systemd/system/cacheweft.service','/etc/cacheweft/config.json','/var/lib/cacheweft'],True),'Not a clean install')
    need(c['installed_modules']==runtime,'Wrong official service runtime')
    ident=c['installed_identity'];need(ident['uid']>0 and int(ident['cap_eff'],16)==0 and ident['no_new_privs']=='1','Privileges')
    need(c['installed_unit_sha256']==v['source']['deploy/cacheweft.service'],'Wrong installed unit')
    book=c['runbook'];need(all(book[k] is True for k in ('quota_refused_without_deletion','request_slot_refused','cancellation_under_full_worker_budget')),'Runbook did not exercise limits')
    s=c['soak'];need(s['seconds']>=POLICY['duration_seconds'] and s['cycles']==POLICY['cycles'] and s['sampler_errors']==[],'Short soak or sampling failure')
    need(s['before']['MainPID']==s['after']['MainPID']==str(s['pid']) and s['before']['InvocationID']==s['after']['InvocationID'] and s['before']['NRestarts']==s['after']['NRestarts']=='0','Restart hid growth')
    cycles=c['cycles'];need([x['index'] for x in cycles]==list(range(POLICY['cycles'])),'Missing cycles')
    for x in cycles:need(x['requests']==4 and x['partial_offset']>0 and x['old_deadline_unchanged'] is True and x['states']==['SUCCEEDED','TIMED_OUT','SUCCEEDED','CANCELLED'] and 0<=x['start_lag_seconds']<=POLICY['max_schedule_lag_seconds'],'Cycle missing actual work')
    samples=c['samples'];need(len(samples)>=600 and all(x['pid']==s['pid'] for x in samples) and len({x['start_ticks'] for x in samples})==1,'Invalid PID/resource trace')
    for field,absolute,growth in [('rss','rss_absolute_bytes','rss_median_growth_bytes'),('fd','fd_absolute','fd_median_growth'),('threads','threads_absolute','threads_median_growth')]:
        early=statistics.median(x[field] for x in samples if POLICY['early_window'][0]<=x['seconds']<POLICY['early_window'][1])
        late=statistics.median(x[field] for x in samples if POLICY['late_window'][0]<=x['seconds']<POLICY['late_window'][1])
        need(s['early_medians'][field]==early and s['late_medians'][field]==late and late-early<=POLICY[growth] and max(x[field] for x in samples)<=POLICY[absolute],'Resource threshold changed or exceeded')
    windows=[statistics.median(x['rss'] for x in samples if a<=x['seconds']<a+30) for a in range(30,180,30)];streak=peak=0
    for a,b in zip(windows,windows[1:]):streak=streak+1 if b-a>POLICY['rss_window_step_bytes'] else 0;peak=max(peak,streak)
    need(s['rss_window_medians']==windows and s['largest_growing_streak']==peak<=POLICY['rss_max_growing_steps'],'Continuous RSS growth')
    need(max(x['disk']['metadata_allocated'] for x in samples)<=POLICY['metadata_allocated_bytes'],'Metadata allocation')
    need(s['history_after']['count']-s['history_before']['count']==4*POLICY['cycles'],'Request accounting')
    history=c['history_full'];need(history['full']['count']==history['gc_history_count']==history['after_restart']['count']==POLICY['records'] and history['after_restart']==history['full'] and history['actual_eviction'] is True and history['historical_result']=='SUCCEEDED' and history['historical_available'] is False,'History reset or only a no-op GC was tested')
    need(history['repair_limit']==POLICY['repaired_records'] and history['after_repair']['count']==POLICY['records']+1 and history['old_epoch_rejected'] is True,'History repair misrepresented')
    disk=c['disk_full'];need(disk['tmpfs_bytes']==POLICY['disk_fault_tmpfs_bytes'] and disk['free_bytes_at_injection']==POLICY['disk_fault_free_bytes'] and disk['checkpoint_before_fault']==262144 and disk['tracer_attached'] is True and disk['enospc_payload_syscalls'] and all('ENOSPC' in line and '/staging/' in line for line in disk['enospc_payload_syscalls']),'Not actual service payload ENOSPC')
    need(disk['state_under_fault']!='SUCCEEDED' and disk['repair_old_state']=='INTERRUPTED' and disk['new_request_state']=='SUCCEEDED','Disk failure falsely succeeded')
    need(all(disk[k] is True for k in ('no_ready_target','mutation_refused','deadline_unchanged','stale_epoch_rejected','mounts_removed','original_catalog_unchanged')),'Disk repair or cleanup')
    need(c['cleanup']==dict(main_pid='0',active_state='inactive',socket_removed=True,ready_removed=True,origin_handlers=0),'Service cleanup')
    need(all(v['cleanup'][k] is True for k in ('vm_deleted','disk_removed','preexisting_unchanged')),'VM cleanup')
    need(v['limits']==dict(vm_only_soak=True,long_term_tested=False,power_loss_tested=False,autostart_tested=False,rollover_implemented=False),'Scope expanded')

def controls(v):
    changes=[lambda x:x['native'].update(skipped=1),lambda x:x['linux']['test_ids'].__setitem__(0,'unrelated'),lambda x:x['stages'][0].update(timed_out=True),
        lambda x:x['stages'][0].update(exit_code=1),lambda x:x['installed_modules'].clear(),lambda x:x['closeout']['contract'].update(rss_absolute_bytes=2**40),
        lambda x:x['closeout']['soak'].update(seconds=20),lambda x:x['closeout']['soak']['after'].update(NRestarts='1'),lambda x:x['closeout']['cycles'].pop(),
        lambda x:x['closeout']['samples'][0].update(start_ticks='different'),lambda x:x['closeout']['soak']['late_medians'].update(rss=0),
        lambda x:x['closeout']['history_full'].update(gc_history_count=0),lambda x:x['closeout']['disk_full'].update(enospc_payload_syscalls=[]),
        lambda x:x['closeout']['disk_full'].update(state_under_fault='SUCCEEDED'),lambda x:x['cleanup'].update(disk_removed=False),
        lambda x:x['closeout']['failures'].append({'error':'hidden'}),lambda x:x['limits'].update(long_term_tested=True),
        lambda x:x['closeout']['clean_install'].update(account_absent=False),
        lambda x:x['closeout']['clean_install']['absent_paths'].update({'/opt/cacheweft/venv':False}),
        lambda x:x['closeout']['disk_full'].update(checkpoint_before_fault=0),
        lambda x:x['closeout']['history_full'].update(actual_eviction=False)]
    for change in changes:
        bad=copy.deepcopy(v);change(bad)
        try:verify(bad)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupt record accepted')
    return dict(all_rejected=True,rejected=len(changes))

def render(v):
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Cacheweft bounded closeout</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Cacheweft bounded closeout</h1><p>Passed: '+str(v.get('passed',False))+'</p><p>Official installed systemd service in a disposable Linux VM. Fixed 180 seconds / 90 cycles; same PID, epoch and invocation, no restart during soak. RSS/FD/threads are daemon-only, Linux VM measurements; metadata growth from retained request history is expected. A bounded observation is not proof of no leaks or long-term rollover. No power-loss or autostart qualification.</p><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
