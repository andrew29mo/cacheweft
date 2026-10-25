"""Fail-closed evidence contract and HTML generated from the same JSON."""
import copy
import html
import json
import math
import re

ARTIFACT_SHA="fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83"
INPUTS={'pyproject.toml','cacheweft/__init__.py','cacheweft/core.py','cacheweft/http.py','cacheweft/cli.py','cacheweft/service.py',
        'tools/__init__.py','tools/cases.py','tools/reboot.py','tools/report.py','tools/unit.py','tools/validate.py','tools/milestone.py','tools/review_cases.py',
        'tests/__init__.py','tests/fixture.py','tests/test_core.py','tests/test_service.py','tests/daemon_fixture.py','tests/review_fixture.py','tests/test_review.py'}


COHORT = (
    'tests.test_core.CoreTests.test_416_not_success',
    'tests.test_core.CoreTests.test_bad_range_refused',
    'tests.test_core.CoreTests.test_cancel_races_durable_publish_intent',
    'tests.test_core.CoreTests.test_cancel_sql_failure_no_success',
    'tests.test_core.CoreTests.test_capacity_refusal_deletes_nothing',
    'tests.test_core.CoreTests.test_changed_validator_restarts',
    'tests.test_core.CoreTests.test_close_keeps_lock_until_reader_closed',
    'tests.test_core.CoreTests.test_coalescing_cancel_one',
    'tests.test_core.CoreTests.test_complete_checkpoint_corruption_not_published',
    'tests.test_core.CoreTests.test_complete_checkpoint_needs_no_origin_or_attempt',
    'tests.test_core.CoreTests.test_complete_checkpoint_without_validator',
    'tests.test_core.CoreTests.test_crash_checkpoints_and_publish_windows',
    'tests.test_core.CoreTests.test_disk_error_retires_authority',
    'tests.test_core.CoreTests.test_encoded_body_refused',
    'tests.test_core.CoreTests.test_existing_target_not_overwritten',
    'tests.test_core.CoreTests.test_fixture_eof_range_is_416',
    'tests.test_core.CoreTests.test_gc_crash_recovery',
    'tests.test_core.CoreTests.test_gc_crash_retains_partial_deletion_ownership',
    'tests.test_core.CoreTests.test_gc_then_read_and_stale_generation',
    'tests.test_core.CoreTests.test_https_verified_content',
    'tests.test_core.CoreTests.test_late_chunk_after_last_cancel',
    'tests.test_core.CoreTests.test_local_quota_and_permission_errors_are_not_network_retries',
    'tests.test_core.CoreTests.test_lock_and_corrupt_on_restart',
    'tests.test_core.CoreTests.test_old_epoch_plan_and_pin_rejected',
    'tests.test_core.CoreTests.test_persistent_attempt_budget',
    'tests.test_core.CoreTests.test_pin_and_read_prevent_stale_gc_plan',
    'tests.test_core.CoreTests.test_publish_boundary_cancel_too_late',
    'tests.test_core.CoreTests.test_range_ignored_restarts',
    'tests.test_core.CoreTests.test_reader_and_job_limits',
    'tests.test_core.CoreTests.test_readonly_database_retires_authority',
    'tests.test_core.CoreTests.test_request_budget_and_conflict',
    'tests.test_core.CoreTests.test_reservation_not_oversold',
    'tests.test_core.CoreTests.test_resume_real_range',
    'tests.test_core.CoreTests.test_retired_download_cannot_block_new_generation',
    'tests.test_core.CoreTests.test_shorter_than_checkpoint_restarts',
    'tests.test_core.CoreTests.test_symlink_destination_never_overwritten',
    'tests.test_core.CoreTests.test_symlink_not_followed',
    'tests.test_core.CoreTests.test_tls_and_origin_restriction',
    'tests.test_core.CoreTests.test_unknown_data_charged_not_deleted',
    'tests.test_core.CoreTests.test_weak_or_absent_etag_restarts',
    'tests.test_core.CoreTests.test_wrong_hash_not_published',
    'tests.test_core.CoreTests.test_wrong_length_refused',
    'tests.test_review.DeadlineReviewTests.test_daemon_admission_commit_reader_cutoff',
    'tests.test_review.DeadlineReviewTests.test_delayed_validation_cannot_commit_after_cutoff',
    'tests.test_review.DeadlineReviewTests.test_publish_commit_reader_cutoff',
    'tests.test_review.DeadlineReviewTests.test_shared_begin_wait_only_live_member_succeeds',
    'tests.test_review.DeadlineReviewTests.test_shared_commit_wait_only_live_member_succeeds',
    'tests.test_service.ServiceTests.test_authority_wait_cannot_turn_expired_fetch_into_hit',
    'tests.test_service.ServiceTests.test_bad_frames_and_config_permissions',
    'tests.test_service.ServiceTests.test_cli_flow_and_read',
    'tests.test_service.ServiceTests.test_coalesced_old_deadline_not_extended',
    'tests.test_service.ServiceTests.test_committed_publication_recovery_preserves_original_deadline',
    'tests.test_service.ServiceTests.test_control_connection_limit',
    'tests.test_service.ServiceTests.test_deadline_check_survives_delayed_sweeper',
    'tests.test_service.ServiceTests.test_deadline_rechecked_after_verification',
    'tests.test_service.ServiceTests.test_deadline_rechecked_after_write_precharge',
    'tests.test_service.ServiceTests.test_duplicate_id_cannot_extend_duration',
    'tests.test_service.ServiceTests.test_managed_core_cannot_import_or_create_unbounded_interest',
    'tests.test_service.ServiceTests.test_manifest_immutable_and_revision_rollback',
    'tests.test_service.ServiceTests.test_queued_job_expires_without_source_request',
    'tests.test_service.ServiceTests.test_restart_interrupts_old_deadline_and_requires_explicit_resume',
    'tests.test_service.ServiceTests.test_shutdown_rejects_preaccepted_incomplete_request',
    'tests.test_service.ServiceTests.test_slow_body_total_deadline',
    'tests.test_service.ServiceTests.test_slow_headers_total_deadline',
    'tests.test_service.ServiceTests.test_slow_source_cancel_and_stale_token',
    'tests.test_service.ServiceTests.test_sqlite_admission_wait_cannot_accept_expired_hit',
    'tests.test_service.ServiceTests.test_sqlite_publish_wait_cannot_create_expired_intent',
    'tests.test_service.ServiceTests.test_watchdog_lives_through_connection_close_response_body',
)


