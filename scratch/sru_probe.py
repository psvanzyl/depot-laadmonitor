import urllib.request, urllib.parse, json, re
BASE="https://repository.overheid.nl/sru"
terms = [
 'laadplein',
 'laadinfrastructuur vrachtwagen',
 'private laadinfrastructuur',
 'middenspanningsaansluiting',
 'transportcapaciteit verzwaren',
 'laadinfrastructuur bedrijf eigen terrein',
 'PGS 37',
 'snelladen bedrijfsterrein',
]
for t in terms:
    q = f'c.product-area=="officielepublicaties" AND cql.textAndIndexes all "{t}"'
    url = BASE + "?" + urllib.parse.urlencode({"operation":"search","query":q,"maximumRecords":"1"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"depot-laadmonitor-recon/0.1"})
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode("utf-8","replace")
        m = re.search(r"numberOfRecords>(\d+)<", body)
        print(f"{t!r:45s} -> {m.group(1) if m else '??'} (HTTP {r.status})")
    except Exception as e:
        print(f"{t!r:45s} -> ERR {e}")
