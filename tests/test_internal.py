from _root import ROOT  # the app folder, one up from tests/
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import mongomock, pymongo, re as _re
from datetime import datetime, timedelta, timezone

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
# Bound by VALUE on purpose. As a closure over the name, any later
# assignment to that name anywhere in this file would silently replace
# the database for the rest of the run.
A.get_mongo_client = lambda _client=_f: _client
A.init_db_collections()
db = _f['pharmacy_db']
meds, txs = db['medications'], db['transactions']
c = A.app.test_client()
# The app refuses a POST without its CSRF token; this makes the
# test client send it, so these checks exercise the real path.
from test_support import arm_csrf, inventory_rows, reconciles
arm_csrf(c)
PASS, FAIL = [], []

def _reg_rows(b, name=None):
    """Rows of the losses register: DATE, Item, QTY, EXPIRY, Batch, Cost, Remarks."""
    seg = b[b.index('Stock Losses for'):]
    seg = seg[:seg.index('</table>')]
    out = []
    for m in _re.findall(r'<tr[^>]*>(.*?)</tr>', seg, _re.S):
        cells = [_re.sub(r'<[^>]+>', '', x).strip()
                 for x in _re.findall(r'<td>(.*?)</td>', m, _re.S)]
        if cells and (name is None or cells[1] == name):
            out.append(cells)
    return out



def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label + (('  ->  ' + detail) if detail and not cond else ''))


def login(role='admin', name='Admin User'):
    with c.session_transaction() as s:
        s['user'] = {'name': name, 'login': 'a', 'role': role}
        s['last_activity'] = datetime.now(timezone.utc).isoformat()


def reset():
    for col in ['medications', 'transactions', 'audit_log', 'deliveries']:
        db[col].delete_many({})


NOW = datetime.utcnow()
M_START = NOW.strftime('%Y-%m-01')
M_END = NOW.strftime('%Y-%m-%d')


def add(name, balance=1000, price=2.5):
    meds.insert_one({'name': name, 'balance': balance, 'batch': 'B', 'price': price,
                     'pack_size': 100, 'pack_price': price * 100,
                     'expiry_date': '2030-01-01', 'schedule': 'not controlled',
                     'supplier': 'S'})


def issue(dept, pairs, to='Sr Mokoena', note='', other=''):
    return c.post('/internal-issue', data={
        'action': 'issue', 'department': dept, 'department_other': other,
        'issued_to': to, 'note': note,
        'med_name': [p[0] for p in pairs],
        'quantity': [str(p[1]) for p in pairs]}, follow_redirects=True)


def damage(name, qty, reason='Broken / spilled', note=''):
    return c.post('/internal-issue', data={
        'action': 'damage', 'med_name': name, 'quantity': str(qty),
        'reason': reason, 'note': note}, follow_redirects=True)


def cells(body, name):
    mm = _re.search(r'<td>' + _re.escape(name) + r'</td>(.*?)</tr>', body, _re.S)
    return [_re.sub(r'<[^>]+>', '', x).strip()
            for x in _re.findall(r'<td>(.*?)</td>', mm.group(1), _re.S)] if mm else []


print('\n--- 1. The page and its departments ---')
reset()
login()
add('Paracetamol, 500 mg')
body = c.get('/internal-issue').data.decode()
check('the page renders', 'Internal Issues' in body)
check('it is in the nav', '/internal-issue' in c.get('/dispense').data.decode())
# The list is checked against the app's own constant, so adding a department
# cannot be done in one place and forgotten in the other.
for d in ['Emergency Department', 'Emergency Trolley', 'PHC', 'OHC', 'Lab',
          'Testing Room', 'Tx Room', 'Doctor Consultation', 'Campaigns',
          'Environment']:
    check(f'department listed: {d}', d in body)
check('every department in the constant reaches the form',
      all(f'<option value="{d}"' in body or f'>{d}<' in body
          for d in A.DEPARTMENTS),
      str([d for d in A.DEPARTMENTS if f'>{d}<' not in body]))
