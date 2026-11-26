"""Local safety contract regression for exact approved checkpoint retirement scope."""
import copy
from retire_approved_checkpoints import EXPECTED,KEEP,RETIRE,IDS,check_scope
from src import checkpoint_retention as r
def fixture():
    records={};assets={};inv={}
    for i,n in enumerate(RETIRE[::-1]+KEEP[::-1]):
        rec={"version":1}
        for k,suffix,j in (("data","",0),("manifest",".json",1)):
            name=n+suffix;m={"id":IDS[n][j],"name":name,"size":10,"digest":"sha256:"+"a"*64,"state":"uploaded","created_at":f"2026-11-25T00:0{i}:00Z"}
            rec[k]=m;assets[name]=m.copy();inv[name]={"id":"fixture-"+str(m["id"]),"size":10}
        inv[n+".registered.json"]={"id":"registration-"+n,"size":20};records[n]=rec
    return inv,assets,records
def reject(f):
    try:f()
    except ValueError:return
    raise AssertionError("Invalid retirement scope accepted")
def main():
    inv,assets,records=fixture();check_scope(inv,assets,records)
    bad=copy.deepcopy(records);bad[RETIRE[0]]["data"]["id"]=0;reject(lambda:check_scope(inv,assets,bad))
    bad=copy.deepcopy(inv);bad[KEEP[0]]["size"]=11;reject(lambda:check_scope(bad,assets,records))
    bad=copy.deepcopy(inv);bad["wawp_forecasts-999999-1.db.zst"]={"id":"extra","size":10};reject(lambda:check_scope(bad,assets,records))
    bad=copy.deepcopy(inv);bad[RETIRE[0]+".retired.json"]={"id":"receipt","size":10};reject(lambda:check_scope(bad,assets,records))
    bad=copy.deepcopy(assets);bad[RETIRE[0]]["digest"]="sha256:"+"b"*64;reject(lambda:check_scope(inv,bad,records))
    print("APPROVED_RETIREMENT_SCOPE_PASS")
if __name__=="__main__":main()
