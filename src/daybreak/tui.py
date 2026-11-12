"""Daybreak's full-screen SSH console. It never runs the scheduler itself."""
from __future__ import annotations
import asyncio
from contextlib import suppress
from datetime import date,datetime,timedelta
import json
import os
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.focus import focus_next,focus_previous
from prompt_toolkit.layout import Layout,HSplit,VSplit,Window,FloatContainer,Float,DynamicContainer,ScrollablePane
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea,Button,Frame,Label,RadioList,Checkbox,Dialog,Box

from . import __version__
from .model import Settings,DAYS,plan_for,upcoming,parse_timestamp
from .native import INTERVAL
from . import system

PAGES=[('overview','Overview'),('next','Next wake-up'),('routine','Routine'),('connection','Connection'),
       ('wifi','Home Wi-Fi'),('sound','Sound'),('diagnostics','Diagnostics'),('history','History'),('system','System')]
STYLE=Style.from_dict({
    '':'bg:#0c121d #d9e3f0','header':'bg:#141e2d #f4bc6b bold','muted':'#8699b2',
    'accent':'#f4bc6b bold','good':'#79d5b0','bad':'#ff8b91','title':'#ecf3ff bold',
    'sidebar':'bg:#101927 #8497b0','sidebar radio-list':'bg:#101927 #a4b4cb',
    'radio-selected':'bg:#233249 #f4bc6b bold','radio-checked':'#f4bc6b bold',
    'frame.border':'#304157','frame.label':'#f4bc6b bold',
    'text-area':'bg:#172436 #e9eef5','text-area.prompt':'#91a8c4',
    'button':'bg:#21334a #dce7f4','button.focused':'bg:#f4bc6b #111927 bold',
    'button.arrow':'#0c121d','button.text':'','checkbox':'#97a9bf',
    'checkbox-selected':'#f4bc6b','checkbox-checked':'#79d5b0',
    'dialog':'bg:#162235 #d9e3f0','dialog.body':'bg:#162235 #d9e3f0',
    'dialog frame.border':'#f4bc6b','dialog shadow':'bg:#05080e',
    'footer':'bg:#141e2d #96acc7','toast':'bg:#132d2a #96dfc3','toast-error':'bg:#38202a #ffabb1',
    'scrollbar.background':'bg:#152031','scrollbar.button':'bg:#415777','scrollbar.arrow':'#f4bc6b',
    'selected-text':'bg:#395275 #ffffff',
})


def plain(text='',height=None,style=''):
    return Window(FormattedTextControl(text),height=height,style=style,wrap_lines=True)


def gap(n=1): return Window(height=n,char=' ')


def field(title,value='',width=None,password=False,height=1):
    area=TextArea(text=str(value),height=height,width=width,multiline=height>1,password=password,
                  wrap_lines=False,style='class:text-area')
    return area,HSplit([plain(title,1,'class:muted'),area],padding=0)


def buttons(*items): return VSplit(list(items),padding=2,height=1,align='LEFT')


def stamp(value,seconds=False):
    d=parse_timestamp(value)
    return d.strftime('%H:%M:%S' if seconds else '%H:%M') if d else '—'


def plan_text(p:dict|None)->str:
    if not p: return 'No wake-up scheduled.'
    fast=p.get('interval_seconds')==INTERVAL
    if p.get('repeat_timezone'):
        lines=[f"{p['date']}  ·  First ring {stamp(p['start'])}  ·  {p['source']}",
               'Every 10 seconds until tag confirmation.' if fast else 'Every minute until tag confirmation.',
               'Daytime volume returns after confirmation.', '', ' FIRST ALARMS']
    else:
        lines=[f"{p['date']}  ·  {stamp(p['start'])} → {stamp(p['end'])}  ·  {p['count']} alarms",
               f"Daytime volume: {stamp(p['restore'])}  ·  {p['source']}", '']
    lines.extend([' PREPARE    RING       PREPARE    RING       PREPARE    RING',
                  ' ───────────────────────────────────────────────────────────'])
    rows=[f" {stamp(x['prepare'],True)}   {stamp(x['ring'],fast)} " for x in p['alarms']]
    for i in range(0,len(rows),3): lines.append('  '.join(rows[i:i+3]))
    return '\n'.join(lines)