check('the treatment room is its own department, not the testing room',
      'Tx Room' in A.DEPARTMENTS and 'Testing Room' in A.DEPARTMENTS)
check('Other is offered', 'Other' in body)
check('damage reasons are offered', 'Broken / spilled' in body and 'Cold chain failure' in body)
check('the consumption/loss distinction is explained on the page',
      'raises the' in body and 'not</strong> counted as consumption' in body)

print('\n--- 2. Issuing to a department ---')
reset()
login()
add('Paracetamol, 500 mg', 1000, price=2.5)
add('Aspirin, 100 mg', 500, price=5.0)
issue('Emergency Trolley', [('Paracetamol, 500 mg', 100), ('Aspirin, 100 mg', 20)])
check('stock left the first item', meds.find_one({'name': 'Paracetamol, 500 mg'})['balance'] == 900)
check('stock left the second item', meds.find_one({'name': 'Aspirin, 100 mg'})['balance'] == 480)
t = txs.find_one({'type': 'internal_issue', 'med_name': 'Paracetamol, 500 mg'})
check('the department is stored', t['department'] == 'Emergency Trolley')
check('who received it is stored', t['issued_to'] == 'Sr Mokoena')
check('it is valued at cost', t['line_value'] == 250.0, str(t['line_value']))
check('both lines share one issue reference',
      len({x['issue_id'] for x in txs.find({'type': 'internal_issue'})}) == 1)
check('it is audited', db['audit_log'].count_documents({'target_type': 'internal_issue'}) == 1)
r = issue('Other', [('Paracetamol, 500 mg', 10)], other='Outreach clinic')
check('Other captures a free-text department',
      txs.find_one({'department': 'Outreach clinic'}) is not None)

print('\n--- 3. Issuing more than is on hand ---')
reset()
login()
add('Paracetamol, 500 mg', 50)
r = issue('Lab', [('Paracetamol, 500 mg', 200)])
check('only what was there is issued',
      txs.find_one({'type': 'internal_issue'})['quantity'] == 50)
check('the balance never goes negative', meds.find_one({})['balance'] == 0)
check('the shortfall is reported', 'only 50 of 200 available' in r.data.decode())
r = issue('Lab', [('Ghost Item, 1 mg', 5)])
check('an unknown medication is refused', 'is not on file' in r.data.decode())
check('and creates nothing', meds.count_documents({}) == 1)
r = issue('', [('Paracetamol, 500 mg', 5)])
check('a missing department is refused', 'both required' in r.data.decode())

print('\n--- 4. Damage ---')
reset()
login()
add('Paracetamol, 500 mg', 1000, price=2.5)
r = damage('Paracetamol, 500 mg', 40, 'Cold chain failure', note='fridge failed')
check('stock is reduced', meds.find_one({})['balance'] == 960)
d = txs.find_one({'type': 'damage'})
check('the quantity is signed negative', d['quantity'] == -40, str(d['quantity']))
check('valued at cost', d['line_value'] == -100.0, str(d['line_value']))
check('the reason is kept', d['reason'] == 'Cold chain failure')
check('the balance before is kept', d['balance_before'] == 1000)
check('the confirmation gives the new balance and value',
      'Balance is now 960' in r.data.decode() and 'R100.00' in r.data.decode())
check('it is audited', db['audit_log'].count_documents({'target_type': 'damage'}) == 1)
r = damage('Paracetamol, 500 mg', 5000)
check('cannot write off more than is held', meds.find_one({})['balance'] == 960)
check('and says how much there is', 'only 960 on hand' in r.data.decode())
for bad in ('0', '-3', 'abc'):
    c.post('/internal-issue', data={'action': 'damage', 'med_name': 'Paracetamol, 500 mg',
                                    'quantity': bad, 'reason': 'Broken / spilled'},
           follow_redirects=True)
