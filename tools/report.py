"""Fail-closed evidence contract and HTML generated from the same JSON."""
import copy
import html
import json


def verify(v):
    def need(value,message):
        if not value:raise ValueError(message)
    need(v.get('passed') is True,'Not passed')
    need(v.get('source_unchanged') is True,'Source changed')
    need(v.get('host_mounts')=='none','Host mount configuration')
    need(v.get('installed_modules')==v.get('native',{}).get('modules')==v.get('linux',{}).get('modules'),'Runtime provenance mismatch')
    need(v['installed_modules']=={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')},'Runtime/source mismatch')
    n,l=v['native'],v['linux']
    need(n['platform']=='Darwin' and l['platform']=='Linux','Platform mismatch')
    need(n['test_ids']==l['test_ids'] and len(set(n['test_ids']))==n['tests']==l['tests'] and n['tests']==37,'Test cohort mismatch')
    for t in (n,l):need(t['passed'] is True and t['errors']==t['failures']==t['skipped']==0,'Tests not clean')
    stages=v['stages'];need(len({x['name'] for x in stages})==len(stages),'Duplicate stages')
    for stage in stages:need(stage['exit_code']==0 and stage['timed_out'] is False,'Stage failure')
    for name in ('create','install','native','linux','linux-cases','reboot-prepare','reboot-finish','delete','final'):
        need(name in [s['name'] for s in stages],'Missing stage '+name)
    need(v['cleanup']['vm_deleted'] and v['cleanup']['disk_removed'] and v['cleanup']['preexisting_unchanged'],'Cleanup incomplete')
    boot=v['reboot'];need(boot['boot_changed'] and boot['catalog_unchanged_before_recovery'] and boot['recovered_correct'],'Reboot invalid')
    need(len(boot['boot_id_after'])==36 and boot['boot_id_after']!=boot['before']['boot_id'],'Boot identity unchanged')
    need(boot['states_before_recovery']==boot['before']['states'] and boot['before']['states']['objects'][0][2]=='PUBLISHING' and boot['before']['states']['requests'][0][2]=='WAITING','Pending state not preserved')
    need(boot['before']['target_absent'] and boot['before']['crash_exit']==73 and boot['before']['stage_sha256'],'Not a pending publication')
    faults=v['linux_cases']['storage'];need(faults['mount_removed'],'Mount left')
    need([x['mode'] for x in faults['cases']]==['enospc','readonly','sqlite_full'],'Missing storage cases')
    for x in faults['cases']:
        need(x['repair_correct'],'Repair failed')
        if x['mode']=='sqlite_full':need(x['sqlite_errorcode']==13 and x['degraded'],'Not SQLITE_FULL')
        else:
            need(x['rejected'] and x['published_before_repair'] is False,'Fault admitted')
            codes=[(e.get('errno'),e.get('sqlite_errorcode')) for e in x['actual_errors']]
            need(any((a==28 or b==13) if x['mode']=='enospc' else (a==30 or (b is not None and b&255==8)) for a,b in codes),'Wrong actual storage error')
    for env in ('native_cases','linux_cases'):
        m=v[env]['measurements'];need(len(m['trials'])==3,'Missing measurements')
        for t in m['trials']:
            need(t['correct'] and t['download_seconds']>0 and t['read_seconds']>0,'Invalid timing/correctness')
            need(t['size']==t['origin_payload_bytes']==1048576,'Unexpected payload accounting')
            if t['mode']=='coalesced':need(t['request_count']==4 and t['actual_origin_requests']==1,'Not coalesced')
            if t['mode']=='resume':need(t['request_ranges']==[None,'bytes=131072-'],'Not resumed')
    need(v['limits']==dict(power_loss_tested=False,wan_tested=False,deployed_service_tested=False,long_term_tested=False),'Limit changed')


def controls(value):
    changes=[lambda v:v['native'].update(skipped=1),lambda v:v['linux'].update(tests=0),
             lambda v:v['stages'][0].update(timed_out=True),lambda v:v['stages'][0].update(exit_code=1),
             lambda v:v['installed_modules'].clear(),lambda v:v['reboot'].update(catalog_unchanged_before_recovery=False),
             lambda v:v['reboot'].update(boot_changed=False),lambda v:v['cleanup'].update(disk_removed=False),
             lambda v:v['linux_cases']['storage']['cases'][2].update(sqlite_errorcode=0),
             lambda v:v['linux_cases']['measurements']['trials'][2].update(actual_origin_requests=4),
             lambda v:v['reboot'].update(boot_id_after=v['reboot']['before']['boot_id']),
             lambda v:v['reboot']['states_before_recovery']['objects'][0].__setitem__(2,'READY'),
             lambda v:v['source'].update({'cacheweft/core.py':'0'*64}),
             lambda v:v['native_cases']['measurements']['trials'][1].update(origin_payload_bytes=0)]
    for change in changes:
        v=copy.deepcopy(value);change(v)
        try:verify(v)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupted evidence accepted')
    return dict(rejected=len(changes),all_rejected=True)


def render(v):
    title='Cacheweft exploration evidence'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:20px}</style><h1>'+title+'</h1><p>Passed: '+str(v.get('passed',False))+'</p><p>Exploration core only. Linux VM metrics are separate from native macOS. No power-loss, WAN, long-term or deployed-service qualification.</p><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
