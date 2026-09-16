from __future__ import annotations
import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description='Daybreak wake-up service and SSH console')
    parser.add_argument('--daemon',action='store_true',help='Run the scheduler (normally started by systemd).')
    parser.add_argument('--demo',action='store_true',help='Preview the console without a server or phone.')
    parser.add_argument('--state-dir',type=Path,default=Path('/var/lib/daybreak'))
    parser.add_argument('--socket',type=Path,default=Path('/run/daybreak/control.sock'))
    args=parser.parse_args()
    if args.daemon:
        from .service import serve
        logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
        try: asyncio.run(serve(args.state_dir,args.socket))
        except (ValueError,RuntimeError,OSError) as ex: parser.exit(1,str(ex)+'\n')
    else:
        from .tui import Console,DemoControl
        from .service import Control
        control=DemoControl() if args.demo else Control(args.socket)
        try: result=asyncio.run(Console(control,demo=args.demo).run())
        except KeyboardInterrupt: return
        if result=='reopen': os.execv('/usr/local/bin/daybreak',['daybreak'])

if __name__=='__main__': main()
