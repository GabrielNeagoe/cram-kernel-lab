"""Count the two outcomes of the inlined pageout anonymous-mapping check on x86-64."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import struct
import subprocess
import time

from trace_sample import BASE, command, histogram


def kernel_build_id(notes):
    offset = 0
    while offset + 12 <= len(notes):
        namesz, descsz, kind = struct.unpack_from("<III", notes, offset)
        offset += 12
        name = notes[offset:offset+namesz]
        offset += (namesz + 3) & ~3
        value = notes[offset:offset+descsz]
        offset += (descsz + 3) & ~3
        if name == b"GNU\0" and kind == 3:
            return value.hex()
    raise ValueError("Running kernel has no GNU build ID")


def pageout_site(elf):
    build_id = kernel_build_id(Path("/sys/kernel/notes").read_bytes())
    notes = subprocess.check_output(["readelf", "-n", elf], text=True)
    assert re.search(r"Build ID:\s*" + build_id + r"\b", notes)
    dis = subprocess.check_output(["objdump", "-d", "--no-show-raw-insn",
                                   "--disassemble=shrink_folio_list", elf], text=True)
    base = int(re.search(r"^([0-9a-f]+) <shrink_folio_list>:$", dis, re.M)[1], 16)
    # Match both source-level predicates and the success destination before
    # using the saved folio register. Other builds may have different layouts.
    pattern = (r"call\s+\w+ <shmem_mapping>\n\w+:\s+test\s+%al,%al\n"
               r"\w+:\s+jne\s+(\w+) [^\n]*\n(\w+):\s+testb\s+\$0x1,0x10\(%rbx\)\n"
               r"\w+:\s+je\s+\w+ [^\n]*\n\1:[^\n]*\n\w+:\s+call\s+\w+ <folio_clear_dirty_for_io>")
    matches = re.findall(pattern, dis)
    assert len(matches) == 1
    return build_id, int(matches[0][1], 16) - base


def sample(pid, workers, debug, detail=False):
    build_id, offset = pageout_site(os.environ["CRAM_VMLINUX"])
    tids = {int(p.name) for p in Path(f"/proc/{pid}/task").iterdir()}
    assert tids <= set(map(int, (workers/"cgroup.threads").read_text().split()))
    predicate = " if " + " || ".join(f"common_pid == {t}" for t in sorted(tids))
    event = "cram_sqlite_pageout/mapping_check"
    instance = BASE/"instances"/"cram_sqlite_pageout"
    with ExitStack() as stack:
        command(BASE/"kprobe_events",
                f"p:{event} shrink_folio_list+{hex(offset)} anon=+16(%bx):b1@0/8", True)
        stack.callback(command, BASE/"kprobe_events", "-:"+event, True)
        instance.mkdir()
        stack.callback(instance.rmdir)
        command(instance/"tracing_on","0")
        probes = [(instance/"events"/event, "hist:keys=anon"),
                  (instance/"events/vmscan/mm_vmscan_lru_shrink_inactive",
                   "hist:keys=reclaim_flags:vals=nr_activate0,nr_scanned")]
        for path, spec in probes:
            command(path/"trigger",spec+predicate)
            stack.callback(command,path/"trigger","!"+spec+predicate)
        start=time.monotonic()
        time.sleep(1)
        for path,spec in probes:
            command(path/"trigger",spec+":pause"+predicate)
        tables=[histogram(path/"hist") for path,_ in probes]
        profile=next(line.split() for line in (BASE/"kprobe_profile").read_text().splitlines()
                     if line.split()[0]=="mapping_check")
        return dict(seconds=round(time.monotonic()-start,3),build_id=build_id,
                    function_offset=offset,anonymous_mapping=tables[0],shrink=tables[1],
                    probe_misses=int(profile[-1]))
