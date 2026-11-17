"""Less work at the stock take.

Asked for: "are there any other opportunities to reduce work?", then "do all,
but for the cycle counting we can just use the classification we already
have". So:
  - a stock take can be opened for some of the shelf groups only, and the
    start page says when each group was last counted;
  - items carry a shelf location and a barcode, and the sheet is in shelf
    order;
  - the CSV for the phone carries location, pack size and barcode, and what
    the phone sends back saves them;
  - the phone can be linked once (QR code) and then fetch the sheet and send
    its counts directly, which wait on the page like an upload.
"""
from _root import ROOT  # the app folder, one up from tests/
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import io, re, csv, html as _html
import mongomock, pymongo
from datetime import datetime, timezone, timedelta

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
A.get_mongo_client = lambda _client=_f: _client
A.init_db_collections()
from test_support import arm_csrf
db = _f['pharmacy_db']
meds = db['medications']
c = A.app.test_client()
arm_csrf(c)
PASS, FAIL = [], []


def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label
          + (('  ->  ' + str(detail)) if detail and not cond else ''))


def login(role='admin', name='Letlotlo Hlaoli', login_='admin'):
    with c.session_transaction() as s:
        s['user'] = {'name': name, 'login': login_, 'role': role}
        s['last_active'] = datetime.now(timezone.utc).isoformat()


def banner(page):
    m = re.search(r'<p class="message[^"]*"[^>]*>(.*?)</p>', page, re.S)
    return _html.unescape(m.group(1)).strip() if m else ''


def med(name, **kw):
    d = {'name': name, 'balance': 10, 'schedule': 'not controlled', 'pack_size': 1,
         'pack_price': 1.0, 'price': 1.0, 'batch': 'B1', 'expiry_date': '2029-01-31',
         'category': 'Pharmaceutical'}
    d.update(kw)
    meds.insert_one(d)


def reset():
    for col in ['medications', 'transactions', 'audit_log', 'upload_drafts', 'stock_takes',
                'stock_take_counts', 'phone_links', 'users']:
        db[col].delete_many({})
    med('Amoxicillin caps, 500 mg', location='B10', pack_size=28)
    med('Paracetamol tabs, 500 mg', location='B2')
    med('Cetirizine tabs, 10 mg')                                   # no location: last
    med('Crepe bandage, 10 cm', category='Medical Supply', location='S1')
    med('Morphine tabs, 10 mg', schedule='controlled', location='CD1', pack_size=10)
    med('Donor ARV tabs', category='Donor Funded', location='D1')


def open_st(groups=None, note='t'):
    data = {'action': 'open', 'note': note, 'groups_shown': '1'}
    if groups is not None:
        data['groups'] = groups
    else:
        data['groups'] = [g for g, _ in A.COUNT_GROUPS]
    r = c.post('/stock-take', data=data, follow_redirects=True)
    st = db['stock_takes'].find_one({'status': 'open'})
    return (str(st['_id']) if st else None), r


def close(sid):
    c.post('/stock-take', data={'action': 'close', 'stock_take_id': sid})


def csv_rows(data):
    return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))


# =====================================================================
print('\n== Cycle counting by group ==')
reset(); login()
page = c.get('/stock-take').text
check('the start form offers each group, all ticked', all(
    f'name="groups" value="{g}" checked' in page for g, _ in A.COUNT_GROUPS))
check('and says none has been counted yet', page.count('never counted') == len(A.COUNT_GROUPS))
sid, r = open_st(['controlled'])
st = db['stock_takes'].find_one()
check('a stock take can cover one group', st['groups'] == ['controlled']
      and 'opened for controlled drugs' in banner(r.text), banner(r.text))
page = c.get(f'/stock-take?stock_take_id={sid}').text
names_on_sheet = re.findall(r'name="med__\d+" value="([^"]+)"', page)
check('its sheet holds that group only', names_on_sheet == ['Morphine tabs, 10 mg'], names_on_sheet)
check('the page says what is being counted', 'Counting:</strong> Controlled Drugs' in page)
rows = csv_rows(c.get(f'/stock-take?stock_take_id={sid}&download=sheet&format=csv').data)
check('and so does its CSV', [x[1] for x in rows[1:]] == ['Morphine tabs, 10 mg'])
up = io.StringIO(); w = csv.writer(up)
w.writerow(['Medication', 'Physical count'])
w.writerow(['Morphine tabs, 10 mg', 9]); w.writerow(['Paracetamol tabs, 500 mg', 4])
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                                'sheet': (io.BytesIO(up.getvalue().encode()), 'c.csv')},
           content_type='multipart/form-data', follow_redirects=True)
