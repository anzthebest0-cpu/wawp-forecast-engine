"""Mock exact-ID readback and scoped-list-free creation; no network."""
from unittest.mock import patch,Mock
from pathlib import Path
from tempfile import TemporaryDirectory
from titan_exact_folder_recovery import ExactFolder,API
def fixture():
    x=ExactFolder.__new__(ExactFolder);x.parent="fixture-parent";x.reserved=["new-id"];x.t=Mock();x.t._headers.return_value={};x.pace=Mock();x.inventory=Mock(side_effect=AssertionError("Unexpected full list"))
    return x
def response(status,data=None,headers=None):
    r=Mock();r.status_code=status;r.headers=headers or {};r.json.return_value=data;r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False);return r
name="charts-"+"a"*64+".zip"
with TemporaryDirectory() as td:
    p=Path(td)/"p";p.write_bytes(b"test")
    data={"id":"new-id","name":name,"size":"4","parents":["fixture-parent"],"trashed":False}
    for status in (200,503):
        x=fixture();x.get=Mock(return_value=data)
        with patch("titan_exact_folder_recovery.requests.post",return_value=response(200,headers={"Location":"https://www.googleapis.com/upload/fixture"})),patch("titan_exact_folder_recovery.requests.put",return_value=response(status,data)):
            assert x.create(name,p)=={"id":"new-id","size":4}
        x.get.assert_called_once_with(API+"/files/new-id",{"fields":"id,name,size,parents,trashed","supportsAllDrives":"true"},allow_missing=True)
        x.inventory.assert_not_called()
    x=fixture();x.get=Mock(return_value={**data,"parents":["unexpected"]})
    try:x.verify_created("new-id",name,4)
    except ValueError:pass
    else:raise AssertionError("Wrong folder accepted")
print("EXACT_ID_RECOVERY_TESTS_PASS")
