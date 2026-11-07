"""Build the small baseline initramfs overlay on the lab host."""
from pathlib import Path
import gzip
import subprocess

base = Path(__file__).resolve().parent
overlay = base/'overlay'
(overlay/'bin').mkdir(parents=True, exist_ok=True)
subprocess.run(['gcc', '-static', '-O2', '-Wall', '-Wextra', '-Werror',
                str(base/'ram-savings-baseline.c'), '-o', str(overlay/'bin/ram-savings-baseline')], check=True)
paths = subprocess.check_output(['find', '.', '-print0'], cwd=overlay)
archive = subprocess.run(['cpio', '--null', '-o', '-H', 'newc'], cwd=overlay, input=paths,
                          stdout=subprocess.PIPE, check=True).stdout
original = Path('/srv/cram-lab/images/initramfs-cram-preflush.cpio.gz')
(base/'baseline.cpio.gz').write_bytes(original.read_bytes()+gzip.compress(archive))
