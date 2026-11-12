"""Pure scheduling and reported-home-network decisions (no I/O)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from math import floor
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ('mon','tue','wed','thu','fri','sat','sun')
UNKNOWN = {'unknown','unavailable','none','<unknown ssid>','', '02:00:00:00:00:00'}
DISCONNECTED = {'not connected','disconnected','off','none connected','no connection'}


def minutes(value: str) -> int:
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
        raise ValueError('Enter a time as HH:MM (00:00–23:59).')
    h,m=map(int,value.split(':')); return h*60+m


@dataclass
class Settings:
    ha_url: str = ''
    notify_service: str = ''
    # Legacy key names remain stable for installed settings and backups. The
    # identifiers apply to HA tag_scanned events from NFC as well as old QR tags.
    qr_tag_id: str = ''
    qr_device_id: str = ''
    timezone: str = 'Europe/Warsaw'
    alarm_package: str = 'com.google.android.deskclock'
    weekly: dict[str,str|None] = field(default_factory=lambda:{d:'05:55' for d in DAYS})
    dense_minutes: int = 15
    dense_step: int = 1
    sparse_minutes: int = 3
    lead_seconds: int = 10
    evening_enabled: bool = True
    evening_time: str = '22:00'
    loud_alarm: int = 7
    loud_ring: int = 7
    normal_alarm: int = 4
    normal_ring: int = 3
    ssid_entity: str = ''
    bssid_entity: str = ''
    home_ssids: list[str] = field(default_factory=list)
    home_bssids: list[str] = field(default_factory=list)
    heartbeat_entity: str = ''
    heartbeat_max_minutes: int = 30
    refresh_seconds: int = 120
    poll_seconds: int = 15

    @classmethod
    def from_dict(cls, raw: dict) -> Settings:
        # Older installations and backups include the former end-of-session settings.
        raw={k:v for k,v in raw.items() if k not in ('minimum_minutes','round_minutes','restore_minutes')}
        allowed={f.name for f in fields(cls)}
        if set(raw)-allowed: raise ValueError('Unrecognized settings: '+', '.join(sorted(set(raw)-allowed)))
        s=cls(**raw); validate(s); return s

    def to_dict(self) -> dict: return asdict(self)

    def ready(self) -> bool:
        return bool(self.ha_url and self.notify_service and self.qr_tag_id and self.qr_device_id
                    and self.ssid_entity and self.bssid_entity and self.home_ssids and self.home_bssids)

    @property
    def tz(self) -> ZoneInfo: return ZoneInfo(self.timezone)


def validate(s: Settings) -> None:
    if s.ha_url:
        u=urlsplit(s.ha_url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Home Assistant URL must be HTTP(S), without a password, query or fragment.')
        if u.path.rstrip('/') in ('/api','/api/websocket'): raise ValueError('Use the HA base URL, not /api.')
    try: ZoneInfo(s.timezone)
    except (ZoneInfoNotFoundError,ValueError,TypeError): raise ValueError('Unknown IANA time zone.') from None
    if s.notify_service and not re.fullmatch(r'notify\.[a-z0-9_]+',s.notify_service):
        raise ValueError('Choose a notify.mobile_app_… action.')
    for attr in ('ssid_entity','bssid_entity','heartbeat_entity'):
        v=getattr(s,attr)
        if v and not re.fullmatch(r'(sensor|binary_sensor)\.[a-z0-9_]+',v): raise ValueError(f'Invalid {attr}.')
    if not isinstance(s.weekly,dict) or set(s.weekly)!=set(DAYS): raise ValueError('Weekly routine must contain all seven weekdays.')
    for v in s.weekly.values():
        if v is not None: minutes(v)
    limits={'dense_minutes':(0,120),'dense_step':(1,30),
            'sparse_minutes':(1,60),'lead_seconds':(1,45),
            'loud_alarm':(0,100),'loud_ring':(0,100),'normal_alarm':(0,100),'normal_ring':(0,100),
            'heartbeat_max_minutes':(1,1440),'refresh_seconds':(30,3600),'poll_seconds':(5,120)}
    for k,(lo,hi) in limits.items():
        v=getattr(s,k)
        if type(v) is not int or not lo<=v<=hi: raise ValueError(f'{k.replace("_"," ").title()} must be {lo}–{hi}.')
    minutes(s.evening_time)
    if type(s.evening_enabled) is not bool: raise ValueError('Evening seed must be enabled or disabled.')
    if not isinstance(s.home_ssids,list) or any(not isinstance(x,str) or not x.strip() for x in s.home_ssids):
        raise ValueError('Home SSIDs must be nonempty network names.')
    if not isinstance(s.home_bssids,list) or any(not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',x) for x in s.home_bssids):
        raise ValueError('BSSID must look like aa:bb:cc:dd:ee:ff.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]+',s.alarm_package): raise ValueError('Invalid Android clock package.')
    for k in ('qr_tag_id','qr_device_id'):
        if not isinstance(getattr(s,k),str) or len(getattr(s,k))>256: raise ValueError('Invalid tag identifier.')


def localize(naive: datetime, tz: ZoneInfo) -> datetime:
    """First occurrence for fall-back; move spring-gap wall times forward."""
    for _ in range(181):
        aware=naive.replace(tzinfo=tz,fold=0)
        if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==naive:
            return aware
        naive+=timedelta(minutes=1)
    raise ValueError('Could not resolve local time.')


def valid_local(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    minutes(hhmm)
    return localize(datetime.combine(day,time.fromisoformat(hhmm)),tz)


@dataclass(frozen=True)
class Cadence:
    dense_minutes: int
    dense_step: int
    sparse_minutes: int
    timezone: str


@dataclass(frozen=True)
class Plan:
    day: str
    start: datetime
    end: datetime|None
    restore: datetime|None
    rings: tuple[datetime,...]
    lead: int
    source: str
    cadence: Cadence|None = None

    def next_ring(self, after: float) -> datetime|None:
        if self.cadence is None:
            return next((ring for ring in self.rings if ring.timestamp()>after),None)
        c=self.cadence
        start=self.start.timestamp()
        minute=max(0,floor((after-start)/60)+1)
        dense=((minute+c.dense_step-1)//c.dense_step)*c.dense_step
        if dense<=c.dense_minutes:
            offset=dense
        else:
            later=max(1,(minute-c.dense_minutes+c.sparse_minutes-1)//c.sparse_minutes)
            offset=c.dense_minutes+later*c.sparse_minutes
        return datetime.fromtimestamp(start+offset*60,ZoneInfo(c.timezone))

    def public(self) -> dict:
        rings=self.rings
        if self.cadence:
            preview=[self.start]
            for _ in range(8):
                preview.append(self.next_ring(preview[-1].timestamp()))
            rings=preview
        result={'date':self.day,'start':self.start.isoformat(),'end':self.end.isoformat() if self.end else None,
                'restore':self.restore.isoformat() if self.restore else None,'source':self.source,
                'count':None if self.cadence else len(rings),
                'alarms':[{'ring':t.isoformat(),
                           'prepare':datetime.fromtimestamp(t.timestamp()-self.lead,t.tzinfo).isoformat()} for t in rings]}
        if self.cadence:
            result.update(cadence=asdict(self.cadence),lead_seconds=self.lead)
        return result

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.public(),sort_keys=True).encode()).hexdigest()[:12]


def plan_for(s:Settings, day:date, override:dict|None=None) -> Plan|None:
    source='Weekly routine'; hhmm=s.weekly[DAYS[day.weekday()]]
    if override and override.get('date')==day.isoformat():
        hhmm=override.get('start'); source='One-time override'
    if hhmm is None: return None
    start=valid_local(day,hhmm,s.tz)
    cadence=Cadence(s.dense_minutes,s.dense_step,s.sparse_minutes,s.timezone)
    return Plan(day.isoformat(),start,None,None,(),s.lead_seconds,source,cadence)


def upcoming(s:Settings, now:datetime, override:dict|None=None, count:int=7) -> list[Plan]:
    plans=[]
    for n in range(-1,15):
        p=plan_for(s,now.date()+timedelta(days=n),override)
        if p and p.start.timestamp()>=now.timestamp()-60: plans.append(p)
        if len(plans)>=count: break
    return plans


def parse_timestamp(v:str|None) -> datetime|None:
    try:
        d=datetime.fromisoformat((v or '').replace('Z','+00:00'))
        return d if d.tzinfo else None
    except (ValueError,TypeError): return None


@dataclass(frozen=True)
class Network:
    state: str
    reason: str
    ssid: str=''
    bssid: str=''
    ssid_reported: str=''
    bssid_reported: str=''
    def public(self)->dict: return asdict(self)


def network_status(s:Settings, states:dict, connected:bool, now:datetime|None=None) -> Network:
    a=states.get(s.ssid_entity,{}) or {}; b=states.get(s.bssid_entity,{}) or {}
    ssid=str(a.get('state','')).strip().strip('"')
    bssid=str(b.get('state','')).strip().lower()
    args=(ssid,bssid,a.get('last_reported') or a.get('last_updated',''),b.get('last_reported') or b.get('last_updated',''))
    def result(status,why): return Network(status,why,*args)
    if not connected: return result('Unknown','Home Assistant connection is not ready.')
    if not (s.ssid_entity and s.bssid_entity and s.home_ssids and s.home_bssids):
        return result('Unknown','Select Wi-Fi sensors and register your home network.')
    if ssid.lower() in DISCONNECTED or bssid in DISCONNECTED:
        return result('Away','Phone reports no Wi-Fi connection.')
    if ssid.lower() in UNKNOWN or bssid in UNKNOWN:
        return result('Unknown','Wi-Fi sensor data is missing or unavailable.')
    if ssid not in s.home_ssids or bssid not in [x.lower() for x in s.home_bssids]:
        return result('Away','Reported Wi-Fi is not an approved home network.')
    if s.heartbeat_entity:
        h=states.get(s.heartbeat_entity,{}) or {}
        stamp=parse_timestamp(h.get('last_reported') or h.get('last_updated'))
        age=((now or datetime.now(timezone.utc))-stamp).total_seconds() if stamp else float('inf')
        if str(h.get('state','')).lower() in UNKNOWN or age>s.heartbeat_max_minutes*60 or age < -60:
            return result('Unknown','Phone heartbeat is missing or too old.')
    return result('Home','SSID and BSSID match the approved home network.')
