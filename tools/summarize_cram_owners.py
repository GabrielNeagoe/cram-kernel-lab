"""Check ownership sampling coverage against independently measured guest balloon states."""
import argparse
import json
from pathlib import Path


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def window(scans, start, end, count=1024, pinned=None):
    assert scans[0][1] <= start < end <= scans[-1][0], "Incomplete sampling window"
    inside = [row for row in scans if start <= row[0] <= row[1] <= end]
    assert inside
    other_indices = set()
    available_indices = set()
    states = {}
    first_available = {}
    reuse_after_available = {}
    for sample_start, sample_end, original, others, available in inside:
        assert original + len(others) == count
        for entry in others:
            index = entry[0]
            assert 0 <= index < count
            other_indices.add(index)
            if pinned is not None:
                states.setdefault(index,set()).add(entry[3])
                if (index in first_available and first_available[index] < sample_start
                        and entry[2] == 6 and entry[3] in ('process-backed','different-file','different-offset')):
                    reuse_after_available.setdefault(index,(sample_start,entry[3]))
        available_indices.update(available)
        for index in available:
            first_available.setdefault(index,sample_end)
    gaps = [inside[0][0] - start, end - inside[-1][1]]
    gaps.extend(b[0] - a[1] for a, b in zip(inside, inside[1:]))
    result = dict(seconds=round(end-start, 3), scans=len(inside),
                minimum_original=min(row[2] for row in inside),
                distinct_different_backing=len(other_indices),
                distinct_free_or_zeroed=len(available_indices),
                maximum_unsampled_ms=round(1000*max(gaps), 3))
    if pinned is not None:
        bounds = [inside[0][1]-start, end-inside[-1][0]]
        bounds.extend(b[1]-a[0] for a,b in zip(inside,inside[1:]))
        result['maximum_per_frame_revisit_bound_ms'] = round(1000*max(bounds),3)
        result['pages'] = [dict(index=i, locked=i in pinned, observed_states=sorted(states.get(i,())),
                                observed_free_or_zeroed=i in available_indices)
                           for i in sorted(other_indices | available_indices)]
        result['ordered_reuse'] = [
            dict(index=i,locked=i in pinned,free_or_zeroed_at_s=round(first_available[i]-start,4),
                 later_active_at_s=round(t-start,4),later_backing=state)
            for i,(t,state) in sorted(reuse_after_available.items())]
    return result


def analyze(base, arm):
    folder = base / ("host-pages-" + arm)
    guest = json.loads((base / arm / "result.json").read_text())
    assert guest["outcome"] == "verified" and guest["cap_bytes"] == 4 * 2**30
    assert guest["verified"]["oom_kills"] == 0 and guest["verified"]["zswpwb"] == guest["filled"]["zswpwb"]
    groups = json.loads((folder / "pinned.json").read_text())
    tracked = set(groups["historic_indices"]) - set(groups["pinned_indices"])
    assert len(tracked) == 1024
    assert json.loads((folder / "locks-released.json").read_text())["locked_at_start_and_end"] == 4096
    scans = records(folder / "ownership.jsonl")
    assert all(a[1] <= b[0] for a, b in zip(scans, scans[1:]))
    events = {row["event"]: row["time"] for row in records(base / arm / "events.jsonl")}
    host = records(base / ("observer-" + arm) / "samples.jsonl")
    control = arm == "control"
    opening = events["control" if control else "release"]
    start = max(row["time"] for row in host if row["guest_time"] < opening and row["hold"] == "Y" and row["current"] == 0)
    end = min(row["time"] for row in host if row["guest_time"] > events["close"])
    snapshot=json.loads((folder/'physical-hold.json').read_text())
    detailed='changed_pages' in snapshot
    pinned=set(groups['pinned_indices']) if detailed else None
    if detailed:
        assert snapshot['comparable']==8192 and len(pinned)==4096
        assert len(snapshot['changed_pages'])==snapshot['changed']
        assert all(row['locked']==(row['index'] in pinned) for row in snapshot['changed_pages'])
        tracked=set(range(8192))
    def summarize(left,right):
        return window(scans,left,right,len(tracked),pinned)
    result = dict(whole_window=summarize(start,end))
    if detailed:
        result['snapshot_changes']=snapshot['changed_pages']
        result['cohort_pages']=len(tracked)
    if control:
        assert not guest["offline_indices_final"] and all(row["current"] == 0 for row in guest["samples"])
    else:
        assert tracked <= set(guest["offline_indices_first"]) & set(guest["offline_indices_final"])
        assert max(row["current"] for row in guest["samples"]) <= 2**20
        held = [row for row in host if events["exact_match"] <= row["guest_time"] < events["close"]]
        assert held and all(row["hold"] == "N" and row["current"] == row["target"] > 0 for row in held)
        result.update(inflation_including_boundaries=summarize(start, held[0]["time"]),
                      confirmed_full_hold=summarize(held[0]["time"], held[-1]["time"]),
                      target_mib=sorted({row["target"]/256 for row in held}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    result = {arm: analyze(directory, arm) for arm in ("full1", "control")}
    (directory / "ownership-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
