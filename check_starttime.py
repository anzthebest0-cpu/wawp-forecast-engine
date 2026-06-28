import glob, re
for f in glob.glob('Archives/Meteologix_MultiModel/20260628/raw_script_*.txt'):
    with open(f, 'r', encoding='utf-8') as file:
        txt = file.read()
        m = re.search(r'hccompact_model_starttime\s*=\s*["\']?(\d+)["\']?', txt)
        print(f, m.group(1) if m else 'NOT FOUND')
