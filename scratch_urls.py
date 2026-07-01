import urllib.request
import re

req = urllib.request.Request(
    'https://web-aviation.bmkg.go.id/web/sigwx_high_level.php',
    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
)
try:
    res = urllib.request.urlopen(req)
    html = res.read().decode('utf-8')
    print('High SIGWX Links:')
    print(re.findall(r'src=[\"\'](.*?)[\"\']', html))
except Exception as e:
    print('High SIGWX Error:', e)

req2 = urllib.request.Request(
    'https://web-aviation.bmkg.go.id/web/windtemp.php',
    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
)
try:
    res2 = urllib.request.urlopen(req2)
    html2 = res2.read().decode('utf-8')
    print('Windtemp Links:')
    print(re.findall(r'src=[\"\'](.*?)[\"\']', html2))
except Exception as e:
    print('Windtemp Error:', e)