def verify(v):
    def need(value,message):
        if not value:raise ValueError(message)
    def number(value,positive=False):
        return type(value) in (int,float) and math.isfinite(value) and (value>0 if positive else value>=0)
    def integer(value,positive=False):
        return type(value) is int and (value>0 if positive else value>=0)
    def sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
    need(v.get('contract')=='cacheweft-milestone1-review-v5','Contract mismatch')
    need(v.get('passed') is True and not v.get('error') and not v.get('verification_error') and not v.get('cleanup_error'),'Not cleanly passed')
    need(v.get('source_unchanged') is True and v.get('host_mounts')=='none','Source/mount configuration')
    expected={k for k in INPUTS if k.startswith('cacheweft/')}
    need(set(v['source'])==INPUTS,'Missing frozen inputs')
    need(set(v['installed_modules'])==expected and all(sha(h) for h in v['source'].values()) and sha(v['wheel_sha256']),'Missing source provenance')
    need(v['installed_modules']==v['native']['modules']==v['linux']['modules'],'Runtime provenance mismatch')
    need(v['installed_modules']=={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')},'Runtime/source mismatch')
    n,l=v['native'],v['linux']
    need(n['platform']=='Darwin' and l['platform']=='Linux','Platform mismatch')
    need(n['test_ids']==l['test_ids']==list(COHORT) and len(set(n['test_ids']))==n['tests']==l['tests']==len(COHORT),'Test cohort mismatch')
    for t in (n,l):
        need(t['passed'] is True and t['errors']==t['failures']==t['skipped']==0 and number(t['seconds'],True),'Tests not clean')
    stages=v['stages'];need(len({x['name'] for x in stages})==len(stages),'Duplicate stages')
    for stage in stages:
        need(type(stage['exit_code']) is int and stage['exit_code']==0 and stage['timed_out'] is False and number(stage['seconds']),'Stage failure')
    for name in ('initial','create','dependencies','upload','install','provenance','native','native-cases','linux','linux-read','linux-cases','linux-cases-read','reboot-prepare','reboot-prepared-read','schedule-reboot','reboot-finish','reboot-finished-read','delete','final'):
        need(name in [s['name'] for s in stages],'Missing stage '+name)
    for name in ('native-service','linux-service','linux-service-read','service-boot-prepare','service-boot-prepared-read','service-schedule-reboot','service-boot-finish','service-boot-finished-read'):
        need(name in [s['name'] for s in stages],'Missing service stage '+name)
    for name in ('native-review','linux-review','linux-review-read'):
        need(name in [s['name'] for s in stages],'Missing review stage '+name)
    need(all(v['cleanup'][key] is True for key in ('vm_deleted','disk_removed','preexisting_unchanged')),'Cleanup incomplete')
    boot=v['reboot'];before=boot['before']
    need(boot['boot_changed'] is True and boot['catalog_unchanged_before_recovery'] is True and boot['recovered_correct'] is True,'Reboot invalid')
    need(len(boot['boot_id_after'])==len(before['boot_id'])==36 and boot['boot_id_after']!=before['boot_id'],'Boot identity unchanged')
    need(sha(before['catalog_sha256']) and boot['catalog_sha256_before_recovery']==before['catalog_sha256'],'Catalog witness differs')
    need(boot['stage_sha256_before_recovery']==before['stage_sha256']==[ARTIFACT_SHA],'Staging witness differs')
    need(boot['states_before_recovery']==before['states'],'Pending state not preserved')
    objects,requests=before['states']['objects'],before['states']['requests']
    need(len(objects)==len(requests)==1,'Boot cohort mismatch')
    obj,req=objects[0],requests[0]
    need(obj[0]==ARTIFACT_SHA and obj[2:] == ['PUBLISHING',1048576] and req==['reboot_request',obj[1],'WAITING'],'Not an unfinished publication')
    need(before['target_absent'] is True and before['crash_exit']==73 and boot['normal_reboot'] is True and boot['power_loss_tested'] is False,'Boot scope mismatch')
    recovered=boot['recovered_request']
    need(recovered['id']==req[0] and recovered['generation']==obj[1] and recovered['digest']==obj[0] and recovered['state']==recovered['result']=='SUCCEEDED','Wrong recovered request')
    faults=v['linux_cases']['storage'];need(faults['mount_removed'] is True,'Mount left')
    need([x['mode'] for x in faults['cases']]==['enospc','readonly','sqlite_full_admission','sqlite_full_publish'],'Missing storage cases')
    for x in faults['cases']:
        need(x['repair_correct'] is True and x['rejected'] is True,'Fault repair/admission failed')
        if x['mode']=='sqlite_full_admission':
            need(x['sqlite_errorcode']==13 and x['degraded'] is True,'Not admission SQLITE_FULL')
        else:
            need(x['published_before_repair'] is False,'Fault published')
            codes=[(e.get('errno'),e.get('sqlite_errorcode')) for e in x['actual_errors']]
            if x['mode']=='sqlite_full_publish':
                need(any(b==13 for a,b in codes) and x['degraded'] is True,'Not publish SQLITE_FULL')
                need(x['state_before_repair']==dict(object='VERIFYING',request='WAITING') and x['repair_origin_requests']==0,'Pending repair differs')
            else:
                need(any((a==28 or b==13) if x['mode']=='enospc' else (a==30 or (b is not None and b&255==8)) for a,b in codes),'Wrong actual storage error')
    for env,platform,kind in (('native_cases','Darwin','peak_RSS'),('linux_cases','Linux','current_RSS')):
        m=v[env]['measurements']
        need(m['environment']==platform and m['memory_kind']==kind,'Metric scope mismatch')
        need([t['mode'] for t in m['trials']]==['cold','resume','coalesced'],'Missing/duplicate modes')
        for t in m['trials']:
            need(t['correct'] is True and number(t['download_seconds'],True) and number(t['read_seconds'],True),'Invalid timing/correctness')
            need(t['size']==t['origin_payload_bytes']==1048576,'Unexpected payload accounting')
            need(integer(t['process_rss_or_peak_bytes'],True) and integer(t['root_allocated_bytes'],True),'Invalid memory/allocation')
            u=t['usage']
            need(u['payload_bytes']==1048576 and u['remaining_reserved']==0 and integer(u['allocated_payload_bytes'],True) and integer(u['free_bytes']) and integer(u['budget']) and u['budget']>=1048576,'Invalid usage')
            need(t['root_allocated_bytes']>=u['allocated_payload_bytes'],'Allocation scopes inverted')
            coalesced=t['mode']=='coalesced';ids=['r','r1','r2','r3'] if coalesced else ['r']
            need(t['request_count']==len(ids) and [o['id'] for o in t['subscriber_outcomes']]==ids,'Request accounting')
            need(all(o['digest']==ARTIFACT_SHA and o['state']==o['result']=='SUCCEEDED' for o in t['subscriber_outcomes']),'Subscriber not successful')
            if coalesced:
                waiting=t['subscribers_before_release']
                need([w['id'] for w in waiting]==ids and all(w['state']=='WAITING' and w['digest']==ARTIFACT_SHA for w in waiting),'Not concurrent subscribers')
                need(len({w['generation'] for w in waiting+t['subscriber_outcomes']})==1,'Subscriber generation differs')
            else:need(t['subscribers_before_release']==[],'Unexpected barrier cohort')
            ranges=[None,'bytes=131072-'] if t['mode']=='resume' else [None]
            need(t['actual_origin_requests']==len(ranges) and t['request_ranges']==ranges,'Origin request/range mismatch')
    for env in ('native_service','linux_service'):
        service=v[env];need(service['cleanup'] is True,'Service cleanup')
        need([x['mode'] for x in service['cases']]==['slow_headers','slow_body','cancel','shared_deadlines']+(['connect_blackhole'] if env=='linux_service' else []),'Missing service cases')
        for case in service['cases'][:3]:
            need(case['result']==('CANCELLED' if case['mode']=='cancel' else 'TIMED_OUT') and case['available'] is False and case['deadline_unchanged'] is True,'Late service success')
            need(number(case['seconds'],True) and case['seconds']<2 and number(case['stop_seconds']) and case['stop_seconds']<2,'Service observation bound')
            need(case['exit_code']==0 and case['receipt_removed'] is True and integer(case['identity']['rss_bytes'],True),'Service lifecycle/memory invalid')
            if env=='linux_service':need([int(x) for x in case['identity']['Uid'].split()]==[65534]*4 and int(case['identity']['CapEff'],16)==0 and case['identity']['NoNewPrivs']=='1','Privilege mismatch')
            else:need(integer(case['identity']['uid'],True),'Native root daemon')
        need(service['cases'][2]['stale_cancel_rejected'] is True,'Stale cancellation accepted')
        need(service['cases'][3]['old_state']=='TIMED_OUT' and service['cases'][3]['new_state']=='SUCCEEDED' and service['cases'][3]['old_deadline_unchanged'] is True,'Shared deadline extended')
        if env=='linux_service':
            x=service['cases'][4]
            need(x['result']=='TIMED_OUT' and x['deadline_unchanged'] is True and x['available'] is False and x['namespace_isolated'] is True and x['unresolved_neighbor'] is True and x['origin_requests']==0 and x['exit_code']==0 and x['network_cleaned'] is True,'Blackhole case invalid')
            need(number(x['terminal_seconds'],True) and number(x['total_through_stop_seconds'],True) and x['total_through_stop_seconds']<1.5,'Blackhole deadline/cleanup observation failed')
    for env in ('native_review','linux_review'):
        review=v[env];need(review['cleanup'] is True,'Review cleanup')
        need([x['mode'] for x in review['cases']]==['admission_commit_reader','publish_commit_reader','shared_writer_wait','shared_reader_wait'],'Missing contention cases')
        admission=review['cases'][0]
        need(admission['subject']=='actual daemon' and admission['rejected'] is True and admission['no_receipt'] is True and admission['healthy_after'] is True and admission['sqlite_busy_timeout_ms']==200 and number(admission['seconds'],True) and admission['busy_statement']=='COMMIT' and integer(admission['busy_count'],True),'Late admission accepted or no contention')
        for case in review['cases'][1:]:
            need(case['subject']=='formal managed backend with scheduling barrier' and case['short_state']=='TIMED_OUT' and case['short_deadline_unchanged'] is True and case['short_revision_delta']==1 and case['sqlite_busy_timeout_ms']==200 and case['busy_timeout_restored'] is True and case['healthy_after'] is True,'Short subscriber revived')
            need(case['lock_acquired_before_cutoff'] is True and case['busy_before_cutoff'] is True and integer(case['busy_count'],True) and case['busy_statement']==('BEGIN IMMEDIATE' if case['mode']=='shared_writer_wait' else 'COMMIT'),'Contention was not exercised')
            if case['mode']=='publish_commit_reader':need(case['no_intent'] is True and case['long_state'] is None,'Late intent accepted')
            else:need(case['long_state']=='SUCCEEDED' and case['no_intent'] is False and number(case['intent_after_short_deadline'],True) and case['bytes_correct'] is True,'Did not cross short deadline with valid long interest')
    boot=v['service_reboot'];before=boot['before'];old=before['rows']['requests'][0];obj=before['rows']['objects'][0]
    need(boot['boot_id_after']!=before['boot_id'] and len(boot['boot_id_after'])==36 and before['crash_exit']==-9,'Service boot witness invalid')
    need(boot['catalog_sha256_before_recovery']==before['catalog_sha256'] and sha(before['catalog_sha256']) and boot['rows_before_recovery']==before['rows'] and boot['stage_sha256_before_recovery']==before['stage_sha256'],'Service journal changed before recovery')
    need(old['id']=='old_request' and old['state']=='WAITING' and 65536<=obj['offset']<1048576 and old['digest']==obj['digest']==ARTIFACT_SHA and old['generation']==obj['generation'],'No old partial request')
    need(boot['epoch_after']!=before['epoch'] and old['epoch']==before['epoch'],'Service epoch unchanged')
    for item in ('old_before_resume','old_after_resume'):
        r=boot[item];need(r['id']==old['id'] and r['state']==r['result']=='INTERRUPTED' and r['deadline']==old['deadline'] and r['epoch']==old['epoch'] and r['revision']==old['revision']+1,'Old deadline/result revived')
    fresh=boot['fresh'];need(fresh['id']=='fresh_request' and fresh['state']==fresh['result']=='SUCCEEDED' and fresh['epoch']==boot['epoch_after'] and fresh['digest']==ARTIFACT_SHA,'Fresh resume invalid')
    need(boot['no_automatic_origin_request'] is True and boot['old_epoch_rejected'] is True and boot['old_token_rejected'] is True,'Automatic or stale operation accepted')
    need(boot['resume_ranges']==['bytes='+str(obj['offset'])+'-'] and boot['read_sha256']==ARTIFACT_SHA and boot['cli_read_exit']==boot['installed_entrypoint_exit']==0,'Resume or CLI incorrect')
    need(boot['process_cleaned'] is True and boot['normal_reboot'] is True and boot['power_loss_tested'] is False,'Service boot cleanup/scope')
    identity=boot['identity_after'];need([int(x) for x in identity['Uid'].split()]==[65534]*4 and int(identity['CapEff'],16)==0 and identity['NoNewPrivs']=='1','Boot privilege mismatch')
    need(v['limits']==dict(power_loss_tested=False,wan_tested=False,deployed_service_tested=True,systemd_tested=False,long_term_tested=False),'Limit changed')


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
             lambda v:v['native_cases']['measurements']['trials'][1].update(origin_payload_bytes=0),
             lambda v:v['linux_cases']['measurements']['trials'][0].update(download_seconds=float('inf')),
             lambda v:v['linux_cases']['measurements']['trials'][0].update(download_seconds=float('nan')),
             lambda v:v['native_cases']['measurements']['trials'][0].update(process_rss_or_peak_bytes=-1),
             lambda v:v['native_cases']['measurements']['trials'][0].update(root_allocated_bytes=-1),
             lambda v:v['native_cases']['measurements']['trials'][0].update(usage={}),
             lambda v:v['linux_cases']['measurements'].update(trials=[copy.deepcopy(v['linux_cases']['measurements']['trials'][0]) for _ in range(3)]),
             lambda v:v.update(stages=[s for s in v['stages'] if s['name']!='native-cases']),
             lambda v:(v.update(installed_modules={}),v['native'].update(modules={}),v['linux'].update(modules={}),v.update(source={k:h for k,h in v['source'].items() if not k.startswith('cacheweft/')})),
             lambda v:v['reboot'].update(catalog_sha256_before_recovery='0'*64),
             lambda v:v['linux_cases']['storage']['cases'][3].update(published_before_repair=True),
             lambda v:v['linux_cases']['measurements']['trials'][2]['subscribers_before_release'][1].update(state='SUCCEEDED'),
             lambda v:v['source'].pop('tests/test_core.py'),
             lambda v:v['linux_service']['cases'][0].update(result='SUCCEEDED'),
             lambda v:v['linux_service']['cases'][3].update(old_deadline_unchanged=False),
             lambda v:v['service_reboot']['old_after_resume'].update(state='SUCCEEDED'),
             lambda v:v['service_reboot'].update(old_epoch_rejected=False),
             lambda v:v['service_reboot']['old_before_resume'].update(deadline=0),
             lambda v:v['linux_service']['cases'][0]['identity'].update(Uid='0 0 0 0'),
             lambda v:v['service_reboot'].update(catalog_sha256_before_recovery='0'*64),
             lambda v:v['service_reboot'].update(resume_ranges=[None]),
             lambda v:v['linux_service']['cases'][4].update(network_cleaned=False),
             lambda v:v['linux_service']['cases'][4].update(total_through_stop_seconds=2.1),
             lambda v:(v['native'].update(test_ids=['unrelated.case_'+str(i) for i in range(len(COHORT))]),v['linux'].update(test_ids=['unrelated.case_'+str(i) for i in range(len(COHORT))])),
             lambda v:v['linux_review']['cases'][0].update(rejected=False),
             lambda v:v['linux_review']['cases'][2].update(short_state='SUCCEEDED'),
             lambda v:v['native_review']['cases'][3].update(short_deadline_unchanged=False),
             lambda v:v['linux_review']['cases'][0].update(busy_count=0),
             lambda v:v['linux_review']['cases'][1].update(busy_before_cutoff=False)]
    for change in changes:
        v=copy.deepcopy(value);change(v)
        try:verify(v)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupted evidence accepted')
    return dict(rejected=len(changes),all_rejected=True)


def render(v):
    title='Cacheweft milestone 1 evidence'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:20px}</style><h1>'+title+'</h1><p>Passed: '+str(v.get('passed',False))+'</p><p>Unprivileged local daemon, deadlines and normal VM reboot. Linux VM metrics are separate from native macOS. No systemd, power-loss, WAN or long-term qualification.</p><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
