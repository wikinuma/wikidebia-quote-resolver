"""Inspect the retained snapshot locally. No network, no quotation text."""
import json,zipfile
from wikirouge_index import open_snapshot,DEFAULT
with open_snapshot() as index:
    db=index.db
    for row in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        print('INDEX_SCHEMA='+json.dumps(dict(row),ensure_ascii=True))
    for row in db.execute("SELECT * FROM info"):
        print('INDEX_INFO='+json.dumps(dict(row),ensure_ascii=True))
    for name in ('pages','fields','pairs','source_urls'):
        print('INDEX_SAMPLE_'+name+'='+json.dumps(dict(db.execute('SELECT * FROM '+name+' LIMIT 1').fetchone()),ensure_ascii=True))
m=json.loads(DEFAULT.read_bytes())
with zipfile.ZipFile(DEFAULT.parent/m['archive']['filename']) as z:
    print('ARCHIVE_MEMBERS='+json.dumps(z.namelist(),ensure_ascii=True))
