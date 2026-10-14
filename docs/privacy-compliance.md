# Privacy and security: what Ziggy does, and why (Oct 2026)

Plain-English record of the privacy and security work for grill critiques #32, #35, #43,
#44, #45, #62, #63, #64 and #65, with the law behind it and the decisions only the founder
can take.

## The law, in one screen

India's **Digital Personal Data Protection Act, 2023** and the **DPDP Rules, 2025**
(notified 13/14 Nov 2025, G.S.R. 846(E); corrigendum G.S.R. 892(E), 10 Dec 2025).

| Duty | Where | When it binds |
|---|---|---|
| Notice before consent: itemised data, each purpose, how to withdraw, use rights, complain to the Board; stands on its own, plain language | Act s.5, Rule 3 | 13 May 2027 |
| Consent free, specific, informed, affirmative; limited to what the purpose needs; fiduciary must prove it | Act s.6(1), 6(10) | 13 May 2027 |
| Withdraw as easily as given; processing stops | Act s.6(4)-(6) | 13 May 2027 |
| Erase on withdrawal or when purpose is over; processors too | Act s.8(7) | 13 May 2027 |
| Security safeguards: encryption, access control, logs, backups, contracts with processors | Act s.8(5), Rule 6 | 13 May 2027 |
| Breach: tell each affected person and the Board (72 h detailed report) | Act s.8(6), Rule 7 | 13 May 2027 |
| Keep processing logs at least one year | Rule 8(3) | 13 May 2027 |
| Publish a contact who answers data questions | Act s.8(9), Rule 9 | 13 May 2027 |
| Grievances answered within 90 days | Act s.13, Rule 14(3) | 13 May 2027 |
| Children (<18): verifiable parental consent, no tracking | Act s.9, Rule 10 | 13 May 2027 |
| Transfers abroad allowed unless the Government restricts a country | Act s.16, Rule 15 | 13 May 2027 |
| Penalties up to ₹250 crore (security failure) | Act Schedule | with the duties |

As of 14 Oct 2026 no instrument has changed these dates. MeitY floated shortening the
18-month runway to 12 months (Jan 2026 consultation); it has not been notified. The
5 Oct 2026 Removal of Difficulties Order (S.O. 5458(E)) only corrects wording in s.9(1)
and s.10(2)(c)(ii).

Sources (official): DPDP Act 2023
<https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf>;
DPDP Rules 2025 <https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf>;
corrigendum <https://www.meity.gov.in/static/uploads/2025/12/3c7ebbae0e5456f493f486e6845df86b.pdf>;
PIB note, 17 Nov 2025 <https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190655>;
PIB release, 14 Nov 2025 <https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014>.
Status of the timeline: <https://dpdprules.org/blog/dpdp-18-months-to-12-months-proposal>
(secondary, checked 14 Oct 2026).

## What the app does now

- **Notice:** `/privacy` (full notice) and `/terms`; `GET /api/privacy/notice` gives the
  same itemised purposes to the app. Linked from sign-up, the Me tab and `/credits`.
- **Consent per purpose** (`smartplate/privacy.py`, table `consent_log`, append-only):
  - `account` (required): sign-up refuses without the "I'm 18 or older and I agree" tick.
  - `health` (optional): medical conditions and body stats are refused unless ticked.
  - `learning` (optional): rating reasons are neither stored nor used without it.
  - `swiggy` (optional): asked right before connecting; `POST .../swiggy/connect` and
    `GET .../swiggy/order-history` answer `409 consent_required` with the notice until
    the person says yes. Other code that imports order history should call
    `privacy.require(user_id, "swiggy")` first.
  - Profiles made before this (and shared samples) keep their earlier use (s.5(2)) except
    Swiggy, which always needs a recorded "yes".
- **Withdraw** in Me → Privacy (`POST /api/user/<id>/privacy`): switching off erases what
  the purpose used (medical + body; rating reasons; Swiggy token, menus, cart state).
- **Delete my data:** Account & devices → Your data → Delete (existing `DELETE
  /api/user/<id>`), now also removes every browser session's hold on the profile and
  writes a dated `erasure` record. That record (profile number, purposes, dates; no name,
  diet or health data) is purged one year later (`privacy.purge_expired`, run at start-up).
