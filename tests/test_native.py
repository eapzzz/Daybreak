import base64
from datetime import timedelta
import hmac

import pytest

from test_engine import creates, dismisses, engine, qr


def messages(phone, key):
    result=[]
    for _, message in phone.calls:
        if message['message']!='command_broadcast_intent': continue
        data=message['data']
        assert data['intent_package_name']=='daybreak.android'
        assert data['intent_class_name']=='daybreak.android.CommandReceiver'
        assert data['intent_action']=='daybreak.android.CONTROL'
        _, token, kind=data['intent_extras'].split(':')
        assert kind=='String'
        content, signature=(base64.urlsafe_b64decode(p+'='*(-len(p)%4)) for p in token.split('.'))
        assert hmac.compare_digest(hmac.digest(bytes.fromhex(key),content,'sha256'),signature)
        result.append(content.decode().split('|'))
    return result


async def confirm_android_trial(engine):
    e,c,h=engine
    key=e.native.pairing_key()
    c.now=c.now.replace(hour=14)
    e.native.start_trial()
    await e.tick()
    trial=e.native.trial()
    c.now+=timedelta(seconds=23)
    await qr(e)
    assert e.db.session('wake:'+c.now.date().isoformat()) is None
    assert e.native.public()['trial_result']=='Tag'
    for _ in range(3):
        await e.tick()
        c.now+=timedelta(seconds=2)
    assert [m[1:3] for m in messages(h,key)]==[['run',trial['sid']]]+[['stop',trial['sid']]]*3
    return key


async def test_paired_app_takes_over_wake_and_stops_on_existing_tag(engine):
    e,c,h=engine
    with pytest.raises(ValueError,match='Android link test'):
        e.native.set_enabled(True)
    key=await confirm_android_trial(engine)
    c.now=c.now.replace(hour=22,minute=0,second=0)
    await e.tick()
    assert len(creates(h))==1
    e.native.set_enabled(True)
    await e.tick()
    assert len(dismisses(h))==1
    tomorrow=c.now+timedelta(days=1)
    c.now=tomorrow.replace(hour=5,minute=54,second=48)
    await e.tick()
    real=messages(h,key)[-1]
    assert real[1:3]==['run','wake:'+tomorrow.date().isoformat()]
    first=tomorrow.replace(hour=5,minute=55,second=0)
    assert int(real[3])==int(first.timestamp()*1000)
    status=e.status()
    assert status['active_wake']['interval_seconds']==10
    preview=status['plans'][0]['alarms']
    assert [p['ring'][11:19] for p in preview[:3]]==['05:55:00','05:55:10','05:55:20']
    assert [p['prepare'][11:19] for p in preview[:3]]==['05:54:58','05:55:08','05:55:18']
    c.now+=timedelta(seconds=26)
    await e.tick()
    assert messages(h,key)[-1][3]==real[3]
    await qr(e)
    await e.tick()
    assert messages(h,key)[-1][1:3]==['stop',real[2]]
    assert e.db.session(real[2])['reason']=='Tag'
    assert len(creates(h))==1
    assert 'native_key' not in e.public_config()['settings']
    assert key not in str(e.status())


async def test_native_wake_stops_when_away_and_resumes_without_resetting_anchor(engine):
    e,c,h=engine
    key=await confirm_android_trial(engine)
    e.native.set_enabled(True)
    c.now=(c.now+timedelta(days=1)).replace(hour=5,minute=54,second=48)
    await e.tick()
    start=messages(h,key)[-1]
    c.now+=timedelta(seconds=22)
    h.states['sensor.wifi']['state']='Other'
    await e.tick()
    assert messages(h,key)[-1][1]=='stop'
    assert e.status()['active_wake'] is not None
    h.states['sensor.wifi']['state']='HomeWiFi'
    c.now+=timedelta(seconds=3)
    await e.tick()
    resumed=messages(h,key)[-1]
    assert resumed[1:4]==start[1:4]
    issued=[int(m[4]) for m in messages(h,key)]
    assert issued==sorted(set(issued))
    assert not creates(h)
