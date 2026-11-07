"""Apply reviewed Ubuntu secmark companion changes to the isolated port."""
from pathlib import Path

root = Path('/srv/cram-lab/linux-apparmor')
ubuntu = Path('/srv/cram-lab/apparmor-study/linux-source-7.0.0')

def replace(path, old, new):
    p = root / path
    s = p.read_text()
    assert s.count(old) == 1, path
    p.write_text(s.replace(old, new))

replace('security/apparmor/af_inet.c',
        '\taa_state_t tmp = state;\n\tstate = match_addr_info',
        '\tstate = match_addr_info')
replace('security/lsm_init.c',
        '\tlsm_blob_size_update(&blobs->lbs_bpf_token, &blob_sizes.lbs_bpf_token);',
        '\tlsm_blob_size_update(&blobs->lbs_bpf_token, &blob_sizes.lbs_bpf_token);\n'
        '\n\tif (blobs->lbs_secmark) {\n'
        '\t\tif (blob_sizes.lbs_secmark)\n'
        '\t\t\tblobs->lbs_secmark = false;\n'
        '\t\telse\n\t\t\tblob_sizes.lbs_secmark = true;\n\t}')
for path, signature in [
    ('security/security.c', 'int security_secctx_to_secid('),
    ('security/selinux/hooks.c', 'static int selinux_secmark_enabled('),
]:
    current = (root / path).read_text()
    source = (ubuntu / path).read_text()
    start = current.index(signature)
    end = current.index('\n}', start) + 2
    srcstart = source.index(signature)
    srcend = source.index('\n}', srcstart) + 2
    replace(path, current[start:end], source[srcstart:srcend])
for path, anchor in [
    ('security/selinux/hooks.c', '\t.lbs_bpf_token = sizeof(struct bpf_security_struct),'),
    ('security/smack/smack_lsm.c', '\t.lbs_xattr_count = SMACK_INODE_INIT_XATTRS,'),
]:
    replace(path, anchor, anchor + '\n\t.lbs_secmark = true,')
print('Ubuntu secmark companion changes applied')