d = db['upload_drafts'].find_one()
check('an uploaded item outside its groups is named and left out',
      [x['name'] for x in d['rows']] == ['Morphine tabs, 10 mg']
      and 'Not in the groups this stock take covers' in banner(r.text), banner(r.text))
c.post('/stock-take', data={'action': 'discard_counts', 'stock_take_id': sid})
page = c.get('/stock-take').text
check('the list shows the groups each stock take covered', '<td>Controlled Drugs</td>' in page)
check('while it is open the start page says that group is being counted',
      'being counted in ' + st['reference'] in page)
close(sid)
db['stock_takes'].update_one({'_id': st['_id']},
                             {'$set': {'closed_at': datetime.utcnow() - timedelta(days=12)}})
page = c.get('/stock-take').text
check('once closed: "last counted ... 12 days ago" for that group, never for the rest',
      'last counted' in page and '12 days ago' in page and page.count('never counted') == 3)
sid2, r = open_st(['supply', 'donor'])
check('two groups at once', db['stock_takes'].find_one({'status': 'open'})['groups'] == ['supply', 'donor'])
close(sid2)
sid3, r = open_st()
check('everything ticked stores no groups and covers all',
      'groups' not in db['stock_takes'].find_one({'status': 'open'})
      and A._scope_label(db['stock_takes'].find_one({'status': 'open'})) == 'Everything')
close(sid3)
_, r = open_st([])
check('nothing ticked is refused', 'at least one group' in banner(r.text)
      and db['stock_takes'].count_documents({'status': 'open'}) == 0, banner(r.text))
r = c.post('/stock-take', data={'action': 'open', 'note': 'old form'}, follow_redirects=True)
check('a form without the group boxes still opens everything',
      db['stock_takes'].count_documents({'status': 'open'}) == 1
      and 'groups' not in db['stock_takes'].find_one({'status': 'open'}))
close(str(db['stock_takes'].find_one({'status': 'open'})['_id']))

# =====================================================================
print('\n== Shelf order, location, pack size, barcode ==')
reset(); login()
sid, _ = open_st()
page = c.get(f'/stock-take?stock_take_id={sid}').text
order = re.findall(r'name="med__\d+" value="([^"]+)"', page)
check('the sheet is in shelf order: B2 before B10, no location last',
      order.index('Paracetamol tabs, 500 mg') < order.index('Amoxicillin caps, 500 mg')
      and order[-1] == 'Cetirizine tabs, 10 mg', order)
check('each row shows its location', '<span class="st-loc">B10</span>' in page)
rows = csv_rows(c.get(f'/stock-take?stock_take_id={sid}&download=sheet&format=csv').data)
check('the CSV carries location, pack size and barcode',
      rows[0] == A.COUNT_SHEET_CSV_HEADINGS and rows[0][8:] == ['Location', 'Pack size', 'Barcode', 'Counted by'])
amox = next(x for x in rows if x[1] == 'Amoxicillin caps, 500 mg')
check('with the values', amox[8] == 'B10' and amox[9] == '28' and amox[10] == '', amox)
check('the CSV is in shelf order too', [x[1] for x in rows[1:]] == order)
import openpyxl
wb = openpyxl.load_workbook(io.BytesIO(c.get(f'/stock-take?stock_take_id={sid}&download=sheet').data))
heads = [x.value for x in wb.active[1]]
check('the Excel sheet carries the location at the end', heads[-1] == 'Location' and heads[:4] ==
      ['Code', 'Medication', 'On system', 'Physical count'], heads)

# What the phone sends back: a location typed in, a barcode scanned.
para = next(x for x in rows if x[1] == 'Paracetamol tabs, 500 mg')
cet = next(x for x in rows if x[1] == 'Cetirizine tabs, 10 mg')
out = io.StringIO(); w = csv.writer(out)
w.writerow(['Code', 'Medication', 'On system', 'Physical count', 'Expiry date', 'Batch', 'Note',
            'Location', 'Barcode'])
w.writerow([para[0], para[1], para[2], '12', '', '', '', 'B2', '6001234567890'])
w.writerow([cet[0], cet[1], cet[2], '', '', '', '', 'c4', ''])          # location only
w.writerow([amox[0], amox[1], amox[2], '', '', '', '', 'B10', '6001234567890'])  # same barcode
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                                'sheet': (io.BytesIO(out.getvalue().encode()), 'p.csv')},
           content_type='multipart/form-data', follow_redirects=True)
d = {x['name']: x for x in db['upload_drafts'].find_one()['rows']}
check('a location given with no count is kept as an entry', d.get('Cetirizine tabs, 10 mg', {}).get('location') == 'C4', d)
check('a barcode already given to another row of the same sheet is caught at posting',
      d['Paracetamol tabs, 500 mg']['barcode'] == '6001234567890')
