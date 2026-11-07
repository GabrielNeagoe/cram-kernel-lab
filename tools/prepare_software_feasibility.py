"""Prepare a separate lab kernel output directory; never installs a kernel."""
from pathlib import Path
import argparse
import shutil
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source',type=Path,default=Path('/srv/cram-lab/linux-apparmor'))
p.add_argument('--base-config',type=Path,default=Path('/srv/cram-lab/build-linux-reporting/.config'))
p.add_argument('--output',type=Path,default=Path('/srv/cram-lab/build-linux-sw-feasibility'))
args=p.parse_args()
args.output.mkdir(exist_ok=False)
shutil.copyfile(args.base_config,args.output/'.config')
subprocess.run([str(args.source/'scripts/config'),'--file',str(args.output/'.config'),
               '--enable','ZSWAP','--enable','ZSMALLOC','--enable','ZSMALLOC_STAT',
               '--enable','MEMCG','--enable','CRYPTO_ZSTD','--enable','ZSWAP_COMPRESSOR_DEFAULT_ZSTD',
               '--disable','ZSWAP_DEFAULT_ON','--set-str','LOCALVERSION','-cram-sw-feasibility'],check=True)
subprocess.run(['make','-C',str(args.source),'O='+str(args.output),'olddefconfig'],check=True)
print('Prepared only. Build with make in this output directory; do not install on the outer VM.')
