"""Home Assistant REST commands and reconnecting WebSocket event stream."""
from __future__ import annotations
import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Awaitable, Callable
import aiohttp
from .model import parse_timestamp

PREFIX='android.intent.extra.alarm.'

class HAError(RuntimeError): pass


def command(message:str,**data)->dict:
    return {'message':message,'data':{'priority':'high','ttl':0,**data}}


def alarm_payload(when:datetime,label:str,package:str)->dict:
    extras=','.join((f'{PREFIX}HOUR:{when.hour}:int',f'{PREFIX}MINUTES:{when.minute}:int',
                     f'{PREFIX}MESSAGE:{label}:String',f'{PREFIX}SKIP_UI:true:boolean',f'{PREFIX}VIBRATE:true:boolean'))
    return command('command_activity',intent_package_name=package,intent_action='android.intent.action.SET_ALARM',intent_extras=extras)


def dismiss_payload(label:str,package:str)->dict:
    return command('command_activity',intent_package_name=package,intent_action='android.intent.action.DISMISS_ALARM',
                   intent_extras=f'{PREFIX}SEARCH_MODE:android.label:String,{PREFIX}MESSAGE:{label}:String')


class HomeAssistant:
    def __init__(self,url:str,token:str,on_event:Callable[[dict],Awaitable[None]]|None=None):
        self.url=url.rstrip('/'); self.token=token; self.on_event=on_event
        self.ready=asyncio.Event(); self.states:dict={}; self.error='Not connected'
        self.session:aiohttp.ClientSession|None=None; self._task=None; self._closing=False
        self._ws=None; self.last_poll=0.0; self.subscribed=False
    async def __aenter__(self): await self.start(); return self
    async def __aexit__(self,*args): await self.close()
    async def start(self):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),trust_env=False)
        self._task=asyncio.create_task(self._listen(),name='ha-events')
    async def close(self):
        self._closing=True; self.ready.clear()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError): await self._task
        if self.session: await self.session.close()
    async def request(self,method:str,path:str,data:dict|None=None,timeout:float=5):
        if not self.session: raise HAError('HA client is not started.')
        try:
            async with self.session.request(method,self.url+path,json=data,
                headers={'Authorization':'Bearer '+self.token},allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status not in (200,201):
                    if response.status==401: raise HAError('HA authentication failed (401). Check the token in Connection.')
                    if response.status==403: raise HAError('HA denied this operation (403).')
                    raise HAError(f'HA returned HTTP {response.status}. Use its direct API base address.')
                return await response.json()
        except HAError: raise
        except asyncio.TimeoutError: raise HAError('HA request timed out; phone execution is unconfirmed.') from None
        except (aiohttp.ClientError,ValueError,OSError,RuntimeError): raise HAError('HA connection failed or returned a non-JSON response.') from None
    async def notify(self,service:str,payload:dict,timeout:float=3):
        domain,action=service.split('.',1)
        if domain!='notify' or not action.replace('_','').isalnum(): raise HAError('Invalid notification action.')
        return await self.request('POST',f'/api/services/notify/{action}',payload,timeout)
    async def services(self): return await self.request('GET','/api/services')
    def _accept_state(self,value:dict):
        eid=value.get('entity_id')
        if not eid: return
        old=self.states.get(eid,{})
        new_time=parse_timestamp(value.get('last_updated')); old_time=parse_timestamp(old.get('last_updated'))
        if new_time and old_time and new_time<old_time: return
        self.states[eid]=value
    async def poll(self):
        states=await self.request('GET','/api/states')
        if not isinstance(states,list): raise HAError('Unexpected HA state response.')
        returned={x.get('entity_id') for x in states}
        for eid in set(self.states)-returned: self.states.pop(eid,None)
        for x in states: self._accept_state(x)
        self.last_poll=asyncio.get_running_loop().time()
        return states
    async def _dispatch(self,message:dict):
        if message.get('type')!='event': return
        event=message.get('event',{})
        if event.get('event_type')=='state_changed':
            data=event.get('data',{}); state=data.get('new_state')
            if state: self._accept_state(state)
            else: self.states.pop(data.get('entity_id'),None)
        if self.on_event: await self.on_event(event)
    async def _listen(self):
        retry=1
        while not self._closing:
            try:
                async with self.session.ws_connect(self.url+'/api/websocket',heartbeat=15,timeout=aiohttp.ClientWSTimeout(ws_receive=45),
                    max_msg_size=8*1024*1024) as ws:
                    self._ws=ws
                    if (await ws.receive_json(timeout=8)).get('type')!='auth_required': raise HAError('Unexpected HA handshake.')
                    await ws.send_json({'type':'auth','access_token':self.token})
                    if (await ws.receive_json(timeout=8)).get('type')!='auth_ok': raise HAError('HA event authentication failed.')
                    for ident,event in enumerate(('state_changed','tag_scanned'),1):
                        await ws.send_json({'id':ident,'type':'subscribe_events','event_type':event})
                        while True:
                            msg=await ws.receive_json(timeout=8)
                            if msg.get('type')=='result' and msg.get('id')==ident:
                                if not msg.get('success'): raise HAError('HA denied the event subscription.')
                                break
                            await self._dispatch(msg)
                    self.subscribed=True
                    await self.poll(); self.ready.set(); self.error=''; retry=1
                    async for msg in ws:
                        if msg.type==aiohttp.WSMsgType.TEXT: await self._dispatch(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.ERROR,aiohttp.WSMsgType.CLOSED): break
                    self.error='HA event connection closed.'
            except asyncio.CancelledError: raise
            except HAError as ex: self.error=str(ex)
            except Exception: self.error='HA event connection interrupted. Retrying.'
            finally: self.ready.clear(); self._ws=None; self.subscribed=False
            await asyncio.sleep(retry); retry=min(retry*2,15)
