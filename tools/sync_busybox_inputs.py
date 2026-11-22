"""Mirror the canonical Alpine backport inputs into the application build contexts."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TEXT_FILES=('source.lock','CVE-2025-60876.patch','build.sh','install.sh','README.md')
FILES=TEXT_FILES+('aports-busybox.tar.gz',)
OPENSSL_FILES=('source.lock','recipe.tar.gz','build.sh','install.sh','libraries.sha256','cli.sha256')

def input_bytes(path):
    data=path.read_bytes()
    return data if path.name.endswith('.tar.gz') else data.replace(b'\r\n',b'\n')

def sync(check=False, backport='busybox-backport', files=FILES):
    source=ROOT/'deploy/images'/backport
    for service in ('frontend','business-backend'):
        destination=ROOT/service/'security'/backport
        for name in files:
            data=input_bytes(source/name)
            target=destination/name
            if check:
                if not target.is_file() or input_bytes(target)!=data:
                    component = 'BusyBox' if backport == 'busybox-backport' else 'OpenSSL'
                    raise SystemExit(f'Generated {component} input differs: '+target.relative_to(ROOT).as_posix())
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    check=parser.parse_args().check
    sync(check)
    sync(check, 'openssl-backport', OPENSSL_FILES)