check('an unchanged location is not an entry', 'Amoxicillin caps, 500 mg' not in d
      or not d['Amoxicillin caps, 500 mg'].get('location'))
r = c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid}, follow_redirects=True)
check('posting saves the count, the location and the barcode',
      meds.find_one({'name': 'Paracetamol tabs, 500 mg'})['balance'] == 12
      and meds.find_one({'name': 'Paracetamol tabs, 500 mg'})['barcode'] == '6001234567890'
      and meds.find_one({'name': 'Cetirizine tabs, 10 mg'})['location'] == 'C4', banner(r.text))
check('one barcode stays on one item only',
      meds.count_documents({'barcode': '6001234567890'}) == 1)
check('the message says shelf details were saved', 'Shelf location or barcode saved' in banner(r.text),
      banner(r.text))
check('the location-only item is still to count',
      db['stock_take_counts'].count_documents({'med_name': 'Cetirizine tabs, 10 mg'}) == 0)
out = io.StringIO(); w = csv.writer(out)
w.writerow(['Medication', 'Physical count', 'Barcode'])
w.writerow(['Amoxicillin caps, 500 mg', '30', '6001234567890'])
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                                'sheet': (io.BytesIO(out.getvalue().encode()), 'q.csv')},
           content_type='multipart/form-data', follow_redirects=True)
check('a barcode already on another item is refused on upload, the count still read',
      'already on Paracetamol' in banner(r.text)
      and db['upload_drafts'].find_one()['rows'][0]['counted'] == 30, banner(r.text))
c.post('/stock-take', data={'action': 'discard_counts', 'stock_take_id': sid})
check('barcode tidying: a spreadsheet float and spaces',
      A._clean_barcode(6001234567890.0) == '6001234567890'
      and A._clean_barcode('6001234567890.0') == '6001234567890'
      and A._clean_barcode(' 600 123 ') == '600123' and A._clean_barcode('-') == '')
check('location tidying', A._clean_location(' b3 ') == 'B3' and A._clean_location('n/a') == '')

# The edit page
m = meds.find_one({'name': 'Morphine tabs, 10 mg'})
form = {'med_name': m['name'], 'new_name': m['name'], 'batch': 'B1', 'category': 'Pharmaceutical',
        'pack_size': '10', 'pack_price': '20', 'expiry_date': '2029-01-31', 'schedule': 'controlled',
        'location': ' cd2 ', 'barcode': '6009876543210'}
r = c.post('/edit-medication', data=form, follow_redirects=True)
m = meds.find_one({'name': 'Morphine tabs, 10 mg'})
check('the edit page saves location and barcode', m.get('location') == 'CD2'
      and m.get('barcode') == '6009876543210', (m.get('location'), m.get('barcode')))
page = c.get('/edit-medication?med_name=' + m['name']).text
check('and shows them', 'value="CD2"' in page and 'value="6009876543210"' in page)
form['barcode'] = '6001234567890'
r = c.post('/edit-medication', data=form, follow_redirects=True)
check('a barcode already on another item is refused there too',
      meds.find_one({'name': 'Morphine tabs, 10 mg'})['barcode'] == '6009876543210'
      and 'already on Paracetamol' in r.text)
close(sid)

# =====================================================================
print('\n== Linking the phone ==')
reset(); login()
page = c.get('/phone-link').text
check('the phone link page loads', 'Link a phone' in page and 'No phone is linked yet' in page)
r = c.post('/phone-link', data={'action': 'create', 'label': 'Pharmacy Samsung'})
page = r.text
m_ = re.search(r'NMCST1\|([^|<]+)\|([A-Za-z0-9_\-]+)', page)
check('linking shows a QR code and the typed code', '<svg' in page and m_ is not None)
key = m_.group(2) if m_ else ''
link = db['phone_links'].find_one()
check('only a hash of the key is kept', link and key and link['key_hash'] == A._phone_key_hash(key)
      and key not in str(link))
with A.app.test_request_context(base_url='http://health-records-two.vercel.app/'):
    check('https for a real host', A._site_address() == 'https://health-records-two.vercel.app')
with A.app.test_request_context(base_url='http://localhost:5000/'):
    check('plain http on this computer', A._site_address() == 'http://localhost:5000')
with c.session_transaction() as s_:
    check('the key is not left in the session', key not in str(dict(s_)))
page = c.get('/phone-link').text
check('the list shows it, and the code is not shown again',
      'Pharmacy Samsung' in page and key not in page)

api = A.app.test_client()          # the phone: no login, no session
H = {'Authorization': f'Bearer {key}'}
r = api.get('/api/phone/hello')
check('without the key the phone is refused', r.status_code == 401 and r.get_json()['ok'] is False)
r = api.get('/api/phone/hello', headers={'Authorization': 'Bearer ' + 'x' * 43})
check('with a wrong key too', r.status_code == 401)
r = api.get('/api/phone/hello', headers=H)
check('with it: linked, and no stock take open', r.status_code == 200
      and r.get_json()['ok'] and r.get_json()['stock_take'] is None, r.get_json())