check('invalid quantities are refused', meds.find_one({})['balance'] == 960)
r = c.post('/internal-issue', data={'action': 'damage', 'med_name': 'Paracetamol, 500 mg',
                                    'quantity': '5', 'reason': ''}, follow_redirects=True)
check('a reason is required', meds.find_one({})['balance'] == 960)

print('\n--- 5. The crucial difference: AMC ---')
reset()
login()
add('Issued Item, 10 mg', 1000, price=2.5)
add('Damaged Item, 10 mg', 1000, price=2.5)
issue('PHC', [('Issued Item, 10 mg', 300)])
damage('Damaged Item, 10 mg', 300)
body = c.post('/reports', data={'report_type': 'inventory',
                               'start_date': M_START, 'end_date': M_END}).data.decode()
_inv = inventory_rows(body)
a = _inv.get('Issued Item, 10 mg', {})
b = _inv.get('Damaged Item, 10 mg', {})
print('   issued :', a)
print('   damaged:', b)
check('both items lost 300 units',
      a.get('Current Balance') == '700' and b.get('Current Balance') == '700',
      f'{a} / {b}')
check('both show the 300 under Issued, the one outward column',
      a.get('Issued') == '300' and b.get('Issued') == '300', f'{a} / {b}')
# Issued folds them together on the page; the AMC must not. The details of
# which was consumption and which was loss are in Internal Issues and Losses.
check('the item issued to a department has a non-zero Adjusted AMC',
      a and int(a['Adjusted AMC']) > 0, str(a))
check('the DAMAGED item has an Adjusted AMC of zero',
      b and int(b['Adjusted AMC']) == 0, str(b))
check('this is the whole point: consumption raises AMC, loss does not',
      a and b and int(a['Adjusted AMC']) > int(b['Adjusted AMC']))
check('so only the consumed item is reordered',
      a and b and int(a['Amount to Order']) > 0 and int(b['Amount to Order']) == 0,
      f"{a.get('Amount to Order')} / {b.get('Amount to Order')}")
check('both rows still reconcile', a and b and reconciles(a) and reconciles(b))

print('\n--- 6. Ordering follows consumption, not loss ---')
reset()
login()
add('Issued Item, 10 mg', 0, price=2.5)
add('Damaged Item, 10 mg', 0, price=2.5)
for d in (10, 40, 70):
    txs.insert_one({'type': 'internal_issue', 'med_name': 'Issued Item, 10 mg',
                    'quantity': 100, 'price': 2.5, 'line_value': 250.0,
                    'department': 'PHC', 'issued_to': 'X', 'user': 'u',
                    'timestamp': NOW - timedelta(days=d)})
    txs.insert_one({'type': 'damage', 'med_name': 'Damaged Item, 10 mg',
                    'quantity': -100, 'price': 2.5, 'line_value': -250.0,
                    'reason': 'Broken / spilled', 'user': 'u',
                    'timestamp': NOW - timedelta(days=d)})
body = c.post('/reports', data={'report_type': 'order_request',
                                'cover_months': '3'}).data.decode()
check('the issued item is proposed for reorder', 'Issued Item, 10 mg' in body)
check('the damaged item is NOT reordered on account of breakage',
      'Damaged Item, 10 mg' not in body)
l = cells(body, 'Issued Item, 10 mg')
check('it orders against the issued quantity (AMC 100)', l[0] == '100.0', str(l))

print('\n--- 7. Non-admins ---')
login('user', 'Pharm')
body = c.post('/reports', data={'report_type': 'inventory',
                               'start_date': M_START, 'end_date': M_END}).data.decode()
check('the outward column is headed "Issued", not "Dispensed"',
      '<th>Issued</th>' in body and '<th>Dispensed</th>' not in body)
check('and the page says what it holds',
      'everything that left the' in body and 'sent to departments' in body)
check('a non-admin does NOT see Damaged', '<th>Damaged</th>' not in body)
check('nor Adjustment or Expired',
      '<th>Adjustment</th>' not in body and '<th>Expired</th>' not in body)
check('a non-admin can still record an issue',
      c.get('/internal-issue').status_code == 200)
