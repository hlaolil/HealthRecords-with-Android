"""Bringing long lists in from a spreadsheet.

Asked for: "let's add that functionality for all lists that have a tendency
to be long ... I can just upload the invoice, stock take sheet and all the
others". Spreadsheets only (Excel or CSV) was chosen over AI reading of PDFs
and photos. Every upload is shown for checking first; nothing reaches stock
until the page's own button is pressed.
"""
from _root import ROOT  # the app folder, one up from tests/
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import io, re, csv, html as _html
import mongomock, pymongo, openpyxl
from datetime import datetime, timezone, timedelta

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
A.get_mongo_client = lambda _client=_f: _client
A.init_db_collections()
from test_support import arm_csrf
db = _f['pharmacy_db']
meds, txs, dels = db['medications'], db['transactions'], db['deliveries']
c = A.app.test_client()
arm_csrf(c)
PASS, FAIL = [], []


def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label
          + (('  ->  ' + detail) if detail and not cond else ''))


def login(role='admin', name='Letlotlo Hlaoli', login_='admin', department=None):
    with c.session_transaction() as s:
        s['user'] = {'name': name, 'login': login_, 'role': role}
        if department:
            s['user']['department'] = department
        s['last_active'] = datetime.now(timezone.utc).isoformat()


def xlsx(rows, title='Sheet1', lead=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in lead:
        ws.append(r)
    for r in rows:
        ws.append(r)
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def csvfile(text):
    return text.encode('utf-8')


def post_file(url, data, content, fname='sheet.xlsx', follow=True):
    d = dict(data)
    d['sheet'] = (io.BytesIO(content), fname)
    return c.post(url, data=d, content_type='multipart/form-data', follow_redirects=follow)


def banner(page):
    m = re.search(r'<p class="message[^"]*"[^>]*>(.*?)</p>', page, re.S)
    return _html.unescape(m.group(1)).strip() if m else ''


def bal(n):
    return meds.find_one({'name': n})['balance']


def reset():
    for col in ['medications', 'transactions', 'deliveries', 'audit_log', 'upload_drafts',
                'stock_takes', 'stock_take_counts', 'dept_requests']:
        db[col].delete_many({})
    meds.insert_many([
        {'name': 'Amoxicillin caps, 500 mg', 'balance': 100, 'schedule': 'not controlled',
         'pack_size': 100, 'pack_price': 50.0, 'price': 0.5, 'batch': 'A0', 'expiry_date': '2027-01-31',
         'category': 'Pharmaceutical'},
        {'name': 'Amoxicillin caps, 250 mg', 'balance': 10, 'schedule': 'not controlled',
         'pack_size': 100, 'pack_price': 30.0, 'price': 0.3, 'category': 'Pharmaceutical'},
        {'name': 'Paracetamol tabs, 500 mg', 'balance': 500, 'schedule': 'not controlled',
         'pack_size': 1000, 'pack_price': 90.0, 'price': 0.09, 'category': 'Pharmaceutical'},
        {'name': 'Pyridoxine, 025 mg', 'balance': 0, 'schedule': 'not controlled',
         'pack_size': 100, 'pack_price': 20.0, 'price': 0.2, 'category': 'Pharmaceutical'},
        {'name': 'Morphine tabs, 10mg', 'balance': 40, 'schedule': 'controlled',
         'pack_size': 10, 'pack_price': 20.0, 'price': 2.0, 'category': 'Pharmaceutical'},
    ])


def open_delivery(total=0.0):
    return str(dels.insert_one({
        'reference': 'DEL-1113', 'supplier': 'NDSO', 'order_number': 'PO-1', 'invoice_number': 'INV-1',
        'invoice_total': total, 'order_total': total, 'stock_receiver': 'L. Hlaoli', 'status': 'open',
        'opened_at': datetime.utcnow(), 'received_by': 'L. Hlaoli'}).inserted_id)


# =====================================================================
print('\n--- 1. Reading a spreadsheet ---')
reset()
match = A._med_matcher([m['name'] for m in meds.find()])
check('an exact name matches, ignoring case and punctuation',
      match('amoxicillin caps 500 MG')[0] == 'Amoxicillin caps, 500 mg')
n, sugg, guessed = match('Paracetamol 500mg')
check('a close name with the same strength is matched, and marked as a guess',
      n == 'Paracetamol tabs, 500 mg' and guessed, str((n, sugg)))
check('a zero-padded strength still counts as the same number',
      match('Pyridoxine 25mg')[0] == 'Pyridoxine, 025 mg')
n, sugg, guessed = match('Amoxicillin caps 125 mg')
check('a different strength is never matched, only suggested', n is None and sugg, str((n, sugg)))
check('a name like nothing on the list is left alone', match('Ceftriaxone 1g')[0] is None)
check('numbers are read as people type them',
      A._cell_num('R1 250.50') == 1250.5 and A._cell_num('12,5') == 12.5
      and A._cell_num('1,200') == 1200 and A._cell_num('abc') is None)


class F:
    def __init__(self, data, name):
        self.filename, self._d = name, data

    def read(self, n=-1):
        return self._d


t = A._find_table(A._read_upload(F(xlsx([['Description', 'Qty', 'Unit Price', 'Batch No.', 'Exp Date']],
                                       lead=[['ACME SUPPLIES INVOICE 778'], [], ['Customer: NMC']]), 'inv.xlsx')),
                  ['med', 'qty', 'unit_price', 'batch', 'expiry'], [['med'], ['qty']])
check('the heading row is found below a letterhead', t['header_row'] == 4, str(t['header_row']))
check('and supplier headings are recognised',
      t['cols'] == {'med': 0, 'qty': 1, 'unit_price': 2, 'batch': 3, 'expiry': 4}, str(t['cols']))
t = A._find_table(A._read_upload(F(csvfile('Medication;Quantity\nGauze;3\n'), 'l.csv')),
                  ['med', 'qty'], [['med'], ['qty']])
check('a CSV with semicolons is read', t['rows'] == [['Gauze', '3']], str(t['rows']))
for bad, why in [(F(b'x', 'notes.txt'), 'a text file'), (F(b'PK\x03\x04junk', 'x.xlsx'), 'a broken workbook')]:
    try:
        A._read_upload(bad); ok = False
    except ValueError:
        ok = True
    check(f'{why} is turned away with a reason', ok)
try:
    A._find_table(A._read_upload(F(xlsx([['Foo', 'Bar'], [1, 2]]), 'x.xlsx')), ['med', 'qty'], [['med'], ['qty']])
    ok = False
except ValueError as e:
    ok = 'download the template' in str(e)
check('a sheet without the headings says what it looks for', ok)

login()
r = c.get('/upload-template/receive')
wb = openpyxl.load_workbook(io.BytesIO(r.data))
check('the invoice template downloads with its headings',
      r.status_code == 200 and [x.value for x in wb.active[1]][:3] == ['Medication', 'Quantity (single units)', 'Pack size'])
check('and a sheet saying how to fill it in', 'How to fill this in' in wb.sheetnames)
check('an unknown template is not served', c.get('/upload-template/nope').status_code == 302)

# =====================================================================
print('\n--- 2. Receiving: an invoice\'s lines ---')
reset(); login()
did = open_delivery(total=1000.0)
inv = xlsx([['Description', 'Qty', 'Pack Size', 'Unit Price', 'Batch', 'Expiry'],
            ['Amoxicillin caps, 500 mg', 5, 100, 52.40, 'AMX1', datetime(2028, 3, 31)],
            ['Paracetamol 500mg', 2, 1000, 88.00, 'P77', '30/06/2028'],
            ['Amoxicillin caps 125 mg', 1, 100, 25.00, 'X', '2028-01-01'],
            ['Morphine tabs, 10mg', 3, 10, 21.00, '', '2027-12-31'],
            ['SUB TOTAL', None, None, 500, None, None]])
r = post_file('/receive', {'action': 'upload_lines', 'delivery_id': did, 'qty_mode': 'packs'}, inv)
page = r.data.decode()
d = db['upload_drafts'].find_one({'kind': 'receive'})
rows = d['rows'] if d else []
check('the lines are read and nothing is added yet',
      len(rows) == 4 and txs.count_documents({}) == 0 and bal('Amoxicillin caps, 500 mg') == 100,
      str(len(rows)))
check('the page says to check them', 'Nothing has been added yet' in banner(page) and 'Uploaded Lines' in page)
check('a total row on the invoice is not read as an item', all('TOTAL' not in x['raw'] for x in rows))
check('Qty read as packs: 5 packs of 100 is 500 units', rows[0]['units'] == 500, str(rows[0]))
check('a unit price next to packs is the pack price', rows[0]['pack_price'] == 52.4)
check('an expiry written dd/mm/yyyy is read', rows[1]['expiry'] == '2028-06-30')
check('a close spelling is matched and marked for checking',
      rows[1]['name'] == 'Paracetamol tabs, 500 mg' and rows[1]['guessed'])
check('an unknown strength is flagged with suggestions',
      not rows[2]['matched'] and 'did you mean' in rows[2]['problem'], rows[2]['problem'])
check('a missing batch is flagged', 'no batch' in rows[3]['problem'])
check('a matched row is ticked, an unmatched one is not',
      'name="inc__0" value="1"\n                       checked' in page or re.search(r'name="inc__0" value="1"\s+checked', page)
      and not re.search(r'name="inc__2" value="1"\s+checked', page))


def bulk(rows, overrides=None):
    data = {'action': 'lines_bulk', 'delivery_id': did, 'n': str(len(rows))}
    for i, r in enumerate(rows):
        o = (overrides or {}).get(i, {})
        if o.get('inc', r.get('matched')):
            data[f'inc__{i}'] = '1'
        data[f'med__{i}'] = o.get('name', r['name'])
        data[f'raw__{i}'] = r['raw']
        data[f'units__{i}'] = str(o.get('units', r['units']))
        data[f'pack_size__{i}'] = str(o.get('pack_size', r['pack_size']))
        data[f'pack_price__{i}'] = str(o.get('pack_price', r['pack_price']))
        data[f'batch__{i}'] = o.get('batch', r['batch'])
        data[f'expiry__{i}'] = o.get('expiry', r['expiry'])
    return c.post('/receive', data=data, follow_redirects=True)


r = bulk(rows)
check('one ticked line missing its batch stops them all',
      txs.count_documents({}) == 0 and 'line 4' in banner(r.data.decode()) and 'batch' in banner(r.data.decode()),
      banner(r.data.decode()))
check('and the review stays, with what was typed', db['upload_drafts'].count_documents({'kind': 'receive'}) == 1)
r = bulk(rows, {3: {'batch': 'M9'}, 2: {'name': 'Amoxicillin caps, 250 mg', 'inc': True}})
check('corrected, every ticked line is added', txs.count_documents({'type': 'receive'}) == 4, banner(r.data.decode()))
check('stock goes onto the right items',
      bal('Amoxicillin caps, 500 mg') == 600 and bal('Paracetamol tabs, 500 mg') == 2500
      and bal('Amoxicillin caps, 250 mg') == 110 and bal('Morphine tabs, 10mg') == 70)
line = txs.find_one({'med_name': 'Amoxicillin caps, 500 mg'})
check('each line is a normal delivery line',
      line['delivery_reference'] == 'DEL-1113' and line['supplier'] == 'NDSO'
      and abs(line['line_value'] - 262.0) < 1e-9 and abs(line['price'] - 0.524) < 1e-9)
check('the schedule comes from the medication',
      txs.find_one({'med_name': 'Morphine tabs, 10mg'})['schedule'] == 'controlled')
check('the review is cleared once added', db['upload_drafts'].count_documents({'kind': 'receive'}) == 0)
check('and the addition is audited',
      db['audit_log'].find_one({'target_id': 'DEL-1113', 'changes': {'$regex': 'uploaded invoice'}}) is not None)

reset(); login(); did = open_delivery()
units_sheet = xlsx([['Medication', 'Quantity (single units)', 'Pack size', 'Price per pack', 'Batch', 'Expiry date'],
                    ['Amoxicillin caps, 500 mg', 250, 100, 50, 'B', '2028-01-31']])
post_file('/receive', {'action': 'upload_lines', 'delivery_id': did}, units_sheet)
rw = db['upload_drafts'].find_one({'kind': 'receive'})['rows'][0]
check("the app's own template reads units as units", rw['units'] == 250 and rw['pack_price'] == 50)
c.post('/receive', data={'action': 'discard_upload', 'delivery_id': did})
check('an upload can be discarded', db['upload_drafts'].count_documents({}) == 0 and txs.count_documents({}) == 0)
tot = xlsx([['Item', 'Packs', 'Total'], ['Amoxicillin caps, 500 mg', 2, 104.0]])
post_file('/receive', {'action': 'upload_lines', 'delivery_id': did}, tot)
rw = db['upload_drafts'].find_one({'kind': 'receive'})['rows'][0]
check('a line total gives the pack price, and the pack size comes from the item',
      rw['units'] == 200 and rw['pack_price'] == 52.0 and rw['pack_size'] == 100, str(rw))
dels.update_one({'_id': A.ObjectId(did)}, {'$set': {'status': 'closed'}})
r = post_file('/receive', {'action': 'upload_lines', 'delivery_id': did}, tot)
check('a closed delivery takes no upload', 'is closed' in banner(r.data.decode()))

# =====================================================================
print('\n--- 3. Stock take: download the sheet, upload the counts ---')
reset(); login()
c.post('/stock-take', data={'action': 'open', 'note': 'November'})
st = db['stock_takes'].find_one({'status': 'open'})
sid = str(st['_id'])
r = c.get(f'/stock-take?stock_take_id={sid}&download=sheet')
wb = openpyxl.load_workbook(io.BytesIO(r.data))
ws = wb.active
heads = [x.value for x in ws[1]]
body = {row[1]: row for row in ws.iter_rows(min_row=2, values_only=True)}
check('the count sheet downloads with every item still to count, code first',
      r.status_code == 200 and heads[:4] == ['Code', 'Medication', 'On system', 'Physical count']
      and len(body) == 5, str(heads))
check('with what the system has, and the count left blank',
      body['Amoxicillin caps, 500 mg'][2] == 100 and body['Amoxicillin caps, 500 mg'][3] is None
      and body['Amoxicillin caps, 500 mg'][4] == '2027-01-31')
page = c.get(f'/stock-take?stock_take_id={sid}').data.decode()
check('the stock take page offers the download and the upload',
      'download=sheet' in page and 'name="action" value="upload_counts"' in page)

# fill it in as a person would: two counted, one with a new expiry, one left blank
ws.cell(row=2, column=4).value = None
rows_by = {ws.cell(row=i, column=2).value: i for i in range(2, ws.max_row + 1)}
ws.cell(row=rows_by['Amoxicillin caps, 500 mg'], column=4).value = 96
ws.cell(row=rows_by['Paracetamol tabs, 500 mg'], column=4).value = 500
ws.cell(row=rows_by['Paracetamol tabs, 500 mg'], column=5).value = datetime(2029, 2, 28)
ws.append([None, 'Ceftriaxone 1g', None, 4, None, None, None])
ws.append([None, 'Morphine 10mg', None, 38, None, None, 'locked cupboard'])
b = io.BytesIO(); wb.save(b)
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid}, b.getvalue())
page = r.data.decode()
msg = banner(page)
check('the counts fill the sheet and nothing is posted',
      db['stock_take_counts'].count_documents({}) == 0 and bal('Amoxicillin caps, 500 mg') == 100
      and 'Nothing has been posted yet' in msg, msg)