r = api.get('/api/phone/sheet', headers=H)
check('no sheet while nothing is open', r.status_code == 404 and 'No stock take is open' in r.get_json()['message'])
sid, _ = open_st(['pharm', 'controlled'])
r = api.get('/api/phone/hello', headers=H)
j = r.get_json()['stock_take']
check('hello names the open stock take and what is left in each group',
      j and j['groups'] == 'Pharmaceuticals, Controlled Drugs' and j['to_count'] == 4
      and [g['group'] for g in j['by_group']] == ['pharm', 'controlled'], j)
r = api.get('/api/phone/sheet?group=controlled', headers=H)
rows = csv_rows(r.data)
check('the phone gets a group sheet as CSV', r.status_code == 200
      and [x[1] for x in rows[1:]] == ['Morphine tabs, 10 mg'] and rows[0] == A.COUNT_SHEET_CSV_HEADINGS)
rows = csv_rows(api.get('/api/phone/sheet', headers=H).data)
check('or the whole sheet', len(rows) == 5)
check('the link records when it was last used', db['phone_links'].find_one()['last_used'] is not None)

out = io.StringIO(); w = csv.writer(out)
w.writerow(rows[0][:7] + ['Location', 'Barcode'])
mor = next(x for x in rows if x[1] == 'Morphine tabs, 10 mg')
w.writerow([mor[0], mor[1], mor[2], '7', '', '', 'cupboard', 'CD1', '6005550001112'])
r = api.post('/api/phone/counts', headers={**H, 'Content-Type': 'text/csv'},
             data=('﻿' + out.getvalue()).encode('utf-8'))
j = r.get_json()
check('the phone sends its counts', r.status_code == 200 and j['ok'] and j['sent'] == 1, j)
e_ = db['phone_entries'].find_one()
check('they wait as that phone\'s entry for the item', e_ and e_['label'] == 'Pharmacy Samsung'
      and e_['key'] == 'Morphine tabs, 10 mg' and e_['data']['counted'] == 7, e_)
check('nothing is posted by sending', meds.find_one({'name': 'Morphine tabs, 10 mg'})['balance'] == 10)
page = c.get(f'/stock-take?stock_take_id={sid}').text
check('the page shows them, ready for Post Uploaded Counts', 'value="post_uploaded"' in page)
c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid})
m = meds.find_one({'name': 'Morphine tabs, 10 mg'})
check('posted: the count and the scanned barcode', m['balance'] == 7 and m['barcode'] == '6005550001112')
r = api.post('/api/phone/counts', headers=H, data=b'just some words')
check('something unreadable is refused with a reason', r.status_code == 400
      and 'could not be read' in r.get_json()['message'], r.get_json())
check('the phone cannot reach the pages', api.get('/stock-take', headers=H).status_code in (301, 302))
r = api.post('/stock-take', headers=H, data={'action': 'close', 'stock_take_id': sid})
check('or post to them', db['stock_takes'].find_one({'_id': db['stock_takes'].find_one()['_id']})['status'] == 'open')
close(sid)
r = api.post('/api/phone/counts', headers=H, data=out.getvalue().encode())
check('counts sent with no stock take open are refused, not lost silently', r.status_code == 409)

db['users'].insert_one({'username': 'admin', 'role': 'user'})
check('a link stops working if its admin is no longer an admin',
      api.get('/api/phone/hello', headers=H).status_code == 401)
db['users'].update_one({'username': 'admin'}, {'$set': {'role': 'admin'}})
check('and works again when they are', api.get('/api/phone/hello', headers=H).status_code == 200)
c.post('/phone-link', data={'action': 'unlink', 'link_id': str(db['phone_links'].find_one()['_id'])})
check('unlinked, the phone is refused', api.get('/api/phone/hello', headers=H).status_code == 401)
check('and the page no longer lists it', 'No phone is linked yet' in c.get('/phone-link').text)
login('user', 'N. Nurse', 'nurse')
r = c.post('/phone-link', data={'action': 'create', 'label': 'x'})
check('only an admin can link a phone', r.status_code == 302 and db['phone_links'].count_documents({}) == 1)
login()