login()

print('\n--- 8. Internal issues report ---')
reset()
login()
add('Paracetamol, 500 mg', 1000, price=2.5)
add('Aspirin, 100 mg', 1000, price=5.0)
issue('Emergency Trolley', [('Paracetamol, 500 mg', 100)])
issue('Lab', [('Aspirin, 100 mg', 40), ('Paracetamol, 500 mg', 20)])
body = c.post('/reports', data={'report_type': 'internal_issues',
                                'start_date': M_START, 'end_date': M_END}).data.decode()
check('the report renders', 'Internal Issues for' in body)
check('both departments appear', 'Emergency Trolley' in body and 'Lab' in body)
seg = body[body.index('By Department'):]
check('departments are ranked by value', seg.index('Lab') < seg.index('Emergency Trolley'))
check('a by-medication breakdown is given', 'By Medication' in body)
check('totals are shown', '<h3>Summary</h3>' in body)
check('total value is R500.00', 'R500.00' in body)
check('it explains that this is consumption', 'toward each item' in body)
check('search narrows by department',
      'Lab' in c.post('/reports', data={'report_type': 'internal_issues',
                                        'start_date': M_START, 'end_date': M_END,
                                        'search': 'Lab'}).data.decode())
check('missing dates do not crash',
      c.post('/reports', data={'report_type': 'internal_issues'}).status_code == 200)

print('\n--- 9. Losses report covers damage as well as expiry ---')
reset()
login()
add('Paracetamol, 500 mg', 1000, price=2.5)
damage('Paracetamol, 500 mg', 40)
meds.update_one({'name': 'Paracetamol, 500 mg'},
                {'$set': {'expiry_date': (NOW - timedelta(days=10)).strftime('%Y-%m-%d')}})
c.post('/remove-expired', data={'med_name': 'Paracetamol, 500 mg', 'quantity': '60',
                                'report_type': 'expired_list'}, follow_redirects=True)
body = c.post('/reports', data={'report_type': 'expiry_writeoffs',
                                'start_date': M_START, 'end_date': M_END}).data.decode()
check('the report covers both kinds', 'Stock Losses for' in body)
rows = _reg_rows(body, 'Paracetamol, 500 mg')
check('both the damage and the expiry line are listed', len(rows) == 2, str(rows))
check('their quantities are 40 and 60',
      sorted(r[2] for r in rows) == ['40', '60'], str([r[2] for r in rows]))
check('each carries its own remark',
      {r[6] for r in rows} == {'Broken / spilled', '-'}
      or len({r[6] for r in rows}) == 2, str([r[6] for r in rows]))
check('both are valued', 'R100.00' in body and 'R150.00' in body)
check('the total is R250.00', 'R250.00' in body)

print('\n--- 10. Period report ---')
body = c.post('/reports', data={'report_type': 'monthly_report', 'period': 'month',
                                'start_date': M_START, 'end_date': M_END}).data.decode()


def prow(b, label):
    mm = _re.search(r'<td>' + _re.escape(label) + r'</td>\s*<td>(.*?)</td>', b, _re.S)
    return _re.sub(r'<[^>]+>', '', mm.group(1)).strip() if mm else None


check('damage has its own line', prow(body, 'Damaged or lost') == 'R-100.00',
      str(prow(body, 'Damaged or lost')))
check('expiry keeps its own line', prow(body, 'Expired stock written off') == 'R-150.00',
      str(prow(body, 'Expired stock written off')))
reset()
login()
add('Paracetamol, 500 mg', 1000, price=2.5)
issue('PHC', [('Paracetamol, 500 mg', 200)])
body = c.post('/reports', data={'report_type': 'monthly_report', 'period': 'month',
                                'start_date': M_START, 'end_date': M_END}).data.decode()
check('internal issues have their own line',
      prow(body, 'Issued to departments') == '−R500.00'
      or 'R500.00' in str(prow(body, 'Issued to departments')),
      str(prow(body, 'Issued to departments')))