check('the counted boxes hold the figures',
      re.search(r'class="st-count"[^>]*\n?[^>]*value="96"', page) is not None
      and re.search(r'class="st-count"[^>]*\n?[^>]*value="500"', page) is not None)
check('a changed expiry is filled in', 'value="2029-02-28"' in page)
check('a close spelling is matched and named for checking',
      '"Morphine 10mg" as Morphine tabs, 10mg' in msg and 'value="locked cupboard"' in page)
check('an item not on the list is named and left out', 'Ceftriaxone 1g' in msg)
check('the filled rows are highlighted', page.count('title="filled from the uploaded sheet"') == 3)
check('rows sent back unchanged from the download are not counted as filled',
      msg.startswith('3 row(s) of the sheet filled'), msg[:60])
# post as the page now stands
names = re.findall(r'name="med__\d+" value="([^"]+)"', page)
form = {'action': 'count_sheet', 'stock_take_id': sid}
for i, nm in enumerate(names):
    form[f'med__{i}'] = nm
    for f in ('counted', 'expiry', 'batch', 'note'):
        m2 = re.search(r'name="' + f + '__' + str(i) + r'"[^>]*?value="([^"]*)"', page, re.S)
        form[f'{f}__{i}'] = m2.group(1) if m2 else ''
c.post('/stock-take', data=form)
check('Post Counts posts them as any count',
      db['stock_take_counts'].count_documents({'stock_take_id': sid, 'counted': {'$ne': None}}) == 3
      and bal('Amoxicillin caps, 500 mg') == 96 and bal('Morphine tabs, 10mg') == 38)