class Console:
    def __init__(self,control,*,input=None,output=None,demo=False):
        self.control=control; self.demo=demo; self.status_data={}; self.cfg=Settings(); self.token_set=False
        self.message='Connecting to Daybreak…'; self.message_error=False; self.busy=False; self.tasks=set()
        self.modal_future=None; self._loaded=False; self._service_label=''; self._system_data=''
        self.menu=RadioList([(key,f' F{i+1}  {title}') for i,(key,title) in enumerate(PAGES)],default='overview',
                            select_on_focus=True,open_character='',select_character='',close_character='',show_scrollbar=False)
        self.views=self._build_pages()
        sidebar=HSplit([gap(),plain('   CONTROL ROOM',1,'class:muted'),gap(),self.menu,
                        Window(height=D(weight=1)),plain('   LOCAL ONLY\n   SSH CONSOLE',2,'class:muted'),gap()],
                       width=24,style='class:sidebar')
        content=HSplit([
            Window(FormattedTextControl(self._header),height=3,style='class:header'),
            VSplit([sidebar,Window(width=1,char='│',style='class:muted'),
                    Box(DynamicContainer(lambda:self.views[self.menu.current_value]),padding=1)],padding=0),
            Window(FormattedTextControl(lambda:self.message),height=1,
                   style=lambda:'class:toast-error' if self.message_error else 'class:toast'),
            plain('  Tab / Shift-Tab  Move    Enter  Select    F1–F9  Pages    Ctrl-Q  Close console',1,'class:footer')
        ])
        self.root=FloatContainer(content=content,floats=[])
        kb=KeyBindings()
        kb.add('tab')(focus_next); kb.add('s-tab')(focus_previous)
        @kb.add('c-q')
        def exit_app(event):
            if self.modal_future and not self.modal_future.done(): self.modal_future.set_result(None)
            else: event.app.exit()
        @kb.add('escape')
        def close_modal(event):
            if self.modal_future and not self.modal_future.done(): self.modal_future.set_result(None)
        for n,(key,_) in enumerate(PAGES,1):
            def switch(event,key=key):
                if not self.modal_future: self.goto(key)
            kb.add('f'+str(n))(switch)
        self.app=Application(layout=Layout(self.root,focused_element=self.menu),key_bindings=kb,style=STYLE,
                             full_screen=True,mouse_support=True,color_depth=ColorDepth.TRUE_COLOR,
                             input=input,output=output,min_redraw_interval=.04)

    def goto(self,page):
        self.menu.current_value=page; self.menu._selected_index=[p[0] for p in PAGES].index(page)
        self.app.layout.focus(self.menu); self.app.invalidate()

    def _header(self):
        suffix='DEMO • no phone commands' if self.demo else 'Wake up. Move on.'
        clock=stamp(self.status_data.get('now'),True)
        return FormattedText([('class:accent','  ──╮  DAYBREAK'),('class:muted',f'    {suffix}'),
                              ('class:muted',f'                     {clock}\n'),
                              ('class:accent','  ──╯  '),('class:muted','Home-aware wake-up service  /  '+__version__)])

    def button(self,label,fn,*args,width=None):
        return Button(label,handler=lambda:self.launch(fn,*args),width=width or max(14,len(label)+4))

    def launch(self,fn,*args):
        if self.busy: self.toast('Wait for the current operation to finish.'); return
        async def job():
            self.busy=True
            try: await fn(*args)
            except asyncio.CancelledError: raise
            except Exception as ex: self.toast(str(ex),True)
            finally: self.busy=False; self.app.invalidate()
        task=asyncio.create_task(job()); self.tasks.add(task); task.add_done_callback(self.tasks.discard)

    def toast(self,text,error=False):
        self.message='  '+str(text).replace('\n',' ')[:250]; self.message_error=error; self.app.invalidate()

    def page(self,title,subtitle,*children,compact=False):
        return ScrollablePane(HSplit([plain(title,1,'class:title'),plain(subtitle,1 if compact else 2,'class:muted'),gap(),*children],padding=0),
                              show_scrollbar=True,width=D(min=1,preferred=80,weight=1))

    def _build_pages(self):
        pages={}
        overview=HSplit([
            VSplit([Frame(plain(self._next_card,4),title='WAKE-UP'),
                    Frame(plain(self._status_card,4),title='SERVICE')],padding=2),gap(),
            Frame(plain(self._home_card,3),title='HOME NETWORK'),gap(),
            buttons(self.button('Set next wake-up',self._goto_next),self.button('Preview series',self.preview_current),
                    self.button('Run live test',self.start_test)),gap(),
            Frame(plain(self._agenda,9),title='COMING UP'),gap(),
            Frame(plain(self._checklist,4),title='READINESS')
        ])
        pages['overview']=self.page('Overview','Your next morning, at a glance.',overview,compact=True)
        self.next_date,nd=field('Date  ·  YYYY-MM-DD',(date.today()+timedelta(days=1)).isoformat(),width=18)
        self.next_time,nt=field('First alarm  ·  HH:MM','07:00',width=18)
        pages['next']=self.page('Next wake-up','One change for one date. The first ring is exactly the time you enter.',
            VSplit([nd,nt],padding=4),gap(),
            buttons(self.button('Preview',self.preview_next),self.button('Save for this date',self.save_next),
                    self.button('Use routine again',self.clear_next)),gap(),
            Frame(plain(self._next_preview,lambda:len(self._next_preview().splitlines())),title='LIVE PREVIEW'))
        self.weekly={}; weekday_cols=[]
        for day in DAYS:
            area,box=field(day.upper(),'05:55',width=6); self.weekly[day]=area; weekday_cols.append(box)
        self.lead,lead=field('Clock preparation lead (sec)',self.cfg.lead_seconds,width=18)
        self.evening=Checkbox(text='Prepare one alarm the evening before',checked=True)
        self.evening_time,et=field('Evening preparation time','22:00',width=18)
        pages['routine']=self.page('Routine','Weekly first-ring times. Leave a day blank to leave it unscheduled.',
            VSplit(weekday_cols,padding=1),gap(),plain(self._repeat_text,1,'class:muted'),
            gap(),lead,gap(),self.evening,gap(),et,gap(),
            buttons(self.button('Save routine',self.save_routine),self.button('Reload saved',self.reload_fields)))
        self.url,url=field('Home Assistant base URL','')
        self.token,token=field('Long-lived token  ·  masked; leave blank to keep the stored token','',password=True)
        self.notify_field,nf=field('Notification action','')
        self.zone,zf=field('Time zone','Europe/Warsaw')
        self.package,pf=field('Android clock package','com.google.android.deskclock')
        self.tag_id,qt=field('Wake-up NFC tag ID','')
        self.tag_device,qd=field('Scanning device ID','')
        pages['connection']=self.page('Connection','Your credentials stay on the server, outside the Git checkout.',
            url,gap(),token,gap(),buttons(self.button('Save connection',self.save_connection),self.button('Check connection',self.check_connection)),
            gap(),nf,gap(),buttons(self.button('Choose phone',self.pick_phone)),gap(),VSplit([zf,pf],padding=4),gap(),
            qt,gap(),qd,gap(),buttons(self.button('Pair NFC tag',self.pair_tag),self.button('Use captured tag',self.save_pair),self.button('Cancel pairing',self.cancel_pair)),
            gap(),plain(self._pair_status,4,'class:good'))
        self.ssid_sensor,ss=field('SSID sensor',''); self.bssid_sensor,bs=field('BSSID sensor','')
        self.heartbeat_sensor,hs=field('Optional phone heartbeat sensor','')
        self.ssids,sa=field('Approved SSIDs  ·  one per line','',height=3)
        self.bssids,ba=field('Approved BSSIDs  ·  one per line','',height=3)
        self.net_fields={}; fields_net=[]
        for name,label in [('heartbeat_max_minutes','Heartbeat maximum age (min)'),('refresh_seconds','Request sensor refresh (sec)'),('poll_seconds','Read HA sensor states (sec)')]:
            a,b=field(label,getattr(self.cfg,name)); self.net_fields[name]=a; fields_net.append(b)
        pages['wifi']=self.page('Home Wi-Fi','Only a matching SSID and BSSID permit new wake-up commands.',
            Frame(plain(self._home_card,3),title='LIVE REPORT'),gap(),ss,
            buttons(self.button('Choose SSID sensor',self.pick_sensor,'ssid_entity')),gap(),bs,
            buttons(self.button('Choose BSSID sensor',self.pick_sensor,'bssid_entity')),gap(),
            VSplit([sa,ba],padding=3),gap(),buttons(self.button('Use current Wi-Fi',self.capture_wifi),self.button('Save home network',self.save_wifi)),
            gap(),hs,buttons(self.button('Choose heartbeat',self.pick_sensor,'heartbeat_entity')),gap(),
            *[HSplit([f,gap()]) for f in fields_net],plain('Heartbeat is optional. Unchanged Wi-Fi values do not become stale just because their name stayed the same.',2,'class:muted'))
        self.sound_fields={}; sound_boxes=[]
        for name,label in [('loud_alarm','Wake-up alarm level'),('loud_ring','Wake-up ringtone level'),
                           ('normal_alarm','Daytime alarm level'),('normal_ring','Daytime ringtone level')]:
            a,b=field(label,getattr(self.cfg,name)); self.sound_fields[name]=a; sound_boxes.append(b)
        pages['sound']=self.page('Sound','Google Clock uses the volume levels below. The Daybreak app uses maximum alarm volume.',
            VSplit(sound_boxes[:2],padding=5),gap(),VSplit(sound_boxes[2:],padding=5),gap(),
            buttons(self.button('Save sound',self.save_sound),self.button('Run full phone test',self.start_test)),gap(),
            Frame(plain(self._native_card,4),title='ANDROID 10-SECOND MODE'),gap(),
            buttons(self.button('Show pairing key',self.native_pair),self.button('Test Android link',self.native_test)),gap(),
            buttons(self.button('Enable 10-second mode',self.native_enable,True),self.button('Use Google Clock',self.native_enable,False)))
        pages['diagnostics']=self.page('Diagnostics','Real tests use a separate session. They do not complete your next morning.',
            buttons(self.button('Check connection',self.check_connection),self.button('Refresh sensors',self.refresh_sensors)),gap(),
            buttons(self.button('Run rolling test',self.start_test),self.button('Run Wi-Fi test',self.start_network_test)),gap(),
            Frame(plain(self._test_card,10),title='LIVE TEST'),gap(),
            buttons(self.button('Confirm test passed',self.confirm_test),self.button('Stop test & clean up',self.stop_test)),gap(),
            Frame(plain(self._diagnostic_card,5),title='HEALTH'),gap(),
            buttons(self.button('Preview full series',self.preview_current),self.button('Clean prepared alarms',self.clean_alarms)))
        self.history_filter,hf=field('Filter history','')
        self.history_area=TextArea(read_only=True,scrollbar=True,wrap_lines=False,height=D(min=8,preferred=18),style='')
        pages['history']=self.page('History','Local execution history. “HA accepted” is not a claim about the physical phone.',
            hf,gap(),self.history_area,gap(),buttons(self.button('Morning summary',self.morning_summary),self.button('Export support report',self.export_report)))
        self.source,sf=field('Git checkout on this server',system.source_path())
        pages['system']=self.page('System','Administrative controls, deployment and configuration backups.',
            Frame(plain(self._system_card,5),title='SERVICE'),gap(),
            buttons(self.button('Start service',self.start_service),self.button('Enable daily routine',self.arm)),gap(),
            buttons(self.button('Enable autostart',self.system_action,'autostart_on'),self.button('Restart service',self.system_action,'restart')),
            gap(),sf,gap(),buttons(self.button('Pull & deploy',self.update_git)),gap(),
            Frame(plain('A fast-forward pull is built in a new release directory. Settings, token and\n'
                        'wake-up history stay outside the checkout. The service is replaced only\n'
                        'after the new environment passes its import check.',3),title='GIT DEPLOYMENT'),gap(),
            buttons(self.button('Backup settings',self.backup),self.button('Restore settings',self.restore_backup)),gap(),
            buttons(self.button('Export support report',self.export_report),self.button('Service details',self.service_details)),gap(),
            buttons(self.button('Pause daily routine',self.pause),self.button('Disable autostart',self.system_action,'autostart_off')))
        return pages

    def _next_card(self):
        wake=self.status_data.get('active_wake')
        if wake:
            next_ring=wake.get('next_ring')
            fast=wake.get('interval_seconds')==INTERVAL
            if self.status_data.get('pending_cleanup'):
                next_line='  Waiting for alarm cleanup.'
            elif next_ring:
                next_line=f"  Next ring {stamp(next_ring,fast)}"
            else:
                next_line='  Waiting for home Wi-Fi.'
            return FormattedText([('class:accent','  AWAITING TAG\n'),
                                  ('',f"  Started {wake['date']} at {stamp(wake['start'])}\n{next_line}\n"),
                                  ('class:muted','  Android app · every 10 seconds' if fast else f"  Alarms tracked: {wake['alarm_count']}")])
        p=next(iter(self.status_data.get('plans',[])),None)
        if not p: return '  No scheduled morning.\n  Configure Routine or Next wake-up.'
        now=parse_timestamp(self.status_data.get('now'))
        start=parse_timestamp(p['start']); secs=max(0,int((start-now).total_seconds())) if now and start else 0
        hours,secs=divmod(secs,3600); mins,_=divmod(secs,60)
        return FormattedText([('class:accent',f"  First ring {stamp(p['start'])}\n"),
                              ('',f"  {p['date']}  ·  {'Every 10 sec' if self._native_interval()==INTERVAL else 'Every minute'} until tag\n"),
                              ('class:muted',f"  Starts in {hours:02d}h {mins:02d}m\n  {p['source']}")])
    def _status_card(self):
        s=self.status_data; net=s.get('network',{}).get('state','Unknown')
        active=s.get('armed',False)
        return FormattedText([('class:good' if active else 'class:accent','  '+('ROUTINE ENABLED' if active else 'SETUP / TEST MODE')+'\n'),
                              ('',f"  HA events   {'Connected' if s.get('connected') else 'Disconnected'}\n"),
                              ('',f"  Network     {net}\n"),('class:muted',f"  Pending cleanup: {s.get('pending_cleanup',0)}")])
    def _home_card(self):
        n=self.status_data.get('network',{})
        return FormattedText([('class:good' if n.get('state')=='Home' else 'class:accent',f"  {n.get('state','Unknown').upper()}   "),
                              ('',f"{n.get('ssid') or 'No SSID'}  /  {n.get('bssid') or 'No BSSID'}\n"),
                              ('class:muted',f"  {n.get('reason','Connect Home Assistant to see phone network reports.')}\n"),
                              ('class:muted',f"  SSID reported: {n.get('ssid_reported') or 'not provided'}")])
    def _agenda(self):
        lines=['  DATE          FIRST RING   SOURCE','  ──────────────────────────────────────────']
        active=self.status_data.get('active_wake') or {}
        for p in self.status_data.get('plans',[])[:7]:
            if p['date']!=active.get('date'):
                lines.append(f"  {p['date']}    {stamp(p['start'])}        {p['source']}")
        return '\n'.join(lines)
    def _checklist(self):
        checks=[('Connection',bool(self.cfg.ha_url and self.token_set)),('Phone + tag',bool(self.cfg.notify_service and self.cfg.qr_tag_id and self.cfg.qr_device_id)),
                ('Home network',bool(self.cfg.home_ssids and self.cfg.home_bssids and self.cfg.ssid_entity and self.cfg.bssid_entity)),
                ('Phone test confirmed',bool(self.status_data.get('test_passed')))]
        return FormattedText(sum(([('class:good' if ok else 'class:muted',f"  {'✓' if ok else '○'}  {name}\n")] for name,ok in checks),[]))
    def _next_preview(self):
        try:
            d=date.fromisoformat(self.next_date.text.strip()); p=plan_for(self.cfg,d,{'date':str(d),'start':self.next_time.text.strip()})
            return plan_text(p.public(self._native_interval()))
        except (ValueError,TypeError): return 'Enter a valid date and HH:MM time to preview the first alarms.'
    def _native_interval(self):
        return INTERVAL if self.status_data.get('native',{}).get('enabled') else 60
    def _repeat_text(self):
        return ('Repeats every 10 seconds until tag confirmation.' if self._native_interval()==INTERVAL
                else 'Repeats every minute until tag confirmation.')
    def _native_card(self):
        state=self.status_data.get('native',{})
        mode='Daybreak app · every 10 seconds' if state.get('enabled') else 'Google Clock · every minute'
        if state.get('testing'): test='In progress. Scan after the second ring.'
        elif state.get('stopping'): test='Tag received. Stop requested.'
        elif state.get('trial_result')=='Tag': test='Tag received. Confirm on the phone before enabling.'
        else: test=state.get('trial_result') or 'Run once with the phone locked.'
        return f"  Mode   {mode}\n  Link   {test}\n  Android app resets alarm volume and schedules each ring 2 seconds ahead."
    def _pair_status(self):
        s=self.status_data
        if s.get('paired'):
            p=s['paired']
            return f"Tag captured. Review, then choose “Use captured tag”.\nTag: {p['tag_id']}\nPhone: {p['device_id']}"
        if s.get('pairing'): return 'Waiting for an NFC tap from the Companion app… (2 minutes)'
        return ('Saved wake-up tag is configured. Choose “Pair NFC tag” to replace it.' if self.cfg.qr_tag_id
                else 'Choose “Pair NFC tag”, then hold your phone near the bathroom tag.')
    def _test_card(self):
        test=self.status_data.get('test')
        if not test: return f'  No test in progress.\n  The rolling test prepares 3 alarms at {self.cfg.lead_seconds} second(s) before each ring.'
        p=test['plan']; lines=[f"  {'COMPLETE' if test['completed'] else 'RUNNING'}  ·  {test['kind']}  ·  {test.get('reason') or 'Waiting for phone / tag'}",'']
        for a in p['alarms']: lines.append(f"  Prepare {stamp(a['prepare'],True)}   →   Ring {stamp(a['ring'],True)}")
        lines.extend(['',f"  Cleanup pending: {self.status_data.get('pending_cleanup',0)}",
                      '  Confirm only after checking the actual phone.'])
        return '\n'.join(lines)
    def _diagnostic_card(self):
        s=self.status_data
        return '\n'.join([f"  HA: {s.get('connection_error') or 'Connected'}",f"  Last tag: {s.get('last_tag') or s.get('last_qr') or 'none'}",
                          f"  Confirmed test: {s.get('test_passed') or 'not yet'}",f"  Last error: {s.get('last_error') or 'none'}"])
    def _system_card(self):
        return '\n'.join([f'  Daybreak {__version__}',f"  Daily routine: {'enabled' if self.status_data.get('armed') else 'setup / paused'}",
                          f'  Console: {"demonstration" if self.demo else "local Unix socket"}',
                          '  Configuration and credentials: /var/lib/daybreak',
                          '  Closing this console does not stop the service.'])

    async def _goto_next(self): self.goto('next')
    async def modal(self,title,body,choices:list[tuple[str,Any]],focus=None):
        if self.modal_future: raise ValueError('Close the current dialog first.')
        future=asyncio.get_running_loop().create_future(); self.modal_future=future
        def done(value):
            if not future.done(): future.set_result(value() if callable(value) else value)
        btns=[Button(label,handler=lambda v=value:done(v)) for label,value in choices]
        dialog=Dialog(title=title,body=body,buttons=btns,width=D(preferred=86,max=100),modal=True)
        floating=Float(content=dialog); old=self.app.layout.current_window
        self.root.floats.append(floating); self.app.layout.focus(focus or btns[-1]); self.app.invalidate()
        try: return await future
        finally:
            self.root.floats.remove(floating); self.modal_future=None
            try: self.app.layout.focus(old)
            except ValueError: self.app.layout.focus(self.menu)
            self.app.invalidate()
    async def confirm(self,title,text):
        return bool(await self.modal(title,Label(text),[('Continue',True),('Cancel',False)]))
    async def show_text(self,title,text):
        area=TextArea(text=text,read_only=True,scrollbar=True,wrap_lines=True,height=D(min=6,preferred=18,max=25),style='')
        await self.modal(title,area,[('Close',True)],focus=area)
    async def picker(self,title,items:list[tuple[str,str]],default=None):
        if not items: raise ValueError('No matching items were reported by Home Assistant.')
        search=TextArea(height=1,multiline=False,prompt='Search: ',style='class:text-area')
        options=RadioList(items,default=default,select_on_focus=True)
        def filtered(buffer):
            term=buffer.text.lower(); selected=[x for x in items if term in x[0].lower() or term in x[1].lower()]
            options.values=selected or [('','No matches')]; options._selected_index=0; options.current_value=options.values[0][0]
            self.app.invalidate()
        search.buffer.on_text_changed+=filtered
        body=HSplit([search,gap(),Box(options,height=D(min=5,preferred=12,max=18),padding=0)])
        return await self.modal(title,body,[('Select',lambda:options.current_value),('Cancel',None)],focus=search)

    def load_fields(self,cfg:dict):
        self.cfg=Settings.from_dict(cfg['settings']); self.token_set=cfg.get('token_set',False)
        for a,v in [(self.url,self.cfg.ha_url),(self.notify_field,self.cfg.notify_service),(self.zone,self.cfg.timezone),
                    (self.package,self.cfg.alarm_package),(self.tag_id,self.cfg.qr_tag_id),(self.tag_device,self.cfg.qr_device_id),
                    (self.ssid_sensor,self.cfg.ssid_entity),(self.bssid_sensor,self.cfg.bssid_entity),
                    (self.heartbeat_sensor,self.cfg.heartbeat_entity),(self.ssids,'\n'.join(self.cfg.home_ssids)),
                    (self.bssids,'\n'.join(self.cfg.home_bssids)),(self.evening_time,self.cfg.evening_time)]: a.text=v
        for k,a in self.weekly.items(): a.text=self.cfg.weekly[k] or ''
        self.lead.text=str(self.cfg.lead_seconds)
        for fields in (self.sound_fields,self.net_fields):
            for k,a in fields.items(): a.text=str(getattr(self.cfg,k))
        self.evening.checked=self.cfg.evening_enabled; self.token.text=''
    async def reload_fields(self):
        self.load_fields(await self.control.config()); self.toast('Saved settings loaded.')
    async def save(self,changes,token=None):
        data=await self.control.save(changes,token); self.load_fields(data); self.toast('Settings saved.')
    async def save_connection(self):
        await self.save({'ha_url':self.url.text.strip().rstrip('/'),'notify_service':self.notify_field.text.strip(),
                         'timezone':self.zone.text.strip(),'alarm_package':self.package.text.strip(),
                         'qr_tag_id':self.tag_id.text.strip(),'qr_device_id':self.tag_device.text.strip()},self.token.text.strip() or None)
    async def save_routine(self):
        await self.save({'lead_seconds':int(self.lead.text.strip()),
                         'weekly':{k:(a.text.strip() or None) for k,a in self.weekly.items()},
                         'evening_enabled':self.evening.checked,'evening_time':self.evening_time.text.strip()})
    async def save_sound(self): await self.save({k:int(a.text.strip()) for k,a in self.sound_fields.items()})
    async def native_pair(self):
        result=await self.control.action('native_pair')
        await self.show_text('Android pairing key',result['key']+'\n\nEnter this key in the Daybreak Android app. Keep it private.')
    async def native_test(self):
        if await self.confirm('Test Android link','Lock the paired phone. The first ring is about 12 seconds after Continue.\nSilence that ring, wait for the second, then scan the wake-up tag.\nThe test expires 30 seconds after the first ring.'):
            result=await self.control.action('native_test')
            self.toast(f"Android link test starts at {stamp(result['start'],True)}.")
    async def native_enable(self,enabled):
        prompt=('Did the locked phone ring twice, return to full alarm volume, and stop after the tag?'
                if enabled else 'Switch daily wake-ups to Google Clock with one alarm per minute?')
        if await self.confirm('Change alarm mode',prompt):
            result=await self.control.action('native_enable',enabled=enabled)
            self.toast(result['message'])
    async def save_wifi(self):
        changes={'ssid_entity':self.ssid_sensor.text.strip(),'bssid_entity':self.bssid_sensor.text.strip(),
                 'heartbeat_entity':self.heartbeat_sensor.text.strip(),
                 'home_ssids':[x.strip() for x in self.ssids.text.splitlines() if x.strip()],
                 'home_bssids':[x.strip().lower() for x in self.bssids.text.splitlines() if x.strip()]}
        changes.update({k:int(a.text.strip()) for k,a in self.net_fields.items()})
        await self.save(changes)
    async def check_connection(self):
        self.toast('Checking HA API and tag event stream…'); data=await self.control.action('connect'); self.toast(data['message'])
    async def pick_phone(self):
        self.toast('Loading notification actions…'); data=await self.control.action('discover'); options=[]
        for domain in data['services']:
            if domain['domain']=='notify':
                options += [(f'notify.{key}',f'notify.{key}') for key in domain['services'] if key.startswith('mobile_app_')]
        selected=await self.picker('Choose notification target',sorted(options),self.notify_field.text)
        if selected: self.notify_field.text=selected; await self.save_connection()
    async def pick_sensor(self,key):
        self.toast('Loading Home Assistant sensors…'); data=await self.control.action('discover')
        options=[]
        for state in data['states']:
            eid=state.get('entity_id','')
            if eid.startswith(('sensor.','binary_sensor.')):
                name=state.get('attributes',{}).get('friendly_name',eid)
                options.append((eid,f"{name}  |  {state.get('state','?')}  |  {eid}"))
        terms={'ssid_entity':('wifi_connection','ssid'),'bssid_entity':('bssid',),'heartbeat_entity':('last_update','heartbeat')}[key]
        options.sort(key=lambda x:(not any(t in x[0] for t in terms),x[0]))
        if key=='heartbeat_entity': options.insert(0,('','None (optional)'))
        selected=await self.picker('Choose '+key.replace('_',' '),options,getattr(self.cfg,key))
        if selected is not None:
            area={'ssid_entity':self.ssid_sensor,'bssid_entity':self.bssid_sensor,'heartbeat_entity':self.heartbeat_sensor}[key]
            area.text=selected; await self.save_wifi()
    async def capture_wifi(self):
        await self.save_wifi(); await self.control.action('refresh')
        self.toast('Waiting briefly for the phone network report…'); await asyncio.sleep(2)
        await self.control.action('connect')
        if await self.confirm('Register current home Wi-Fi','The selected sensors must currently describe your home Wi-Fi.\nAdd their current SSID and BSSID to the approved network list?'):
            data=await self.control.action('use_wifi'); self.load_fields(data); self.toast('Current Wi-Fi registered as Home.')
    async def pair_tag(self):
        data=await self.control.action('pair'); self.toast(data['message'])
    async def cancel_pair(self):
        data=await self.control.action('cancel_pair'); self.toast(data['message'])
    async def save_pair(self):
        status=await self.control.status(); paired=status.get('paired')
        if not paired: raise ValueError('No tag has been captured yet. Choose “Pair NFC tag” first.')
        message=(f"Tag: {paired['tag_id']}\nPhone: {paired['device_id']}\n\n"
                 'This replaces the saved wake-up tag. The previous identifier will stop completing wake-ups.\n'
                 'If the identifiers change, daily wake-ups pause until you confirm a new phone test and enable the routine.\n'
                 'Update any separate Home Assistant tag handlers too.')
        if not await self.confirm('Save wake-up tag',message): return
        data=await self.control.action('save_pair'); self.load_fields(data)
        self.toast('Tag saved. If changed, run Diagnostics → rolling test, then enable the daily routine in System.')
    async def preview_current(self):
        p=next(iter(self.status_data.get('plans',[])),None); await self.show_text('Alarm timeline',plan_text(p))
    async def preview_next(self):
        p=await self.control.action('preview',date=self.next_date.text.strip(),start=self.next_time.text.strip())
        await self.show_text('One-time wake-up preview',plan_text(p))
    async def save_next(self):
        p=await self.control.action('preview',date=self.next_date.text.strip(),start=self.next_time.text.strip())
        if await self.confirm('Save next wake-up',f"{p['date']}\nFirst ring {stamp(p['start'])}. {self._repeat_text()}"):
            await self.control.action('set_override',override={'date':p['date'],'start':self.next_time.text.strip()})
            self.toast('Next wake-up saved. Previous evening seed will be reconciled.')
    async def clear_next(self):
        if await self.confirm('Use the weekly routine','Remove the one-time override and use the normal routine again?'):
            await self.control.action('set_override',override=None); self.toast('One-time override cleared.')
    async def start_test(self):
        if await self.confirm('Real phone test','This creates three real alarms, a minute apart, and changes phone volume.\nLet two alarms ring, then hold your phone near the bathroom NFC tag.\nThe next real morning is not marked complete.'):
            await self.control.action('start_test'); self.goto('diagnostics'); self.toast('Rolling test started. Watch the live timeline.')
    async def start_network_test(self):
        if await self.confirm('Home Wi-Fi test',f'A real test alarm is prepared {self.cfg.lead_seconds} second(s) before ringing.\nAfter it is prepared, disconnect the phone from home Wi-Fi.\nVerify Daybreak cancels its alarm and restores daytime volume.'):
            await self.control.action('start_network_test'); self.goto('diagnostics'); self.toast('Wi-Fi test started.')
    async def confirm_test(self):
        if await self.confirm('Confirm actual phone behavior','Did the phone behave as expected, with the prepared alarm stopped\nand no remaining test alarm or incorrect volume?'):
            data=await self.control.action('accept_test'); self.toast(data['message'])
    async def stop_test(self):
        await self.control.action('stop_test'); self.toast('Test cleanup queued. Wait for pending cleanup to reach zero.')
    async def clean_alarms(self):
        if await self.confirm('Clean prepared Daybreak alarms','Send cancellation for tracked Daybreak alarms only?'):
            await self.control.action('cleanup'); self.toast('Cleanup queued.')
    async def refresh_sensors(self): self.toast((await self.control.action('refresh'))['message'])
    async def arm(self):
        if await self.confirm('Enable daily routine','Enable the configured daily wake-up routine?\nA confirmed phone test is required.'):
            self.toast((await self.control.action('arm',enabled=True))['message'])
    async def pause(self):
        if await self.confirm('Maintenance','Pause the daily routine and clean its tracked alarms?'):
            self.toast((await self.control.action('arm',enabled=False))['message'])
    async def system_action(self,action):
        if self.demo: raise ValueError('System administration is unavailable in the demo.')
        if await self.confirm('Service administration','Apply '+action.replace('_',' ')+' to the Daybreak service?'):
            self.toast(await system.service_action(action))
    async def start_service(self):
        if self.demo: raise ValueError('The demo does not control system services.')
        self.toast(await system.service_action('start')); await asyncio.sleep(1); await self.reload_fields()
    async def update_git(self):
        if self.demo: raise ValueError('Git deployment is unavailable in the demo.')
        if await self.confirm('Deploy from Git','Pull the selected checkout with --ff-only and build a fresh release?\nConfiguration and credentials stay on this server.\nThe console reopens after installation.'):
            self.toast('Pulling and building a fresh release. This may take a few minutes…')
            result=await system.deploy_git(self.source.text.strip())
            await self.show_text('Deployment complete',result+'\n\nThe console will now reopen.'); self.app.exit(result='reopen')
    async def backup(self):
        result=await self.control.action('backup'); self.toast('Settings backup created: '+result['name'])
    async def restore_backup(self):
        data=await self.control.action('backups'); selected=await self.picker('Restore configuration',[(n,n) for n in data['backups']])
        if selected and await self.confirm('Restore settings',f'Restore {selected}?\nThe stored token is unchanged.'):
            self.load_fields(await self.control.action('restore_backup',file=selected)); self.toast('Configuration restored.')
    async def morning_summary(self):
        lines=['DATE        FIRST   COMPLETED     ALARMS  RESULT', '──────────────────────────────────────────────────────────────']
        for r in self.status_data.get('sessions',[]):
            if r['kind']!='real': continue
            p=r['plan']
            completed=parse_timestamp(r.get('completed_at'))
            when=completed.astimezone(self.cfg.tz).strftime('%m-%d %H:%M') if completed else '—'
            lines.append(f"{p['date']}  {stamp(p['start'])}   {when:11}   {r.get('alarm_count',0):4}  {r.get('reason') or 'Scheduled / active'}")
        if len(lines)==2: lines.append('No real morning has run yet.')
        await self.show_text('Morning history', '\n'.join(lines))

    async def export_report(self):
        data=await self.control.action('report')
        path=TextArea(text=str(Path.home()/('daybreak-report-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')),height=1,multiline=False)
        result=await self.modal('Export support report',HSplit([Label('Token and network identifiers are excluded.'),gap(),path]),
                                [('Export',lambda:path.text),('Cancel',None)],focus=path)
        if result: self.toast('Report saved: '+system.export_report(data,result))
    async def service_details(self):
        if self.demo: await self.show_text('Service details','Demo: service active; autostart enabled.'); return
        text=''
        for args in [('systemctl','show','daybreak','--property=ActiveState,SubState,UnitFileState,MainPID'),
                     ('journalctl','-u','daybreak','-n','30','--no-pager')]:
            try: text+=await system.run(*args)+'\n'
            except (ValueError,OSError) as ex: text+=str(ex)+'\n'
        await self.show_text('Service details',text)

    async def refresh_loop(self):
        while True:
            try:
                self.status_data=await self.control.status()
                if not self._loaded:
                    self.load_fields(await self.control.config()); self._loaded=True
                    override=self.status_data.get('override')
                    if override: self.next_date.text=override['date']; self.next_time.text=override['start']
                    else:
                        plans=self.status_data.get('plans',[])
                        if plans: self.next_date.text=plans[0]['date']; self.next_time.text=stamp(plans[0]['start'])
                    self.toast('Console connected. '+('Complete Connection and Home Wi-Fi setup.' if not self.cfg.ready() else 'Ready.'))
                term=self.history_filter.text.casefold()
                history='\n'.join(f"{stamp(x['at'],True)}  {x['level']:<5}  {x['message']}" for x in self.status_data.get('history',[]) if term in x['message'].casefold())
                if self.history_area.text!=history: self.history_area.text=history
            except (ValueError,OSError) as ex:
                self.toast(str(ex),True)
            self.app.invalidate(); await asyncio.sleep(1)
    async def run(self):
        loop_task=asyncio.create_task(self.refresh_loop())
        try: return await self.app.run_async()
        finally:
            loop_task.cancel()
            with suppress(asyncio.CancelledError): await loop_task
            for task in list(self.tasks): task.cancel()
            for task in list(self.tasks):
                with suppress(asyncio.CancelledError): await task