# =====================================================================
print('\n== Receiving with the phone ==')
reset(); login()
db['deliveries'].delete_many({})
r = c.post('/phone-link', data={'action': 'create', 'label': 'Receiving phone'})
key = re.search(r'NMCST1\|[^|<]+\|([A-Za-z0-9_\-]+)', r.text).group(1)
H = {'Authorization': f'Bearer {key}'}
api = A.app.test_client()
did = str(db['deliveries'].insert_one({
    'reference': 'DEL-PHONE', 'supplier': 'NDSO', 'order_number': 'PO-9', 'invoices': [{'number': 'INV-7', 'total': 100}],
    'invoice_number': 'INV-7', 'invoice_total': 100.0, 'order_total': 100.0, 'stock_receiver': 'L. Hlaoli',
    'status': 'open', 'opened_at': datetime.utcnow(), 'received_by': 'L. Hlaoli'}).inserted_id)
meds.update_one({'name': 'Paracetamol tabs, 500 mg'}, {'$set': {'barcode': '6001111111111', 'code': 'MED-0002'}})
j = api.get('/api/phone/deliveries', headers=H).get_json()
check('the phone lists open deliveries', j['ok'] and [x['reference'] for x in j['deliveries']] == ['DEL-PHONE']
      and j['deliveries'][0]['lines'] == 0 and j['deliveries'][0]['invoices'] == 'INV-7', j)
r = api.get(f'/api/phone/delivery/{did}/lines', headers=H)
check('with no invoice uploaded, it says to upload it first', r.status_code == 409
      and 'Upload the invoice' in r.get_json()['message'])
inv = ('Medication,Quantity,Pack size,Price per pack,Batch,Expiry date\n'
       '"Amoxicillin caps, 500 mg",56,28,40.00,,\n'
       '"Paracetamol tabs, 500 mg",100,1,0.50,P1,2028-06-30\n'
       '"Cetirizine tabs, 10 mg",30,1,0.20,,\n')
c.post('/receive', data={'action': 'upload_lines', 'delivery_id': did, 'qty_mode': 'units',
                         'sheet': (io.BytesIO(inv.encode()), 'inv.csv')}, content_type='multipart/form-data')
j = api.get('/api/phone/deliveries', headers=H).get_json()
check('after the upload it shows 3 lines waiting', j['deliveries'][0]['lines'] == 3, j)
r = api.get(f'/api/phone/delivery/{did}/lines', headers=H)
rows = csv_rows(r.data)
check('the phone gets the lines as CSV', r.status_code == 200 and rows[0] == A.PHONE_RECEIVE_HEADINGS
      and len(rows) == 4, rows[:2])
para = rows[2]
check('each with line number, quantity, pack size, and the barcode on file',
      para[0] == '2' and para[2] == 'Paracetamol tabs, 500 mg' and para[3] == '100'
      and para[4] == '1' and para[7] == '30/06/2028' and para[8] == '6001111111111', para)
out = io.StringIO(); w = csv.writer(out)
w.writerow(['Line', 'Medication', 'Quantity', 'Batch', 'Expiry date', 'Barcode', 'Note', 'Checked'])
w.writerow(['1', 'Amoxicillin caps, 500 mg', '56', 'AX9', '04/2028', '6002222222222', '', 'yes'])   # as invoiced
w.writerow(['2', 'Paracetamol tabs, 500 mg', '90', '', '', '', 'one box crushed', 'yes'])       # short
w.writerow(['3', 'Cetirizine tabs, 10 mg', '', '', '', '', '', ''])                              # not checked
r = api.post(f'/api/phone/delivery/{did}/lines', headers=H, data=('﻿' + out.getvalue()).encode())
j = r.get_json()
check('the phone sends its checks', r.status_code == 200 and j['checked'] == 2 and j['short'] == 1, j)
check('the message says what differs and that nothing is received yet',
      '1 short' in j['message'] and 'Nothing is received yet' in j['message'], j['message'])
def waiting():
    return (A._receive_waiting(db, 'admin', db['deliveries'].find_one()) or {}).get('rows') or []
L = waiting()
check('a checked line takes the batch and expiry read off the box',
      L[0]['batch'] == 'AX9' and L[0]['expiry'] == '2028-04-30' and L[0]['phone_checked'], L[0])
check('a line counted as invoiced has nothing left to flag', L[0]['problem'] == '', L[0]['problem'])
check('a short line says so, with the note',
      L[1]['units'] == 90 or str(L[1]['units']) == '90')
check('in words: invoice 100, counted 90, short by 10',
      'invoice says 100, counted 90 (short by 10)' in L[1]['problem'] and 'one box crushed' in L[1]['problem'],
      L[1]['problem'])
check('an unchecked line is left as uploaded', not L[2].get('phone_checked') and L[2]['units'] in (30, '30'))
check('a scanned barcode is saved to the item',
      meds.find_one({'name': 'Amoxicillin caps, 500 mg'}).get('barcode') == '6002222222222')
