# NMC Pharmacy — Health Records (with the Android app)

The NMC pharmacy web app (LD-HSE/NMC/HRD/6.1.3.3): dispensing, receiving, stock take,
internal issues, requests, loans, registers and reports. Flask, MongoDB Atlas, deployed on Vercel.

This copy works with the **NMC Stock Take** phone app
([hlaolil/NMC_StockTake_App](https://github.com/hlaolil/NMC_StockTake_App)):

- **Stock take:** open a stock take for all or some shelf groups (cycle counting); the phone gets the
  count sheet in shelf order, counts (sums, packs, barcode scanning, label reading, short-dated
  warnings) and sends the counts back. They wait on the Stock Take page until posted.
- **Receiving:** upload a delivery's invoice as usual; the phone gets its lines, checks each box
  (quantity, batch, expiry, barcode) and sends them back, in parts if you like. Differences from
  the invoice are marked in red. Nothing is received until Add These Lines is pressed.
- **Linking a phone:** Stock Take page > *link the phone app* (or `/phone-link`) shows a QR code
  to scan once. Phones can be unlinked there.

## Deploying on Vercel

Import this repository as a new Vercel project (Python, `app.py`). Set these environment variables (copy them from the existing project):

| Variable | What |
|---|---|
| `SECRET_KEY` | a long random string (required) |
| `MONGODB_URI` | the MongoDB Atlas connection string |
| `ADMIN_PASSWORD` | the password that opens the Register page, for adding users |
| `PHARMACY_UTC_OFFSET` | optional; hours from UTC for the pharmacy's clock (default 2, Lesotho) |

`requirements.txt` includes `qrcode` for the phone-link QR code; if it is missing the page shows the
code as text to paste instead.

## Tests

`python3 tests/run_all.py` (or `python3 tests/run_all.py stocktake` for one area). The tests use a
fake database (mongomock) and never touch the live one; `tests/` is not deployed (`.vercelignore`).
