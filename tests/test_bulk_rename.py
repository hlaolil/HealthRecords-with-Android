"""Standardising medication names from a reviewed list (Add Medication page)."""
from _root import ROOT  # the app folder, one up from tests/
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import csv, io, sys
from datetime import datetime, timezone
import mongomock, pymongo, openpyxl

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
A.get_mongo_client = lambda _client=_f: _client
A.init_db_collections()
db = _f['pharmacy_db']
meds, txs = db['medications'], db['transactions']
c = A.app.test_client()
from test_support import arm_csrf
arm_csrf(c)
PASS, FAIL = [], []


def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label + (('  ->  ' + detail) if detail and not cond else ''))


def login(role='admin', name='Admin User'):
    with c.session_transaction() as s:
        s['user'] = {'name': name, 'login': 'a', 'role': role}
        s['last_activity'] = datetime.now(timezone.utc).isoformat()


def sheet(rows, heads=('Current name', 'Standard name', 'Apply')):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(list(heads))
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


def upload(data, name='renames.xlsx'):
    return c.post('/add-medication', data={'action': 'upload_names', 'sheet': (io.BytesIO(data), name)},
                  content_type='multipart/form-data', follow_redirects=True)


def draft():
    return db['upload_drafts'].find_one({'kind': 'renames'}) or {}


def apply_ticked(ticks, edits=None):
    data = {'action': 'names_bulk'}
    for i in ticks:
        data[f'ren__{i}'] = '1'
    for i, v in (edits or {}).items():
        data[f'new__{i}'] = v
    return c.post('/add-medication', data=data, follow_redirects=True)


login()
# =====================================================================
print('\n--- 1. Small cases ---')
for n in ['Amoxycillin Cap, 250 mg', 'Cetrizine, 010 mg', 'Paracetamol tabs, 500 mg', 'Zinc Tablets, 020 mg']:
    meds.insert_one({'name': n, 'balance': 10, 'price': 1.0, 'pack_size': 1, 'pack_price': 1.0,
                     'schedule': 'not controlled', 'category': 'Pharmaceutical'})
A._assign_missing_codes(meds)
txs.insert_one({'type': 'dispense', 'med_name': 'Amoxycillin Cap, 250 mg', 'quantity': 3,
                'timestamp': datetime.utcnow(), 'user': 'u'})
page = c.get('/add-medication').data.decode()
check('the page offers the rename upload and its template',
      'value="upload_names"' in page and '/upload-template/renames' in page)
r = upload(sheet([
    ('Amoxycillin Cap, 250 mg', 'Amoxicillin capsules, 250 mg', 'Yes'),
    ('Cetrizine, 010 mg', 'Cetirizine tablets, 10 mg', 'Check'),
    ('Paracetamol tabs, 500 mg', 'Paracetamol tabs, 500 mg', 'Yes'),
    ('Not a real item', 'Something', 'Yes'),
    ('Zinc Tablets, 020 mg', 'paracetamol TABS, 500mg', 'Yes'),
]))
d = draft()
st = {x['current']: (x['status'], x['apply']) for x in d.get('rows', [])}
check('nothing is renamed on upload', meds.find_one({'name': 'Amoxycillin Cap, 250 mg'}) is not None)
check('a ready Yes row starts ticked', st['Amoxycillin Cap, 250 mg'] == ('ok', True), str(st))
check('a Check row is ready but not ticked', st['Cetrizine, 010 mg'] == ('ok', False))
check('an unchanged name is shown as such', st['Paracetamol tabs, 500 mg'][0] == 'same')
check('an unknown current name is flagged', st['Not a real item'][0] == 'missing')
check('a new name another item has (any spelling) is a clash', st['Zinc Tablets, 020 mg'][0] == 'clash')
page = c.get('/add-medication').data.decode()
check('the review shows on the page', 'id="names-review"' in page and 'Rename Ticked Items' in page)
r = apply_ticked([0, 1, 4], edits={1: 'Cetirizine tablets, 10 mg'})
check('ticked rows are renamed', meds.find_one({'name': 'Amoxicillin capsules, 250 mg'}) is not None
      and meds.find_one({'name': 'Cetirizine tablets, 10 mg'}) is not None)
