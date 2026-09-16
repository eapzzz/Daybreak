"""Smoke-test the actual independent daemon process, without systemd or a phone."""
import asyncio
import os
from pathlib import Path
import sys
from daybreak.service import Control

async def test_daemon_start_control_single_instance_and_clean_exit(tmp_path):
    state=tmp_path/'private'; socket=tmp_path/'runtime'/'control.sock'
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    args=(sys.executable,'-m','daybreak','--daemon','--state-dir',str(state),'--socket',str(socket))
    process=await asyncio.create_subprocess_exec(*args,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
    try:
        for _ in range(200):
            if socket.exists(): break
            if process.returncode is not None: raise AssertionError((await process.communicate())[0].decode())
            await asyncio.sleep(.01)
        c=Control(socket); status=await c.status()
        assert status['armed'] is False and status['network']['state']=='Unknown'
        assert (await c.config())['token_set'] is False
        second=await asyncio.create_subprocess_exec(*args,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        output,_=await asyncio.wait_for(second.communicate(),5)
        assert second.returncode!=0 and b'already using' in output
        # A second console/control client does not own or stop the daemon.
        assert (await Control(socket).status())['armed'] is False
    finally:
        if process.returncode is None: process.terminate()
        output,_=await asyncio.wait_for(process.communicate(),5)
    assert process.returncode==0, output.decode()
    assert not socket.exists()
    assert (state/'daybreak.sqlite3').exists()
