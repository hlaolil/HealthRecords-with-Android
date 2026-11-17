"""The standard diagnosis list: Lesotho STG 2022 conditions, ICD-10 codes,
primary or secondary level, and how the codes reach the records and reports."""
from _root import ROOT  # the app folder, one up from tests/
import os
os.environ['SECRET_KEY'] = 'k'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017'
import collections, csv, io, json, re
from datetime import datetime, timezone
import mongomock, pymongo

_f = mongomock.MongoClient()
pymongo.MongoClient = lambda *a, **k: _f
import app as A
import diagnosis_list as L
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


TODAY = datetime.utcnow()
login()

# =====================================================================
print('\n--- 1. The list is well formed ---')
names = [d[0] for d in L.DIAGNOSES]
check('no entry appears twice', len(names) == len(set(names)),
      str([n for n, k in collections.Counter(names).items() if k > 1]))
check('nor twice in different case', len({n.lower() for n in names}) == len(names))
check('every entry is Primary or Secondary', all(d[2] in ('Primary', 'Secondary') for d in L.DIAGNOSES))
bad = [(d[0], d[1]) for d in L.DIAGNOSES if d[1] and not re.fullmatch(r'[A-Z]\d\d(\.\d)?', d[1])]
check('every code is shaped like ICD-10 (letter, two digits, optional .digit)', not bad, str(bad))
uncoded = [d[0] for d in L.DIAGNOSES if not d[1]]
check('only services that are not diagnoses go without a code',
      set(uncoded) <= {'I & D', 'Normal menstrual period'}, str(uncoded))
stg = [d for d in L.DIAGNOSES if d[3]]
check('most of the list comes from the Lesotho STG', len(stg) >= 250, str(len(stg)))
chapters = {d[3].split('.')[0] for d in stg}
check('and every STG chapter with conditions is represented',
      {str(n) for n in range(1, 20)} <= chapters, str(sorted(chapters, key=int)))
check('the slash joins of old are not back', not any(' / ' in n for n in names))
check('the app offers exactly this list, alphabetically',
      A.DIAGNOSES_OPTIONS == sorted(names, key=str.lower))

# =====================================================================
print('\n--- 2. Codes checked against WHO ICD-10 ---')
KNOWN = {   # confirmed against the WHO 5th edition tabular list
    'Hypertension': 'I10', 'Diabetes mellitus': 'E14.9', 'Diabetes mellitus, type 2': 'E11.9',
    'Diabetic ketoacidosis (DKA)': 'E14.1', 'Upper respiratory tract infection (URTI)': 'J06.9',
    'Influenza': 'J11.1', 'Pneumonia': 'J18.9', 'Asthma': 'J45.9',
    'Pulmonary tuberculosis (TB)': 'A16.2', 'HIV disease': 'B24',
    'Urinary tract infection (UTI)': 'N39.0', 'Chlamydia': 'A56.2',
    'Male urethritis syndrome': 'N34.1',
    'Immune reconstitution inflammatory syndrome (IRIS)': 'D89.3',
    'Septic shock': 'R57.2', 'Post-COVID hyperactive airway disease': 'U09.9',
    'Haemorrhoids': 'K64.9', 'Acute appendicitis': 'K35.8',
    'Diarrhoea': 'A09.9', 'Dysentery': 'A09.0', 'Bacillary dysentery (shigellosis)': 'A03.9',
    'Amoebic dysentery': 'A06.0', 'Giardiasis': 'A07.1', 'Food poisoning': 'A05.9',
    'Abrasion': 'T14.0', 'Contusion': 'T14.0', 'Laceration': 'T14.1', 'Animal bite': 'T14.1',
    'Fracture, site unspecified': 'T14.2', 'Ligament sprain': 'T14.3',
    'Tendon injury': 'T14.6', 'Injury, site unspecified': 'T14.9', 'Snake bite': 'T63.0',
    'Immunisation': 'Z26.9', 'Vernal keratoconjunctivitis': 'H16.2',
}
for n, code in KNOWN.items():
    check(f'{n} is {code}', A.DIAGNOSIS_CODES.get(n) == code, str(A.DIAGNOSIS_CODES.get(n)))