check('a clash row cannot be forced by ticking it', meds.find_one({'name': 'Zinc Tablets, 020 mg'}) is not None)
check('history moves with the rename',
      txs.count_documents({'med_name': 'Amoxicillin capsules, 250 mg'}) == 1
      and txs.count_documents({'med_name': 'Amoxycillin Cap, 250 mg'}) == 0)
m = meds.find_one({'name': 'Amoxicillin capsules, 250 mg'})
check('the old name is kept as a former name, and the code stays',
      m.get('former_names') == ['Amoxycillin Cap, 250 mg'] and m.get('code', '').startswith('MED-'))
check('the old name still finds it', A._med_matcher(['Amoxicillin capsules, 250 mg'])('Amoxycillin Cap, 250 mg')[0]
      == 'Amoxicillin capsules, 250 mg')
check('the audit log records the batch', db['audit_log'].count_documents(
    {'changes': {'$regex': 'names standardised from a list'}}) == 1)
check('the list is cleared afterwards', not draft())
check('the message says how many', '2 item(s) renamed' in r.data.decode())

print('\n--- 2. A name typed in the review that is taken is refused at apply ---')
upload(sheet([('Zinc Tablets, 020 mg', 'Zinc tablets, 20 mg', 'Yes')]))
r = apply_ticked([0], edits={0: 'Paracetamol tabs, 500 mg'})
check('the item keeps its name', meds.find_one({'name': 'Zinc Tablets, 020 mg'}) is not None)
check('and the message says why', 'new name is taken' in r.data.decode())

print('\n--- 3. Two items swapping names in one list ---')
for n in ['Swap A', 'Swap B']:
    meds.insert_one({'name': n, 'balance': 1, 'price': 1.0})
upload(sheet([('Swap A', 'Swap C', 'Yes'), ('Swap B', 'Swap A', 'Yes')]))
st = {x['current']: x['status'] for x in draft()['rows']}
check('a new name freed by another row of the list is not a clash', st == {'Swap A': 'ok', 'Swap B': 'ok'}, str(st))
apply_ticked([0, 1])
check('both renamed, in the order that works',
      meds.find_one({'name': 'Swap C'}) and meds.find_one({'name': 'Swap A'}) and not meds.find_one({'name': 'Swap B'}))
upload(sheet([('Swap C', 'Twin', 'Yes'), ('Swap A', 'twin', 'Yes')]))
st = {x['current']: x['status'] for x in draft()['rows']}
check('two rows giving the same new name are both clashes', set(st.values()) == {'clash'}, str(st))
c.post('/add-medication', data={'action': 'discard_names'})
check('discarding clears the list', not draft())

print('\n--- 4. Only admins ---')
login(role='user', name='Pharm')
upload(sheet([('Swap C', 'Hacked', 'Yes')]))
check('a non-admin cannot upload renames', not draft() and not meds.find_one({'name': 'Hacked'}))
r = c.get('/upload-template/renames')
check('nor download the names sheet', r.status_code == 302)
login()

