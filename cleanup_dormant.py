#!/usr/bin/env python3
"""Retire medications that have had no movement for a long time.

WHY THIS DOES NOT SIMPLY DELETE
-------------------------------
Deleting a medication does not delete its history. Transactions reference a
medication BY NAME, so removing the medication record orphans every transaction
it ever had:

  * Reports that back-calculate balances lose the current balance and unit price
    they work from, so a period that contained the item stops reconciling.
  * The controlled drug register is built by looking up medications with
    schedule 'controlled'. Delete one and its historical register entries vanish
    entirely — that is a statutory record.
  * A medication with stock on hand would take that stock with it, silently,
    with no write-off transaction behind it. That is exactly the hole that was
    closed in Edit Medication.

So the default action is ARCHIVE: the item stops appearing in stock lists, the
order request, the dashboard and the stock-take sheet, while every transaction
it ever had stays exactly where it is and every historical report still
reconciles. It is also reversible.

Hard deletion is available, but only for records that carry no history at all
and no stock — items that were created and never used. Anything else is
refused, by name, with the reason.

USAGE
-----
    python cleanup_dormant.py                      # dry run, changes nothing
    python cleanup_dormant.py --days 365           # choose the dormancy window
    python cleanup_dormant.py --apply --archive    # archive the safe candidates
    python cleanup_dormant.py --apply --delete     # delete never-used records only
    python cleanup_dormant.py --list-archived
    python cleanup_dormant.py --apply --restore "Item name"

Set MONGO_URI in the environment, as the app does.
"""
import argparse
import os
import sys
from typing import Any
from datetime import datetime, timedelta

from pymongo import MongoClient

MOVEMENT_TYPES = ['dispense', 'receive', 'adjustment', 'opening_balance',
                  'expiry_removal', 'unmet_demand']


def classify(med, last_ts, tx_count, cutoff):
    """Return (action, reason) for one medication."""
    name = med.get('name', '')
    balance = med.get('balance', 0) or 0
    schedule = (med.get('schedule') or '').lower()

    if last_ts and last_ts >= cutoff:
        return 'keep', f'active — last movement {last_ts:%Y-%m-%d}'
    if schedule == 'controlled':
        return 'blocked', 'controlled drug — its register is a statutory record'
    if balance > 0:
        return 'blocked', (f'{balance} unit(s) still on hand — write the stock off '
                           f'first (Reports → Expired Stock on Shelf) or leave it')
    if tx_count == 0:
        return 'deletable', 'never used, no stock — safe to delete outright'
    return 'archivable', (f'no movement since '
                          f'{last_ts:%Y-%m-%d}' if last_ts else 'no movement ever recorded')


def gather(db, cutoff):
    meds = list(db['medications'].find({}))
    tx = db['transactions']
    rows = []
    for m in meds:
        name = m.get('name')
        last = list(tx.find({'med_name': name, 'type': {'$in': MOVEMENT_TYPES}},
                            {'_id': 0, 'timestamp': 1}).sort('timestamp', -1).limit(1))
        last_ts = last[0]['timestamp'] if last else None
        count = tx.count_documents({'med_name': name})
        action, reason = classify(m, last_ts, count, cutoff)
        rows.append({'name': name, 'balance': m.get('balance', 0) or 0,
                     'archived': bool(m.get('archived')),
                     'last': last_ts, 'tx': count,
                     'action': action, 'reason': reason})
    return sorted(rows, key=lambda r: (r['action'], (r['name'] or '').lower()))


