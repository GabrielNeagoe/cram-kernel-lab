"""Derive an explicitly separate diagnostic version of the recorded phased source."""
from pathlib import Path
import argparse
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path);p.add_argument('output',type=Path)
p.add_argument('--pin-cpu0',action='store_true')
a=p.parse_args()
text=a.source.read_text()
anchor='static void flush(unsigned char *p) {'
target='if (rc || delta != PAGES) return 17;'
assert text.count(anchor)==text.count(target)==1
text=text.replace(anchor,'#include "cram-partial-inspect.h"\n'+anchor)
text=text.replace(target,'if (rc || delta != PAGES) { inspect_partial(p, run); return 17; }')
if a.pin_cpu0:
    text=text.replace('int main(int argc, char **argv) {','int main(int argc, char **argv) {\n    cpu_set_t pinned; CPU_ZERO(&pinned); CPU_SET(0,&pinned);\n    if (sched_setaffinity(0,sizeof(pinned),&pinned)) { perror("affinity"); return 39; }')
a.output.write_text(text)
