"""Pair the existing bounded balloon controller with rapid sampling and RAMMap file snapshots."""
import argparse, concurrent.futures, ctypes, hashlib, json, mmap, os, subprocess, time, uuid
from pathlib import Path
import numpy as np
import psutil
import win32api, win32con, win32job, win32process, win32event
from track_cram_frame_residency import verify, capture, mapping, wait, win
from rapid_cram_residency import record, check_response
from cram_rammap_snapshot import read_snapshot

DIGEST='e970913798481432cd590991577089e68510b861dc669dcab24feb06aae0df52'

def take_snapshot(exe, output, stop):
    assert hashlib.sha256(exe.read_bytes()).hexdigest()==DIGEST
    assert not output.exists() and psutil.disk_usage(str(output.parent)).free>5*2**30
    job=win32job.CreateJobObject(None,'CRAM-snapshot-'+uuid.uuid4().hex)
    limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags']=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE|win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits['ProcessMemoryLimit']=24*2**30
    win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
    proc,thread,pid,_=win32process.CreateProcess(str(exe),subprocess.list2cmdline([str(exe),str(output)]),None,None,False,win32con.CREATE_SUSPENDED,None,None,win32process.STARTUPINFO())
    start=time.time();peak=0
    try:
        win32job.AssignProcessToJobObject(job,proc)
        api=ctypes.WinDLL('kernel32',use_last_error=True).SetProcessWorkingSetSizeEx
        api.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_uint32]
        if not api(int(proc),2**20,3*2**30,4): raise ctypes.WinError(ctypes.get_last_error())
        win32process.ResumeThread(thread)
        watched=psutil.Process(pid)
        while win32event.WaitForSingleObject(proc,500)==258:
            peak=max(peak,watched.memory_info().rss)
            if stop.exists() or time.time()-start>260 or psutil.virtual_memory().available<16*2**30:
                raise RuntimeError('physical snapshot reached its time or memory guard')
        code=win32process.GetExitCodeProcess(proc)
        peak_commit=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)['PeakProcessMemoryUsed']
        if code or not output.is_file():
            raise RuntimeError(f'RAMMap exit 0x{code & 0xffffffff:08x}, peak committed bytes {peak_commit}, snapshot present {output.is_file()}')
        assert 0 < output.stat().st_size < 3*2**30, 'snapshot outside size bounds'
        return dict(start=start,end=time.time(),observer_peak_sampled_ws=peak,observer_peak_commit=peak_commit)
    finally:
        if win32process.GetExitCodeProcess(proc)==259:win32api.TerminateProcess(proc,1)
        job.Close();thread.Close();proc.Close()