check('it is labelled as consumption', 'counts as consumption' in body)
check('a department breakdown is included', 'Internal Issues by Department' in body)
check('PHC appears in it', 'PHC' in body[body.index('Internal Issues by Department'):])

# =====================================================================
print('\n--- 11. Environment is issuable, and headings differ by role ---')
reset()
login()
add('Disinfectant, 1 L', 100, price=20.0)
issue('Environment', [('Disinfectant, 1 L', 10)])
check('stock can be issued to Environment',
      txs.find_one({'department': 'Environment'}) is not None)
check('the balance moved', meds.find_one({})['balance'] == 90)
body = c.post('/reports', data={'report_type': 'internal_issues',
                                'start_date': M_START, 'end_date': M_END}).data.decode()
check('Environment appears in the internal issues report', 'Environment' in body)

admin_body = c.post('/reports', data={'report_type': 'inventory',
                                      'start_date': M_START, 'end_date': M_END}).data.decode()
login('user', 'Pharm')
user_body = c.post('/reports', data={'report_type': 'inventory',
                                     'start_date': M_START, 'end_date': M_END}).data.decode()
login()
# One table for everyone now: Issued folds in everything that left the shelf,
# department issues included. Which part was a department issue is in the
# Internal Issues report above.
check('admin and non-admin see the same headings',
      _re.findall(r'<th>([^<]+)</th>', admin_body[admin_body.index('Beginning Balance') - 200:])[:8]
      == _re.findall(r'<th>([^<]+)</th>', user_body[user_body.index('Beginning Balance') - 200:])[:8])
check('neither sees the word Dispensed as a column',
      '<th>Dispensed</th>' not in admin_body and '<th>Dispensed</th>' not in user_body)
a = inventory_rows(admin_body).get('Disinfectant, 1 L', {})
u = inventory_rows(user_body).get('Disinfectant, 1 L', {})
check('the department issue is in Issued, for both roles',
      a.get('Issued') == '10' and u.get('Issued') == '10', f'{a} / {u}')
check('and it still counts as consumption',
      int(a.get('Adjusted AMC', 0)) > 0, str(a))
check('both rows reconcile', reconciles(a) and reconciles(u), f'{a} / {u}')

damage('Disinfectant, 1 L', 20)
login('user', 'Pharm')
u = inventory_rows(c.post('/reports', data={'report_type': 'inventory',
                                            'start_date': M_START,
                                            'end_date': M_END}).data.decode()
                   ).get('Disinfectant, 1 L', {})
login()
check('a write-off joins the department issue in Issued', u.get('Issued') == '30', str(u))
check('and the row still reconciles', reconciles(u), str(u))

# =====================================================================
print('\n--- Editing an issue ---')
reset(); login()
add('Edit gloves', balance=100, price=2.0)
add('Edit swabs', balance=50, price=1.0)
issue('Lab', [('Edit gloves', 10)], to='Sr Mokoena', note='weekly')
tx = txs.find_one({'type': 'internal_issue', 'med_name': 'Edit gloves'})
bal = lambda n: meds.find_one({'name': n})['balance']
check('(issued 10 gloves: 100 -> 90)', bal('Edit gloves') == 90)

def ed(tx_, **kw):
    data = {'action': 'edit_issue', 'tx_id': str(tx_['_id']), 'quantity': str(tx_['quantity']),
            'department': tx_['department'], 'issued_to': tx_.get('issued_to') or '',
            'note': tx_.get('note') or ''}
    data.update({k: str(v) for k, v in kw.items()})
    return c.post('/internal-issue', data=data, follow_redirects=True)

page = c.get('/internal-issue').text
check('admins see an Edit button on issue lines', 'class="ii-edit-btn"' in page
      and 'value="edit_issue"' in page and 'value="delete_issue"' in page)
