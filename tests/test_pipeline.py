"""Exercise the real local control API + HA HTTP/WebSocket adapter + scheduler."""
import asyncio
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from aiohttp import web
from daybreak.engine import Engine
from daybreak.ha import HomeAssistant
from daybreak.model import Settings
from daybreak.service import build_app,Control
from daybreak.store import SettingsFiles,Store
from test_ha import fake_ha

async def wait_until(check):
    for _ in range(100):
        if check(): return
        await asyncio.sleep(.01)
    raise AssertionError('Event did not reach the scheduler')

@pytest.mark.parametrize('completion',['qr','away','unknown'])
async def test_phone_event_to_durable_cleanup_through_real_transports(tmp_path,completion):
    states=[{'entity_id':'sensor.wifi','state':'HomeWiFi'},
            {'entity_id':'sensor.bssid','state':'aa:bb:cc:dd:ee:ff'}]
    async with fake_ha(states) as (url,seen,peers):
        files=SettingsFiles(tmp_path)
        files.save(Settings(ha_url=url,notify_service='notify.mobile_app_phone',
            qr_tag_id='bathroom',qr_device_id='phone',ssid_entity='sensor.wifi',bssid_entity='sensor.bssid',
            home_ssids=['HomeWiFi'],home_bssids=['aa:bb:cc:dd:ee:ff']),'valid')
        db=Store(tmp_path/'pipeline.sqlite3'); db.set_meta('armed',True)
        clock=[datetime(2026,9,17,5,54,49,tzinfo=ZoneInfo('Europe/Warsaw'))]
        engine=Engine(files,db,clock=lambda:clock[0])
        async with HomeAssistant(url,'valid',engine.on_event) as ha:
            engine.ha=ha; await asyncio.wait_for(ha.ready.wait(),3)
            runner=web.AppRunner(build_app(engine)); await runner.setup()
            socket=tmp_path/'pipeline.sock'; await web.UnixSite(runner,str(socket)).start()
            try:
                console=Control(socket)
                assert (await console.status())['network']['state']=='Home'
                assert (await console.action('preview',date='2026-09-18',start='07:10'))['end'][11:16]=='08:30'
                await engine.tick(); assert not seen
                clock[0]+=timedelta(seconds=1); await engine.tick()
                assert len(seen)==4
                assert seen[0]['data']['intent_action']=='android.intent.action.SET_ALARM'
                if completion=='qr':
                    event={'event_type':'tag_scanned','data':{'tag_id':'bathroom','device_id':'phone'}}
                    check=lambda: bool(db.meta('last_qr'))
                else:
                    state='Other' if completion=='away' else 'unavailable'
                    event={'event_type':'state_changed','data':{'entity_id':'sensor.wifi',
                        'new_state':{'entity_id':'sensor.wifi','state':state}}}
                    check=lambda: engine.network().state!='Home'
                await peers[0].send_json({'id':2 if completion=='qr' else 1,'type':'event','event':event})
                await wait_until(check)
                clock[0]+=timedelta(seconds=1); await engine.tick()
                assert any(p['data'].get('intent_action')=='android.intent.action.DISMISS_ALARM' for p in seen)
                assert any(p['message']=='command_volume_level' and p['data'].get('command')==4 for p in seen)
                status=await console.status(); assert status['pending_cleanup']==0
                clock[0]=clock[0].replace(minute=55,second=50); await engine.tick()
                assert sum(p['data'].get('intent_action')=='android.intent.action.SET_ALARM' for p in seen)==1
            finally: await runner.cleanup()
        if completion=='qr':
            db.close(); db=Store(tmp_path/'pipeline.sqlite3')
            assert db.session('wake:2026-09-17')['completed']
        db.close()