# =====================================================================
print('\n--- 3. Level of care follows the STG ---')
LEVELS = {  # STG treatment tables: health-centre treatment = Primary; hospital or refer = Secondary
    'Hypertension': 'Primary', 'Asthma': 'Primary', 'Acute otitis media': 'Primary',
    'Chronic suppurative otitis media (CSOM)': 'Secondary', 'Diabetic ketoacidosis (DKA)': 'Secondary',
    'Hypothyroidism': 'Secondary', 'Glaucoma': 'Secondary', 'Trachoma': 'Primary',
    'Acute appendicitis': 'Secondary', 'Schizophrenia': 'Primary', 'Parkinsonism': 'Secondary',
    'Acute kidney injury': 'Secondary', 'Malaria': 'Secondary', 'Scabies': 'Primary',
}
for n, lvl in LEVELS.items():
    check(f'{n}: {lvl}', A.DIAGNOSIS_LEVELS.get(n) == lvl, str(A.DIAGNOSIS_LEVELS.get(n)))

# =====================================================================
print('\n--- 4. Nothing recorded before today is lost ---')
src = open(os.path.join(ROOT, 'app.py')).read()
OCTOBER = ['HTN', 'DM', 'TB', 'CCF', 'COPD', 'CVA', 'TIA', 'URTI', 'UTI', 'PUD', 'Common cold',
           'Abscess', 'Abscess, site unspecified', 'Injury', 'Acne', 'Dog bite', 'Typhoid',
           'Viral rhinitis / common cold', 'STI (MUS, VDS, herpes genitalia, syphilis etc)']
for old in OCTOBER:
    now = A._canonical_diagnosis(old)
    check(f'a record saying "{old}" counts as {now}, which is on the list', now in A.DIAGNOSES_OPTIONS)
    check(f'...and carries its code ({A._diagnosis_code(old)})', A._diagnosis_code(old) is not None)
check('a chain of renames lands on the last name ("Abscess" -> October -> November)',
      A._canonical_diagnosis('Abscess') == 'Furuncle, carbuncle and skin abscess')
check('case and spacing do not matter', A._canonical_diagnosis('  HYPERTENSION ') == 'Hypertension'
      and A._diagnosis_code('influenza') == 'J11.1')
check('wording off the list is left as typed, with no code',
      A._canonical_diagnosis('Sore left knee') == 'Sore left knee'
      and A._diagnosis_code('Sore left knee') is None)
check('the retired IBD wording offers the two diseases it was split into',
      {"Crohn's disease", 'Ulcerative colitis'} <= set(A._diagnosis_matches('inflammatory bowel disease')[:3]))

# =====================================================================
print('\n--- 5. Finding an entry ---')
FIND = {'htn': 'Hypertension', 'I10': 'Hypertension', 'j06': 'Upper respiratory tract infection (URTI)',
        'tb': 'Pulmonary tuberculosis (TB)', 'abscess': 'Furuncle, carbuncle and skin abscess',
        'bilharzia': 'Schistosomiasis', 'quinsy': 'Peritonsillar abscess',
        'roundworm': 'Ascariasis (roundworm)', 'pep': 'HIV post-exposure prophylaxis (PEP)',
        'sti': 'STI, unspecified', 'flu': 'Influenza'}
for typed, want in FIND.items():
    got = A._diagnosis_matches(typed)
    check(f'"{typed}" puts {want} first', got[:1] == [want], str(got[:3]))
got = A._diagnosis_matches('pneumonia')
check('equal matches: primary-care pneumonia before hospital-level aspiration pneumonia',
      got.index('Pneumonia') < got.index('Aspiration pneumonia'), str(got))
