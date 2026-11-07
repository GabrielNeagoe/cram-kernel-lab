"""Check ownership sampling coverage against independently measured guest balloon states."""
import argparse
import json
from pathlib import Path


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def window(scans, start, end):
    assert scans[0][1] <= start < end <= scans[-1][0], "Incomplete sampling window"
    inside = [row for row in scans if start <= row[0] <= row[1] <= end]
    assert inside
    other_indices = set()
    available_indices = set()
    for _, _, original, others, available in inside:
        assert original + len(others) == 1024
        other_indices.update(index for index, _, _ in others)
        available_indices.update(available)
    gaps = [inside[0][0] - start, end - inside[-1][1]]
    gaps.extend(b[0] - a[1] for a, b in zip(inside, inside[1:]))
    return dict(seconds=round(end-start, 3), scans=len(inside),
                minimum_original=min(row[2] for row in inside),
                distinct_different_backing=len(other_indices),
                distinct_free_or_zeroed=len(available_indices),
                maximum_unsampled_ms=round(1000*max(gaps), 3))


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
    result = dict(whole_window=window(scans, start, end))
    if control:
        assert not guest["offline_indices_final"] and all(row["current"] == 0 for row in guest["samples"])
    else:
        assert tracked <= set(guest["offline_indices_first"]) & set(guest["offline_indices_final"])
        assert max(row["current"] for row in guest["samples"]) <= 2**20
        held = [row for row in host if events["exact_match"] <= row["guest_time"] < events["close"]]
        assert held and all(row["hold"] == "N" and row["current"] == row["target"] > 0 for row in held)
        result.update(inflation_including_boundaries=window(scans, start, held[0]["time"]),
                      confirmed_full_hold=window(scans, held[0]["time"], held[-1]["time"]),
                      target_mib=sorted({row["target"]/256 for row in held}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    result = {arm: analyze(directory, arm) for arm in ("full1", "control")}
    (directory / "ownership-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
