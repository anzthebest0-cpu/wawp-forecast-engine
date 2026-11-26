"""Strict same-cycle rain interval construction from validated decoded records."""
import numpy as np
def unique(records):
    if not records:return None
    a=records[0]
    keys=("start","end","step","short","units","dx","level","code")
    if any(any(a[k]!=b[k] for k in keys) or not np.array_equal(a["array"],b["array"],equal_nan=True) for b in records[1:]):raise ValueError("Conflicting duplicate accumulation")
    return a
def compatible(a,b):
    return all(a[k]==b[k] for k in ("step","short","units","dx","level","code")) and a["array"].shape==b["array"].shape
def interval(records,start,end):
    if start<0 or end<=start:raise ValueError("Invalid window")
    direct=unique([x for x in records if x["start"]==start and x["end"]==end])
    if direct:return {**direct,"method":"native exact accumulation"}
    # Difference only when both cumulative records have a common origin.
    for origin in sorted({x["start"] for x in records if x["start"]<start}):
        a=unique([x for x in records if (x["start"],x["end"])==(origin,end)])
        b=unique([x for x in records if (x["start"],x["end"])==(origin,start)])
        if a is None or b is None or not compatible(a,b):continue
        arr=a["array"]-b["array"];finite=np.isfinite(arr)
        ea,eb=a.get("packing_error_mm"),b.get("packing_error_mm")
        tol=ea+eb+1e-8 if ea is not None and eb is not None else 1e-8
        if not finite.any() or np.any(finite&(arr < -tol)):continue
        return {**a,"array":np.where(finite&(arr<0),0,arr),"start":start,"method":"same-origin cumulative difference","packing_bound_mm":tol,"roundoff_cells":int((finite&(arr<0)).sum())}
    # Tiling only exact contiguous native intervals; no overlap or extrapolation.
    for mid in sorted({x["end"] for x in records if start<x["end"]<end}):
        a=unique([x for x in records if (x["start"],x["end"])==(start,mid)])
        b=unique([x for x in records if (x["start"],x["end"])==(mid,end)])
        if a is not None and b is not None and compatible(a,b):
            return {**b,"array":a["array"]+b["array"],"start":start,"method":"sum of contiguous native intervals"}
    return None
