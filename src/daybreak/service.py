"""Local-only control API and independent daemon lifecycle."""
from __future__ import annotations
import asyncio
from contextlib import suppress
from datetime import date,datetime,timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import sys
import aiohttp
from aiohttp import web
from . import __version__
from .engine import Engine
from .model import Settings,plan_for
from .ha import HAError,HomeAssistant
from .store import Store,SettingsFiles,atomic_json

LOG=logging.getLogger('daybreak')

@web.middleware
async def errors(request,handler):
    try: return await handler(request)
    except (ValueError,TypeError,KeyError,HAError) as ex:
        return web.json_response({'error':str(ex)},status=400)
    except web.HTTPException: raise
    except Exception:
        LOG.exception('Local control request failed')
        return web.json_response({'error':'Internal error. Check Diagnostics or the service journal.'},status=500)


def build_app(engine:Engine)->web.Application:
    app=web.Application(middlewares=[errors],client_max_size=128*1024)
    async def status(req): return web.json_response(engine.status())
    async def get_config(req): return web.json_response(engine.public_config())
    async def put_config(req):
        data=await req.json()
        return web.json_response(await engine.configure(data.get('settings',{}),data.get('token')))
    async def action(req):
        data=await req.json(); name=data.get('name')
        if name=='connect': result=await engine.check_connection()
        elif name=='discover':
            if not engine.ha: raise ValueError('Save the HA URL and token first.')
            await engine.ha.poll(); engine.services_cache=await engine.ha.services()
            result={'states':list(engine.ha.states.values()),'services':engine.services_cache}
        elif name=='refresh': result=await engine.refresh_sensors()
        elif name=='pair': result=engine.begin_pairing()
        elif name=='save_pair':
            if not engine.paired: raise ValueError('No QR has been captured yet.')
            result=await engine.configure({'qr_tag_id':engine.paired['tag_id'],'qr_device_id':engine.paired['device_id']})
            engine.paired=None
        elif name=='use_wifi':
            n=engine.network()
            if not engine.ha or not engine.connected(): raise ValueError('Connect to Home Assistant first.')
            states=engine.ha.states
            ssid=str(states.get(engine.cfg.ssid_entity,{}).get('state','')).strip().strip('"')
            bssid=str(states.get(engine.cfg.bssid_entity,{}).get('state','')).lower().strip()
            from .model import UNKNOWN,DISCONNECTED
            if ssid.lower() in UNKNOWN|DISCONNECTED or bssid in UNKNOWN|DISCONNECTED:
                raise ValueError('The selected sensors must currently report your home Wi-Fi.')
            result=await engine.configure({'home_ssids':list(dict.fromkeys(engine.cfg.home_ssids+[ssid])),
                                           'home_bssids':list(dict.fromkeys(engine.cfg.home_bssids+[bssid]))})
        elif name=='preview':
            p=plan_for(engine.cfg,date.fromisoformat(data['date']),{'date':data['date'],'start':data['start']})
            result=p.public()
        elif name=='set_override': result=await engine.set_override(data.get('override'))
        elif name in ('start_test','start_network_test'):
            result=await engine.start_test('test' if name=='start_test' else 'network-test')
        elif name=='accept_test': result=engine.accept_test()
        elif name=='stop_test': await engine.finish_test(); result={'message':'Test cleanup queued.'}
        elif name=='cleanup':
            engine.db.cancel_all(); await engine.finish_test(); result={'message':'Daybreak alarm cleanup queued.'}
        elif name=='arm':
            enabled=data.get('enabled')
            if type(enabled) is not bool: raise ValueError('enabled must be true or false.')
            if enabled and (not engine.cfg.ready() or not engine.db.meta('test_passed')):
                raise ValueError('Complete setup and confirm a real phone test before enabling the routine.')
            engine.db.set_meta('armed',enabled)
            if not enabled:
                engine.db.cancel_all()
                for r in engine.db.work_sessions():
                    if r['needs_normal']: engine.db.complete(r['id'],'Operator maintenance')
            result={'message':'Daily routine '+('enabled.' if enabled else 'paused.')}
        elif name=='backup': result={'name':engine.files.backup()}
        elif name=='backups': result={'backups':engine.files.backups()}
        elif name=='restore_backup':
            backup=engine.files.read_backup(data['file']); result=await engine.configure(backup.to_dict())
        elif name=='report':
            result={'daybreak':__version__,'python':sys.version.split()[0],
                    'network':engine.network().state,'connected':engine.connected(),
                    'armed':engine.db.meta('armed',False),'pending_cleanup':engine.status()['pending_cleanup'],
                    'events':engine.db.events(100)}
        else: raise ValueError('Unknown control action.')
        return web.json_response(result)
    app.router.add_get('/status',status); app.router.add_get('/config',get_config)
    app.router.add_post('/config',put_config); app.router.add_post('/action',action)
    return app


