"""The only scheduler. The terminal is a client, never the clock."""
from __future__ import annotations
import asyncio
from datetime import datetime, date, timedelta, timezone
import json
import uuid
from dataclasses import asdict
from typing import Callable
from .model import Settings, Plan, upcoming, plan_for, network_status, valid_local, parse_timestamp
from .store import Store, SettingsFiles
from .ha import HomeAssistant, HAError, alarm_payload, dismiss_payload, command


def read_plan(raw:dict) -> Plan:
    return Plan(raw['date'],datetime.fromisoformat(raw['start']),datetime.fromisoformat(raw['end']),
                datetime.fromisoformat(raw['restore']),tuple(datetime.fromisoformat(x['ring']) for x in raw['alarms']),
                int((datetime.fromisoformat(raw['alarms'][0]['ring'])-datetime.fromisoformat(raw['alarms'][0]['prepare'])).total_seconds()),
                raw['source'])


class Engine:
    def __init__(self,files:SettingsFiles,store:Store,ha=None,clock:Callable[[],datetime]|None=None):
        self.files=files; self.db=store; self.cfg=files.load(); self.ha=ha
        self.clock=clock or (lambda:datetime.now(self.cfg.tz))
        self.lock=asyncio.Lock(); self.revision=0; self.last_network=None; self.retry={}
        self.last_error=''; self.pair_until=0.; self.paired=None; self.cancel_next=0.
        self.test_id=self.db.meta('active_test')
        if self.test_id:
            r=self.db.session(self.test_id)
            if r and not r['completed']: self.db.complete(self.test_id,'Interrupted test')
        self.services_cache=[]; self.event_count=0
    def now(self): return self.clock().astimezone(self.cfg.tz)
    def connected(self): return bool(self.ha and self.ha.ready.is_set())
    def network(self):
        return network_status(self.cfg,self.ha.states if self.ha else {},self.connected(),self.now())
    def note(self,message:str,level:str='INFO'):
        self.db.event(level,message)
        if level=='ERROR': self.last_error=message
    def _session_plan(self,p:Plan)->Plan:
        sid='wake:'+p.day; existing=self.db.session(sid)
        if existing and not existing['completed']:
            saved=read_plan(existing['plan'])
            if saved.start.timestamp()<=self.now().timestamp()<saved.restore.timestamp(): return saved
        return p
    def plans(self,count=7):
        return [self._session_plan(p) for p in upcoming(self.cfg,self.now(),self.db.meta('override'),count)]
    def _ensure(self,p:Plan,kind='real',sid=None)->str:
        sid=sid or 'wake:'+p.day
        self.db.ensure_session(sid,p.public(),kind)
        existing=self.db.session(sid)
        if existing['plan']!=p.public() and not existing['completed']:
            self.db.replace_plan(sid,p.public())
        return sid
    def _active_test(self):
        r=self.db.session(self.test_id) if self.test_id else None
        return r if r and not r['completed'] else None
    def _session_allows_wakeup(self,sid:str,revision:int)->bool:
        r=self.db.session(sid)
        return bool(r and not r['completed'] and self.revision==revision and self.network().state=='Home')
    def _allowed(self,sid:str,due:datetime,revision:int)->bool:
        return self._session_allows_wakeup(sid,revision) and due.timestamp()-self.now().timestamp()>2
    def _label(self,sid:str,due:datetime)->str:
        suffix='_'+sid.split(':',1)[1][:8] if sid.startswith('test:') else ''
        return 'Daybreak_'+due.strftime('%Y%m%d_%H%M')+suffix

    async def on_event(self,event:dict):
        if event.get('event_type')!='tag_scanned': return
        data=event.get('data',{})
        if self.pair_until>self.now().timestamp():
            if data.get('tag_id') and data.get('device_id'):
                self.paired={'tag_id':str(data['tag_id']),'device_id':str(data['device_id'])}
                self.pair_until=0; self.note('Tag captured. Review and save it from Connection to pair.')
            return
        if data.get('tag_id')!=self.cfg.qr_tag_id or data.get('device_id')!=self.cfg.qr_device_id: return
        now=self.now(); self.db.set_meta('last_tag',now.isoformat())
        # Retain the legacy metadata/status contract for existing HA bridges.
        self.db.set_meta('last_qr',now.isoformat())
        test=self._active_test()
        if test: scopes=[test['id']]
        else:
            scopes=[]
            for p in self.plans():
                if p.start.timestamp()<=now.timestamp()<=p.restore.timestamp(): scopes.append(self._ensure(p))
            if not scopes:
                p=plan_for(self.cfg,now.date(),self.db.meta('override'))
                if p: scopes=[self._ensure(p)]
        # No await before durable inhibition: a tag arriving during a REST call wins.
        for sid in scopes:
            self.db.complete(sid,'Tag'); self.note('Wake-up tag accepted. '+('Test finished.' if sid.startswith('test:') else 'Today is complete.'))

    def begin_pairing(self):
        self.paired=None; self.pair_until=self.now().timestamp()+120
        return {'message':'Hold your phone near the bathroom NFC tag within two minutes. Review the captured identifiers before saving.'}

    def cancel_pairing(self):
        self.paired=None; self.pair_until=0
        return {'message':'Pairing cancelled. The saved wake-up tag is unchanged.'}

    async def tick(self):
        async with self.lock:
            now=self.now(); net=self.network()
            if net.state!=self.last_network:
                self.note(f'Home network: {net.state}. {net.reason}')
                if net.state!='Home':
                    self.db.cancel_all()
                    t=self._active_test()
                    if t and t['kind']=='network-test' and self.last_network=='Home' and self.db.count(t['id']):
                        self.db.complete(t['id'],'Network left home')
                self.last_network=net.state
            # Record an expected morning even when the home gate suppresses it.
            if self.db.meta('armed',False) and not self._active_test():
                for p in self.plans():
                    if p.start.timestamp()-p.lead <= now.timestamp() < p.restore.timestamp():
                        self._ensure(p)
            for r in self.db.work_sessions():
                end=parse_timestamp(r['plan']['restore'])
                if not r['completed'] and end and now.timestamp()>=end.timestamp():
                    skipped = r['kind']=='real' and not self.db.count(r['id']) and net.state!='Home'
                    self.db.complete(r['id'],'Skipped: away or network unknown' if skipped else 'Window ended')
                    self.note('Session window ended: '+r['id'])
            await self._cleanup()
            # Finish cancellation before preparing another alarm on the same phone.
            if any(a['status']=='cancel_pending' for a in self.db.alarms()): return
            if net.state!='Home' or not self.cfg.ready(): return
            test=self._active_test()
            if test:
                await self._run_plan(read_plan(test['plan']),test['id']); return
            if not self.db.meta('armed',False): return
            for p in self.plans():
                sid='wake:'+p.day; r=self.db.session(sid)
                if r and r['completed']: continue
                if p.start.timestamp()-self.cfg.lead_seconds <= now.timestamp() <= p.end.timestamp():
                    sid=self._ensure(p); await self._run_plan(p,sid)
                if self._seed_window(p,now):
                    sid=self._ensure(p)
                    label=self._label(sid,p.start); a=self.db.alarm(label)
                    if not a or a['status']=='cancelled': await self._prepare(sid,p.start,seed=True)

    def _seed_window(self,p:Plan,now:datetime)->bool:
        if not self.cfg.evening_enabled or p.start.timestamp()-now.timestamp()<=300: return False
        evening=valid_local(p.start.date()-timedelta(days=1),self.cfg.evening_time,self.cfg.tz)
        return evening.timestamp()<=now.timestamp()<p.start.timestamp()

    async def _run_plan(self,p:Plan,sid:str):
        now=self.now()
        for due in p.rings:
            delta=due.timestamp()-now.timestamp()
            if 2<delta<=p.lead:
                alarm=self.db.alarm(self._label(sid,due))
                if not alarm or not alarm['prepared']: await self._prepare(sid,due)

    async def _prepare(self,sid:str,due:datetime,seed=False):
        revision=self.revision; label=self._label(sid,due)
        if self.now().timestamp()<self.retry.get(label,0) or not self._allowed(sid,due,revision): return
        cfg=self.cfg
        self.db.record_alarm(label,sid,due,cfg,seed)
        self.retry[label]=self.now().timestamp()+3
        try:
            remaining=due.timestamp()-self.now().timestamp()-2
            await self.ha.notify(cfg.notify_service,alarm_payload(due,label,cfg.alarm_package),timeout=min(3,max(.2,remaining)))
            if not seed:
                for payload in (command('command_ringer_mode',command='normal'),
                                command('command_volume_level',media_stream='alarm_stream',command=cfg.loud_alarm),
                                command('command_volume_level',media_stream='ring_stream',command=cfg.loud_ring)):
                    if not self._allowed(sid,due,revision):
                        if not self._session_allows_wakeup(sid,revision):
                            self.db.cancel_all(sid)
                        else:
                            # A slow response must not cancel an otherwise valid alarm.
                            self.db.sent(label,prepared=True)
                            self.note('Alarm accepted; no time remains for further volume commands.','ERROR')
                        return
                    self.db.loud(sid,cfg)
                    remaining=due.timestamp()-self.now().timestamp()-2
                    await self.ha.notify(cfg.notify_service,payload,timeout=min(2,max(.2,remaining)))
            self.db.sent(label,prepared=not seed)
            if not self._session_allows_wakeup(sid,revision): self.db.cancel_all(sid)
            self.note(('Evening seed' if seed else 'Alarm + volume')+f' requested for {due:%H:%M} (HA accepted).')
        except HAError as ex:
            self.note(str(ex),'ERROR')

    async def _cleanup(self):
        now=self.now().timestamp()
        if not self.ha: return
        # A single cancellation per tick keeps tag/network changes responsive.
        for a in self.db.alarms():
            if a['status']!='cancel_pending' or now<self.cancel_next: continue
            key='cancel:'+a['label']
            if now<self.retry.get(key,0): continue
            self.retry[key]=now+3; self.cancel_next=now+1
            try:
                await self.ha.notify(a['notify_service'],dismiss_payload(a['label'],a['package']))
                self.db.cancelled(a['label'])
                self.note('Cancellation requested: '+a['label']+' (HA accepted).')
            except HAError as ex: self.note(str(ex),'ERROR')
            break
        for r in self.db.work_sessions():
            if not r['needs_normal']: continue
            if not r['completed'] and self.network().state=='Home': continue
            key='normal:'+r['id']
            if now<self.retry.get(key,0): continue
            self.retry[key]=now+3
            try:
                for stream,level in [('alarm_stream',r['normal_alarm']),('ring_stream',r['normal_ring'])]:
                    await self.ha.notify(r['normal_service'],command('command_volume_level',media_stream=stream,command=level))
                self.db.normal_done(r['id']); self.note('Daytime volume requested (HA accepted).')
            except HAError as ex: self.note(str(ex),'ERROR')

    def _invalidate_future(self):
        now=self.now().timestamp()
        for r in self.db.work_sessions():
            if r['kind']!='real' or r['completed']: continue
            if datetime.fromisoformat(r['plan']['start']).timestamp()>now:
                self.db.cancel_all(r['id'])
        self.revision+=1

    async def set_override(self,data:dict|None):
        async with self.lock:
            if data:
                if set(data)!= {'date','start'}: raise ValueError('Choose a date and exact start time.')
                try: day=date.fromisoformat(data['date'])
                except (ValueError,TypeError): raise ValueError('Use a valid YYYY-MM-DD date.') from None
                start=valid_local(day,data['start'],self.cfg.tz)
                if start.timestamp()<=self.now().timestamp()+self.cfg.lead_seconds:
                    raise ValueError('Next wake-up must be in the future.')
                r=self.db.session('wake:'+data['date'])
                if r and (r['completed'] or datetime.fromisoformat(r['plan']['start']).timestamp()<=self.now().timestamp()):
                    raise ValueError('This day already started or completed. Choose another date.')
            self._invalidate_future(); self.db.set_meta('override',data)
            self.note('Next wake-up override '+('saved.' if data else 'cleared.'))
            return self.status()

    async def configure(self,changes:dict,token:str|None=None):
        raw=self.cfg.to_dict(); raw.update(changes); new=Settings.from_dict(raw)
        async with self.lock:
            tag_changed=(new.qr_tag_id,new.qr_device_id)!=(self.cfg.qr_tag_id,self.cfg.qr_device_id)
            if tag_changed:
                now=self.now().timestamp()
                if self._active_test() or any(not r['completed'] and
                        datetime.fromisoformat(r['plan']['start']).timestamp()-self.cfg.lead_seconds <= now <=
                        datetime.fromisoformat(r['plan']['restore']).timestamp() for r in self.db.work_sessions()):
                    raise ValueError('Finish the running wake-up or phone test before replacing the tag.')
            if new.notify_service!=self.cfg.notify_service and any(a['status']!='cancelled' for a in self.db.alarms()):
                raise ValueError('Complete alarm cleanup in Diagnostics before changing the phone.')
            self.files.backup()
            if tag_changed:
                # Pause durably before changing the saved identifiers. Never let
                # an old completed test re-confirm a newly paired tag.
                self.db.set_meta('armed',False); self.db.set_meta('test_passed',None)
                self.db.set_meta('active_test',None); self.test_id=None
            self.files.save(new,token)
            reconnect=new.ha_url!=self.cfg.ha_url or bool(token)
            self._invalidate_future(); self.cfg=new
            if tag_changed:
                self.note('Wake-up tag changed. Daily routine paused; run and confirm a new phone test before enabling it.')
            if reconnect:
                if self.ha: await self.ha.close()
                self.ha=None
                if new.ha_url and self.files.token():
                    self.ha=HomeAssistant(new.ha_url,self.files.token(),self.on_event); await self.ha.start()
            self.note('Configuration saved. Running sessions keep their time plan.')
            return self.public_config()

    def public_config(self): return {'settings':self.cfg.to_dict(),'token_set':bool(self.files.token())}

    async def start_test(self,kind='test'):
        async with self.lock:
            if kind not in ('test','network-test'): raise ValueError('Unknown test type.')
            if not self.cfg.ready() or self.network().state!='Home': raise ValueError('Finish Connection and Home Wi-Fi setup; status must be Home.')
            if self._active_test(): raise ValueError('A test is already running. Finish it first.')
            if self.db.meta('armed',False):
                for p in self.plans():
                    r=self.db.session('wake:'+p.day)
                    if p.start.timestamp()-p.lead<=self.now().timestamp()<=p.restore.timestamp() and not (r and r['completed']):
                        raise ValueError('A real wake-up is in progress. Run the test outside that window.')
            if any(a['status']=='cancel_pending' for a in self.db.alarms()): raise ValueError('Wait for pending cleanup to finish.')
            start=self.now().replace(second=0,microsecond=0)+timedelta(minutes=2)
            rings=tuple(start+timedelta(minutes=i) for i in range(3 if kind=='test' else 1))
            p=Plan(start.date().isoformat(),start,rings[-1],rings[-1]+timedelta(minutes=3),rings,self.cfg.lead_seconds,'Live test')
            self.test_id='test:'+uuid.uuid4().hex[:12]
            self.db.ensure_session(self.test_id,p.public(),kind); self.db.set_meta('active_test',self.test_id)
            self.note('Live '+kind+' started. '+('Hold your phone near the NFC tag after the second alarm.' if kind=='test' else 'Leave home Wi-Fi after the test alarm has been prepared.'))
            return p.public()

    def accept_test(self):
        r=self.db.session(self.test_id) if self.test_id else None
        if not r or not r['completed'] or r['reason'] not in ('Tag','QR','Network left home') or not self.db.count(r['id']):
            raise ValueError('Wait for a prepared alarm and tag/network completion before confirming.')
        if any(a['status']=='cancel_pending' for a in self.db.alarms(r['id'])) or r['needs_normal']:
            raise ValueError('Wait for cleanup to finish before confirming.')
        self.db.set_meta('test_passed',self.now().isoformat()); self.note('User confirmed the live phone test.')
        return {'message':'Test confirmed.'}

    async def finish_test(self):
        r=self._active_test()
        if r: self.db.complete(r['id'],'Test stopped'); self.note('Test stopped; cleanup queued.')

    async def check_connection(self):
        if not self.ha: raise ValueError('Save a Home Assistant URL and token in Connection first.')
        await self.ha.poll(); self.services_cache=await self.ha.services()
        return {'message':'HA API reachable. '+('Tag event stream ready.' if self.connected() else 'Waiting for tag event stream.'),
                'services':self.services_cache,'states':list(self.ha.states.values())}

    async def refresh_sensors(self):
        if not self.ha or not self.cfg.notify_service: raise ValueError('Select a phone first.')
        await self.ha.notify(self.cfg.notify_service,command('command_update_sensors'))
        return {'message':'Sensor refresh requested. Unchanged values may keep their original timestamp.'}

    def status(self):
        plans=[]
        for p in self.plans():
            r=self.db.session('wake:'+p.day)
            if not r or not r['completed']: plans.append(p.public())
        test=self.db.session(self.test_id) if self.test_id else None
        return {'now':self.now().isoformat(),'armed':self.db.meta('armed',False),'ready':self.cfg.ready(),
                'connected':self.connected(),'connection_error':self.ha.error if self.ha else 'Enter HA URL and token in Connection.',
                'wake_tag_id':self.cfg.qr_tag_id,'wake_tag_device_id':self.cfg.qr_device_id,
                'network':self.network().public(),'plans':plans,'override':self.db.meta('override'),
                'last_tag':self.db.meta('last_tag',self.db.meta('last_qr')),
                'last_qr':self.db.meta('last_tag',self.db.meta('last_qr')),'test':test,'test_passed':self.db.meta('test_passed'),
                'pending_cleanup':sum(a['status']=='cancel_pending' for a in self.db.alarms()),
                'last_error':self.last_error,'pairing':self.pair_until>self.now().timestamp(),'paired':self.paired,
                'history':self.db.events(80),'sessions':[{**r,'alarm_count':self.db.count(r['id'])} for r in self.db.sessions(30)]}
