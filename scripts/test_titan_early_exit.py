from titan_early_exit import plan,CYCLES
from pilot_cycle_discovery import LEADS,MODELS
from copy import deepcopy
def fixture():
    return {"as_of_utc":"2026-11-26T12:00:00+00:00","results":[{"model":m,"selected":CYCLES[m],"available_leads":[h for h in LEADS if m!="aifs-025" or h%6==0]} for m in MODELS]}
d=fixture();assert plan(d)["decision"]=="no_change"
d["results"][0]["selected"]="2026-11-26T06:00:00+00:00";assert plan(d)["decision"]=="update"
d["results"][0]["available_leads"].pop();assert plan(d)["decision"]=="degraded_no_advance"
d=fixture();d["results"][0]["selected"]=None;assert plan(d)["decision"]=="degraded_no_advance"
d=fixture();d["as_of_utc"]="2026-11-27T00:00:01+00:00";assert plan(d)["decision"]=="blocked_stale"
d=fixture();d["results"][0]["selected"]="2026-11-26T18:00:00+00:00"
try:plan(d)
except ValueError:pass
else:raise AssertionError("Future cycle accepted")
print("EARLY_EXIT_TESTS_PASS")
