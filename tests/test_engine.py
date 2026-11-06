import asyncio
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from daybreak.model import Settings,plan_for
from daybreak.store import Store,SettingsFiles
from daybreak.engine import Engine
from daybreak.ha import HAError

TZ=ZoneInfo('Europe/Warsaw')
class Clock:
    def __init__(self): self.now=datetime(2026,9,17,5,54,49,tzinfo=TZ)
    def __call__(self): return self.now
class Phone:
    def __init__(self):
        self.ready=asyncio.Event(); self.ready.set(); self.error=''; self.calls=[]; self.hook=None; self.fail=False
        self.states={'sensor.wifi':{'state':'HomeWiFi'},'sensor.bssid':{'state':'aa:bb:cc:dd:ee:ff'}}
    async def notify(self,service,payload,timeout=3):
        self.calls.append((service,payload))
        if self.hook: await self.hook(payload)
        if self.fail: raise HAError('test transport failure')
    async def poll(self): return list(self.states.values())
    async def services(self): return []
    async def close(self): pass

@pytest.fixture
def engine(tmp_path):
    files=SettingsFiles(tmp_path)
    cfg=Settings(lead_seconds=10,ha_url='http://home.test:8123',notify_service='notify.mobile_app_phone',qr_tag_id='bathroom',qr_device_id='phone',
                 ssid_entity='sensor.wifi',bssid_entity='sensor.bssid',home_ssids=['HomeWiFi'],home_bssids=['aa:bb:cc:dd:ee:ff'])
    files.save(cfg,'private'); db=Store(tmp_path/'state.db'); db.set_meta('armed',True)
    c=Clock(); ha=Phone(); e=Engine(files,db,ha,c)
    yield e,c,ha
    db.close()

def creates(ha): return [p for _,p in ha.calls if p['data'].get('intent_action')=='android.intent.action.SET_ALARM']
def volumes(ha): return [p for _,p in ha.calls if p['message']=='command_volume_level']
def dismisses(ha): return [p for _,p in ha.calls if p['data'].get('intent_action')=='android.intent.action.DISMISS_ALARM']
async def qr(e): await e.on_event({'event_type':'tag_scanned','data':{'tag_id':'bathroom','device_id':'phone'}})

async def test_prepare_at_ten_seconds_only(engine):
    e,c,h=engine; await e.tick(); assert not creates(h) and not volumes(h)
    c.now+=timedelta(seconds=1); await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2
    c.now+=timedelta(seconds=10); await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2
    c.now=c.now.replace(minute=55,second=50); await e.tick(); assert len(creates(h))==2

