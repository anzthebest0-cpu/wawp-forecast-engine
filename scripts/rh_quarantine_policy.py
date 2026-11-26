"""Conservative experimental policy, not an ECMWF physical validity standard."""
import numpy as np
def quarantine(a,sp,p,lo=-1.,hi=110.):
    if a.shape!=sp.shape or np.isinf(a).any() or not np.isfinite(sp).all():
        return None,{"accepted":False,"reason":"invalid_pressure_or_array"}
    finite=np.isfinite(a);ground=sp>=p
    bad=finite&((a<lo)|(a>hi));above=bad&ground;below=bad&~ground
    count=int(above.sum());denom=int((finite&ground).sum())
    excess=float(np.maximum(lo-a[above],a[above]-hi).max()) if count else 0.
    fraction=count/max(denom,1)
    # Deliberately narrow trial envelope: at most 0.01% and at most one
    # percentage point outside prior QC, never alter other values.
    allowed=denom>0 and fraction<=.0001 and excess<=1.
    report={"accepted":bool(allowed),"above_ground_masked":count if allowed else 0,"above_ground_anomalies":count,"below_ground_anomalies":int(below.sum()),"above_ground_finite":denom,"fraction":fraction,"maximum_excess_pct_points":excess,"policy":"experimental <=0.01% above-ground finite cells, <=1 percentage point outside existing QC; no clipping or interpolation"}
    if not allowed:return None,report
    out=a.copy();out[bad]=np.nan
    if not np.isfinite(out[ground]).any():return None,{**report,"accepted":False,"reason":"no_valid_above_ground_data"}
    return out,report
