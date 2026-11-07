"""Install a validated experimental VM kernel as an additional boot option.

Requires root. Retains all existing kernels and the existing stock-default drop-in.
Never reboots. The stage manifest and validation record must already exist.
"""
import argparse,hashlib,json,os,re,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',type=Path);a=p.parse_args()
assert os.geteuid()==0,'Run this prepared installer through sudo'
stage=a.stage.resolve();manifest=json.loads((stage/'manifest.json').read_text());gate=json.loads((stage/'installation-ready.json').read_text())
assert gate['experimental_boot_ready'] is True and gate['kernel_sha256']==manifest['kernel_sha256']
version=manifest['version'];assert re.fullmatch(r'[A-Za-z0-9._+-]+',version) and version.endswith('-cram-vm-arena-r2+')
stock='7.0.0-38-generic';recovery=Path('/etc/default/grub.d/99-cram-recovery.cfg')
recovery_data=recovery.read_bytes();assert stock.encode() in recovery_data
assert Path('/boot/vmlinuz-'+stock).is_file() and Path('/boot/initrd.img-'+stock).is_file()
image=stage/'vmlinuz';assert hashlib.sha256(image.read_bytes()).hexdigest()==manifest['kernel_sha256']
config=(stage/'config').read_text()
for item in ['CONFIG_CRAM_ARENA=y','CONFIG_FUSION_SPI=y','CONFIG_EXT4_FS=y','# CONFIG_ZSWAP_DEFAULT_ON is not set']:
 assert item in config,item
modules=stage/'lib/modules'/version;assert modules.is_dir()
module_hashes=json.loads((stage/'module-hashes.json').read_text())
assert 'CONFIG_E1000=y' in config or ('CONFIG_E1000=m' in config and 'kernel/drivers/net/ethernet/intel/e1000/e1000.ko' in module_hashes),'Missing VMware network driver'
for name,digest in module_hashes.items():
 target=modules/name;assert target.resolve().is_relative_to(modules.resolve())
 assert hashlib.sha256(target.read_bytes()).hexdigest()==digest,name
needed=sum(f.stat().st_size for f in modules.rglob('*') if f.is_file() and not f.is_symlink())+image.stat().st_size+256*1024*1024
assert shutil.disk_usage('/').free>needed+512*1024*1024,'Insufficient installation/recovery headroom'
assert not Path('/lib/modules',version).exists()
for name in ('vmlinuz-','System.map-','config-','initrd.img-'):assert not Path('/boot',name+version).exists()
custom=Path('/etc/grub.d/42_cram_arena_r2');assert not custom.exists()
backup=Path(tempfile.mkdtemp(prefix='cram-arena-',dir='/var/backups'))
shutil.copy2('/boot/grub/grub.cfg',backup/'grub.cfg')
shutil.copy2(recovery,backup/'99-cram-recovery.cfg')
try:
 shutil.copytree(modules,Path('/lib/modules')/version,symlinks=True)
 for src,dst in [('vmlinuz','vmlinuz-'),('System.map','System.map-'),('config','config-')]:
  dest=Path('/boot',dst+version);shutil.copyfile(stage/src,dest);dest.chmod(0o644)
 subprocess.run(['depmod','-a',version],check=True)
 subprocess.run(['update-initramfs','-c','-k',version],check=True)
 subprocess.run(['update-grub'],check=True)
 lines=Path('/boot/grub/grub.cfg').read_text().splitlines(True);start=None;entry=[];depth=0
 for i,line in enumerate(lines):
  if re.match(r"\s*menuentry 'Ubuntu, with Linux "+re.escape(version)+r"'",line):start=i;break
 assert start is not None,'New kernel menu entry not found'
 for line in lines[start:]:
  entry.append(line);depth+=line.count('{')-line.count('}')
  if depth==0:break
 assert depth==0 and len(entry)>2
 entry[0]=re.sub(r"menuentry '[^']+'", "menuentry 'CRAM arena experimental (4 MiB)'",entry[0],count=1)
 entry[0]=re.sub(r"'gnulinux-[^']+'", "'cram-arena-r2'",entry[0],count=1)
 linux_lines=0
 for i,line in enumerate(entry):
  if re.match(r'\s*linux\s',line):
   assert 'vmlinuz-'+version in line
   line=re.sub(r'\bzswap\.(?:enabled|cram_arena_mb|cram_arena_fail_every)=\S+','',line)
   entry[i]=line.rstrip()+' zswap.enabled=1 zswap.cram_arena_mb=4 zswap.cram_arena_fail_every=0\n';linux_lines+=1
 assert linux_lines==1
 custom.write_text('#!/bin/sh\nexec tail -n +3 "$0"\n'+''.join(entry));custom.chmod(0o755)
 subprocess.run(['update-grub'],check=True)
 subprocess.run(['grub-script-check','/boot/grub/grub.cfg'],check=True)
 assert recovery.read_bytes()==recovery_data,'Stock-default drop-in changed'
 grub=Path('/boot/grub/grub.cfg').read_text();assert 'cram-arena-r2' in grub and stock in grub
 result={'installed':True,'version':version,'kernel_sha256':manifest['kernel_sha256'],'stock_recovery_retained':True,'stock_default_dropin_unchanged':True,'rebooted':False,'menu_title':'CRAM arena experimental (4 MiB)'}
 (stage/'installation-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
except BaseException:
 shutil.copy2(backup/'grub.cfg','/boot/grub/grub.cfg')
 if custom.exists():custom.unlink()
 print('Installation incomplete. Previous GRUB configuration restored; existing kernels retained. No reboot performed.')
 raise
