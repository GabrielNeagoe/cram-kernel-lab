from pathlib import Path
import subprocess,hashlib,json
release='7.2.0-rc2-cram-vm-arena-r2+'
assert 'CONFIG_VMWARE_BALLOON=m' in Path('/boot/config-'+release).read_text()
image=Path('/boot/initrd.img-'+release)
names=subprocess.check_output(['lsinitramfs',str(image)],text=True)
assert not any('vmw_balloon' in line or 'vmw-balloon' in line for line in names.splitlines())
p=Path('/etc/modprobe.d/cram-startup-guard.conf')
content='blacklist vmw_balloon\ninstall vmw_balloon /bin/false\n'
assert not p.exists()
p.write_text(content);p.chmod(0o644)
if Path('/sys/module/vmw_balloon').exists(): subprocess.run(['rmmod','vmw_balloon'],check=True)
result=subprocess.run(['modprobe','vmw_balloon'],capture_output=True,text=True)
assert result.returncode!=0 and not Path('/sys/module/vmw_balloon').exists()
print(json.dumps(dict(startup_guard_installed=True,stock_load_blocked=True,initramfs_has_balloon=False,initramfs_sha256=hashlib.sha256(image.read_bytes()).hexdigest())))