def show(rows, days, applying):
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r['action'], []).append(r)
    print()
    print('=' * 78)
    print(f'DORMANT MEDICATION REVIEW — no movement in {days} days'
          f'{"" if applying else "   (DRY RUN — nothing will be changed)"}')
    print('=' * 78)
    titles = {
        'archivable': 'WOULD ARCHIVE — hidden from working lists, history kept',
        'deletable': 'WOULD DELETE — never used, no stock, no history',
        'blocked': 'SKIPPED — not safe to touch',
        'keep': 'ACTIVE — left alone',
    }
    for key in ['archivable', 'deletable', 'blocked', 'keep']:
        rs = buckets.get(key, [])
        print(f'\n{titles[key]}  ({len(rs)})')
        print('-' * 78)
        if not rs:
            print('  (none)')
            continue
        if key == 'keep':
            print(f'  {len(rs)} medication(s) still in use.')
            continue
        for r in rs:
            last = r['last'].strftime('%Y-%m-%d') if r['last'] else 'never'
            flag = ' [already archived]' if r['archived'] else ''
            print(f'  {r["name"][:44]:<46} bal {r["balance"]:>7}  '
                  f'last {last:<10} tx {r["tx"]:>5}{flag}')
            print(f'      -> {r["reason"]}')
    print()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--days', type=int, default=365,
                   help='dormancy window in days (default 365)')
    p.add_argument('--apply', action='store_true',
                   help='actually make changes; without it this is a dry run')
    p.add_argument('--archive', action='store_true',
                   help='archive dormant items (the safe default action)')
    p.add_argument('--delete', action='store_true',
                   help='hard-delete records that have NO history and NO stock')
    p.add_argument('--restore', metavar='NAME',
                   help='un-archive one medication by name')
    p.add_argument('--list-archived', action='store_true')
    p.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    args = p.parse_args()

    uri = os.environ.get('MONGO_URI')
    if not uri:
        sys.exit('MONGO_URI is not set. Export it the way the app does, then re-run.')
    db: Any = MongoClient(uri, serverSelectionTimeoutMS=8000)['pharmacy_db']

    if args.list_archived:
        rows = list(db['medications'].find({'archived': True}, {'_id': 0, 'name': 1,
                                                               'balance': 1}))
        print(f'\n{len(rows)} archived medication(s):')
        for r in sorted(rows, key=lambda r: r['name'].lower()):
            print(f'  {r["name"]}  (balance {r.get("balance", 0)})')
        return

    if args.restore:
        res = db['medications'].update_one({'name': args.restore},
                                           {'$unset': {'archived': ''}})
        if res.matched_count:
            print(f'Restored "{args.restore}" to the working lists.')
        else:
            print(f'No medication named "{args.restore}".')
        return

    cutoff = datetime.utcnow() - timedelta(days=args.days)
    rows = gather(db, cutoff)
    show(rows, args.days, args.apply)

    if not args.apply:
        print('Dry run only. Re-run with --apply --archive (or --delete) to act.')
        print('Read the SKIPPED list first: those are the ones that need a decision.')
        return
    if not (args.archive or args.delete):
        sys.exit('--apply needs either --archive or --delete.')

    targets = [r for r in rows
               if (args.archive and r['action'] == 'archivable')
               or (args.delete and r['action'] == 'deletable')]
    if args.delete and args.archive:
        targets = [r for r in rows if r['action'] in ('archivable', 'deletable')]
    if not targets:
        print('Nothing to do.')
        return

    verb = 'archive/delete' if (args.archive and args.delete) else (
        'archive' if args.archive else 'delete')
    if not args.yes:
        reply = input(f'{verb.capitalize()} {len(targets)} medication(s)? '
                      f'Type "yes" to proceed: ').strip().lower()
        if reply != 'yes':
            print('Aborted. Nothing was changed.')
            return

    archived = deleted = 0
    for r in targets:
        if r['action'] == 'deletable' and args.delete:
            db['medications'].delete_one({'name': r['name']})
            deleted += 1
            what = 'deleted'
        else:
            db['medications'].update_one({'name': r['name']},
                                         {'$set': {'archived': True,
                                                   'archived_at': datetime.utcnow()}})
            archived += 1
            what = 'archived'
        try:
            db['audit_log'].insert_one({
                'timestamp': datetime.utcnow(), 'action': 'DELETE' if what == 'deleted' else 'UPDATE',
                'target_type': 'medication', 'target_id': r['name'],
                'user': 'cleanup_dormant script', 'ip': None,
                'changes': f'{what} — no movement in {args.days} days '
                           f'(balance {r["balance"]}, {r["tx"]} transaction(s))',
            })
        except Exception as e:
            print(f'  warning: audit entry failed for {r["name"]}: {e}')

    print(f'\nDone. {archived} archived, {deleted} deleted.')
    if archived:
        print('Archived items keep all their history and still appear in past '
              'reports. Use --restore "Name" to bring one back.')


if __name__ == '__main__':
    main()