async def serve(state_dir:Path,socket:Path):
    state_dir.mkdir(parents=True,exist_ok=True); socket.parent.mkdir(parents=True,exist_ok=True)
    lock=(state_dir/'service.lock').open('a')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise RuntimeError('A Daybreak daemon is already using this data directory.') from None
    files=SettingsFiles(state_dir); db=Store(state_dir/'daybreak.sqlite3'); e=Engine(files,db)
    if e.cfg.ha_url and files.token():
        e.ha=HomeAssistant(e.cfg.ha_url,files.token(),e.on_event); await e.ha.start()
    if socket.exists(): socket.unlink()
    runner=web.AppRunner(build_app(e)); await runner.setup()
    await web.UnixSite(runner,str(socket)).start(); os.chmod(socket,0o660)
    stop=asyncio.Event(); loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,stop.set)
    e.note('Daybreak service started. Control socket ready.'); LOG.info('Daybreak service ready.')
    async def scheduler():
        while not stop.is_set():
            try: await e.tick()
            except Exception:
                LOG.exception('Scheduler tick failed'); e.note('Scheduler error; retrying. See service journal.','ERROR')
            await asyncio.sleep(.25)
    async def sensors():
        last_poll=0.; last_refresh=0.
        while not stop.is_set():
            ha=e.ha; now=loop.time()
            if ha and now-last_poll>=e.cfg.poll_seconds:
                last_poll=now
                try:
                    await ha.poll()
                    if getattr(ha,'subscribed',False): ha.ready.set()
                except HAError:
                    ha.ready.clear()
            if ha and e.cfg.notify_service and now-last_refresh>=e.cfg.refresh_seconds:
                last_refresh=now
                with suppress(HAError): await e.refresh_sensors()
            await asyncio.sleep(1)
    tasks=[asyncio.create_task(scheduler()),asyncio.create_task(sensors())]
    try: await stop.wait()
    finally:
        for task in tasks: task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError): await task
        if e.ha: await e.ha.close()
        await runner.cleanup(); db.close(); lock.close()
        with suppress(FileNotFoundError): socket.unlink()


class Control:
    """The TUI never opens the scheduler DB or credentials file."""
    def __init__(self,socket:Path): self.socket=socket
    async def request(self,method:str,path:str,data:dict|None=None):
        try:
            async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(self.socket)),
                  timeout=aiohttp.ClientTimeout(total=25)) as session:
                async with session.request(method,'http://localhost'+path,json=data) as response:
                    value=await response.json()
                    if response.status>=400: raise ValueError(value.get('error','Control request failed.'))
                    return value
        except (aiohttp.ClientError,OSError,asyncio.TimeoutError):
            raise ValueError('Daybreak service is not reachable. Open System → Start service.') from None
    async def status(self): return await self.request('GET','/status')
    async def config(self): return await self.request('GET','/config')
    async def save(self,settings:dict,token:str|None=None): return await self.request('POST','/config',{'settings':settings,'token':token})
    async def action(self,name:str,**data): return await self.request('POST','/action',{'name':name,**data})
