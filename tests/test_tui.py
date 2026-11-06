import asyncio
from contextlib import asynccontextmanager
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size
import pytest
from daybreak.tui import Console,DemoControl,PAGES,plan_text

class Output(DummyOutput):
    def __init__(self,columns=120,rows=42): self.columns=columns; self.rows=rows
    def get_size(self): return Size(rows=self.rows,columns=self.columns)

@asynccontextmanager
async def console(columns=120,rows=42):
    with create_pipe_input() as pipe:
        c=Console(DemoControl(),input=pipe,output=Output(columns,rows),demo=True)
        task=asyncio.create_task(c.run())
        for _ in range(100):
            if c.app.is_running and c._loaded: break
            await asyncio.sleep(.01)
        try: yield c,pipe
        finally:
            if c.app.is_running: c.app.exit()
            await asyncio.wait_for(task,2)

def screen_text(c):
    screen=c.app.renderer._last_screen
    if not screen: return ''
    return '\n'.join(''.join(v.char for k,v in sorted(row.items())) for y,row in sorted(screen.data_buffer.items()))

@pytest.mark.parametrize('page,title',PAGES)
async def test_every_page_renders(page,title):
    async with console() as (c,p):
        c.goto(page); await asyncio.sleep(.10)
        text=screen_text(c)
        assert title in text
        assert 'Window too small' not in text

async def test_next_preview_rounding_in_actual_console():
    async with console() as (c,p):
        c.next_date.text='2026-09-17'; c.next_time.text='07:10'; c.goto('next'); await asyncio.sleep(.1)
        text=screen_text(c); assert '07:10' in text and '08:30' in text

async def test_config_save_button_handler():
    async with console() as (c,p):
        c.sound_fields['loud_alarm'].text='6'; await c.save_sound()
        assert c.control.cfg.loud_alarm==6 and c.cfg.loud_alarm==6

async def test_modal_cancel_keyboard():
    async with console() as (c,p):
        task=asyncio.create_task(c.confirm('Confirm','Continue?')); await asyncio.sleep(.1)
        p.send_text('\x1b'); assert not await asyncio.wait_for(task,2)
        assert not c.root.floats

async def test_picker_search_and_select():
    async with console() as (c,p):
        task=asyncio.create_task(c.picker('Select sensor',[('sensor.one','One'),('sensor.two','Two')]))
        await asyncio.sleep(.1); p.send_text('two'); await asyncio.sleep(.1)
        # Tab goes from search to options, then to Select.
        p.send_text('\t\t\r')
        assert await asyncio.wait_for(task,2)=='sensor.two'

async def test_blank_token_never_shown():
    async with console() as (c,p):
        c.goto('connection'); c.token.text='VERY_PRIVATE_TOKEN'; await asyncio.sleep(.1)
        assert 'VERY_PRIVATE_TOKEN' not in screen_text(c)

async def test_keyboard_navigation():
    async with console() as (c,p):
        # F2's VT100 escape sequence.
        p.send_text('\x1bOQ'); await asyncio.sleep(.2)
        assert c.menu.current_value=='next'

async def test_compact_terminal():
    async with console(100,30) as (c,p):
        for page,_ in PAGES:
            c.goto(page); await asyncio.sleep(.04)
            assert 'Window too small' not in screen_text(c)

async def test_bad_time_preview_is_safe():
    async with console() as (c,p):
        c.next_time.text='bad'; assert 'Enter a valid' in c._next_preview()

async def test_inline_preview_contains_last_alarm_without_truncation():
    async with console(122,43) as (c,p):
        c.next_date.text='2026-09-17'; c.next_time.text='07:10'; c.goto('next')
        await asyncio.sleep(.1)
        assert '08:29:50' in screen_text(c)

async def test_connection_and_diagnostics_show_nfc_controls():
    async with console() as (c,p):
        c.goto('connection'); await asyncio.sleep(.1)
        text=screen_text(c)
        assert 'Pair NFC tag' in text and 'Use captured tag' in text and 'Cancel pairing' in text
        assert 'Pair existing QR' not in text
        c.status_data['paired']={'tag_id':'captured-nfc','device_id':'captured-phone'}
        assert 'captured-nfc' in c._pair_status() and 'captured-phone' in c._pair_status()
        c.status_data['last_tag']='2026-11-06T08:00:00+01:00'
        assert 'Last tag:' in c._diagnostic_card()
