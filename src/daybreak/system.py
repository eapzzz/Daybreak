"""Fixed local administration operations used by the SSH console."""
from __future__ import annotations
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import pwd
import re


def scrub(text:str)->str:
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)


async def run(*args:str,timeout:float=40)->str:
    """No shell. Commands come from the allowlisted functions below."""
    process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
    try: out,_=await asyncio.wait_for(process.communicate(),timeout)
    except asyncio.TimeoutError:
        process.kill(); await process.wait(); raise ValueError('Operation timed out.') from None
    text=scrub(out.decode(errors='replace'))
    if process.returncode: raise ValueError((text.strip() or f'Command failed ({process.returncode}).')[-3000:])
    return text.strip()


async def service_status()->str:
    try: return await run('systemctl','is-active','daybreak',timeout=5)
    except (ValueError,OSError): return 'inactive'


async def service_action(action:str)->str:
    operations={'start':('start','daybreak'),'restart':('restart','daybreak'),
                'autostart_on':('enable','daybreak'),'autostart_off':('disable','daybreak')}
    if action not in operations: raise ValueError('Unknown service operation.')
    if os.geteuid()!=0: raise ValueError('Open the console with sudo for system administration.')
    return await run('systemctl',*operations[action]) or 'Service operation completed.'


def source_path()->str:
    path=Path('/etc/daybreak/source-path')
    return path.read_text().strip() if path.exists() else ''


def git_command(source:Path,*args:str)->tuple[str,...]:
    source=source.resolve()
    owner=pwd.getpwuid(source.stat().st_uid).pw_name
    command=('git','-C',str(source),*args)
    return ('runuser','-u',owner,'--',*command) if os.geteuid()==0 and source.stat().st_uid!=0 else command


async def deploy_git(source:str)->str:
    if os.geteuid()!=0: raise ValueError('Git deployment requires the administrative console.')
    folder=Path(source).expanduser().resolve()
    if not (folder/'.git').exists() or not (folder/'install.sh').is_file():
        raise ValueError('Select the Daybreak Git checkout containing install.sh.')
    status=await run(*git_command(folder,'status','--porcelain'))
    if status: raise ValueError('The server checkout has local changes. Commit or resolve them before deploying.')
    await run(*git_command(folder,'pull','--ff-only'),timeout=180)
    if not (folder/'pyproject.toml').is_file(): raise ValueError('The pulled checkout is not a Daybreak project.')
    return await run('bash',str(folder/'install.sh'),'--console-update',timeout=600)


def export_report(report:dict,path:str)->str:
    target=Path(path).expanduser()
    if target.exists(): raise ValueError('That report file already exists. Choose a new name.')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(report,f,indent=2)
    return str(target.resolve())