check('the uploaded figures are cleared once posted', db['upload_drafts'].count_documents({}) == 0)
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid},
              xlsx([['Medication', 'Count'], ['Pyridoxine, 025 mg', 'many']]))
check('a count in words is not read', 'Nothing has been posted' in banner(r.data.decode())
      and 'Pyridoxine' not in str(db['upload_drafts'].find_one() or {}))
c.post('/stock-take', data={'action': 'discard_counts', 'stock_take_id': sid})
check('uploaded figures can be cleared without posting', db['upload_drafts'].count_documents({}) == 0)
login('employee', 'N. Nurse', 'nurse')
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid},
              xlsx([['Medication', 'Count'], ['Pyridoxine, 025 mg', 3]]), follow=False)
check('only an admin can upload counts', r.status_code == 302 and db['upload_drafts'].count_documents({}) == 0)
login()

# =====================================================================
print('\n--- 4. Add Medication: many new items at once ---')
reset(); login()
page = c.get('/add-medication').data.decode()
check('the page offers an upload and a template',
      'name="action" value="upload_meds"' in page and '/upload-template/medications' in page)
sheet = xlsx([['Medication', 'Category', 'Schedule', 'Pack size', 'Price per pack', 'Opening balance', 'Batch', 'Expiry date'],
              ['Cetirizine tabs, 10 mg', 'Pharmaceutical', 'not controlled', 30, 18.5, 60, 'C1', '2028-05-31'],
              ['Crepe bandage 10cm', 'consumables', '', 12, 60, None, '', ''],
              ['Pethidine inj, 50 mg', '', 'yes', 10, 95, 0, '', ''],
              ['amoxicillin caps 500mg', '', '', 100, 50, 0, '', ''],
              ['Paracetamol 500mg', '', '', 1000, 90, 0, '', ''],
              ['Cetirizine tabs, 10 mg', '', '', 30, 18.5, 0, '', ''],
              ['Odd item', 'snacks', 'maybe', 1, 1, 0, '', '']])
