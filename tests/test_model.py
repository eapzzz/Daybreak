from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from daybreak.model import Settings, plan_for, network_status, validate, valid_local

D = date(2026, 9, 17)
TZ = ZoneInfo('Europe/Warsaw')

@pytest.mark.parametrize('start', ['05:55','07:00','07:10','07:30','23:40'])
def test_exact_first_with_no_end(start):
    p=plan_for(Settings(),D,{'date':D.isoformat(),'start':start})
    assert p.start.strftime('%H:%M')==start
    assert p.next_ring(p.start.timestamp()-1)==p.start
    assert p.end is p.restore is None
    preview=p.public()
    assert preview['count'] is None and len(preview['alarms'])==9

def test_default_sequence():
    p=plan_for(Settings(),D)
    first=p.start.timestamp()
    assert [p.next_ring(first+60*m-1).strftime('%H:%M') for m in range(16)] == [
        f'05:{m:02d}' for m in range(55,60)]+[f'06:{m:02d}' for m in range(11)]
    assert p.next_ring(first+15*60).strftime('%H:%M')=='06:13'
    assert p.next_ring(first+120*60).strftime('%H:%M')=='07:58'
    assert p.next_ring(first+24*3600).date()==D+timedelta(days=1)

def test_old_duration_settings_load_without_limiting_the_cadence():
    cfg=Settings.from_dict({'minimum_minutes':60,'round_minutes':30,'restore_minutes':15,'dense_minutes':90})
    assert cfg.dense_minutes==90
    assert 'minimum_minutes' not in cfg.to_dict()

def test_override_only_selected_day():
    s=Settings(); o={'date':D.isoformat(),'start':'07:30'}
    assert plan_for(s,D,o).start.hour==7
    assert plan_for(s,D+timedelta(days=1),o).start.hour==5

def test_disabled_weekday_override():
    s=Settings(); s.weekly['thu']=None
    assert plan_for(s,D) is None
    assert plan_for(s,D,{'date':str(D),'start':'08:00'}) is not None

@pytest.mark.parametrize('change',[{'lead_seconds':0},{'lead_seconds':60},{'dense_minutes':-1},{'ha_url':'http://a:b@home/'},{'timezone':'fake/zone'},{'sparse_minutes':0}])
def test_invalid_config(change):
    s=Settings(); s.__dict__.update(change)
    with pytest.raises(ValueError): validate(s)

def test_unknown_keys_rejected():
    with pytest.raises(ValueError): Settings.from_dict({'not_a_field':True})

def test_spring_gap_skipped():
    start=valid_local(date(2026,3,29),'02:30',TZ)
    assert start.hour==3 and start.minute==0

def test_fall_first_instance():
    t=valid_local(date(2026,10,25),'02:30',TZ)
    assert t.fold==0

def state(value): return {'state':value,'last_updated':'2026-09-16T20:00:00+00:00','attributes':{}}

def home():
    s=Settings(ssid_entity='sensor.wifi',bssid_entity='sensor.bssid',home_ssids=['HomeWiFi'],home_bssids=['aa:bb:cc:dd:ee:ff'])
    return s, {'sensor.wifi':state('"HomeWiFi"'),'sensor.bssid':state('AA:BB:CC:DD:EE:FF')}

def test_home_normalization():
    s,states=home(); assert network_status(s,states,True).state=='Home'

@pytest.mark.parametrize('value,expected',[('Other','Away'),('Not Connected','Away'),('unknown','Unknown'),('unavailable','Unknown'),('<unknown ssid>','Unknown')])
def test_ssid_states(value,expected):
    s,states=home(); states['sensor.wifi']=state(value)
    assert network_status(s,states,True).state==expected

def test_bssid_must_also_match():
    s,states=home(); states['sensor.bssid']=state('aa:bb:cc:dd:ee:00')
    assert network_status(s,states,True).state=='Away'

def test_missing_and_offline_are_unknown():
    s,states=home()
    assert network_status(s,{},True).state=='Unknown'
    assert network_status(s,states,False).state=='Unknown'

def test_optional_heartbeat_expires_not_unchanged_ssid():
    s,states=home(); s.heartbeat_entity='sensor.heartbeat'; s.heartbeat_max_minutes=30
    states['sensor.heartbeat']=state('Periodic'); states['sensor.heartbeat']['last_updated']='2026-09-17T05:00:00+00:00'
    now=datetime(2026,9,17,5,15,tzinfo=timezone.utc)
    assert network_status(s,states,True,now).state=='Home'
    assert network_status(s,states,True,now+timedelta(hours=1)).state=='Unknown'
