import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from src import checkpoint_retention as retention
from src.storage_snapshot import file_digest, manifest_path, pack


def fixture(tmp_path, monkeypatch, *, changed=False):
    monkeypatch.chdir(tmp_path)
    cfg=tmp_path/'rclone.conf'; cfg.write_text('[wawp]\ntype = local\n')
    monkeypatch.setenv('RCLONE_CONFIG',str(cfg))
    remote=tmp_path/'checkpoints'/'rolling'; remote.mkdir(parents=True)
    db=tmp_path/'candidate.db'
    assets=[]
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript('CREATE TABLE openmeteo_forecasts(id INTEGER PRIMARY KEY AUTOINCREMENT,value REAL); CREATE TABLE observations(id INTEGER PRIMARY KEY,data REAL); INSERT INTO observations VALUES(1,2);');conn.commit()
        for i in range(1,5):
            conn.execute('INSERT INTO openmeteo_forecasts(value) VALUES(?)',(float(i),))
            if changed and i==4:
                conn.execute('UPDATE observations SET data=99')
            conn.commit()
            name=f'wawp_forecasts-{i}-1.db.zst'; path=tmp_path/name
            pack(db,path)
            pair=[]
            for f in (path,manifest_path(path)):
                shutil.copyfile(f,remote/f.name)
                a={'id':len(assets)+1,'name':f.name,'size':f.stat().st_size,'digest':'sha256:'+file_digest(f),'state':'uploaded','created_at':f'2026-11-24T0{i}:00:00Z'}
                assets.append(a);pair.append(a)
            if i<4:
                (remote/(name+'.registered.json')).write_text(json.dumps({'version':1,'data':pair[0],'manifest':pair[1]}))
    # A pre-policy original is never selected by the rolling-folder registry.
    assets.append({'id':100,'name':'wawp_forecasts-34999537381-1.db','size':2145767424,'digest':'sha256:original','state':'uploaded','created_at':'2026-09-15T00:00:00Z'})
    (remote/'unrelated.db.zst').write_bytes(b'not managed')
    original_run=retention.run
    deleted=[]
    def run(*args):
        if args[:2]==('gh','api'):
            if '--method' in args:
                v=int(args[-1].split('/')[-1]);deleted.append(v)
                assets[:]=[x for x in assets if x['id']!=v];return ''
            return json.dumps(assets)
        return original_run(*args)
    monkeypatch.setattr(retention,'run',run)
    return remote,db,tmp_path/'wawp_forecasts-4-1.db.zst',assets,deleted


def test_retire_only_oldest_managed_after_verified_superset(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch)
    out=tmp_path/'report.json'
    retention.retire('repo',1,newest,db,out)
    assert json.loads(out.read_text())['retired']==['wawp_forecasts-1-1.db.zst']
    assert deleted==[2,1] and any(a['id']==100 for a in assets)
    assert not (remote/'wawp_forecasts-1-1.db.zst').exists()
    assert (remote/'wawp_forecasts-1-1.db.zst.json').is_file()
    assert (remote/'wawp_forecasts-1-1.db.zst.retired.json').is_file()
    assert (remote/'unrelated.db.zst').is_file()
    assert all((remote/f'wawp_forecasts-{i}-1.db.zst').is_file() for i in (2,3,4))


def test_non_equivalent_snapshot_never_retires(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch,changed=True)
    with pytest.raises(ValueError,match='require review'):
        retention.retire('repo',1,newest,db,tmp_path/'report.json')
    assert not deleted and (remote/'wawp_forecasts-1-1.db.zst').is_file()


def test_pin_and_selection_guards():
    assert not retention.name_allowed('wawp_forecasts-45206519366-1.db.zst')
    assert not retention.name_allowed('wawp_forecasts-45279365274-1.db.zst')
    assert not retention.name_allowed('wawp_forecasts-123-1.db')
    with pytest.raises(ValueError,match='not registered'):
        retention.eligible({},'wawp_forecasts-123-1.db.zst')


def test_corrupted_old_snapshot_never_retires(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch)
    old=remote/'wawp_forecasts-1-1.db.zst'
    with old.open('ab') as f:
        f.write(b'damaged')
    with pytest.raises(ValueError,match='require review'):
        retention.retire('repo',1,newest,db,tmp_path/'report.json')
    assert not deleted and old.is_file()


def test_release_pagination(monkeypatch):
    monkeypatch.setattr(retention,'run',lambda *args:'[{"id":1}]\n[{"id":2}]\n')
    assert retention.release_assets('repo',1)==[[{'id':1}],[{'id':2}]]


def test_resume_after_release_manifest_delete(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch)
    out=tmp_path/'report.json';real=retention.run
    def interrupt(*args):
        result=real(*args)
        if '--method' in args and args[-1].endswith('/2'):
            raise OSError('interrupted after manifest removal')
        return result
    monkeypatch.setattr(retention,'run',interrupt)
    with pytest.raises(ValueError,match='requires review'):
        retention.retire('repo',1,newest,db,out)
    assert deleted==[2] and (remote/'wawp_forecasts-1-1.db.zst.retiring.json').exists()
    monkeypatch.setattr(retention,'run',real)
    retention.retire('repo',1,newest,db,out)
    assert deleted==[2,1] and json.loads(out.read_text())['retired']==['wawp_forecasts-1-1.db.zst']

def test_resume_after_trash_before_receipt(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch)
    out=tmp_path/'report.json';real=retention.RollingTransport.put_json
    def interrupt(self,name,value,work):
        if name.endswith('.retired.json'):raise OSError('interrupted after trash')
        return real(self,name,value,work)
    monkeypatch.setattr(retention.RollingTransport,'put_json',interrupt)
    with pytest.raises(ValueError,match='requires review'):
        retention.retire('repo',1,newest,db,out)
    assert deleted==[2,1] and not (remote/'wawp_forecasts-1-1.db.zst').exists()
    monkeypatch.setattr(retention.RollingTransport,'put_json',real)
    retention.retire('repo',1,newest,db,out)
    assert deleted==[2,1] and (remote/'wawp_forecasts-1-1.db.zst.retired.json').is_file()

def test_unregistered_managed_payload_blocks_retirement(tmp_path,monkeypatch):
    remote,db,newest,assets,deleted=fixture(tmp_path,monkeypatch)
    (remote/'wawp_forecasts-999-1.db.zst').write_bytes(b'unknown')
    with pytest.raises(ValueError,match='requires review'):
        retention.retire('repo',1,newest,db,tmp_path/'report.json')
    assert not deleted

def test_download_counter_not_asset_identity():
    a={'id':1,'name':'x','size':1,'digest':'sha256:a','state':'uploaded','created_at':'t','download_count':1}
    b=dict(a,download_count=2)
    assert retention.stable([[a]])==retention.stable([[b]])
    assert retention.stable([[a]])!=retention.stable([[dict(b,size=2)]])