def physical(a,name,folder,deadline,stop):
    private=Path(a.private)
    calibration=private/('cram-calibration-'+uuid.uuid4().hex+'.bin')
    points=[4096*7,4096*511,4096*1937]
    baseline=None
    owner_pool=None
    owner_job=None
    try:
        with calibration.open('xb+') as f:
            f.truncate(8*2**20)
            with mmap.mmap(f.fileno(),0) as anchor:
                for point in points:
                    anchor[point:point+32]=os.urandom(32)
                    address=ctypes.addressof(ctypes.c_char.from_buffer(anchor,point))
                    if not win.k.VirtualLock(ctypes.c_void_p(address),ctypes.c_size_t(4096)):raise ctypes.WinError(ctypes.get_last_error())
                for phase in ['baseline','hold']:
                    wait((folder/('begin-'+phase)).exists,deadline,stop)
                    if phase == 'hold' and a.fast_only_hold:
                        started = time.time()
                        # Match the preceding pair's guest-observed hold durations.
                        duration = 229 if name == 'full1' else 235
                        finish = time.monotonic() + duration
                        while time.monotonic() < finish:
                            if stop.exists() or time.monotonic() > deadline:
                                raise TimeoutError('Fast-only hold interrupted')
                            if owner_job.done():
                                owner_job.result()
                                raise RuntimeError('Ownership sampler exited early')
                            time.sleep(.1)
                        (folder/'fast-only-hold.json').write_text(json.dumps(dict(
                            start=started,end=time.time(),cohort_pages=8192,hold_snapshot=False)))
                        (folder/'hold-done').touch()
                        continue
                    if a.retention_control: wait((folder/'pinned.json').exists,time.monotonic()+20,stop)
                    time.sleep(6)  # Let the independent observer bracket the capture.
                    target=json.loads((private/(name+'.json')).read_text())
                    filename=target['file']; offsets=target['offsets']
                    raw=private/(name+'-'+phase+'.rmp')
                    try:
                        meta=take_snapshot(Path(a.rammap),raw,stop)
                        owner_baseline=a.frame_owners and phase=='baseline'
                        data=read_snapshot(raw,{filename:np.array(offsets,dtype=np.uint64),calibration.name:np.array(points,dtype=np.uint64)},include_keys=owner_baseline)
                        if owner_baseline: data,file_keys=data
                        anchors=[data[calibration.name].get(k,set()) for k in points]
                        assert all(len(v)==1 for v in anchors) and len(set.union(*anchors))==len(points),'physical offset calibration failed'
                        frames=[data[filename].get(k,set()) for k in offsets]
                        if owner_baseline:
                            from cram_owner_sampler import record as record_owners
                            groups=json.loads((folder/'pinned.json').read_text())
                            indices=sorted(set(groups['historic_indices'])-set(groups['pinned_indices']))
                            assert len(indices)==1024
                            if a.all_owners: indices=list(range(8192))
                            assert all(len(frames[i])==1 for i in indices)
                            owner_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            owner_job=owner_pool.submit(record_owners, folder,
                                [next(iter(frames[i])) for i in indices], [offsets[i] for i in indices],
                                file_keys[filename],stop,deadline)
                            wait(lambda:owner_job.done() or (folder/'ownership-ready').exists(),time.monotonic()+10,stop)
                            if owner_job.done():owner_job.result()
                        meta.update(single_identity=sum(len(v)==1 for v in frames),missing=sum(not v for v in frames),ambiguous=sum(len(v)>1 for v in frames),calibration_pages=len(points))
                        if phase=='baseline': baseline=(frames,anchors)
                        else:
                            assert anchors==baseline[1],'locked calibration frames changed'
                            pairs=[(x,y) for x,y in zip(baseline[0],frames) if len(x)==len(y)==1]
                            meta.update(comparable=len(pairs),same=sum(x==y for x,y in pairs),changed=sum(x!=y for x,y in pairs))
                            if a.all_owners:
                                from cram_owner_sampler import backing_state
                                from calibrate_rammap_reuse import inspect
                                changed=[i for i,(x,y) in enumerate(zip(baseline[0],frames))
                                         if len(x)==len(y)==1 and x!=y]
                                meta['changed_pages']=[]
                                if changed:
                                    old=[next(iter(baseline[0][i])) for i in changed]
                                    seen,_=inspect(raw,np.asarray(old,dtype=np.uint64),())
                                    for i,frame in zip(changed,old):
                                        row=seen.get(frame)
                                        state=backing_state(*row,file_keys[filename],offsets[i]//4096) if row else 'missing'
                                        meta['changed_pages'].append(dict(index=i,locked=i in groups['pinned_indices'],
                                            original_frame_state=state))
                        (folder/('physical-'+phase+'.json')).write_text(json.dumps(meta,indent=2))
                    except Exception as e:
                        (folder/('physical-'+phase+'-error.json')).write_text(json.dumps(dict(error=type(e).__name__,detail=str(e)[:200])))
                        if a.frame_owners:raise
                    finally:
                        raw.unlink(missing_ok=True)
                        time.sleep(6)
                        (folder/(phase+'-done')).touch()
    finally:
        calibration.unlink(missing_ok=True)
        if owner_pool:
            owner_pool.shutdown()
            owner_job.result()

def coordinate(a,name,folder,deadline,stop):
    verify(a,name,folder,deadline,stop)
    ssh=[a.plink,'-batch','-ssh',a.destination,'-i',a.key]
    run='/srv/cram-lab/frame-study-20261107/'+a.prefix+name
    for phase,event in [('baseline','baseline'),('hold','control' if name=='control' else 'exact_match')]:
        def observed():
            r=subprocess.run(ssh+['sudo -n cat '+run+'/events.jsonl'],capture_output=True,timeout=8)
            return r.returncode==0 and any(json.loads(s)['event']==event for s in r.stdout.splitlines())
        wait(observed,deadline,stop)
        (folder/('begin-'+phase)).touch()
        wait((folder/(phase+'-done')).exists,deadline,stop)
        subprocess.run(ssh+['sudo -n touch '+run+'/'+phase+'-finished'],check=True,timeout=8)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--retention-control',action='store_true')
    p.add_argument('--frame-owners',action='store_true')
    p.add_argument('--all-owners',action='store_true')
    p.add_argument('--fast-only-hold',action='store_true')
    p.add_argument('--watch-reentry',action='store_true')
    p.add_argument('mode',choices=['capture','verify'])
    for k in ['private','out','rammap','vm-dir','plink','key','destination','prefix']:p.add_argument('--'+k)
    a=p.parse_args();a.count=8192;out=Path(a.out);stop=out/'stop';deadline=time.monotonic()+2400
    if a.frame_owners and not a.retention_control:p.error('--frame-owners requires --retention-control')
    if a.all_owners and not a.frame_owners:p.error('--all-owners requires --frame-owners')
    if a.fast_only_hold and (not a.all_owners or a.watch_reentry):
        p.error('--fast-only-hold requires --all-owners and disables the reentry watcher')
    if a.mode=='capture':
        check_response()
        if a.watch_reentry:
            import cram_working_set_events as reentry
            (out/'reentry-calibration.json').write_text(json.dumps(reentry.calibrate()))
        def ready():
            try:
                h,_,_=mapping();win.k.CloseHandle(h);return True
            except (ValueError,OSError):return False
        wait(ready,time.monotonic()+150,stop)
        (out/'monitor-ready').touch()
    for name in ['full1','control']:
        folder=out/('host-pages-'+name)
        wait((folder/'start').exists,deadline,stop)
        if a.mode=='verify':coordinate(a,name,folder,deadline,stop)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as threads:
                jobs=[threads.submit(physical,a,name,folder,deadline,stop)]
                if not a.fast_only_hold:
                    jobs.append(threads.submit(record,Path(a.private),folder,name,stop,deadline))
                if a.retention_control:
                    import cram_retention_control as retention
                    jobs.append(threads.submit(retention.record,a,name,folder,stop,deadline))
                elif a.watch_reentry:
                    jobs.append(threads.submit(reentry.record,Path(a.private),folder,name,stop,deadline))
                if a.fast_only_hold:
                    (folder/'windows-page-metadata.jsonl').write_text(json.dumps(win.snapshot())+'\n')
                    while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                        time.sleep(.25)
                    (folder/'stopped').touch()
                else:
                    capture(a,name,folder,deadline,stop)
                for j in jobs:j.result()