r = ed(tx, quantity=6)
tx2 = txs.find_one({'_id': tx['_id']})
check('10 recorded, 6 went out: 4 go back on the shelf (90 -> 94)', bal('Edit gloves') == 94, str(bal('Edit gloves')))
check('the line now says 6, valued at 6 x price', tx2['quantity'] == 6 and tx2['line_value'] == 12.0)
check('the date stays the date it was issued', tx2['timestamp'] == tx['timestamp'])
check('the change is kept on the line, with who', tx2['edits'][0]['by'] == 'Admin User'
      and 'quantity 10 -> 6' in tx2['edits'][0]['changes'])
check('and in the audit log', db['audit_log'].count_documents(
    {'target_type': 'internal_issue', 'changes': {'$regex': 'quantity 10 -> 6'}}) == 1)
check('the message says what changed', 'quantity 10 -&gt; 6' in r.text or 'quantity 10 -> 6' in r.text)
r = ed(tx2, quantity=9)
check('raised again to 9: 3 more come off (94 -> 91)', bal('Edit gloves') == 91)
r = ed(txs.find_one({'_id': tx['_id']}), quantity=200)
check('raising it past what is on hand is refused', 'only 91 are on hand' in r.text and bal('Edit gloves') == 91)
r = ed(txs.find_one({'_id': tx['_id']}), department='Other', department_other='Dental', issued_to='Dr Lebona', note='')
tx3 = txs.find_one({'_id': tx['_id']})
check('department, receiver and note can be corrected',
      tx3['department'] == 'Dental' and tx3['issued_to'] == 'Dr Lebona' and tx3['note'] == '')
check('without moving stock', bal('Edit gloves') == 91)
r = ed(tx3)
check('saving with nothing changed says so', 'nothing was changed' in r.text)
r = ed(tx3, quantity=0)
check('a quantity of 0 is refused (Delete is for that)', 'use Delete' in r.text)
r = ed(tx3, quantity='x')
check('a quantity that is not a whole number is refused', 'whole number' in r.text)
r = ed(tx3, issued_to='')
check('the receiver is still required', 'both required' in r.text)
page = c.get('/internal-issue').text
check('the edit history shows on the page', 'Edited ' in page and 'quantity 10 -&gt; 6' in page)

rows = inventory_rows(c.post('/reports', data={'report_type': 'inventory',
                      'start_date': M_START, 'end_date': M_END}).data.decode())
row = rows.get('Edit gloves', {})
check('the inventory shows 9 issued and still adds up', row.get('Issued') == '9' and reconciles(row), str(row))

print('\n--- Deleting an issue ---')
issue('Lab', [('Edit swabs', 7)])
sw = txs.find_one({'type': 'internal_issue', 'med_name': 'Edit swabs'})
r = c.post('/internal-issue', data={'action': 'delete_issue', 'tx_id': str(sw['_id'])}, follow_redirects=True)
check('deleting an issue puts the stock back (43 -> 50)', bal('Edit swabs') == 50)
check('and removes the line', txs.find_one({'_id': sw['_id']}) is None and 'Issue deleted' in r.text)
check('the audit log keeps what was deleted', db['audit_log'].count_documents(
    {'action': 'DELETE', 'target_type': 'internal_issue', 'changes': {'$regex': 'Edit swabs x7'}}) == 1)

print('\n--- Issues given against a department request ---')
txs.insert_one({'type': 'internal_issue', 'issue_id': 'REQ-9', 'request_ref': 'REQ-9',
                'med_name': 'Edit swabs', 'quantity': 5, 'price': 1.0, 'line_value': 5.0,
                'department': 'Emergency Department', 'issued_to': 'Sr A', 'note': 'request REQ-9',
                'user': 'Admin User', 'timestamp': datetime.utcnow()})
meds.update_one({'name': 'Edit swabs'}, {'$inc': {'balance': -5}})
rq = txs.find_one({'request_ref': 'REQ-9'})
page = c.get('/internal-issue').text
check('a request line offers no quantity box or Delete', 'given against request REQ-9' in page)
r = ed(rq, quantity=3)
check('its quantity cannot be changed here', 'follow the request' in r.text
      and txs.find_one({'_id': rq['_id']})['quantity'] == 5 and bal('Edit swabs') == 45)
