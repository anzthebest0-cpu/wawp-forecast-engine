"""Regression tests for the stable read-only inventory comparison."""
import copy
from rehearse_targeted_checkpoint_readback import stable_release_inventory,inventory_diff,fingerprint

def sample():
    return {"checkpoint.db.zst":{"id":100,"name":"checkpoint.db.zst","size":74,
             "digest":"sha256:"+"a"*64,"state":"uploaded","created_at":"2026-11-25T00:00:00Z",
             "download_count":1,"label":None,"updated_at":"2026-11-25T00:00:00Z"}}
def main():
    old=sample();new=copy.deepcopy(old)
    n="checkpoint.db.zst";new[n]["download_count"]=2
    assert old!=new
    stable=stable_release_inventory(old)
    assert stable==stable_release_inventory(new)
    assert fingerprint(stable)==fingerprint(stable_release_inventory(new))
    for field,value in (("id",200),("size",75),("digest","sha256:"+"b"*64),("state","new"),("created_at","2026-11-26T00:00:00Z")):
        new=copy.deepcopy(old);new[n][field]=value
        diff=inventory_diff(stable,stable_release_inventory(new))
        assert diff["changed"]=={n:[field]},diff
    assert inventory_diff(stable,{})["removed"]==[n]
    assert inventory_diff({},stable)["added"]==[n]
    assert inventory_diff({"a":{"id":"x","size":5}},{"a":{"id":"y","size":5}})["changed"]=={"a":["id"]}
    print("STABLE_INVENTORY_TEST_PASS: mutable counters ignored; object ID, size, hash, state, creation, additions and removals detected")
if __name__=="__main__":main()