check('nothing is received by sending', db['transactions'].count_documents({'type': 'receive'}) == 0)
page = c.get(f'/receive?delivery_id={did}').text
check('the delivery page shows the phone checks and the shortage',
      '2 of 3</strong> line(s) checked on the phone' in page and page.count('checked on Receiving phone</span>') == 2
      and 'short by 10' in page)
rows = csv_rows(api.get(f'/api/phone/delivery/{did}/lines', headers=H).data)
check('getting the lines again still shows the invoiced figure', rows[2][3] == '100', rows[2])
check('and brings back what was checked: Checked, Received and the note',
      rows[2][10] == 'yes' and rows[2][11] == '90' and rows[2][9] == 'one box crushed'
      and rows[3][10] == '' and rows[3][11] == '', rows[2:])
check('a line not checked on the phone is unticked on the page',
      L[2].get('include') is False
      and re.search(r'name="inc__2" value="1"\s*>', page) is not None
      and re.search(r'name="inc__0" value="1"\s*checked', page) is not None)
w2 = io.StringIO(); ww = csv.writer(w2)
ww.writerow(['Line', 'Medication', 'Quantity', 'Checked'])
ww.writerow(['2', 'Cetirizine tabs, 10 mg', '5', 'yes'])
j = api.post(f'/api/phone/delivery/{did}/lines', headers=H, data=w2.getvalue().encode()).get_json()
check('a line number pointing at a different item is refused, not applied',
      j['checked'] == 0 and 'not Cetirizine' in j['message'], j)
# Receive as checked: the page form carries the phone's figures
form = {'action': 'lines_bulk', 'delivery_id': did, 'n': '3'}
for i, x in enumerate(L):
    form.update({f'inc__{i}': '1' if i < 2 else '', f'med__{i}': x['name'], f'raw__{i}': x['raw'],
                 f'units__{i}': str(x['units']), f'pack_size__{i}': str(x['pack_size']),
                 f'pack_price__{i}': str(x['pack_price']), f'batch__{i}': x['batch'], f'expiry__{i}': x['expiry']})
r = c.post('/receive', data=form, follow_redirects=True)
check('Add These Lines receives what was counted on the phone',
      sorted(t['quantity'] for t in db['transactions'].find({'type': 'receive'})) == [56, 90], banner(r.text))
left = waiting()
check('the unticked line stays waiting, with its own line number',
      [(x['name'], x['line']) for x in left] == [('Cetirizine tabs, 10 mg', 3)]
      and 'still waiting below' in banner(r.text), (left, banner(r.text)))
rows = csv_rows(api.get(f'/api/phone/delivery/{did}/lines', headers=H).data)
check('the phone then gets only that line, still numbered 3', [x[0] for x in rows[1:]] == ['3'], rows)
w3 = io.StringIO(); ww = csv.writer(w3)
ww.writerow(['Line', 'Medication', 'Units', 'Batch', 'Expiry date', 'Checked'])
ww.writerow(['3', 'Cetirizine tabs, 10 mg', '30', 'C5', '12/2028', 'yes'])
j = api.post(f'/api/phone/delivery/{did}/lines', headers=H, data=w3.getvalue().encode()).get_json()
left = waiting()
check('and checks it later', j['checked'] == 1 and left[0]['batch'] == 'C5'
      and left[0]['expiry'] == '2028-12-31' and left[0]['problem'] == '', (j, left))
form = {'action': 'lines_bulk', 'delivery_id': did, 'n': '1', 'inc__0': '1', 'med__0': left[0]['name'],
        'raw__0': left[0]['raw'], 'units__0': '30', 'pack_size__0': '30', 'pack_price__0': '6',
        'batch__0': 'C5', 'expiry__0': '2028-12-31'}
r = c.post('/receive', data=form, follow_redirects=True)
check('received too, and the invoice is done', db['transactions'].count_documents({'type': 'receive'}) == 3
      and db['upload_drafts'].count_documents({'kind': 'receive'}) == 0, banner(r.text))
db['deliveries'].update_one({'_id': db['deliveries'].find_one()['_id']}, {'$set': {'status': 'closed'}})
r = api.get(f'/api/phone/delivery/{did}/lines', headers=H)
check('a closed delivery is refused', r.status_code == 409 and 'closed' in r.get_json()['message'])
check('without the key, refused', api.get('/api/phone/deliveries').status_code == 401)


# =====================================================================
print('\n== Several phones at once: stock take ==')
reset(); login()
db['phone_entries'].delete_many({})
def link_phone(label):
    r = c.post('/phone-link', data={'action': 'create', 'label': label})
    k = re.search(r'NMCST1\|[^|<]+\|([A-Za-z0-9_\-]+)', r.text).group(1)
    return {'Authorization': f'Bearer {k}'}
