import requests
import re
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
try:
    r = requests.get('https://web-aviation.bmkg.go.id/web/sigwx_high_level.php', headers=headers, timeout=10)
    print('High SIGWX Status:', r.status_code)
    if r.status_code == 200:
        links = set(re.findall(r'/model/[^\'\"]+', r.text))
        print('High SIGWX Links:', links)
except Exception as e:
    print('High SIGWX Error:', e)

try:
    r2 = requests.get('https://web-aviation.bmkg.go.id/web/wind_temp.php', headers=headers, timeout=10)
    print('Wind/Temp Status:', r2.status_code)
    if r2.status_code == 200:
        links = set(re.findall(r'/model/[^\'\"]+', r2.text))
        print('Wind/Temp Links:', links)
except Exception as e:
    print('Wind/Temp Error:', e)