async def test_no_duplicate_after_restart(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick()
    again=Engine(e.files,e.db,h,c); await again.tick(); assert len(creates(h))==1

async def test_away_does_not_create_or_raise(engine):
    e,c,h=engine; h.states['sensor.wifi']['state']='Other'; c.now+=timedelta(seconds=1)
    await e.tick(); assert not h.calls

async def test_unknown_does_not_create(engine):
    e,c,h=engine; h.ready.clear(); c.now+=timedelta(seconds=1)
    await e.tick(); assert not creates(h)

async def test_qr_stops_rest_of_day_and_restores(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick(); await qr(e)
    for _ in range(4): await e.tick(); c.now+=timedelta(seconds=1)
    assert dismisses(h)
    assert any(p['data'].get('command')==4 for p in volumes(h))
    c.now=c.now.replace(hour=6,minute=0,second=50); await e.tick(); assert len(creates(h))==1
    assert e.db.session('wake:2026-09-17')['completed']==1

async def test_wrong_qr_is_ignored(engine):
    e,c,h=engine
    await e.on_event({'event_type':'tag_scanned','data':{'tag_id':'bathroom','device_id':'another'}})
    assert e.db.meta('last_qr') is None

async def test_away_cancels_seed_and_restores(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick()
    h.states['sensor.wifi']['state']='Other'; c.now+=timedelta(seconds=1)
    await e.tick(); assert dismisses(h)
    assert any(p['data'].get('command')==4 for p in volumes(h))

async def test_qr_during_creation_prevents_loud(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1)
    async def hook(p):
        if p['message']=='command_activity': await qr(e)
    h.hook=hook; await e.tick(); h.hook=None; await e.tick()
    assert not [p for p in volumes(h) if p['data']['command']==7]
    assert dismisses(h)

async def test_network_change_during_creation_prevents_loud(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1)
    async def hook(p): h.states['sensor.wifi']['state']='Other'
    h.hook=hook; await e.tick(); h.hook=None; await e.tick()
    assert not [p for p in volumes(h) if p['data']['command']==7]
    assert dismisses(h)

async def test_never_create_past_alarm(engine):
    e,c,h=engine; c.now=c.now.replace(hour=5,minute=55,second=1); await e.tick()
    assert not creates(h)

async def test_evening_seed_uses_next_plan(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22,minute=0,second=0)
    e.db.set_meta('override',{'date':'2026-09-18','start':'07:10'})
    await e.tick(); assert len(creates(h))==1
    assert 'HOUR:7:int' in creates(h)[0]['data']['intent_extras']
    assert 'MINUTES:10:int' in creates(h)[0]['data']['intent_extras']
    assert not volumes(h)

async def test_evening_away_sends_nothing(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22); h.states['sensor.wifi']['state']='Other'
    await e.tick(); assert not h.calls

async def test_evening_before_time_sends_nothing(engine):
    e,c,h=engine; c.now=c.now.replace(hour=21,minute=59,second=0); await e.tick()
    assert not creates(h)

async def test_override_replace_cancels_old_seed(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22); await e.tick()
    await e.set_override({'date':'2026-09-18','start':'07:30'})
    for _ in range(4): await e.tick(); c.now+=timedelta(seconds=1)
    assert dismisses(h) and len(creates(h))==2
    assert 'HOUR:7:int' in creates(h)[-1]['data']['intent_extras']

async def test_one_off_does_not_slide_when_away(engine):
    e,c,h=engine; await e.set_override({'date':'2026-09-18','start':'07:30'})
    p=plan_for(e.cfg,c.now.date()+timedelta(days=2),e.db.meta('override'))
    assert p.start.strftime('%H:%M')=='05:55'

async def test_cleanup_retries_after_error(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick(); await qr(e); h.fail=True
    await e.tick(); assert any(a['status']=='cancel_pending' for a in e.db.alarms())
    h.fail=False; c.now+=timedelta(seconds=5); await e.tick()
    assert all(a['status']=='cancelled' for a in e.db.alarms())

async def test_test_scope_does_not_complete_real_day(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14,minute=0,second=0)
    p=await e.start_test(); assert len(p['alarms'])==3
    c.now=datetime.fromisoformat(p['alarms'][0]['prepare']); await e.tick(); await qr(e)
    for _ in range(4): await e.tick(); c.now+=timedelta(seconds=1)
    assert e.db.session(e.test_id)['completed']==1
    assert not e.db.session('wake:2026-09-17') or not e.db.session('wake:2026-09-17')['completed']

async def test_unarmed_only_test_sends(engine):
    e,c,h=engine; e.db.set_meta('armed',False); c.now+=timedelta(seconds=1); await e.tick(); assert not creates(h)
    c.now=c.now.replace(hour=14); p=await e.start_test(); c.now=datetime.fromisoformat(p['alarms'][0]['prepare']); await e.tick()
    assert creates(h)

async def test_early_test_confirmation_rejected(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14)
    await e.start_test()
    with pytest.raises(ValueError): e.accept_test()

async def test_midnight_qr_only_current_session(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22); await e.tick(); await qr(e)
    assert e.db.session('wake:2026-09-18')['completed']==0

async def test_stale_override_rejected(engine):
    e,c,h=engine
    with pytest.raises(ValueError): await e.set_override({'date':'2026-09-16','start':'07:00'})

async def test_validating_unknown_config_keeps_existing(engine):
    e,c,h=engine
    with pytest.raises(ValueError): await e.configure({'lead_seconds':0})
    assert e.cfg.lead_seconds==10

async def test_evening_seed_is_cancelled_when_phone_leaves(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22,minute=0,second=0)
    await e.tick(); assert len(creates(h))==1
    h.states['sensor.bssid']['state']='11:22:33:44:55:66'; c.now+=timedelta(seconds=1)
    await e.tick(); assert len(dismisses(h))==1 and not volumes(h)
    c.now+=timedelta(minutes=15); await e.tick(); assert len(creates(h))==1

async def test_returning_home_can_prepare_future_slot_after_cleanup(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick()
    h.states['sensor.wifi']['state']='Other'; c.now+=timedelta(seconds=1); await e.tick()
    h.states['sensor.wifi']['state']='HomeWiFi'; c.now+=timedelta(seconds=3); await e.tick()
    assert len(creates(h))==2
    c.now=c.now.replace(minute=55,second=1); await e.tick(); assert len(creates(h))==2

async def test_network_test_completes_without_marking_morning_done(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14,minute=0,second=0)
    p=await e.start_test('network-test'); c.now=datetime.fromisoformat(p['alarms'][0]['prepare'])
    await e.tick(); h.states['sensor.wifi']['state']='Other'; c.now+=timedelta(seconds=1); await e.tick()
    assert e.db.session(e.test_id)['reason']=='Network left home'
    assert e.accept_test()['message']=='Test confirmed.'
    assert e.db.session('wake:2026-09-17') is None

async def test_away_morning_is_recorded_as_skipped(engine):
    e,c,h=engine; h.states['sensor.wifi']['state']='Other'; c.now+=timedelta(seconds=1)
    await e.tick(); c.now=c.now.replace(hour=7,minute=15,second=0); await e.tick()
    r=e.db.session('wake:2026-09-17')
    assert r is not None and r['completed'] and r['reason']=='Skipped: away or network unknown'
    assert not creates(h) and not volumes(h)

async def test_old_pending_volume_restore_is_not_lost_to_history_limit(engine):
    e,c,h=engine
    old=plan_for(e.cfg,c.now.date()-timedelta(days=100)); sid=e._ensure(old)
    e.db.loud(sid,e.cfg); e.db.complete(sid,'QR')
    for n in range(70):
        p=plan_for(e.cfg,c.now.date()-timedelta(days=99-n)); other=e._ensure(p)
        e.db.complete(other,'QR')
    await e.tick()
    assert any(p['data']['command']==4 for p in volumes(h))
    assert not e.db.session(sid)['needs_normal']

async def test_slow_creation_keeps_valid_upcoming_alarm_without_late_volume(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1)
    async def late_response(payload):
        if payload['data'].get('intent_action')=='android.intent.action.SET_ALARM':
            c.now+=timedelta(seconds=10)
    h.hook=late_response
    await e.tick()
    assert len(creates(h))==1 and not volumes(h)
    assert not any(a['status']=='cancel_pending' for a in e.db.alarms())

async def test_replace_tag_revokes_previous_identifier_and_survives_restart(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14)
    e.db.set_meta('test_passed','old-phone-test')
    await e.configure({'qr_tag_id':'new-nfc-tag'})
    assert not e.db.meta('armed') and e.db.meta('test_passed') is None
    await qr(e)
    assert e.status()['last_tag'] is None
    restarted=Engine(e.files,e.db,h,c)
    await restarted.on_event({'event_type':'tag_scanned','data':{'tag_id':'new-nfc-tag','device_id':'another-phone'}})
    assert restarted.status()['last_tag'] is None
    await restarted.on_event({'event_type':'tag_scanned','data':{'tag_id':'new-nfc-tag','device_id':'phone'}})
    assert restarted.db.session('wake:2026-09-17')['reason']=='Tag'
    assert restarted.status()['last_tag']==restarted.status()['last_qr']
    assert restarted.status()['wake_tag_id']=='new-nfc-tag'

async def test_same_pair_keeps_armed_state_and_confirmed_test(engine):
    e,c,h=engine; e.db.set_meta('test_passed','existing-test')
    await e.configure({'qr_tag_id':e.cfg.qr_tag_id,'qr_device_id':e.cfg.qr_device_id})
    assert e.db.meta('armed') and e.db.meta('test_passed')=='existing-test'

async def test_replacing_tag_during_active_session_is_rejected(engine):
    e,c,h=engine; c.now+=timedelta(seconds=1); await e.tick()
    with pytest.raises(ValueError,match='running wake-up'):
        await e.configure({'qr_tag_id':'new-nfc-tag'})
    assert e.cfg.qr_tag_id=='bathroom' and e.db.meta('armed')

async def test_replacing_tag_during_live_test_is_rejected(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14); await e.start_test()
    with pytest.raises(ValueError,match='phone test'):
        await e.configure({'qr_tag_id':'new-nfc-tag'})
    assert e.cfg.qr_tag_id=='bathroom'

async def test_tag_pairing_capture_cancel_and_timeout(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14)
    e.begin_pairing()
    await e.on_event({'event_type':'tag_scanned','data':{'tag_id':'new-nfc-tag','device_id':'phone'}})
    assert e.paired=={'tag_id':'new-nfc-tag','device_id':'phone'}
    assert e.cfg.qr_tag_id=='bathroom' and e.status()['last_tag'] is None
    e.cancel_pairing(); assert e.paired is None and not e.status()['pairing']
    e.begin_pairing(); c.now+=timedelta(minutes=3)
    await e.on_event({'event_type':'tag_scanned','data':{'tag_id':'expired-tag','device_id':'phone'}})
    assert e.paired is None and e.cfg.qr_tag_id=='bathroom'

async def test_last_tag_reads_legacy_history(engine):
    e,c,h=engine; e.db.set_meta('last_qr','legacy-timestamp')
    assert e.status()['last_tag']==e.status()['last_qr']=='legacy-timestamp'

async def test_old_completed_test_cannot_confirm_a_replacement_tag(engine):
    e,c,h=engine; c.now=c.now.replace(hour=14)
    p=await e.start_test(); c.now=datetime.fromisoformat(p['alarms'][0]['prepare'])
    await e.tick(); await qr(e)
    for _ in range(4): await e.tick(); c.now+=timedelta(seconds=1)
    e.accept_test()
    await e.configure({'qr_tag_id':'new-nfc'})
    assert e.test_id is None and e.db.meta('active_test') is None
    with pytest.raises(ValueError): e.accept_test()

async def test_evening_seed_is_not_set_again_before_first_ring_or_after_restart(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22,minute=0,second=0); await e.tick()
    assert len(creates(h))==1 and not volumes(h)
    c.now=(c.now+timedelta(days=1)).replace(hour=5,minute=54,second=50)
    e=Engine(e.files,e.db,h,c); await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2
    e=Engine(e.files,e.db,h,c); await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2
    await qr(e); await e.tick()
    assert len(dismisses(h))==1 and not e.db.session('wake:2026-09-18')['needs_normal']

async def test_failed_seed_sound_preparation_retries_without_setting_alarm_again(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22,minute=0,second=0); await e.tick()
    c.now=(c.now+timedelta(days=1)).replace(hour=5,minute=54,second=50)
    h.fail=True; await e.tick(); assert len(creates(h))==1
    h.fail=False;c.now+=timedelta(seconds=3);await e.tick()
    assert len(creates(h))==1 and any(a['prepared'] for a in e.db.alarms())

async def test_unacknowledged_evening_seed_is_retried_before_first_ring(engine):
    e,c,h=engine; c.now=c.now.replace(hour=22,minute=0,second=0);h.fail=True;await e.tick()
    assert len(creates(h))==1
    c.now=(c.now+timedelta(days=1)).replace(hour=5,minute=54,second=50)
    h.fail=False;await e.tick()
    assert len(creates(h))==2 and len(volumes(h))==2

@pytest.mark.parametrize('lead',[1,2])
async def test_short_lead_dispatches_alarm_and_sound_only_in_configured_window(engine,lead):
    e,c,h=engine;await e.configure({'lead_seconds':lead})
    c.now=c.now.replace(hour=5,minute=54,second=59-lead);await e.tick()
    assert not h.calls
    c.now+=timedelta(seconds=1);await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2
    c.now+=timedelta(seconds=1);await e.tick()
    assert len(creates(h))==1 and len(volumes(h))==2

async def test_one_second_slow_creation_does_not_send_volume_after_deadline(engine):
    e,c,h=engine;await e.configure({'lead_seconds':1})
    c.now=c.now.replace(hour=5,minute=54,second=59)
    async def late(payload):
        if payload['data'].get('intent_action')=='android.intent.action.SET_ALARM':c.now+=timedelta(seconds=1)
    h.hook=late;await e.tick()
    assert len(creates(h))==1 and not volumes(h)

@pytest.mark.parametrize('lead',[1,2])
async def test_short_lead_request_timeouts_stay_inside_remaining_window(engine,lead):
    e,c,h=engine;await e.configure({'lead_seconds':lead})
    c.now=c.now.replace(hour=5,minute=54,second=60-lead);timeouts=[]
    original=h.notify
    async def notify(service,payload,timeout=3):
        timeouts.append(timeout);await original(service,payload,timeout)
        c.now+=timedelta(milliseconds=150)
    h.notify=notify;await e.tick()
    assert len(timeouts)==4 and all(0<t<lead for t in timeouts)
    assert timeouts==sorted(timeouts,reverse=True)

async def test_one_second_tag_during_creation_still_prevents_loud_commands(engine):
    e,c,h=engine;await e.configure({'lead_seconds':1});c.now=c.now.replace(hour=5,minute=54,second=59)
    async def scanned(payload):
        if payload['data'].get('intent_action')=='android.intent.action.SET_ALARM':await qr(e)
    h.hook=scanned;await e.tick();h.hook=None;await e.tick()
    assert not [p for p in volumes(h) if p['data']['command']==7]
    assert dismisses(h)