r = ed(rq, issued_to='Sr B', note='request REQ-9 - collected by Sr B')
check('but who received it and the note can be', txs.find_one({'_id': rq['_id']})['issued_to'] == 'Sr B')
r = c.post('/internal-issue', data={'action': 'delete_issue', 'tx_id': str(rq['_id'])}, follow_redirects=True)
check('and it cannot be deleted', txs.find_one({'_id': rq['_id']}) is not None and bal('Edit swabs') == 45)

print('\n--- Only admins ---')
login(role='user', name='Pharm')
page = c.get('/internal-issue').text
check('a non-admin sees no Edit buttons', 'class="ii-edit-btn"' not in page)
r = ed(txs.find_one({'_id': tx['_id']}), quantity=1)
check('and cannot edit by posting', 'Only admins' in r.text and bal('Edit gloves') == 91)
r = c.post('/internal-issue', data={'action': 'delete_issue', 'tx_id': str(tx['_id'])}, follow_redirects=True)
check('or delete', txs.find_one({'_id': tx['_id']}) is not None)
r = c.post('/internal-issue', data={'action': 'edit_issue', 'tx_id': 'nonsense'}, follow_redirects=True)
login()
r = c.post('/internal-issue', data={'action': 'edit_issue', 'tx_id': 'nonsense'}, follow_redirects=True)
check('a bad id is handled', 'not found' in r.text)
loss_id = None
c.post('/internal-issue', data={'action': 'damage', 'med_name': 'Edit gloves', 'quantity': '1', 'reason': 'Other'})
loss = txs.find_one({'type': 'damage', 'med_name': 'Edit gloves'})
r = c.post('/internal-issue', data={'action': 'delete_issue', 'tx_id': str(loss['_id'])}, follow_redirects=True)
check('a loss cannot be deleted through the issue edit', txs.find_one({'_id': loss['_id']}) is not None)

# =====================================================================
print('\n--- Issue dates ---')
reset(); login()
add('Dated gauze', balance=100, price=1.0)
today_l = A.local_now().date()
past = today_l - timedelta(days=12)

def issue_on(day, qty=5, med='Dated gauze'):
    return c.post('/internal-issue', data={
        'action': 'issue', 'department': 'Lab', 'issued_to': 'Sr Mokoena', 'note': '',
        'issue_date': day, 'med_name': [med], 'quantity': [str(qty)]}, follow_redirects=True)

page = c.get('/internal-issue').text
check('the issue form has a date, today by default, no later than today',
      f'name="issue_date" type="date" value="{today_l}" max="{today_l}"' in page)
r = issue_on(past.isoformat())
tx = txs.find_one({'type': 'internal_issue', 'med_name': 'Dated gauze'})
check('a past date is kept: the issue falls on that day on the pharmacy clock',
      A.to_local(tx['timestamp'], '%Y-%m-%d') == past.isoformat(), str(tx['timestamp']))
check('at midday, so the time difference cannot move it a day',
      A.to_local(tx['timestamp'], '%H:%M') == '12:00')
check('the message says it was dated', f"dated {past.strftime('%d %b %Y')}" in r.text)
check('when it was actually entered is kept too', tx.get('recorded_at') is not None
      and A.to_local(tx['recorded_at'], '%Y-%m-%d') == today_l.isoformat())
check('the issue number carries the issue date', past.strftime('%Y%m%d') in tx['issue_id'])
page = c.get('/internal-issue').text
check('the list shows the date it was issued and the day it was entered',
      past.isoformat() in page and 'entered ' in page)
r = issue_on(today_l.isoformat(), qty=2)
t2 = txs.find_one({'type': 'internal_issue', 'med_name': 'Dated gauze', 'quantity': 2})
check('today keeps the real time', abs((datetime.utcnow() - t2['timestamp']).total_seconds()) < 60)
check('an issue entered later but dated earlier is still at the top of the list',
      c.get('/internal-issue').text.index('Dated gauze') >= 0 and
      list(txs.find({'type': 'internal_issue'}).sort('_id', -1))[0]['_id'] == t2['_id'])