HA, HB = link_phone('Samsung'), link_phone('Tecno')
sid, _ = open_st()
def send(h, rows_):
    o = io.StringIO(); w = csv.writer(o)
    w.writerow(['Medication', 'Physical count', 'Note'])
    for r_ in rows_:
        w.writerow(r_)
    return api.post('/api/phone/counts', headers=h, data=o.getvalue().encode()).get_json()
ja = send(HA, [['Amoxicillin caps, 500 mg', 40, ''], ['Paracetamol tabs, 500 mg', 10, '']])
rows = csv_rows(api.get('/api/phone/sheet', headers=HB).data)
amx = next(x for x in rows if x[1] == 'Amoxicillin caps, 500 mg')
check("phone B's sheet says phone A has counted an item", amx[11] == 'Samsung' and amx[3] == '', amx)
rows_a = csv_rows(api.get('/api/phone/sheet', headers=HA).data)
amx_a = next(x for x in rows_a if x[1] == 'Amoxicillin caps, 500 mg')
check("phone A's own sheet brings back its own figure", amx_a[3] == '40' and amx_a[11] == '', amx_a)
j = api.get('/api/phone/hello', headers=HB).get_json()
check('items already sent are not "still to count"', j['stock_take']['to_count'] == 4, j['stock_take'])
jb = send(HB, [['Amoxicillin caps, 500 mg', 38, 'top shelf'], ['Cetirizine tabs, 10 mg', 5, '']])
check('phone B is told it disagrees with another phone', jb['disputed'] == 1
      and 'differ from another phone' in jb['message'], jb)
check("each phone's figures are kept separately", db['phone_entries'].count_documents({}) == 4)
w_ = {r_['name']: r_ for r_ in A._stock_take_waiting(db, 'admin', db['stock_takes'].find_one({'status': 'open'}))['rows']}
check('the disagreement is flagged with both figures, and no count is filled in',
      w_['Amoxicillin caps, 500 mg'].get('conflict') == 'Samsung 40, Tecno 38'
      and w_['Amoxicillin caps, 500 mg']['counted'] == '', w_['Amoxicillin caps, 500 mg'])
check('items only one phone counted carry its name',
      w_['Paracetamol tabs, 500 mg']['phones'] == 'Samsung' and w_['Cetirizine tabs, 10 mg']['phones'] == 'Tecno')
page = c.get(f'/stock-take?stock_take_id={sid}').text
check('the page shows the disagreement in red, and who sent the others',
      'Phones disagree: Samsung 40, Tecno 38. Count it again.' in page and 'from Samsung' in page
      and 'from Tecno' in page and '1 item(s) were counted' in page)
r = c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid}, follow_redirects=True)
check('Post Uploaded Counts posts the agreed items, not the disputed one',
      meds.find_one({'name': 'Paracetamol tabs, 500 mg'})['balance'] == 10
      and meds.find_one({'name': 'Cetirizine tabs, 10 mg'})['balance'] == 5
      and meds.find_one({'name': 'Amoxicillin caps, 500 mg'})['balance'] == 10
      and 'phones disagree on were not posted' in banner(r.text), banner(r.text))
line = db['stock_take_counts'].find_one({'med_name': 'Paracetamol tabs, 500 mg'})
check('each count records the phone it came from', line.get('source') == 'phone: Samsung', line)
check('the disputed item stays waiting; the posted ones are cleared',
      sorted(e['key'] for e in db['phone_entries'].find()) == ['Amoxicillin caps, 500 mg'] * 2)
send(HB, [['Amoxicillin caps, 500 mg', 40, 'recounted']])
w_ = {r_['name']: r_ for r_ in A._stock_take_waiting(db, 'admin', db['stock_takes'].find_one({'status': 'open'}))['rows']}
check('when phone B recounts and agrees, the item is ready, both named',
      not w_['Amoxicillin caps, 500 mg'].get('conflict') and w_['Amoxicillin caps, 500 mg']['counted'] == 40
      and w_['Amoxicillin caps, 500 mg']['agreed_by'] == 'Samsung, Tecno', w_['Amoxicillin caps, 500 mg'])
page = c.get(f'/stock-take?stock_take_id={sid}').text
check('the page says they agree', 'Samsung, Tecno agree' in page)
c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid})
line = db['stock_take_counts'].find_one({'med_name': 'Amoxicillin caps, 500 mg'})
check('posted, recorded as counted by both phones',
      meds.find_one({'name': 'Amoxicillin caps, 500 mg'})['balance'] == 40
      and line.get('source') == 'phone: Samsung, Tecno' and db['phone_entries'].count_documents({}) == 0, line)
page = c.get(f'/stock-take?stock_take_id={sid}').text
check('the counted list shows where each count came from', 'phone: Samsung, Tecno' in page)

# Phones sending at the same moment
import threading
reset(); login(); db['phone_entries'].delete_many({})
for k in range(60):
    med(f'Race item {k:02d}')
