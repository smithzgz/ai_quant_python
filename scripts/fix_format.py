import json

path = r'D:\code\Python\ai_quant_python\visualization\grafana\dashboards\bond_basic.json'
with open(path, encoding='utf-8') as f:
    d = json.load(f)

changed = 0
for p in d['panels']:
    ptype = p.get('type', '')
    pid = p.get('id', '')
    if ptype in ('row',):
        continue
    for t in p.get('targets', []):
        fmt = t.get('format', None)
        if ptype == 'timeseries':
            # timeseries panels should NOT have format=table
            if fmt == 'table':
                del t['format']
                changed += 1
                print(f"Panel {pid} ({ptype}): removed format=table")
        else:
            # stat, table panels need format=table
            if fmt is None:
                t['format'] = 'table'
                changed += 1
                print(f"Panel {pid} ({ptype}): added format=table")

with open(path, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)

print(f"\nTotal changes: {changed}")