print('\n--- 5. The real list, 14 November 2026 ---')
csv_path = '/mnt/user-data/uploads/ce8737a9-stock-on-hand-20261114.csv'
review = '/home/claude/names/out/medication-names-review.xlsx'
if os.path.exists(csv_path) and os.path.exists(review):
    meds.delete_many({}); txs.delete_many({})
    items = list(csv.DictReader(open(csv_path)))
    for row in items:
        meds.insert_one({'name': row['Medication'], 'balance': int(float(row['Balance'] or 0)),
                         'price': float(row['Unit Price'] or 0)})
        txs.insert_one({'type': 'receive', 'med_name': row['Medication'], 'quantity': 1,
                        'timestamp': datetime.utcnow(), 'user': 'u'})
    A._assign_missing_codes(meds)
    r = upload(open(review, 'rb').read(), 'medication-names-review.xlsx')
    rows = draft().get('rows', [])
    by = {}
    for x in rows:
        by.setdefault(x['status'], 0); by[x['status']] += 1
    check('all 302 rows read from the review sheet (not the How to use tab)', len(rows) == 302, str(len(rows)))
    check('no clashes and nothing missing', by.get('clash', 0) == 0 and by.get('missing', 0) == 0, str(by))
    ticked = [i for i, x in enumerate(rows) if x['apply']]
    check('the 235 rows marked Yes start ticked', len(ticked) == 235, str(len(ticked)))
    r = apply_ticked(ticked)
    names = {m['name'] for m in meds.find({}, {'name': 1})}
    check('all 235 renamed', '235 item(s) renamed' in r.data.decode())
    check('302 items still, none lost or merged', meds.count_documents({}) == 302)
    check('every item still has its history', txs.count_documents({}) == 302
          and all(t['med_name'] in names for t in txs.find({}, {'med_name': 1})))
    check('a sample reads as the standard',
          {'Amoxicillin capsules, 250 mg', 'Cetirizine tablets, 10 mg',
           'Rifampicin/isoniazid/pyrazinamide/ethambutol tablets, 150/75/400/275 mg',
           'Crepe bandage, 150 mm'} <= names)
    check('the Check rows are untouched until approved',
          'Amiodarone, 006 mg' in names and 'Emergency Pill' in names)
    m = A._med_matcher(sorted(names))
    check('invoices with the old names still match',
          m('Amoxycillin Cap, 250 mg')[0] == 'Amoxicillin capsules, 250 mg'
          and m('CTX 960mg')[0] == 'Co-trimoxazole tablets, 960 mg')
    r = c.get('/upload-template/renames')
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    heads = [x.value for x in wb.worksheets[0][1]]
    check('the names sheet downloads with the columns the upload reads',
          heads == ['Current name', 'Standard name', 'Apply', 'Code'], str(heads))
else:
    print('  (the real list is not in this workspace; section skipped)')

print('\n--- 6. Fast enough for the live site, and safe to run again ---')
import mongomock as _mm
meds.delete_many({}); txs.delete_many({})
pairs = []
for k in range(240):
    meds.insert_one({'name': f'Old item {k:03d}', 'balance': 1})
    for _ in range(5):
        txs.insert_one({'type': 'dispense', 'med_name': f'Old item {k:03d}', 'quantity': 1})
    pairs.append((f'Old item {k:03d}', f'Standard item {k:03d}'))
calls = {'n': 0}
_C = _mm.collection.Collection
_saved = {}
for meth in ['find', 'find_one', 'update_one', 'update_many', 'bulk_write', 'insert_one',
             'delete_one', 'delete_many', 'count_documents', 'aggregate']:
    _saved[meth] = getattr(_C, meth)
    def _w(self, *a, _o=_saved[meth], **k):
        calls['n'] += 1
        return _o(self, *a, **k)
    setattr(_C, meth, _w)
meds.update_many({}, {'$unset': {'code': ''}})
calls['n'] = 0
A._assign_missing_codes(meds)
codes_calls = calls['n']
# a run that was cut off: 10 items fully renamed, 5 more with history moved but not the name
calls['n'] = 0
A._rename_many(db, pairs[:10])
txs.update_many({'med_name': {'$in': [o for o, _ in pairs[10:15]]}}, {'$set': {'med_name': 'moved'}})
for o, n in pairs[10:15]:
    txs.update_many({'med_name': 'moved', 'quantity': 1}, {'$set': {'med_name': n}})
calls['n'] = 0
done, stuck, already = A._apply_renames(db, pairs)
rename_calls = calls['n']
for meth, f in _saved.items():
    setattr(_C, meth, f)
check(f'numbering 240 codes takes a few requests, not one each ({codes_calls})', codes_calls <= 5)
check(f'renaming 240 items takes a few requests, not thousands ({rename_calls})', rename_calls <= 20)
check('a run cut off part way: the second run reports what was already done',
      len(already) == 10 and len(done) == 230 and not stuck, f'{len(already)} {len(done)} {stuck}')
check('and finishes the rest, history and all',
      meds.count_documents({'name': {'$regex': '^Old item'}}) == 0
      and meds.count_documents({'name': {'$regex': '^Standard item'}}) == 240
      and txs.count_documents({'med_name': {'$regex': '^Old item'}}) == 0)
upload(sheet([(o, n, 'Yes') for o, n in pairs[:3]]))
st = {x['current']: x['status'] for x in draft()['rows']}
check('uploading the sheet again shows finished rows as already renamed',
      set(st.values()) == {'done'}, str(st))
c.post('/add-medication', data={'action': 'discard_names'})

print(f'\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========')
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    raise SystemExit(1)
