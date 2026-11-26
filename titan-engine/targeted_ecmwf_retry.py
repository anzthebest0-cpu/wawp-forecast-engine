"""Bounded retry for transient provider transfers only; no relaxed decode checks."""
import urllib.error,http.client,socket,time
RETRY_HTTP={408,429,500,502,503,504}
def wrap(fetch,delays=(0,10,35),sleep=time.sleep,report=lambda x:None):
    def get(url,cap,interval=None):
        for i,delay in enumerate(delays):
            if delay:sleep(delay)
            try:return fetch(url,cap,interval)
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_HTTP or i==len(delays)-1:raise
                report({"attempt":i+1,"http":e.code,"interval":interval})
            except (TimeoutError,ConnectionError,http.client.IncompleteRead,socket.timeout,urllib.error.URLError) as e:
                if i==len(delays)-1:raise
                report({"attempt":i+1,"reason":type(e).__name__,"interval":interval})
    return get
