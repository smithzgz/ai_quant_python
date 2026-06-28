import json
with open(r'D:\code\Python\ai_quant_python\visualization\grafana\dashboards\bond_basic.json', encoding='utf-8') as f:
    d = json.load(f)
print('Valid JSON, panels:', len(d['panels']))
for p in d['panels']:
    if p.get('type') == 'row': continue
    for t in p.get('targets', []):
        fmt = t.get('format', 'NONE')
        pid = p.get('id', '?')
        pt = p.get('type', '?')
        print(f'  Panel {pid} ({pt}): format={fmt}')
