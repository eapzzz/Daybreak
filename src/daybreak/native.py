"""Signed delivery of a wake-up to the optional Android alarm app."""
from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime
import hmac
import secrets
from typing import TYPE_CHECKING

from .ha import HAError, command

if TYPE_CHECKING:
    from .engine import Engine

INTERVAL=10
START_LEAD=12
LEASE_MS=75_000
REFRESH_MS=25_000
STOP_RETRY_MS=2_000


def payload(key:str,action:str,sid:str,start:int,issued:int,expires:int)->dict:
    message=f'1|{action}|{sid}|{start}|{issued}|{expires}'.encode('ascii')
    signature=hmac.digest(bytes.fromhex(key),message,'sha256')
    token='.'.join(urlsafe_b64encode(part).rstrip(b'=').decode('ascii') for part in (message,signature))
    return command('command_broadcast_intent',intent_action='daybreak.android.CONTROL',
                   intent_package_name='daybreak.android',intent_class_name='daybreak.android.CommandReceiver',
                   intent_extras=f'token:{token}:String')


class NativeAlarms:
    def __init__(self,engine:Engine):
        self.engine=engine
        self.db=engine.db

    @property
    def enabled(self)->bool:
        return self.db.meta('native_enabled',False)

    @property
    def interval(self)->int:
        return INTERVAL if self.enabled else 60

    def now(self)->int:
        return int(self.engine.now().timestamp()*1000)

    def pairing_key(self)->str:
        key=self.db.meta('native_key')
        if not key:
            key=secrets.token_hex(32)
            self.db.set_meta('native_key',key)
        return key

    def trial(self):
        trial=self.db.meta('native_trial')
        if trial and not trial['result'] and self.now()<trial['expires']:
            return trial
        return None

    def unverify(self):
        self.db.set_meta('native_enabled',False)
        self.db.set_meta('native_trial',None)

    def _check_idle(self):
        e=self.engine
        if e.active_wake() or e._active_test() or self.trial():
            raise ValueError('Finish the current wake-up or test first.')
        if self.db.meta('native_delivery'):
            raise ValueError('Wait for the Android alarm to stop.')

    def start_trial(self):
        self._check_idle()
        e=self.engine
        if not self.db.meta('native_key'):
            raise ValueError('Show the pairing key and save it in the Android app first.')
        if not e.cfg.ready() or e.network().state!='Home' or self.db.pending_cleanup():
            raise ValueError('The phone must be on home Wi-Fi and previous cleanup must be complete.')
        start=((self.now()+999)//1000+START_LEAD)*1000
        expires=start+30_000
        if self.db.meta('armed',False) and any(p.start.timestamp()*1000<=expires+START_LEAD*1000 for p in e.plans()):
            raise ValueError('The daily wake-up is due soon. Test the app after it finishes.')
        self.db.set_meta('native_trial',{'sid':'app-test:'+secrets.token_hex(6),'start':start,
                                         'expires':expires,'result':None})
        e.note('Android link test started.')
        return {'start':datetime.fromtimestamp(start/1000,e.cfg.tz).isoformat()}

    def tag(self)->bool:
        trial=self.trial()
        if not trial: return False
        delivery=self.db.meta('native_delivery')
        if (self.now()>=trial['start']+INTERVAL*1000 and delivery
                and delivery['sid']==trial['sid'] and delivery['accepted']):
            result='Tag'
        else:
            result='Tag scanned before second ring'
        self.db.set_meta('native_trial',{**trial,'result':result})
        self.engine.note('Android link test: '+result)
        return True

    def set_enabled(self,enabled:bool):
        self._check_idle()
        if enabled and (self.db.meta('native_trial') or {}).get('result')!='Tag':
            raise ValueError('Run the Android link test and scan the tag after the second ring first.')
        if enabled!=self.enabled:
            self.db.set_meta('native_enabled',enabled)
            self.engine._invalidate_future()
            self.engine.note('Android 10-second mode '+('enabled.' if enabled else 'disabled.'))
        return {'message':'Android 10-second mode '+('enabled.' if enabled else 'disabled.')}

    def public(self):
        trial=self.db.meta('native_trial')
        delivery=self.db.meta('native_delivery')
        result=trial['result'] if trial else None
        if trial and not result and self.now()>=trial['expires']:
            result='Test expired'
        return {'enabled':self.enabled,'paired':bool(self.db.meta('native_key')),
                'testing':bool(self.trial()),'trial_result':result,
                'stopping':bool(result and delivery and delivery['sid']==trial['sid'])}

    async def tick(self,active,allowed:bool)->bool:
        now=self.now()
        trial=self.trial()
        desired=None
        if allowed:
            if trial:
                desired=(trial['sid'],trial['start'],trial['expires'])
            elif self.enabled and active:
                sid,plan=active
                desired=(sid,int(plan.start.timestamp()*1000),now+LEASE_MS)
        delivery=self.db.meta('native_delivery')
        if delivery and (not desired or desired[0]!=delivery['sid']):
            await self._stop(delivery,now)
            return True
        if desired:
            await self._run(desired,delivery,now)
        return bool(trial or desired)

    def _issued(self,now:int)->int:
        issued=max(now,self.db.meta('native_issued',0)+1)
        self.db.set_meta('native_issued',issued)
        return issued

    async def _run(self,desired,delivery,now:int):
        sid,start,expires=desired
        if delivery and now<delivery['next_attempt']: return
        service=self.engine.cfg.notify_service
        issued=self._issued(now)
        # Record the possible delivery before awaiting HA, including on timeout.
        record={'sid':sid,'expires':expires,'service':service,
                'next_attempt':now+STOP_RETRY_MS,'accepted':False,'stops':0}
        if delivery:
            record['expires']=max(record['expires'],delivery['expires'])
            record['accepted']=delivery['accepted']
        self.db.set_meta('native_delivery',record)
        try:
            await self.engine.ha.notify(service,payload(self.db.meta('native_key'),'run',sid,start,issued,expires))
        except HAError as error:
            self.engine.note(str(error),'ERROR')
        else:
            record.update(accepted=True,next_attempt=now+REFRESH_MS)
            self.db.set_meta('native_delivery',record)

    async def _stop(self,delivery,now:int):
        if now>=delivery['expires']:
            self.db.set_meta('native_delivery',None)
            return
        if now<delivery.get('next_stop',0) or not self.engine.ha: return
        issued=self._issued(now)
        delivery['next_stop']=now+STOP_RETRY_MS
        delivery['next_attempt']=now
        self.db.set_meta('native_delivery',delivery)
        try:
            await self.engine.ha.notify(delivery['service'],
                payload(self.db.meta('native_key'),'stop',delivery['sid'],0,issued,0))
        except HAError as error:
            self.engine.note(str(error),'ERROR')
        else:
            delivery['stops']+=1
            self.db.set_meta('native_delivery',delivery if delivery['stops']<3 else None)