r = post_file('/add-medication', {'action': 'upload_meds'}, sheet)
page = r.data.decode()
d = db['upload_drafts'].find_one({'kind': 'medications'})
rows = {x['name']: x for x in d['rows']} if d else {}
check('the list is read and nothing is added yet',
      len(d['rows']) == 7 and meds.count_documents({}) == 5 and 'Nothing has been added yet' in banner(page),
      banner(page))
check('categories and schedules are read from the words people use',
      rows['Crepe bandage 10cm']['category'] == 'Medical Supply'
      and rows['Pethidine inj, 50 mg']['schedule'] == 'controlled'
      and rows['Cetirizine tabs, 10 mg']['schedule'] == 'not controlled')
check('an item already on the list is marked, under any spelling',
      rows['amoxicillin caps 500mg']['status'] == 'exists' and 'Amoxicillin caps, 500 mg' in rows['amoxicillin caps 500mg']['note'])
check('a near-duplicate is flagged to check, not refused',
      rows['Paracetamol 500mg']['status'] == 'new' and 'close to' in rows['Paracetamol 500mg']['note'])
check('items that only share "tabs, 10 mg" are not called near-duplicates',
      d['rows'][0]['note'] == '', d['rows'][0]['note'])
check('the same item twice on the sheet is caught', [x['status'] for x in d['rows']].count('exists') == 2)
check('words it cannot read are flagged',
      'not recognised' in d['rows'][6]['problem'])
check('a row with something flagged starts unticked',
      re.search(r'name="inc__6" value="1"\s+>', page) is not None)
check('rows already on the list cannot be ticked',
      re.search(r'name="inc__3" value="1"\s+disabled', page) is not None)


def meds_bulk(rows, untick=()):
    data = {'action': 'meds_bulk', 'n': str(len(rows))}
    for i, x in enumerate(rows):
        if x['status'] != 'exists' and i not in untick:
            data[f'inc__{i}'] = '1'
        for f in ('name', 'category', 'schedule', 'pack_size', 'pack_price', 'opening', 'batch', 'expiry',
                  'status', 'note'):
            data[f'{f}__{i}'] = str(x.get(f, ''))
    return c.post('/add-medication', data=data, follow_redirects=True)


r = meds_bulk(d['rows'], untick=(4,))
check('the new items are added', meds.count_documents({}) == 9, banner(r.data.decode()))
cet = meds.find_one({'name': 'Cetirizine tabs, 10 mg'})
check('each as the form would add it',
      cet['balance'] == 60 and abs(cet['price'] - 18.5 / 30) < 1e-9 and cet['category'] == 'Pharmaceutical')
check('with its opening stock recorded as opening stock',
      txs.find_one({'type': 'opening_balance', 'med_name': 'Cetirizine tabs, 10 mg'})['quantity'] == 60)
check('the controlled one is controlled', meds.find_one({'name': 'Pethidine inj, 50 mg'})['schedule'] == 'controlled')
check('an unticked near-duplicate is not added', meds.find_one({'name': 'Paracetamol 500mg'}) is None)
check('the page says so', 'added successfully' in banner(r.data.decode()) and '1 with opening stock' in banner(r.data.decode()))
post_file('/add-medication', {'action': 'upload_meds'}, sheet)
d = db['upload_drafts'].find_one({'kind': 'medications'})
n0 = meds.count_documents({})
check('uploading the same list again marks everything added last time as on the list',
      [x['status'] for x in d['rows']].count('exists') == 6, str([x['status'] for x in d['rows']]))
r = meds_bulk(d['rows'], untick=(4,))
check('so nothing is added twice', meds.count_documents({}) == n0, banner(r.data.decode()))
c.post('/add-medication', data={'action': 'discard_meds'})
login('employee', 'N. Nurse', 'nurse')
r = post_file('/add-medication', {'action': 'upload_meds'}, sheet, follow=False)
check('only an admin can upload new items', r.status_code == 302 and db['upload_drafts'].count_documents({}) == 0)
login()

# =====================================================================
print('\n--- 5. Internal issue: a ward list ---')
reset(); login()
lst = xlsx([['Item', 'Qty Issued'], ['Paracetamol tabs, 500 mg', 200], ['Amoxicilin caps 500mg', 30],
            ['Ceftriaxone 1g', 5], ['Morphine tabs, 10mg', '']])
page = c.get('/internal-issue').data.decode()
check('the issue page offers an upload', 'name="action" value="upload_list"' in page and '/upload-template/list' in page)
r = post_file('/internal-issue', {'action': 'upload_list'}, lst)
page = r.data.decode()
check('the items go into the form, and nothing is issued',
      'value="Paracetamol tabs, 500 mg"' in page and 'value="200"' in page
      and txs.count_documents({}) == 0 and 'Nothing has been issued yet' in banner(page), banner(page))