issue_on(past.isoformat(), qty=1)
newest = list(txs.find({'type': 'internal_issue'}).sort('_id', -1))[0]
page = c.get('/internal-issue').text
first_row = page[page.index('id="recent"'):]
first_row = first_row[first_row.index('<tbody>'):][:900]
check('(the back-dated one just entered shows first)', past.isoformat() in first_row, first_row[:300])
r = issue_on((today_l + timedelta(days=1)).isoformat())
check('a future date is refused, and nothing is issued', 'cannot be in the future' in r.text
      and txs.count_documents({'type': 'internal_issue'}) == 3)
r = issue_on((today_l - timedelta(days=400)).isoformat())
check('a date over a year back is refused as a likely slip', 'check the year' in r.text)
r = issue_on('14/11/2026')
check('a date that cannot be read is refused', 'could not be read' in r.text)
rows = inventory_rows(c.post('/reports', data={'report_type': 'inventory',
    'start_date': past.isoformat(), 'end_date': past.isoformat()}).data.decode())
check('the inventory for that past day counts the back-dated issues',
      rows.get('Dated gauze', {}).get('Issued') == '6', str(rows.get('Dated gauze')))

print('\n--- Editing the date ---')
other = today_l - timedelta(days=3)
data = {'action': 'edit_issue', 'tx_id': str(tx['_id']), 'quantity': '5', 'department': 'Lab',
        'issued_to': 'Sr Mokoena', 'note': '', 'issue_date': other.isoformat()}
r = c.post('/internal-issue', data=data, follow_redirects=True)
tx = txs.find_one({'_id': tx['_id']})
check('the date can be edited', A.to_local(tx['timestamp'], '%Y-%m-%d') == other.isoformat())
check('the change is recorded', f'date {past.isoformat()} -> {other.isoformat()}' in tx['edits'][-1]['changes'])
check('and stock does not move for a date change', meds.find_one({'name': 'Dated gauze'})['balance'] == 92)
page = c.get('/internal-issue').text
check('the edit form offers the date', 'name="issue_date" required value="' + other.isoformat() in page)
r = c.post('/internal-issue', data=dict(data, issue_date=(today_l + timedelta(days=2)).isoformat()),
           follow_redirects=True)
check('it cannot be moved into the future', 'cannot be in the future' in r.text and
      A.to_local(txs.find_one({'_id': tx['_id']})['timestamp'], '%Y-%m-%d') == other.isoformat())
r = c.post('/internal-issue', data=dict(data, issue_date=today_l.isoformat(), quantity='4'),
           follow_redirects=True)
tx = txs.find_one({'_id': tx['_id']})
check('date and quantity together', A.to_local(tx['timestamp'], '%Y-%m-%d') == today_l.isoformat()
      and tx['quantity'] == 4 and meds.find_one({'name': 'Dated gauze'})['balance'] == 93)
txs.insert_one({'type': 'internal_issue', 'issue_id': 'REQ-7', 'request_ref': 'REQ-7',
                'med_name': 'Dated gauze', 'quantity': 1, 'price': 1.0, 'line_value': 1.0,
                'department': 'Lab', 'issued_to': 'Sr A', 'note': '', 'user': 'Admin User',
                'timestamp': datetime.utcnow()})
rq = txs.find_one({'request_ref': 'REQ-7'})
r = c.post('/internal-issue', data={'action': 'edit_issue', 'tx_id': str(rq['_id']), 'quantity': '1',
           'department': 'Lab', 'issued_to': 'Sr A', 'note': '',
           'issue_date': (today_l - timedelta(days=5)).isoformat()}, follow_redirects=True)
check('a request line keeps the day it was served', 'day the request was served' in r.text)

print(f'\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========')
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    raise SystemExit(1)
