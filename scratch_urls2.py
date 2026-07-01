import urllib.request
import re

req = urllib.request.Request(
    'https://web-aviation.bmkg.go.id/',
    headers={'User-Agent': 'Mozilla/5.0'}
)
try:
    res = urllib.request.urlopen(req)
    html = res.read().decode('utf-8')
    print('Links:')
    for link in re.findall(r'href=[\"\'](.*?)[\"\']', html):
        if 'wind' in link.lower() or 'temp' in link.lower() or 'sigwx' in link.lower():
            print(link)
except Exception as e:
    print('Error:', e)
