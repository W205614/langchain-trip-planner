"""Keep the two independent Docker build contexts on the same canonical inputs."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=('source.lock','CVE-2025-60876.patch','build.sh','install.sh','README.md')

def sync(check=False):
    source=ROOT/'deploy/images/busybox-backport'
    for service in ('frontend','business-backend'):
        destination=ROOT/service/'security/busybox-backport'
        for name in FILES:
            data=(source/name).read_bytes().replace(b'\r\n',b'\n')
            target=destination/name
            if check:
                if not target.is_file() or target.read_bytes().replace(b'\r\n',b'\n')!=data:
                    raise SystemExit('Generated BusyBox input differs: '+target.relative_to(ROOT).as_posix())
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    sync(parser.parse_args().check)
