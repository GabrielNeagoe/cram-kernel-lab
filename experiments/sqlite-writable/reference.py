"""Derive independent expected search counts and final row digests from pristine files."""
import hashlib
import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root/"corpus/manifest.json").read_text())
needles = [x.encode() for x in manifest["needles"][:3]]
parts = {}
for spec in manifest["parts"]:
    path = root/"corpus"/spec["file"]
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    digest = hashlib.sha256()
    hits = [0, 0, 0]
    changed = 0
    for key, name, body in db.execute("SELECT rowid,path,body FROM source ORDER BY rowid"):
        update = key % 8 == 0 and len(body) > 0
        changed += int(update)
        for i, needle in enumerate(needles):
            payload = bytes([i+1]) + body[1:] if update else body
            hits[i] += needle in payload
        for field in (name.encode(), payload):
            digest.update(len(field).to_bytes(8, "little"))
            digest.update(field)
    db.close()
    assert changed
    parts[spec["file"]] = dict(changed=changed, hits=hits, sha256=digest.hexdigest())
(root/"reference.json").write_text(json.dumps(parts,indent=2)+"\n")
