import numpy as np
from rh_quarantine_policy import quarantine
a=np.full((151,221),80.);sp=np.full(a.shape,900.)
a[30,40]=110.1257
b,r=quarantine(a,sp,700)
assert r["accepted"] and r["above_ground_masked"]==1 and np.isnan(b[30,40])
assert np.array_equal(b[~np.isnan(b)],a[~np.isnan(b)])
a[30,40]=112.
assert quarantine(a,sp,700)[0] is None
a[30,40]=110.1;a[0,:]=110.1
assert quarantine(a,sp,700)[0] is None
sp[0,:]=600.;sp[30,40]=600.
b,r=quarantine(a,sp,700)
assert r["accepted"] and r["above_ground_masked"]==0 and np.isnan(b[0,:]).all()
sp[2,2]=np.nan
assert quarantine(a,sp,700)[0] is None
print("RH_QUARANTINE_POLICY_TESTS_PASS")