check('a close spelling is put in as the item on file, and marked',
      'value="Amoxicillin caps, 500 mg"' in page and 'read &#34;Amoxicilin caps 500mg&#34;' in page)
check('an unknown item stays as written, flagged', 'value="Ceftriaxone 1g"' in page and 'not on the list' in page)
check('a missing quantity is flagged', 'no quantity' in page)
form = {'action': 'issue', 'department': 'Lab', 'issued_to': 'Lab Tech', 'note': '',
        'med_name': re.findall(r'name="med_name" list="med_suggestions"[^>]*value="([^"]*)"', page),
        'quantity': re.findall(r'name="quantity" type="number" min="0"[^>]*value="([^"]*)"', page)}
form['med_name'] = form['med_name'][:4]; form['quantity'] = form['quantity'][:4]
r = c.post('/internal-issue', data=form, follow_redirects=True)
check('Record Issue issues what is right, and names what is not',
      bal('Paracetamol tabs, 500 mg') == 300 and bal('Amoxicillin caps, 500 mg') == 70
      and 'Ceftriaxone 1g' in banner(r.data.decode()), banner(r.data.decode()))
check('the uploaded list is cleared after the issue', db['upload_drafts'].count_documents({}) == 0)
post_file('/internal-issue', {'action': 'upload_list'}, lst)
c.post('/internal-issue', data={'action': 'discard_list'})
check('the uploaded list can be cleared without issuing', db['upload_drafts'].count_documents({}) == 0)

print('\n--- 6. Requests: a ward list ---')
reset()
login('department', 'Sr. Mokoena', 'ed', 'Emergency Department')
page = c.get('/requests').data.decode()
check('a department can upload its list', 'name="action" value="upload_list"' in page)
r = c.get('/upload-template/list')
check('and download the list template', r.status_code == 200 and r.data[:2] == b'PK')
r = post_file('/requests', {'action': 'upload_list'},
              csvfile('Medication,Quantity\nParacetamol tabs 500mg,100\nAmoxicillin caps 500 mg,20\n'), 'ward.csv')
page = r.data.decode()
check('the items go into the request form, nothing is sent',
      'value="Paracetamol tabs, 500 mg"' in page and 'value="100"' in page
      and db['dept_requests'].count_documents({}) == 0 and 'Nothing has been sent yet' in banner(page), banner(page))
names = re.findall(r'name="med_name" list="req_meds" autocomplete="off" value="([^"]*)"', page)
qtys = re.findall(r'name="quantity" type="number" min="1" step="1" value="([^"]*)"', page)
r = c.post('/requests', data={'action': 'post', 'note': '', 'med_name': names, 'quantity': qtys},
           follow_redirects=True)
d = db['dept_requests'].find_one()
check('Send Request sends it as any request',
      d and {l['med_name']: l['requested'] for l in d['lines']}
      == {'Paracetamol tabs, 500 mg': 100, 'Amoxicillin caps, 500 mg': 20}, banner(r.data.decode()))
check('and the uploaded list is cleared', db['upload_drafts'].count_documents({}) == 0)
big = 'Medication,Quantity\n' + ''.join(f'Item {i},1\n' for i in range(40))
r = post_file('/requests', {'action': 'upload_list'}, csvfile(big), 'big.csv')
check('a list longer than a request can hold is refused with a reason',
      'Split it into two' in banner(r.data.decode()) and db['upload_drafts'].count_documents({}) == 0)
login()
r = post_file('/requests', {'action': 'upload_list'},
              csvfile('Medication,Quantity\nGauze swabs,3\nParacetamol tabs, 500 mg,10\n'), 'p.csv')
page = r.data.decode()
check('staff posting for a department get the list in that form, opened',
      re.search(r'<details style="margin: 6px 0 18px;" open>', page) is not None)

# =====================================================================
print('\n--- 7. A long sheet does not overflow the session cookie ---')
# Reported: uploading a stock-take sheet came back as Vercel's "400
# BAD_REQUEST" without reaching the app. The message carried to the next page
# lives in the session cookie, and naming every unmatched line of a long sheet
# made that cookie larger than browsers and Vercel accept.
reset(); login()
c.post('/stock-take', data={'action': 'open', 'note': 'big'})
sid = str(db['stock_takes'].find_one({'status': 'open'})['_id'])
rows = [['NMC Pharmacy - Stock Take (Partial - In Progress)'], ['Completed items: 41 of 303'], [],
        ['Medication', 'On System', 'Physical Count', 'Expiry', 'Batch No.', 'Note']]
rows += [[f'Unlisted remedy number {i:03d} {i % 40 * 25}mg', '10', '9', '13/26', f'B{i}', None]
         for i in range(300)]
wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Stock Take Progress'
for r_ in rows:
    ws.append(r_)
b = io.BytesIO(); wb.save(b)
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid}, b.getvalue(), follow=False)
cookie = sum(len(v) for k, v in r.headers.items() if k.lower() == 'set-cookie')
check('300 unmatched lines leave the cookie well under the 4 KB browsers allow',
      r.status_code == 302 and cookie < 3000, f'{cookie} bytes')
page = c.get(r.headers['Location']).data.decode()
msg = banner(page)
check('the message names a few and counts the rest', 'and 297 more' in msg, msg[:300])
check('while the page lists them all', page.count('Unlisted remedy number') >= 300)
check('every message is capped as a backstop', len(msg) <= A.FLASH_MAX_CHARS + 20, str(len(msg)))

print('\n--- 8. Expiry written as month and year ---')
for raw, want in [('12/26', '2026-12-31'), ('6/28', '2028-06-30'), ('02/2028', '2028-02-29'),
                  ('Jun 2028', '2028-06-30'), ('jun-28', '2028-06-30'), ('30/06/28', '2028-06-30'),
                  (datetime(2027, 5, 1), '2027-05-01')]:
    check(f'"{raw}" is read as {want}', A._checklist_date(raw) == (want, None), str(A._checklist_date(raw)))
for raw in ['\u2014', '-', 'N/A', 'none']:
    check(f'"{raw}" means no expiry, not a bad date', A._checklist_date(raw) == (None, None))