class DemoControl:
    """Deterministic presentation data. No access to HA or any physical phone."""
    def __init__(self):
        self.cfg=Settings(ha_url='http://home-assistant.local:8123',notify_service='notify.mobile_app_phone',
                          qr_tag_id='demo-bathroom',qr_device_id='demo-phone',ssid_entity='sensor.phone_wifi',
                          bssid_entity='sensor.phone_bssid',home_ssids=['Home Wi-Fi'],home_bssids=['aa:bb:cc:dd:ee:ff'])
        self.now=datetime(2026,9,16,21,42,18,tzinfo=self.cfg.tz); self.override=None
    async def config(self): return {'settings':self.cfg.to_dict(),'token_set':True}
    async def save(self,settings,token=None):
        raw=self.cfg.to_dict(); raw.update(settings); self.cfg=Settings.from_dict(raw); return await self.config()
    async def status(self):
        return {'now':self.now.isoformat(),'armed':True,'ready':True,'connected':True,'connection_error':'',
                'network':{'state':'Home','reason':'SSID and BSSID match the approved home network.','ssid':'Home Wi-Fi',
                           'bssid':'aa:bb:cc:dd:ee:ff','ssid_reported':'2026-09-16T19:40:03+00:00'},
                'plans':[p.public() for p in upcoming(self.cfg,self.now,self.override)],'override':self.override,
                'last_tag':'2026-09-16T06:04:21+02:00','last_qr':'2026-09-16T06:04:21+02:00','test_passed':'2026-09-16T20:15:00+02:00',
                'pending_cleanup':0,'last_error':'','test':None,'sessions':[],
                'history':[{'at':'2026-09-16T19:40:03+00:00','level':'INFO','message':'Home network: Home. Approved Wi-Fi report.'},
                           {'at':'2026-09-16T18:15:00+00:00','level':'INFO','message':'User confirmed the live phone test.'}]}
    async def action(self,name,**data):
        if name=='preview': return plan_for(self.cfg,date.fromisoformat(data['date']),{'date':data['date'],'start':data['start']}).public()
        if name=='set_override': self.override=data.get('override'); return await self.status()
        if name=='connect': return {'message':'Demo Home Assistant connection ready.'}
        if name=='discover': return {'services':[{'domain':'notify','services':{'mobile_app_phone':{}}}],
            'states':[{'entity_id':'sensor.phone_wifi','state':'Home Wi-Fi','attributes':{'friendly_name':'Phone Wi-Fi'}},
                      {'entity_id':'sensor.phone_bssid','state':'aa:bb:cc:dd:ee:ff','attributes':{'friendly_name':'Phone BSSID'}}]}
        if name=='report': return {'daybreak':__version__,'mode':'demo','events':[]}
        if name=='backups': return {'backups':['settings-demo.json']}
        raise ValueError('This operation is available on the installed service, not in the demonstration.')
