"""Runner-local, same-initialization exact-request cache. No persistent store."""
import hashlib
from urllib.parse import urlsplit
class SharedFetch:
    def __init__(self, upstream, *, max_bytes=256*1024**2, max_items=1000):
        self.upstream=upstream;self.max_bytes=max_bytes;self.max_items=max_items
        self.store={};self.events=[];self.bytes=0
    def __call__(self,url,cap,interval=None):
        u=urlsplit(url)
        if u.scheme!="https" or u.hostname!="data.ecmwf.int" or not u.path.startswith("/forecasts/20261125/00z/") or u.query or u.fragment:
            raise ValueError("Outside fixed-cycle ECMWF pilot")
        if not isinstance(cap,int) or cap<=0:raise ValueError("Invalid cap")
        if interval is not None:
            start,size=interval
            if not isinstance(start,int) or not isinstance(size,int) or start<0 or not 0<size<=cap:raise ValueError("Invalid range")
        key=(url,tuple(interval) if interval else None)
        hit=key in self.store
        if hit:data=self.store[key]
        else:
            if len(self.store)>=self.max_items:raise ValueError("Cache entry budget")
            if interval and self.bytes+interval[1]>self.max_bytes:raise ValueError("Cache byte budget")
            data=self.upstream(url,min(cap,self.max_bytes-self.bytes),interval)
        if not isinstance(data,bytes) or not data or len(data)>cap or (interval and len(data)!=interval[1]):raise ValueError("Invalid cached payload")
        if not hit:
            if self.bytes+len(data)>self.max_bytes:raise ValueError("Cache byte budget")
            self.store[key]=data;self.bytes+=len(data)
        self.events.append({"url":url,"interval":interval,"hit":hit,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
        return data
