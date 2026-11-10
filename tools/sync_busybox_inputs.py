"""Keep the two independent Docker build contexts on the same canonical inputs."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TEXT_FILES=('source.lock','CVE-2025-60876.patch','build.sh','install.sh','README.md')
FILES=TEXT_FILES+('aports-busybox.tar.gz',)

def input_bytes(path):
    data=path.read_bytes()
    return data.replace(b'\r\n',b'\n') if path.name in TEXT_FILES else data

def sync(check=False):
    source=ROOT/'deploy/images/busybox-backport'
    for service in ('frontend','business-backend'):
        destination=ROOT/service/'security/busybox-backport'
        for name in FILES:
            data=input_bytes(source/name)
            target=destination/name
            if check:
                if not target.is_file() or input_bytes(target)!=data:
                    raise SystemExit('Generated BusyBox input differs: '+target.relative_to(ROOT).as_posix())
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    sync(parser.parse_args().check)