- **Download my data:** unchanged (`/api/user/<id>/data.json`).
- **No raw Swiggy replies kept or logged:** reply *shapes* only (unchanged), and every
  Swiggy error message and the Swiggy problem log go through `privacy.redact` (emails,
  phone numbers, coordinates, PIN codes, tokens, address fields masked). A test fails if a
  phone number or real e-mail appears in `tests/fixtures/swiggy*.json` or `docs/vendor/`.
- **Processors written down** on `/privacy`: Render (Singapore), Neon on AWS
  ap-southeast-1 (Singapore), browser push services. Swiggy is a separate fiduciary.
  Open-Meteo gets the city only. No analytics, no ad trackers, no LLM (`AGENT_BRAIN`
  stays `deterministic`; turning on an LLM brain needs a DPA and a notice update first).

## Security changes

- **#62 identity:** an HttpOnly, Secure (on HTTPS), SameSite=Lax session cookie, signed
  with `SMARTPLATE_SECRET`, stored server-side as a SHA-256, re-issued every day of use,
  ended after 60 idle days (`smartplate/websession.py`). Writes that rely on it need the
  CSRF token (`X-CSRF-Token`, double-submit). The app no longer keeps any profile key in
  localStorage: on first load it moves an old key into the session (`/api/session/claim`)
  and forgets it. Recovery codes are shown once (on creation or rotation). Header keys
  still work for old tabs and scripts.
- **#63 rate limits:** token buckets in the database (`rate_buckets`), shared by workers
  and kept across restarts, same SQL on SQLite and Postgres.
- **#64 encryption key:** production refuses to start without a strong
  `SMARTPLATE_SECRET`; rotation via `SMARTPLATE_SECRET_PREVIOUS`; the old database-kept
  trial key is migrated away and deleted; generated keys (VAPID) are sealed at rest. See
  docs/production-deployment.md, "The encryption key".
- **#65 errors:** only Ziggy's own messages reach the client; anything else gets a
  generic sentence plus a request id (`X-Request-ID`), and the detail is logged.
- **#32:** `/api/meta` no longer publishes the internal feature catalogue (now
  docs/feature-map.md).

## Decisions only the founder can take

1. **Grievance / data contact: decided (Oct 2026).** The operator values are below. They are
   read from Render → Environment (they are not in the repo's Blueprint); the privacy and terms
   pages fill them in at request time. Until all three are set on the service, the pages fall
   back to "will be published before launch", which is not enough once Rule 9 binds
   (13 May 2027) or before public users.

   | Variable | Value | Shown on |
   |---|---|---|
   | `SMARTPLATE_OPERATOR` | `VijeyaHasen K H` | /privacy, /terms ("who runs Ziggy") |
   | `SMARTPLATE_GRIEVANCE_NAME` | `VijeyaHasen K H` | /privacy (grievance officer) |
   | `SMARTPLATE_PRIVACY_CONTACT` | `hasenhamesh15@gmail.com` | /privacy, /terms (mailto link) |
   | `SMARTPLATE_PUSH_CONTACT` | `mailto:hasenhamesh15@gmail.com` | Web Push VAPID contact (not shown to users) |

   Check after a deploy: open `/privacy` and confirm the name and e-mail appear instead of the
   placeholder sentence. A dedicated address (e.g. `privacy@<your domain>`) can replace the
   Gmail address later; change only the variable.
2. **Retention of unused profiles.** Today: kept until the person deletes them. Ziggy has
   no e-mail, so it cannot give the 48-hour warning Rule 8(2) describes for the large
   platforms it applies to. Suggested: delete profiles unused for 12 months, announced on
   the privacy page. Not built until you decide.
3. **Neon backups.** Free plan keeps 6 hours of history
   (<https://neon.com/docs/introduction/history-window>), so deleted data is gone from
   backups within 6 hours. If you move to a paid plan (up to 7 or 30 days) or take manual
   snapshots, update `SMARTPLATE_BACKUP_WINDOW` so the notice stays true.
4. **Processor contracts.** Accept Render's and Neon's DPAs (both offer one online) and
   keep copies; Rule 6(1)(f) expects a contract with each processor.
5. **Recipe dataset** (#35): kept by your decision; provenance and credits are in
   `smartplate/data/README.md` and `/credits`. Asking Archana's Kitchen for written
   permission remains the clean long-term fix.
6. **Languages.** s.5(3) and 6(3) require the notice to be available in English or any
   Eighth-Schedule language on request; a Tamil version is the obvious next one.
