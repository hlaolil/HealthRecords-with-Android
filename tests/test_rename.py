"""Product codes, and renaming a medication without losing its history."""
from _root import ROOT  # the app folder, one up from tests/
import io
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import mongomock, pymongo
import html as _html
from datetime import datetime, timedelta, timezone

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
A.get_mongo_client = lambda _client=_f: _client
A.init_db_collections()
db = _f['pharmacy_db']
meds, txs = db['medications'], db['transactions']
c = A.app.test_client()
from test_support import arm_csrf, inventory_rows, reconciles
arm_csrf(c)
PASS, FAIL = [], []


def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label + (('  ->  ' + detail) if detail and not cond else ''))


def login(role='admin', name='Admin User'):
    with c.session_transaction() as s:
        s['user'] = {'name': name, 'login': 'a', 'role': role}
        s['last_activity'] = datetime.now(timezone.utc).isoformat()


def add(name, balance=100, **extra):
    doc = {'name': name, 'balance': balance, 'batch': 'B1', 'price': 1.0, 'pack_size': 10,
           'pack_price': 10.0, 'expiry_date': '2030-01-31', 'schedule': 'not controlled',
           'category': 'Pharmaceutical'}
    doc.update(extra)
    meds.insert_one(doc)


def text(r):
    return _html.unescape(r.data.decode())


login()
# =====================================================================
print('\n--- 1. Every item gets a code ---')
add('Zinc tabs, 20 mg')
add('Amoxicillin caps, 500 mg')
add('Metformin tabs, 500 mg', code='MED-0007')
n = A._assign_missing_codes(meds)
check('items without a code are numbered', n == 2)
check('after the highest code already in use, in list order',
      meds.find_one({'name': 'Amoxicillin caps, 500 mg'})['code'] == 'MED-0008'
      and meds.find_one({'name': 'Zinc tabs, 20 mg'})['code'] == 'MED-0009')
check('a code already given is left alone', meds.find_one({'name': 'Metformin tabs, 500 mg'})['code'] == 'MED-0007')
check('running it again changes nothing', A._assign_missing_codes(meds) == 0)
check('the next new code follows on', A._next_med_code(meds) == 'MED-0010')
check('codes are cleaned: spaces gone, capitals', A._clean_code(' med 12 ') == 'MED12')
check('odd characters are refused', A._code_problem('MED#1') != '' and A._code_problem('NDSO-123/4') == '')

r = c.post('/add-medication', data={'med_name': 'Cetirizine tabs, 10 mg', 'category': 'Pharmaceutical',
           'pack_size': '30', 'pack_price': '18', 'schedule': 'not controlled', 'initial_balance': '0'})
check('a new item is numbered automatically', meds.find_one({'name': 'Cetirizine tabs, 10 mg'})['code'] == 'MED-0010'
      and 'Code MED-0010' in text(r))
r = c.post('/add-medication', data={'med_name': 'Ibuprofen tabs, 200 mg', 'code': 'ndso-555',
           'category': 'Pharmaceutical', 'pack_size': '30', 'pack_price': '18',
           'schedule': 'not controlled', 'initial_balance': '0'})
check('or takes an official code typed in', meds.find_one({'name': 'Ibuprofen tabs, 200 mg'})['code'] == 'NDSO-555')
r = c.post('/add-medication', data={'med_name': 'Ibuprofen tabs, 400 mg', 'code': 'NDSO-555',
           'category': 'Pharmaceutical', 'pack_size': '30', 'pack_price': '18',
           'schedule': 'not controlled', 'initial_balance': '0'})
check('a code already in use is refused', meds.find_one({'name': 'Ibuprofen tabs, 400 mg'}) is None
      and 'already used by Ibuprofen tabs, 200 mg' in text(r))
page = text(c.get('/add-medication'))
check('the add form offers the code', 'name="code"' in page)

# =====================================================================
print('\n--- 2. A rename takes the history with it ---')
OLD, NEW = 'Amoxicillin caps, 500 mg', 'Amoxycillin capsules, 500 mg'
now = datetime.utcnow()
base = now - timedelta(days=20)
for t in [{'type': 'receive', 'quantity': 50, 'timestamp': base},
          {'type': 'dispense', 'quantity': 12, 'timestamp': base + timedelta(days=2)},
          {'type': 'internal_issue', 'quantity': 5, 'department': 'Lab', 'timestamp': base + timedelta(days=3)},
          {'type': 'damage', 'quantity': -2, 'timestamp': base + timedelta(days=4)},
          {'type': 'adjustment', 'quantity': -1, 'stock_take_id': 'x', 'timestamp': base + timedelta(days=5)},
          {'type': 'unmet_demand', 'quantity': 0, 'shortfall': 4, 'timestamp': base + timedelta(days=6)}]:
    txs.insert_one(dict(t, med_name=OLD, user='u'))
