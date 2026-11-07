/* Drain the Windows working-set event queue for a short bounded interval.
   Count only page ordinals in the validated 32 GiB view. No page contents are read. */
#include <windows.h>
#include <psapi.h>
#include <stdint.h>

typedef struct { uint64_t page; uint32_t ordinal; uint32_t reserved; } entry;
typedef struct { uint64_t total, lost, in_view, drains; } totals;

__declspec(dllexport) DWORD drain_burst(HANDLE h, uintptr_t base, const entry *pages,
        uint32_t n, uint32_t *hits, totals *counts, unsigned milliseconds) {
    if (n!=8192 || milliseconds>20 || !milliseconds) return ERROR_INVALID_PARAMETER;
    static PSAPI_WS_WATCH_INFORMATION_EX events[8192]; /* one reader per process */
    typedef BOOL (WINAPI *query_fn)(HANDLE,PSAPI_WS_WATCH_INFORMATION_EX *,PDWORD);
    query_fn query=(query_fn)GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"K32GetWsChangesEx");
    if (!query) return ERROR_PROC_NOT_FOUND;
    LARGE_INTEGER freq,now;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&now);
    LONGLONG end=now.QuadPart+freq.QuadPart*milliseconds/1000;
    do {
        DWORD bytes=sizeof(events);
        if (!query(h,events,&bytes)) {
            DWORD err=GetLastError();
            if (err!=ERROR_NO_MORE_ITEMS) return err;
            SwitchToThread();
        } else {
            size_t i=0;
            for (; i<8192 && events[i].BasicInfo.FaultingPc; ++i) {
                uintptr_t offset=(uintptr_t)events[i].BasicInfo.FaultingVa-base;
                if (offset>=((uintptr_t)32<<30)) continue;
                ++counts->in_view;
                uint64_t page=offset>>12;
                size_t lo=0,hi=n;
                while(lo<hi) {
                    size_t mid=lo+(hi-lo)/2;
                    if (pages[mid].page<page) lo=mid+1; else hi=mid;
                }
                if (lo<n && pages[lo].page==page) ++hits[pages[lo].ordinal];
            }
            if (i==8192) return ERROR_INVALID_DATA;
            counts->total+=i;
            counts->lost+=(uintptr_t)events[i].BasicInfo.FaultingVa;
        }
        ++counts->drains;
        QueryPerformanceCounter(&now);
    } while(now.QuadPart<end);
    return ERROR_SUCCESS;
}