HA, HB = link_phone('Samsung'), link_phone('Tecno')
sid, _ = open_st()
def burst(h, names):
    cl = A.app.test_client()
    o = io.StringIO(); w = csv.writer(o); w.writerow(['Medication', 'Physical count'])
    for n in names:
        w.writerow([n, 3])
    cl.post('/api/phone/counts', headers=h, data=o.getvalue().encode())
names = [f'Race item {k:02d}' for k in range(60)]
ts = [threading.Thread(target=burst, args=(h, part)) for h, part in
      ((HA, names[:30]), (HB, names[30:]), (HA, names[:30]), (HB, names[30:]))]
for t_ in ts: t_.start()
for t_ in ts: t_.join()
w_ = A._stock_take_waiting(db, 'admin', db['stock_takes'].find_one({'status': 'open'}))['rows']
check('two phones sending at once: nothing lost', sorted(r_['name'] for r_ in w_) == names, len(w_))

# =====================================================================
print('\n== Several phones at once: receiving ==')
reset(); login(); db['phone_entries'].delete_many({}); db['deliveries'].delete_many({})
HA, HB = link_phone('Samsung'), link_phone('Tecno')
did = str(db['deliveries'].insert_one({
    'reference': 'DEL-TWO', 'supplier': 'NDSO', 'order_number': 'PO-2', 'invoice_number': 'INV-9',
    'invoice_total': 50.0, 'order_total': 50.0, 'stock_receiver': 'x', 'status': 'open',
    'opened_at': datetime.utcnow(), 'received_by': 'x'}).inserted_id)
inv = ('Medication,Quantity,Pack size,Price per pack,Batch,Expiry date\n'
       '"Amoxicillin caps, 500 mg",56,28,40,,\n"Paracetamol tabs, 500 mg",100,1,0.5,P1,2028-06-30\n'
       '"Cetirizine tabs, 10 mg",30,1,0.2,C1,2028-01-31\n')
c.post('/receive', data={'action': 'upload_lines', 'delivery_id': did, 'qty_mode': 'units',
                         'sheet': (io.BytesIO(inv.encode()), 'inv.csv')}, content_type='multipart/form-data')
def check_lines(h, rows_):
    o = io.StringIO(); w = csv.writer(o)
    w.writerow(['Line', 'Units', 'Batch', 'Expiry date', 'Checked'])
    for r_ in rows_:
        w.writerow(r_)
    return api.post(f'/api/phone/delivery/{did}/lines', headers=h, data=o.getvalue().encode()).get_json()
check_lines(HA, [[1, 56, 'AX1', '04/2028', 'yes'], [2, 100, '', '', 'yes']])
rows = csv_rows(api.get(f'/api/phone/delivery/{did}/lines', headers=HB).data)
check("phone B sees which lines phone A checked", rows[1][12] == 'Samsung' and rows[1][10] == 'yes'
      and rows[3][12] == '', rows[1])
jb = check_lines(HB, [[1, 50, 'AX1', '04/2028', 'yes'], [3, 30, '', '', 'yes']])
check('phone B is told it disagrees on line 1', jb['disputed'] == 1, jb)
L = {r_['line']: r_ for r_ in (A._receive_waiting(db, 'admin', db['deliveries'].find_one()) or {})['rows']}
check('line 1 is flagged with both counts and unticked',
      L[1].get('conflict') == 'Samsung 56, Tecno 50' and L[1]['include'] is False
      and 'phones disagree: Samsung 56, Tecno 50 - count it again' in L[1]['problem'], L[1])
check('lines 2 and 3, each checked by one phone, are ticked and named',
      L[2]['include'] and L[2]['phones'] == 'Samsung' and L[3]['include'] and L[3]['phones'] == 'Tecno')
page = c.get(f'/receive?delivery_id={did}').text
check('the delivery page shows it', 'phones disagree</span>' in page and 'checked on Samsung</span>' in page
      and 'checked on Tecno</span>' in page)
rows = csv_rows(api.get(f'/api/phone/delivery/{did}/lines', headers=HA).data)
check('a disputed line goes back to the phones unchecked, saying why',
      rows[1][10] == '' and 'phones disagree' in rows[1][9], rows[1])
check_lines(HA, [[1, 50, 'AX1', '04/2028', 'yes']])
L = {r_['line']: r_ for r_ in (A._receive_waiting(db, 'admin', db['deliveries'].find_one()) or {})['rows']}
check('recounted to agree: ticked, at the agreed figure', not L[1].get('conflict') and L[1]['include']
      and str(L[1]['units']) == '50' and 'short by 6' in L[1]['problem'], L[1])

print(f"\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========")
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    raise SystemExit(1)