meds.update_one({'name': OLD}, {'$set': {'balance': 130}})
db['stock_take_counts'].insert_one({'stock_take_id': 'x', 'med_name': OLD, 'counted': 9, 'timestamp': now})
db['dept_requests'].insert_one({'ref': 'REQ-1', 'department': 'Lab', 'status': 'Pending',
                                'lines': [{'med_name': OLD, 'requested': 10},
                                          {'med_name': 'Zinc tabs, 20 mg', 'requested': 3}]})
db['dept_standards'].insert_one({'department': 'Emergency Trolley', 'item': 'AMOXIL 500', 'med_name': OLD,
                                 'required': 2, 'order': 1})
db['upload_drafts'].insert_one({'login': 'a', 'kind': 'issue', 'context': '',
                                'rows': [{'name': OLD, 'qty': 3}], 'meta': {}})
period = {'report_type': 'inventory', 'start_date': (base - timedelta(days=1)).strftime('%Y-%m-%d'),
          'end_date': now.strftime('%Y-%m-%d')}
before = inventory_rows(c.post('/reports', data=period).data.decode()).get(OLD)
code = meds.find_one({'name': OLD})['code']

page = text(c.get('/edit-medication', query_string={'med_name': OLD}))
check('the edit page lets the name be changed, and shows the code',
      'name="new_name"' in page and f'value="{code}"' in page and 'keeps the history' in page)
form = {'med_name': OLD, 'new_name': NEW, 'code': code, 'batch': 'B1', 'category': 'Pharmaceutical',
        'pack_size': '10', 'pack_price': '10', 'expiry_date': '2030-01-31', 'schedule': 'not controlled'}
r = c.post('/edit-medication', data=form)
m = meds.find_one({'code': code})
check('the item has its new name and keeps its code', m['name'] == NEW and meds.find_one({'name': OLD}) is None)
check('its balance is untouched', m['balance'] == 130)
check('the old name is kept as a former name', m.get('former_names') == [OLD])
check('every ledger entry moved: receive, dispense, issue, loss, count, unmet demand',
      txs.count_documents({'med_name': OLD}) == 0 and txs.count_documents({'med_name': NEW}) == 6)
check('stock-take counts moved', db['stock_take_counts'].find_one({'med_name': NEW}) is not None)
req = db['dept_requests'].find_one({'ref': 'REQ-1'})
check('request lines moved, and only that line',
      [l['med_name'] for l in req['lines']] == [NEW, 'Zinc tabs, 20 mg'])
std = db['dept_standards'].find_one({'item': 'AMOXIL 500'})
check('the trolley list link moved, its own wording kept', std['med_name'] == NEW and std['item'] == 'AMOXIL 500')
check('a waiting upload moved', db['upload_drafts'].find_one({'kind': 'issue'})['rows'][0]['name'] == NEW)
check('the message says the history moved', 'its history moved with it (6 record(s))' in text(r))
check('the audit log records the rename and what moved', db['audit_log'].count_documents(
    {'changes': {'$regex': 'renamed from "Amoxicillin caps, 500 mg".*6 ledger entries'}}) == 1)
after = inventory_rows(c.post('/reports', data=period).data.decode())
same = lambda row: {k: v for k, v in (row or {}).items() if k != 'Item'}
check('the inventory row is the same row under the new name',
      before and OLD not in after and same(after.get(NEW)) == same(before),
      f'{before} vs {after.get(NEW)}')
check('and it still adds up', reconciles(after.get(NEW, {})))

# =====================================================================
print('\n--- 3. The old name and the code still find it ---')
match = A._med_matcher([x['name'] for x in meds.find({}, {'name': 1})])
check('a spreadsheet using the old name finds the renamed item', match(OLD)[0] == NEW and not match(OLD)[2])
check('so does the code', match(code)[0] == NEW)
check('the current name of another item always wins over a former name',
      match('Zinc tabs, 20 mg')[0] == 'Zinc tabs, 20 mg')

# =====================================================================
print('\n--- 4. What a rename refuses ---')
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name='zinc TABS,  20mg'))
check('a name that is already on the list (any spelling) is refused',
      'already on the list' in text(r) and meds.find_one({'name': NEW}) is not None)
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name=NEW, code='MED-0007'))
check('a code already used by another item is refused', 'already used by Metformin' in text(r)
      and meds.find_one({'name': NEW})['code'] == code)
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name=NEW, code='BAD#'))
check('an invalid code is refused', 'letters, numbers' in text(r))
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name=NEW, code='ndso-900'))
check('the code can be changed to an official one', meds.find_one({'name': NEW})['code'] == 'NDSO-900')
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name='   '))
check('an empty name is refused', meds.find_one({'name': NEW}) is not None)

# =====================================================================
print('\n--- 5. Renamed back, and a rename interrupted ---')
r = c.post('/edit-medication', data=dict(form, med_name=NEW, new_name=OLD, code='NDSO-900'))
m = meds.find_one({'code': 'NDSO-900'})
check('renamed back: the old name is current again, not listed as former',
      m['name'] == OLD and m['former_names'] == [NEW] and txs.count_documents({'med_name': OLD}) == 6)
