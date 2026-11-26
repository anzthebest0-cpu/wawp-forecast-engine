import json,os,urllib.request
RUN=45767345982
REV="bc6f9718b0dcc2a6735eda74b00599c9dea33220"
url=os.environ["GITHUB_API_URL"]+"/repos/"+os.environ["GITHUB_REPOSITORY"]+"/actions/runs/"+str(RUN)
r=urllib.request.Request(url,headers={"Authorization":"Bearer "+os.environ["GH_TOKEN"],"Accept":"application/vnd.github+json"})
with urllib.request.urlopen(r,timeout=20) as x:d=json.load(x)
assert d["id"]==RUN and d["head_sha"]==REV
ready=d["status"]=="completed" and d["conclusion"]=="success"
with open(os.environ["GITHUB_OUTPUT"],"a") as f:f.write("ready="+str(ready).lower()+"\n")
print("RETIREMENT_PREREQUISITE",json.dumps({"run":RUN,"status":d["status"],"conclusion":d["conclusion"],"ready":ready}),flush=True)
if d["status"]=="completed" and not ready:raise SystemExit("Previous remote publication failed; retirement blocked")