for raw in ['13/26', '31/06/2027', 'soon']:
    check(f'"{raw}" is not taken as a date', A._checklist_date(raw)[0] is None)
reset(); login()
c.post('/stock-take', data={'action': 'open', 'note': 'dates'})
sid = str(db['stock_takes'].find_one({'status': 'open'})['_id'])
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid},
              xlsx([['Medication', 'On System', 'Physical Count', 'Expiry', 'Batch No.'],
                    ['Gauze swabs', '60', '58', '12/26', 'G7'],
                    ['Amoxicillin caps, 500 mg', '100', '96', '6/28', '4610']],
                   lead=[['NMC Pharmacy - Stock Take'], ['Completed items: 2 of 5'], []]))
page = r.data.decode()
check("a sheet laid out like the pharmacy's own fills counts and month-end expiries",
      'value="2028-06-30"' in page and re.search(r'class="st-count"[^>]*\n?[^>]*value="96"', page) is not None
      and 'Not read' not in banner(page), banner(page))

# =====================================================================
print('\n--- 9. Sent as text, not as an attachment ---')
# Reported again after the cookie fix: Vercel still answered the upload with
# "400 BAD_REQUEST" before the app saw it. Every ordinary form worked; only
# the attachment ("multipart") form failed. The browser now reads the file
# and sends it as a plain field, so an upload is an ordinary form.
import base64 as _b64
reset(); login()
c.post('/stock-take', data={'action': 'open', 'note': 'text'})
sid = str(db['stock_takes'].find_one({'status': 'open'})['_id'])
sheet = xlsx([['Medication', 'On System', 'Physical Count', 'Expiry'],
              ['Amoxicillin caps, 500 mg', '100', '96', '6/28']])
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                                'sheet_b64': _b64.b64encode(sheet).decode(),
                                'sheet_name': 'count.xlsx'}, follow_redirects=True)
page = r.data.decode()
check('a sheet sent as text is read like an attachment',
      '1 row(s) of the sheet filled' in banner(page) and 'value="2028-06-30"' in page, banner(page))
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid,
                                'sheet_b64': 'not base64 at all!!', 'sheet_name': 'x.xlsx'},
           follow_redirects=True)
check('garbled text is turned away politely', 'could not be read' in banner(r.data.decode()))
r = c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid}, follow_redirects=True)
check('no file at all asks for one', 'choose a spreadsheet' in banner(r.data.decode()))
pages = {'/stock-take?stock_take_id=' + sid: 'sheet', '/add-medication': 'sheet', '/internal-issue': 'sheet',
         '/requests': 'sheet'}
did = open_delivery()
pages['/receive?delivery_id=' + did] = 'sheet'
for url, field in pages.items():
    html_ = c.get(url).data.decode()
    check(f'{url.split("?")[0]}: no form sends a file as an attachment',
          'multipart/form-data' not in html_ and f'data-up-field="{field}"' in html_
          and f'name="{field}_b64"' in html_ and re.search(r'<input type="file" name=', html_) is None)
html_ = c.get('/requests?topup=Emergency+Trolley').data.decode()
check('the trolley checklist is sent as text too',
      'data-up-field="checklist"' in html_ and 'name="checklist_b64"' in html_)


# --- The count sheet sends only what was filled in -------------------------
# A full sheet is hundreds of rows; the page switches untouched rows off
# before sending, and the app must cope with rows missing from the post.
print('\n== Post Counts with untouched rows left out ==')
c.post('/stock-take', data={'action': 'open'}, follow_redirects=True)
st2 = db['stock_takes'].find_one({'status': 'open'})
sid2 = str(st2['_id'])
page = c.get(f'/stock-take?stock_take_id={sid2}').text
rows = dict((int(i), n) for i, n in re.findall(r'name="med__(\d+)" value="([^"]+)"', page))
check('each row carries its own numbered name box', len(rows) >= 3, str(rows))
check('the sheet form switches untouched rows off before sending',
      'stRowUntouched' in page and 'inp.disabled = true' in page)
pick = sorted(rows)[-1]                       # only the last row, by number
before = bal(rows[pick])
c.post('/stock-take', data={'action': 'count_sheet', 'stock_take_id': sid2,
                            f'med__{pick}': rows[pick], f'counted__{pick}': str(before + 2)})
check('a post holding one row, not the first, counts that row',
      bal(rows[pick]) == before + 2, f'{bal(rows[pick])} vs {before + 2}')
check('and touches no other row',
      db['stock_take_counts'].count_documents({'stock_take_id': sid2}) == 1)

print('\n== Post Uploaded Counts sends nothing but the button ==')
first = rows[sorted(rows)[0]]
b0 = bal(first)
import openpyxl as _ox
wb = _ox.Workbook(); ws = wb.active
ws.append(['Medication', 'Physical Count', 'Expiry', 'Batch No.', 'Note'])
ws.append([first, b0 + 5, '—', '—', '—'])
buf = io.BytesIO(); wb.save(buf)
c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid2,
                            'sheet': (io.BytesIO(buf.getvalue()), 'dash.xlsx')},
       content_type='multipart/form-data')
d = db['upload_drafts'].find_one({'kind': 'stock_take', 'context': sid2})
check('a dash in the batch and note columns is read as empty',
      d and d['rows'][0]['batch'] == '' and d['rows'][0]['note'] == '', str(d and d['rows']))
page = c.get(f'/stock-take?stock_take_id={sid2}').text
check('the notice offers Post Uploaded Counts', 'value="post_uploaded"' in page)
c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid2})
check('Post Uploaded Counts posts the figure from the saved upload', bal(first) == b0 + 5,
      f'{bal(first)} vs {b0 + 5}')
check('and clears the upload afterwards',
      db['upload_drafts'].count_documents({'kind': 'stock_take', 'context': sid2}) == 0)
r = c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid2},
           follow_redirects=True)
check('pressed again with nothing waiting, it says so',
      'no uploaded counts waiting' in r.text)


