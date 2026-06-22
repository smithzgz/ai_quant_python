import requests

auth = ('admin', 'admin')

# Check Data Overview
r = requests.get('http://localhost:8080/api/dashboards/uid/data-overview', auth=auth)
d = r.json()['dashboard']

def find_ds(panels, depth=0):
    for p in panels:
        ds = p.get('datasource', {})
        if ds:
            print(f"{'  '*depth}{p.get('title','?')}: type={ds.get('type','?')} uid={ds.get('uid','?')}")
        if p.get('type') == 'row' and p.get('panels'):
            find_ds(p['panels'], depth+1)

print("=== Data Overview ===")
find_ds(d.get('panels', []))

# Check Stock KLine
r2 = requests.get('http://localhost:8080/api/dashboards/uid/stock-kline', auth=auth)
d2 = r2.json()['dashboard']
print("\n=== Stock KLine ===")
find_ds(d2.get('panels', []))

# Check Backtest
r3 = requests.get('http://localhost:8080/api/dashboards/uid/backtest-analysis', auth=auth)
d3 = r3.json()['dashboard']
print("\n=== Backtest Analysis ===")
find_ds(d3.get('panels', []))
