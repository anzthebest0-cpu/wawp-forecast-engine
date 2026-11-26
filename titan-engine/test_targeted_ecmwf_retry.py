import urllib.error
from targeted_ecmwf_retry import wrap
def simulate(exceptions):
    calls=[]
    def fetch(*args):
        calls.append(args)
        if exceptions:raise exceptions.pop(0)
        return b"verified"
    return wrap(fetch,sleep=lambda _:None),calls
for code in (429,503):
    f,c=simulate([urllib.error.HTTPError("https://example.test",code,"",{},None)])
    assert f("x",8,(0,8))==b"verified" and len(c)==2
for e in (ValueError("Wrong units"),urllib.error.HTTPError("https://example.test",404,"",{},None)):
    f,c=simulate([e])
    try:f("x",8)
    except type(e):assert len(c)==1
    else:raise AssertionError("Nontransient QC error retried")
f,c=simulate([TimeoutError(),TimeoutError(),TimeoutError()])
try:f("x",8)
except TimeoutError:assert len(c)==3
else:raise AssertionError("Retry budget ignored")
print("TARGETED_TRANSFER_RETRY_TESTS_PASS")