r = c.get('/api/diagnoses?query=htn')
check('the plain answer is still a list of names', json.loads(r.data)[0] == 'Hypertension')
d = json.loads(c.get('/api/diagnoses?query=htn&detail=1').data)
check('the detailed answer gives the code and level',
      d[0] == {'name': 'Hypertension', 'code': 'I10', 'level': 'Primary'}, str(d[:1]))
page = c.get('/dispense').data.decode()
check('the dispensing form asks for the detailed answer', 'detail=1' in page and 'o.label' in page)

# =====================================================================
print('\n--- 6. The code goes with the record ---')
meds.insert_one({'name': 'Paracetamol, 500 mg', 'balance': 100, 'batch': 'B', 'price': 1.0,
                 'pack_size': 100, 'pack_price': 100.0, 'expiry_date': '2030-01-01',
                 'schedule': 'not controlled'})
c.post('/dispense', data={
    'patient': 'Thabo', 'company': 'C', 'position': 'P', 'birth_decade': '1980', 'gender': 'M',
    'outcome': 'Sick leave', 'outcome_days': '2', 'diagnoses': ['Hypertension', 'URTI', 'sore knee'],
    'prescriber': 'Dr X', 'dispenser': 'D', 'date': TODAY.strftime('%Y-%m-%d'),
    'med_names': ['Paracetamol, 500 mg'], 'quantities': ['2']}, follow_redirects=True)
tx = txs.find_one({'type': 'dispense', 'patient': 'Thabo'})
check('a dispense stores each diagnosis code, in order, None off the list',
      tx and tx.get('diagnosis_codes') == ['I10', 'J06.9', None], str(tx and tx.get('diagnosis_codes')))
check('the diagnoses themselves are kept as typed', tx and tx['diagnoses'] == ['Hypertension', 'URTI', 'sore knee'])
# an old record, written before codes existed
txs.insert_one({'type': 'dispense', 'patient': 'Old', 'med_name': 'Paracetamol, 500 mg', 'quantity': 1,
                'price': 1.0, 'diagnoses': ['HTN', 'Common cold'], 'transaction_id': 'OLD-1',
                'outcome': 'Sick leave', 'outcome_days': 1, 'timestamp': TODAY,
                'date': TODAY.strftime('%Y-%m-%d')})
check('an old record gets its codes worked out from its words',
      A._icd10_text(['HTN', 'Common cold']) == 'I10, J00')
check('wording off the list shows a dash, so the columns line up',
      A._icd10_text(['Hypertension', 'sore knee']) == 'I10, -')

day = TODAY.strftime('%Y-%m-%d')
r = c.get(f'/dispense?export=csv&start_date={day}&end_date={day}')
rows = list(csv.reader(io.StringIO(r.data.decode()))) if r.status_code == 200 else []
if rows and 'ICD-10' in rows[0]:
    i = rows[0].index('ICD-10')
    byp = {x[rows[0].index('Patient')]: x[i] for x in rows[1:] if x}
    check('the dispensing record download has the codes', byp.get('Thabo') == 'I10, J06.9, -'
          and byp.get('Old') == 'I10, J00', str(byp))
else:
    check('the dispensing record download has an ICD-10 column', False, str(rows[:1]))
body = c.post('/reports', data={'report_type': 'sick_leave', 'start_date': day,
                                'end_date': day}).data.decode()
check('the sick-leave register shows the codes', 'I10, J06.9, -' in body and 'I10, J00' in body)
dash = c.get('/dashboard').data.decode()
seg = dash[dash.index('Disease Trends'):]
check('the dashboard disease trends show code and level',
      '<th>ICD-10</th><th>Level</th>' in seg
      and re.search(r'<td>Hypertension</td><td>I10</td>\s*<td>Primary</td>', seg) is not None)

print(f'\n=========  {len(PASS)} passed, {len(FAIL)} failed  =========')
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    raise SystemExit(1)
