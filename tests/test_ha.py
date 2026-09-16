import asyncio
from contextlib import asynccontextmanager
from aiohttp import web, ClientSession
import pytest
from daybreak.ha import HomeAssistant, HAError, alarm_payload, dismiss_payload
from datetime import datetime,timezone

@asynccontextmanager
async def fake_ha(states=None):
    seen=[]; peers=[]
    states=states or [{'entity_id':'sensor.wifi','state':'HomeWiFi'}]
    async def api(req):
        if req.headers.get('Authorization')!='Bearer valid': return web.Response(status=401)
        if req.path=='/api/states': return web.json_response(states)
        if req.path=='/api/services': return web.json_response([{'domain':'notify','services':{'mobile_app_phone':{}}}])
        if req.path.startswith('/api/services/'):
            seen.append(await req.json()); return web.json_response([])
        return web.json_response({'message':'API running.'})
    async def websocket(req):
        ws=web.WebSocketResponse(); await ws.prepare(req); peers.append(ws)
        await ws.send_json({'type':'auth_required'})
        auth=await ws.receive_json()
        await ws.send_json({'type':'auth_ok' if auth.get('access_token')=='valid' else 'auth_invalid'})
        async for msg in ws:
            if msg.type==web.WSMsgType.TEXT:
                data=msg.json()
                if data['type']=='subscribe_events': await ws.send_json({'id':data['id'],'type':'result','success':True,'result':None})
                elif data['type']=='ping': await ws.send_json({'id':data['id'],'type':'pong'})
        return ws
    app=web.Application(); app.router.add_get('/api/websocket',websocket); app.router.add_route('*','/api/{tail:.*}',api)
    runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
    url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    try: yield url,seen,peers
    finally:
        for ws in peers: await ws.close()
        await runner.cleanup()

async def test_rest_and_websocket():
    async with fake_ha() as (url,seen,peers):
        events=[]
        async def cb(event): events.append(event)
        async with HomeAssistant(url,'valid',cb) as client:
            await asyncio.wait_for(client.ready.wait(),3)
            assert client.states['sensor.wifi']['state']=='HomeWiFi'
            assert 'mobile_app_phone' in (await client.services())[0]['services']
            await client.notify('notify.mobile_app_phone',{'message':'command_volume_level','data':{'command':7}})
            assert seen[0]['data']['command']==7
            await peers[0].send_json({'id':2,'type':'event','event':{'event_type':'tag_scanned','data':{'tag_id':'bathroom'}}})
            for _ in range(50):
                if events: break
                await asyncio.sleep(.01)
            assert events[-1]['data']['tag_id']=='bathroom'

async def test_authentication_error_has_no_token():
    async with fake_ha() as (url,_,__):
        client=HomeAssistant(url,'never-print-me')
        async with ClientSession() as session:
            client.session=session
            with pytest.raises(HAError) as e: await client.request('GET','/api/states')
            assert 'never-print-me' not in str(e.value)

async def test_redirect_not_followed():
    app=web.Application()
    async def redirect(r): return web.Response(status=302,headers={'Location':'http://example.invalid'})
    app.router.add_get('/api/states',redirect)
    runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
    try:
        c=HomeAssistant('http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]),'private')
        async with ClientSession() as session:
            c.session=session
            with pytest.raises(HAError,match='302'): await c.request('GET','/api/states')
    finally: await runner.cleanup()

def test_intent_exact_typed_keys():
    p=alarm_payload(datetime(2026,9,17,7,24,tzinfo=timezone.utc),'Daybreak_20260917_0724','com.google.android.deskclock')
    x=p['data']['intent_extras']
    assert ', ' not in x
    assert 'MINUTES:24:int' in x and 'HOUR:7:int' in x
    assert p['data']['ttl']==0 and p['data']['priority']=='high'
    assert 'SEARCH_MODE:android.label:String' in dismiss_payload('Daybreak_20260917_0724','com.google.android.deskclock')['data']['intent_extras']

async def test_older_state_does_not_overwrite_newer_report():
    client=HomeAssistant('http://home.test','valid')
    client._accept_state({'entity_id':'sensor.wifi','state':'Other','last_updated':'2026-09-17T05:00:02Z'})
    await client._dispatch({'type':'event','event':{'event_type':'state_changed','data':{
        'new_state':{'entity_id':'sensor.wifi','state':'HomeWiFi','last_updated':'2026-09-17T05:00:01Z'}}}})
    assert client.states['sensor.wifi']['state']=='Other'

async def test_removal_is_unknown_instead_of_retaining_home():
    client=HomeAssistant('http://home.test','valid'); client.states['sensor.wifi']={'state':'HomeWiFi'}
    await client._dispatch({'type':'event','event':{'event_type':'state_changed',
        'data':{'entity_id':'sensor.wifi','new_state':None}}})
    assert 'sensor.wifi' not in client.states