# a rename stopped after the ledger moved but before the item did
txs.update_many({'med_name': OLD}, {'$set': {'med_name': 'Amoxil 500'}})
check('(half done: ledger moved, item not)', meds.find_one({'name': OLD}) is not None)
moved = A._rename_medication(db, OLD, 'Amoxil 500')
check('saving again finishes the job without doubling anything',
      meds.find_one({'name': 'Amoxil 500'}) is not None and moved['ledger'] == 0
      and txs.count_documents({'med_name': 'Amoxil 500'}) == 6
      and db['stock_take_counts'].find_one({'med_name': 'Amoxil 500'}) is not None)

# =====================================================================
print('\n--- 6. The count sheet carries codes ---')
c.post('/stock-take', data={'action': 'open', 'note': 'codes'})
st = db['stock_takes'].find_one({'status': 'open'}); sid = str(st['_id'])
import openpyxl
r = c.get(f'/stock-take?stock_take_id={sid}&download=sheet')
wb = openpyxl.load_workbook(io.BytesIO(r.data)); ws = wb.worksheets[0]
heads = [x.value for x in ws[1]]
rows = {row[1]: row for row in ws.iter_rows(min_row=2, values_only=True)}
check('the count sheet has a Code column first, filled in', heads[0] == 'Code'
      and rows['Zinc tabs, 20 mg'][0] == 'MED-0009', str(heads))
# rename Zinc after the sheet was printed, then upload the old sheet
zf = dict(form, med_name='Zinc tabs, 20 mg', new_name='Zinc sulphate tabs, 20 mg', code='MED-0009')
c.post('/edit-medication', data=zf)
for i in range(2, ws.max_row + 1):
    if ws.cell(i, 2).value == 'Zinc tabs, 20 mg':
        ws.cell(i, 2).value = 'zinc thing'          # even a scribbled name
        ws.cell(i, 4).value = 77
b = io.BytesIO(); wb.save(b)
c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                            'sheet': (io.BytesIO(b.getvalue()), 'old.xlsx')},
       content_type='multipart/form-data')
d = db['upload_drafts'].find_one({'kind': 'stock_take', 'context': sid})
check('a sheet printed before a rename still matches by code',
      d and [x['name'] for x in d['rows']] == ['Zinc sulphate tabs, 20 mg'] and d['rows'][0]['counted'] == 77,
      str(d and d['rows']))

# =====================================================================
print('\n--- 7. Uploading new items with codes ---')
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['Medication', 'Category', 'Schedule', 'Pack size', 'Price per pack', 'Opening balance',
           'Batch', 'Expiry date', 'Code'])
ws.append(['Loratadine tabs, 10 mg', 'Pharmaceutical', 'not controlled', 30, 12, 0, '', '', 'NDSO-700'])
ws.append(['Folic acid tabs, 5 mg', 'Pharmaceutical', 'not controlled', 28, 9, 0, '', '', ''])
ws.append(['Vitamin C tabs, 100 mg', 'Pharmaceutical', 'not controlled', 28, 9, 0, '', '', 'MED-0007'])
b = io.BytesIO(); wb.save(b)
c.post('/add-medication', data={'action': 'upload_meds', 'sheet': (io.BytesIO(b.getvalue()), 'new.xlsx')},
       content_type='multipart/form-data')
d = db['upload_drafts'].find_one({'kind': 'medications'})
by = {x['name']: x for x in d['rows']}
check('the code column is read', by['Loratadine tabs, 10 mg']['code'] == 'NDSO-700')
check('a code already in use is flagged before anything is added',
      'already used by Metformin' in by['Vitamin C tabs, 100 mg']['problem'])
page = text(c.get('/add-medication'))
check('the review table shows the codes', 'name="code__0"' in page and 'NDSO-700' in page)
post = {'action': 'meds_bulk', 'n': '2'}
for i, nm in enumerate(['Loratadine tabs, 10 mg', 'Folic acid tabs, 5 mg']):
    post.update({f'inc__{i}': '1', f'name__{i}': nm, f'code__{i}': by[nm]['code'],
                 f'category__{i}': 'Pharmaceutical', f'schedule__{i}': 'not controlled',
                 f'pack_size__{i}': '30', f'pack_price__{i}': '10', f'opening__{i}': '0'})
c.post('/add-medication', data=post)
check('added with the code given', meds.find_one({'name': 'Loratadine tabs, 10 mg'})['code'] == 'NDSO-700')
check('or the next number when none was given',
      meds.find_one({'name': 'Folic acid tabs, 5 mg'})['code'].startswith('MED-'))

# =====================================================================
print('\n--- 8. Only admins ---')
login(role='user', name='Pharm')
r = c.post('/edit-medication', data=dict(form, med_name='Amoxil 500', new_name='Hacked'))
check('a non-admin cannot rename', meds.find_one({'name': 'Hacked'}) is None)

print(f'\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========')
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    raise SystemExit(1)