# --- A count sheet per shelf group -----------------------------------------
print('\n== Count sheets by group ==')
for name, cat, sch in [('Group test bandage', 'Medical Supply', 'not controlled'),
                       ('Group test donor ARV', 'Donor Funded', 'not controlled'),
                       ('Group test pethidine', 'Pharmaceutical', 'controlled'),
                       ('Group test donor morphine', 'Donor Funded', 'controlled')]:
    db['medications'].insert_one({'name': name, 'balance': 7, 'pack_size': 1, 'pack_price': 1.0,
        'price': 1.0, 'schedule': sch, 'category': cat, 'batch': 'G1', 'expiry_date': '2028-01-31'})
check('a controlled drug counts with the controlled drugs whatever its category',
      A._count_group({'category': 'Donor Funded', 'schedule': 'controlled'}) == 'controlled'
      and A._count_group({}) == 'pharm'
      and A._count_group({'category': 'Medical Supply'}) == 'supply')
page = c.get(f'/stock-take?stock_take_id={sid2}').text
for g, label in A.COUNT_GROUPS:
    check(f'the page offers a {label} download', f'group={g}' in page and label in page)
check('rows carry their group for the on-screen buttons',
      'data-group="controlled"' in page and 'data-group="supply"' in page
      and 'class="st-groups"' in page)

def sheet_names(g):
    r = c.get(f'/stock-take?stock_take_id={sid2}&download=sheet&group={g}')
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    ws = wb.worksheets[0]
    return r, ws.title, [row[1] for row in ws.iter_rows(min_row=2, values_only=True) if row[1]]

r, title, got = sheet_names('controlled')
check('the controlled sheet holds the controlled drugs only',
      'Group test pethidine' in got and 'Group test donor morphine' in got
      and 'Group test bandage' not in got and 'Group test donor ARV' not in got, str(got))
check('and is named for its group',
      title == 'Controlled Drugs' and 'controlled-drugs' in r.headers['Content-Disposition'])
_, _, got = sheet_names('donor')
check('the donor sheet leaves out the controlled donor item',
      'Group test donor ARV' in got and 'Group test donor morphine' not in got, str(got))
_, _, got = sheet_names('supply')
check('the supplies sheet holds the supplies', got == ['Group test bandage'], str(got))
r = c.get(f'/stock-take?stock_take_id={sid2}&download=sheet')
everything = [row[1] for row in openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
              .iter_rows(min_row=2, values_only=True) if row[1]]
check('the one-sheet download still holds everything',
      {'Group test bandage', 'Group test pethidine', 'Group test donor ARV'} <= set(everything))

print('\n== Uploads from several group sheets add up ==')
def group_upload(rows_, tab):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = tab
    ws.append(['Medication', 'On system', 'Physical count', 'Expiry date', 'Batch', 'Note'])
    for n, q in rows_:
        ws.append([n, 7, q, None, None, None])
    b = io.BytesIO(); wb.save(b)
    return c.post('/stock-take', data={'action': 'upload_counts', 'stock_take_id': sid2,
                  'sheet': (io.BytesIO(b.getvalue()), tab + '.xlsx')},
                  content_type='multipart/form-data', follow_redirects=True)
group_upload([('Group test bandage', 5)], 'Medical Supplies')
r = group_upload([('Group test pethidine', 6), ('Group test donor morphine', 7)], 'Controlled Drugs')
d = db['upload_drafts'].find_one({'kind': 'stock_take', 'context': sid2})
names_ = sorted(x['name'] for x in d['rows'])
check('the second sheet adds to the first instead of replacing it',
      names_ == ['Group test bandage', 'Group test donor morphine', 'Group test pethidine'], str(names_))
check('the message says how many are waiting in all', '3 uploaded row(s) are now waiting' in r.text,
      _html.unescape(r.text)[:0])
check('both sheet names are kept', d['meta']['sheets'] == ['Medical Supplies', 'Controlled Drugs'],
      str(d['meta']))
group_upload([('Group test bandage', 4)], 'Medical Supplies')
d = db['upload_drafts'].find_one({'kind': 'stock_take', 'context': sid2})
check('the same item uploaded again takes the newer figure',
      {x['name']: x['counted'] for x in d['rows']}['Group test bandage'] == 4
      and len(d['rows']) == 3 and d['meta']['sheets'] == ['Controlled Drugs', 'Medical Supplies'])
c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid2})
check('Post Uploaded Counts posts all the groups at once',
      bal('Group test bandage') == 4 and bal('Group test pethidine') == 6
      and bal('Group test donor morphine') == 7)
page = c.get(f'/stock-take?stock_take_id={sid2}').text
check('counted items leave the sheet and the group totals',
      'Group test bandage' not in page.split('id="st_sheet"')[1].split('</table>')[0])



# --- A blank trolley checklist to fill in ----------------------------------
print('\n== Blank checklist download ==')
login()
TROLLEY = 'Emergency Trolley'
r = c.get(f'/upload-template/checklist?department={TROLLEY.replace(" ", "+")}')
check('staff can download a blank checklist for the trolley',
      r.status_code == 200 and 'checklist' in r.headers.get('Content-Disposition', ''),
      str(r.status_code))
wb_ = openpyxl.load_workbook(io.BytesIO(r.data))
ws_ = wb_.worksheets[0]
std = list(db['dept_standards'].find({'department': TROLLEY}).sort('order', 1))
items_ = [row[0] for row in ws_.iter_rows(min_row=2, values_only=True) if row[0]]
check('it lists the whole standard list, in order', items_ == [x['item'] for x in std],
      f'{len(items_)} vs {len(std)}')
check('the tab is named for this month, so the import picks it',
      ws_.title.lower().startswith(A.MONTH_NAMES[A.local_now().month - 1][:3].lower()), ws_.title)
heads_ = [x.value for x in ws_[1]]
check('with REQUIRED, QTY and EXPIRY DATE columns',
      heads_[0].startswith('EMERGENCY DRUGS') and heads_[1:4] == ['REQUIRED', 'QTY', 'EXPIRY DATE'],
      str(heads_))
