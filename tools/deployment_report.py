"""Deployment-only evidence contract. Historical download benchmarks stay separate."""
import copy
import html
import json
import math
from .report import COHORT,INPUTS,ARTIFACT_SHA

EXTRA={'tools/deployment.py','tools/deployment_validate.py','tools/deployment_report.py','deploy/install.sh','deploy/cacheweft.service','deploy/test_notify.py'}
TESTS=sorted((*COHORT,*('deploy.test_notify.NotifyTests.'+n for n in ('test_ready_then_stopping_from_actual_daemon','test_failed_notify_is_failed_start_and_cleans_socket','test_no_notify_environment'))))
STAGES={'initial','create','dependencies','upload','install','provenance','native','linux','linux-read','deployment','deployment-read','delete','final'}

def need(v,message):
    if not v:raise ValueError(message)

def verify(v):
    need(v['contract']=='cacheweft-deployment-v1' and v['passed'] is True,'Not passed')
    need(set(v['source'])==INPUTS|EXTRA and v['source_unchanged'] is True,'Source set')
    need(all(len(h)==64 and all(c in '0123456789abcdef' for c in h) for h in v['source'].values()),'Source hashes')
    runtime={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')}
    need(v['installed_modules']==runtime,'Wheel modules')
    for platform,key in [('Darwin','native'),('Linux','linux')]:
        u=v[key];need(u['passed'] is True and u['platform']==platform and u['test_ids']==TESTS and u['tests']==71 and u['failures']==u['errors']==u['skipped']==0 and u['modules']==runtime,'Unit provenance/results')
    stages=v['stages'];names=[s['name'] for s in stages]
    need(STAGES<=set(names) and len(names)==len(set(names)),'Stages missing/duplicated')
    need(all(s['exit_code']==0 and s['timed_out'] is False and math.isfinite(s['seconds']) and s['seconds']>=0 for s in stages),'Failed stage')
    need(all(v['cleanup'][k] is True for k in ('vm_deleted','disk_removed','preexisting_unchanged')),'Cleanup')
    d=v['deployment']
    def ident(i):
        need(i['uid']>0 and int(i['cap_eff'],16)==0 and i['no_new_privs']=='1' and i['pid']>0,'Privileges')
        need(type(i['rss_bytes']) is int and i['peak_rss_bytes']>=i['rss_bytes']>0 and i['fd_count']>0,'Memory')
        p=i['properties'];need(p['User']==p['Group']=='cacheweft' and p['KillMode']=='control-group' and p['Restart']=='on-failure' and p['StartLimitBurst']=='3' and p['StartLimitIntervalUSec']=='30s' and p['RestartUSec']=='1s' and p['NoNewPrivileges']=='yes' and p['CapabilityBoundingSet']=='','Policy')
        need(p['FragmentPath']=='/etc/systemd/system/cacheweft.service','Unit path')
    ident(d['start']['identity']);c=d['crash_takeover'];ident(c['old_identity']);ident(c['new_identity'])
    need(c['old_identity']==d['start']['identity'] and c['old_identity']['pid']!=c['new_identity']['pid'] and c['old_identity']['properties']['InvocationID']!=c['new_identity']['properties']['InvocationID'],'No new invocation')
    need(c['journal_state_after_kill']=='WAITING' and c['old_state']=='INTERRUPTED' and c['resume_state']=='SUCCEEDED' and c['stale_rejections']==2 and c['cli_exit']==0 and c['read_sha256']==ARTIFACT_SHA,'Recovery')
    need(all(c[k] is True for k in ('deadline_unchanged','no_automatic_get','lock_inode_unchanged')),'Takeover fence')
    for x in (d['start']['seconds'],c['seconds_until_ready_rpc'],d['stop']['seconds']):need(math.isfinite(x) and 0<x<30,'Timing')
    stop=d['stop'];need(all(stop[k] is True for k in ('process_gone','cgroup_empty','socket_removed','ready_removed','lock_retained')) and stop['properties']['Result']=='success','Stop')
    limit=d['restart_limit'];ident(limit['repaired_identity'])
    need(limit['properties']['Result'] in ('start-limit-hit','exit-code') and limit['properties']['MainPID']=='0' and 1<=int(limit['properties']['NRestarts'])<=3 and limit['no_ready'] is True and limit['explicit_reset_failed'] is True and limit['interrupted_stop_request'] is True and limit['old_request_state']=='INTERRUPTED','Restart policy')
    w=d['limit_witness'];need(len(w['failed_client_pids'])==3 and len(set(w['failed_client_pids']))==3 and w['stable_properties']==limit['properties'] and math.isfinite(w['stable_seconds']) and 2.2<=w['stable_seconds']<10,'Restart stability')
    need(w['manager_events'] and all(e.get('_PID')=='1' and e.get('MESSAGE')=='cacheweft.service: Start request repeated too quickly.' for e in w['manager_events']),'No manager limit witness')
    need(d['final_cleanup']==dict(main_pid=0,socket_removed=True,ready_removed=True),'Final service cleanup')
    need(v['limits']==dict(vm_only_systemd=True,autostart_tested=False,vm_reboot_tested=False,power_loss_tested=False,boot_zero_gap_tested=False,wan_tested=False,long_term_tested=False),'Scope')

def controls(v):
    changes=[lambda x:x['native'].update(skipped=1),lambda x:x['linux'].update(test_ids=['other']*71),
        lambda x:x['stages'][0].update(timed_out=True),lambda x:x['stages'][0].update(exit_code=1),
        lambda x:x['installed_modules'].clear(),lambda x:x['source'].pop('deploy/cacheweft.service'),
        lambda x:x['deployment']['crash_takeover'].update(old_state='SUCCEEDED'),
        lambda x:x['deployment']['crash_takeover'].update(journal_state_after_kill='INTERRUPTED'),
        lambda x:x['deployment']['crash_takeover'].update(stale_rejections=0),
        lambda x:x['deployment']['stop'].update(cgroup_empty=False),
        lambda x:x['deployment']['restart_limit']['properties'].update(Result='success'),
        lambda x:x['deployment']['start']['identity'].update(uid=0),
        lambda x:x['deployment']['crash_takeover'].update(seconds_until_ready_rpc=float('nan')),
        lambda x:x['limits'].update(power_loss_tested=True),lambda x:x['cleanup'].update(disk_removed=False),
        lambda x:x['deployment']['limit_witness'].update(manager_events=[]),
        lambda x:x['deployment']['limit_witness'].update(failed_client_pids=[1,1,1]),
        lambda x:x['deployment']['limit_witness'].update(stable_seconds=0)]
    for change in changes:
        bad=copy.deepcopy(v);change(bad)
        try:verify(bad)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupt evidence accepted')
    return dict(rejected=len(changes),all_rejected=True)

def render(v):
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Cacheweft deployment</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Cacheweft deployment evidence</h1><p>Linux VM systemd service. Native macOS tests are separate. Timings and RSS are controlled observations, not guarantees. No autostart, VM reboot, power loss, boot zero gap, WAN or long-term qualification in this run.</p><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
