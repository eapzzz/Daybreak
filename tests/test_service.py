import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from aiohttp import web,ClientSession,UnixConnector
import pytest
from daybreak.store import SettingsFiles,Store
from daybreak.engine import Engine
from daybreak.service import build_app

@asynccontextmanager
async def api(tmp_path):
    files=SettingsFiles(tmp_path); db=Store(tmp_path/'state.db'); engine=Engine(files,db)
    app=build_app(engine); runner=web.AppRunner(app); await runner.setup()
    sock=tmp_path/'control.sock'; site=web.UnixSite(runner,str(sock)); await site.start()
    async with ClientSession(connector=UnixConnector(path=str(sock))) as client:
        try: yield client,engine
        finally: await runner.cleanup(); db.close()

async def test_config_never_exposes_token(tmp_path):
    async with api(tmp_path) as (c,e):
        e.files.save(e.cfg,'super-secret')
        async with c.get('http://localhost/config') as r:
            text=await r.text(); assert 'super-secret' not in text; assert 'token_set' in text

async def test_default_is_unarmed(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.get('http://localhost/status') as r:
            assert not (await r.json())['armed']

async def test_bad_settings_have_structured_error(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.post('http://localhost/config',json={'settings':{'lead_seconds':1}}) as r:
            assert r.status==400 and 'error' in await r.json()
        assert e.cfg.lead_seconds==10

async def test_action_allowlist(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.post('http://localhost/action',json={'name':'run_shell','command':'echo bad'}) as r:
            assert r.status==400

async def test_arm_requires_successful_test(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.post('http://localhost/action',json={'name':'arm','enabled':True}) as r: assert r.status==400

async def test_preview_has_exact_times_without_changes(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.post('http://localhost/action',json={'name':'preview','date':'2026-09-18','start':'07:10'}) as r:
            p=await r.json(); assert p['start'][11:16]=='07:10' and p['end'][11:16]=='08:30'
        assert e.db.meta('override') is None

async def test_report_redacts_network_and_token(tmp_path):
    async with api(tmp_path) as (c,e):
        e.files.save(e.cfg,'never-output')
        async with c.post('http://localhost/action',json={'name':'report'}) as r:
            txt=await r.text(); assert 'never-output' not in txt
            assert 'token' not in txt

async def test_captured_nfc_requires_explicit_save_and_can_be_cancelled(tmp_path):
    async with api(tmp_path) as (c,e):
        async with c.post('http://localhost/action',json={'name':'pair'}) as r: assert r.status==200
        await e.on_event({'event_type':'tag_scanned','data':{'tag_id':'nfc-bathroom','device_id':'phone'}})
        assert not e.cfg.qr_tag_id
        async with c.post('http://localhost/action',json={'name':'save_pair'}) as r: assert r.status==200
        assert e.files.load().qr_tag_id=='nfc-bathroom'
        assert not e.db.meta('armed') and e.db.meta('test_passed') is None
        async with c.post('http://localhost/action',json={'name':'pair'}) as r: assert r.status==200
        async with c.post('http://localhost/action',json={'name':'cancel_pair'}) as r: assert r.status==200
        async with c.post('http://localhost/action',json={'name':'save_pair'}) as r: assert r.status==400
        assert e.files.load().qr_tag_id=='nfc-bathroom'