check('QTY and expiry start empty', all(ws_.cell(i_, 3).value is None and ws_.cell(i_, 4).value is None
                                        for i_ in range(2, ws_.max_row + 1)))
# fill it in the way a nurse would and bring it back
for i_ in range(2, ws_.max_row + 1):
    ws_.cell(i_, 3).value = 1
    ws_.cell(i_, 4).value = '31/12/2027'
b_ = io.BytesIO(); wb_.save(b_)
found_ = A._read_checklist(b_.getvalue(), None, std)
check('filled in and uploaded, every item is read back',
      len(found_['values']) == len(std) and not found_['unmatched'],
      f"{len(found_['values'])} of {len(std)}; unmatched {found_['unmatched']}")
check('with the expiry dates', all(v['expiry'] == '2027-12-31' for v in found_['values'].values()))
r = c.post('/requests', data={'action': 'import_checklist', 'department': TROLLEY,
                              'checklist': (io.BytesIO(b_.getvalue()), 'trolley.xlsx')},
           content_type='multipart/form-data', follow_redirects=True)
check('and through the page, the import fills every item',
      f'{len(std)} of {len(std)} items' in _html.unescape(r.text))
page_ = c.get('/requests?topup=Emergency+Trolley').text
check('the checklist box offers the blank checklist', '/upload-template/checklist?department=' in page_
      and 'Download a blank checklist' in page_)
r = c.get('/upload-template/checklist?department=Pharmacy', follow_redirects=True)
check('a department with no standard list gets a message, not a file',
      'no standard list' in r.text)
login(role='department', name='Sr. Mokoena', login_='ed', department=TROLLEY)
r = c.get('/upload-template/checklist?department=Pharmacy')
items_d = [row[0] for row in openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
           .iter_rows(min_row=2, values_only=True) if row[0]] if r.status_code == 200 else []
check('a department login gets its own list whatever it asks for',
      items_d == [x['item'] for x in std], str(r.status_code))
login()


# --- The phone app: CSV count sheet out, the app's export back in -----------
print('\n== Phone app CSV round trip ==')
reset(); login()
c.post('/stock-take', data={'action': 'open', 'note': 'Phone'})
sid3 = str(db['stock_takes'].find_one({'status': 'open'})['_id'])
r = c.get(f'/stock-take?stock_take_id={sid3}&download=sheet&format=csv')
text = r.data.decode('utf-8-sig')
rows_csv = list(csv.reader(io.StringIO(text)))
check('the CSV count sheet downloads with a BOM, as CSV',
      r.status_code == 200 and r.data.startswith(b'\xef\xbb\xbf')
      and 'text/csv' in r.headers['Content-Type']
      and '.csv' in r.headers['Content-Disposition'], r.headers.get('Content-Disposition', ''))
check('its columns are the ones the app reads',
      rows_csv[0] == ['Code', 'Medication', 'On system', 'Physical count', 'Expiry date', 'Batch', 'Note', 'Group',
                      'Location', 'Pack size', 'Barcode', 'Counted by'],
      str(rows_csv[0]))
by_csv = {row[1]: row for row in rows_csv[1:]}
amox = by_csv.get('Amoxicillin caps, 500 mg')
check('every item is there, with code, balance, a blank count and a dd/mm/yyyy expiry',
      len(by_csv) == 5 and amox and amox[0].startswith('MED-') and amox[2] == '100'
      and amox[3] == '' and amox[4] == '31/01/2027' and amox[7], str(amox))
page = c.get(f'/stock-take?stock_take_id={sid3}').data.decode()
check('the page offers the CSV links', page.count('format=csv') >= 2 and 'class="st-csv"' in page)
r = c.get(f'/stock-take?stock_take_id={sid3}&download=sheet&format=csv&group=supply')
grp_rows = list(csv.reader(io.StringIO(r.data.decode('utf-8-sig'))))[1:]
check('a group CSV holds that group only',
      r.status_code == 200 and '-medical-supplies' in r.headers['Content-Disposition']
      and all(x[7] == 'Medical Supplies' for x in grp_rows), r.headers.get('Content-Disposition', ''))
# What the app writes back: only touched items, the code kept, sums already
# worked out, a blank count for an item given only a new expiry, mm/yyyy dates.
para = by_csv['Paracetamol tabs, 500 mg']
app_out = io.StringIO(); w = csv.writer(app_out)
w.writerow(['Code', 'Medication', 'On system', 'Physical count', 'Expiry date', 'Batch', 'Note'])
w.writerow([amox[0], amox[1], amox[2], '34', '31/01/2027', amox[5], ''])
w.writerow([para[0], para[1], para[2], '', '03/2029', 'PX9', 'new box seen'])
r = post_file('/stock-take', {'action': 'upload_counts', 'stock_take_id': sid3},
              ('﻿' + app_out.getvalue()).encode('utf-8'), fname='NMC-count.csv')
msg = banner(r.data.decode())
d = db['upload_drafts'].find_one({'kind': 'stock_take', 'context': sid3})
got = {x['name']: x for x in (d or {}).get('rows', [])}
check('the app export is read back: the count and the detail-only line',
      'Amoxicillin caps, 500 mg' in got and str(got['Amoxicillin caps, 500 mg'].get('counted')) in ('34', '34.0'),
      msg + ' ' + str(got))
pg = got.get('Paracetamol tabs, 500 mg') or {}
check('a blank count stays blank (not 0) and mm/yyyy reads as the month end',
      pg and pg.get('counted') in (None, '') and pg.get('expiry') == '2029-03-31'
      and pg.get('batch') == 'PX9', str(pg))
c.post('/stock-take', data={'action': 'post_uploaded', 'stock_take_id': sid3})
check('Post Uploaded Counts posts the phone count', bal('Amoxicillin caps, 500 mg') == 34)
check('and leaves the uncounted item uncounted',
      db['stock_take_counts'].count_documents({'stock_take_id': sid3, 'med_name': 'Paracetamol tabs, 500 mg',
                                               'counted': {'$ne': None}}) == 0)


print(f"\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========")
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
