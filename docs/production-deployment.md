# SmartPlate private production pilot

## Current gate

On 30 September 2026, a real browser sign-in from `https://smartplate-xgxd.onrender.com/` reached Swiggy with `redirect_uri=https://smartplate-xgxd.onrender.com/swiggy/callback`. Swiggy displayed **“Oops, Onrender isn't whitelisted yet”** and **“This client isn't supported for Swiggy MCP sign-in yet.”** This is direct evidence that this deployed origin cannot sign in now. The earlier approval email did not state the approved URI. `smartplate.app` in the submission document was a proposed domain that the SmartPlate owner does not control. Do not put it into OAuth configuration.

Reply to `builders@swiggy.in` in the existing approval thread with:

> Thank you for whitelisting Smart Plate. Our actual deployed HTTPS callback is `https://smartplate-xgxd.onrender.com/swiggy/callback`. A sign-in attempt on 30 September 2026 returned “Onrender isn't whitelisted yet / This client isn't supported for Swiggy MCP sign-in yet.” Please confirm the **exact** URI and client/origin you approved, and allow this Render callback for Smart Plate's Food MCP integration. If onrender.com is ineligible, please tell us the domain requirements so we can register a domain we own. Please also confirm which Food tools and staging/production order permissions are enabled. We can provide an approximate UTC attempt time and request ID if your engineers need it.

No Swiggy account, OAuth code, cart, payment, or real order was used in the observed failure. Send this request only from the account that received approval; never share tokens or a full authorization URL with its PKCE state.

## Deployment steps

1. Review the draft PR and CI. The existing `render.yaml` remains a free, disposable demo. Select `render.production.yaml` **explicitly** as the Blueprint file when ready. Its service name is `smartplate`, so Render will attempt to apply it to the existing service. Check the target service and billed plan before syncing.
2. The production Blueprint selects one always-on paid instance with a 1 GB persistent disk at `/var/data`, SQLite at `/var/data/smartplate.db`, the exact current public URL/callback, `SMARTPLATE_SWIGGY=live`, and `SMARTPLATE_LIVE_ORDERS=off`. Preserve the current `SMARTPLATE_SECRET` on an existing service; to change it, follow “The encryption key” below (an old key must stay in `SMARTPLATE_SECRET_PREVIOUS` until the restart has re-sealed every token). Set `SMARTPLATE_PUSH_CONTACT` to a real contact in Render's Environment settings; `sync: false` prompts only when a Blueprint is first created.
3. Before attaching a disk, decide whether any existing profiles matter. The current free instance's database is ephemeral; no migration is implied by the Blueprint. Export or migrate user data using an approved, private route before changing storage. Confirm the application starts with `/readyz` returning 200, then redeploy once and verify profiles, favourites, connection state and reminders survive. The disk is tied to one instance; do not scale horizontally with this SQLite design.
   A no-cost alternative to the paid disk is Postgres through `DATABASE_URL` (Neon's free tier; README, “Keep your data”). It needs no disk, survives redeploys, and `scripts/sqlite_to_postgres.py` copies an existing SQLite database into it.
4. Back up SQLite with `SMARTPLATE_DB=/var/data/smartplate.db python scripts/backup_sqlite.py /secure/staging/smartplate-YYYYMMDD.db`. Schedule this outside the web process, transfer the snapshot to encrypted off-host storage, restrict access, retain the matching `SMARTPLATE_SECRET` separately, and restore a snapshot to a throwaway service to prove it works. The script creates a consistent snapshot and runs SQLite integrity checking; it does not schedule, encrypt, or transfer backups.
5. After Swiggy confirms the exact callback, use a pilot account to complete sign-in and `tools/list`; select a real address; search for a restaurant in its service area; open its current menu; review one exact item; add it to the Swiggy cart; verify total and offered payments. Exercise empty results, address change, token expiry, 401/429, variants, stock changes, unavailable COD, and a cart modified in Swiggy. Capture redacted provider responses and compare them with the documented schemas.
6. For a supervised order pilot only, confirm Swiggy explicitly permits Food placement and its COD response matches the adapter. Set `SMARTPLATE_LIVE_ORDERS=on` only after a support owner, refund/cancellation route, backup restore, and staging test exist. Start with a small allowed order and a single user. Every real order requires a fresh approval that displays full address, item, total and payment method. If the response is uncertain, inspect Swiggy orders and contact support; do not retry the same cart blindly. UPI placement is intentionally unsupported in SmartPlate; users can use Swiggy checkout for those payments.
7. Monitor `/readyz`, failed OAuth, provider errors, unknown order attempts, backup age and disk fill. Roll back code by redeploying the prior known-good commit while retaining the persistent disk and secret. Do not roll back the database file over live orders without an incident plan.

## Data controls and incident recovery

Private profiles can download their local data and permanently delete their active
database records from **More → Profiles**. Exports omit tokens, password/device
hashes and push encryption material. Deletion also removes new published community
templates linked to that profile, its pending OAuth/checkout state, subscriptions
and order attempts. It does not cancel orders or delete an independent Swiggy
account. Legacy public templates without an author ID need operator review rather
than guessing their owner from a name. Document backup retention and account
deletion handling; an old restore can resurrect deleted data and revoked credentials.

Users can replace a compromised recovery code from the same screen. Rotation
invalidates earlier profile codes and device tokens and clears pending approvals.
Password sign-in remains available; change a compromised password separately.
Never rotate the global `SMARTPLATE_SECRET` as an account recovery action (it signs everyone out of their browser sessions).

Keep uncertain order attempts blocked until the support owner obtains provider
evidence of acceptance or non-placement. The recent-orders view and stored order
IDs aid investigation. There is no automatic matching by names/totals and no
public "retry anyway" control. A record without a provider ID requires provider
support reconciliation before any manual database correction. Preserve an incident
record and backup before correcting financial attempt state.

## The encryption key (`SMARTPLATE_SECRET`)

**What it is.** One long random password that only the server knows. Ziggy uses it to
(1) encrypt each person's Swiggy sign-in token before saving it in the database, (2) encrypt
the Web Push key, and (3) sign the browser session cookie. If someone ever copied the
database (a leaked backup, a Neon account breach), the tokens in it are useless without this
key. That only works if the key is **not** stored in the same database, so it lives in
Render's environment settings instead.

**What Ziggy does about it.** In production (on Render, or with a non-local
`DATABASE_URL`) the app refuses to start when `SMARTPLATE_SECRET` is missing, shorter than
32 characters, or a well-known example value such as `changeme`. The deploy log then says
exactly that. `SMARTPLATE_VAULT_KEY` is accepted as another name for the same setting.

**How to set it on Render (once).**
1. Open <https://dashboard.render.com>, click the Ziggy web service, then **Environment**.
2. If `SMARTPLATE_SECRET` is already listed (render.yaml's `generateValue: true` creates it),
   leave it alone. You are done.
3. If not: click **Add Environment Variable**. Key: `SMARTPLATE_SECRET`. Value: click
   **Generate** (Render makes a random 256-bit value), or paste the output of
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
4. Click **Save, rebuild and deploy**. On start, Ziggy re-encrypts tokens that an older
   version had sealed with a key kept in the database, then deletes that database-kept key.
   Nobody is signed out of Swiggy.
5. Keep a copy of the value in your password manager. A database backup cannot be read
   without it.

**How to rotate it** (only if you think the key leaked, or once a year as hygiene).
1. In **Environment**, add `SMARTPLATE_SECRET_PREVIOUS` with the *current* value of
   `SMARTPLATE_SECRET` (several old values may be listed, comma-separated).
2. Set `SMARTPLATE_SECRET` to a new generated value. Save and deploy.
3. On start Ziggy opens every token with the old key and re-seals it with the new one.
   Browser sessions signed with the old key end, so people open Ziggy once with their
   sign-in or recovery code; Swiggy connections stay.
4. After that deploy is live, delete `SMARTPLATE_SECRET_PREVIOUS` and deploy again.

Never put the key in GitHub, in `render.yaml` as a literal value, or in a chat message.

## Release boundary

The PR prepares the app for a **small supervised pilot**, not an unqualified public commerce launch. The live provider contract remains untested until Swiggy accepts this origin. A single SQLite web process, browser-held recovery keys, best-effort in-process reminders, and a demo meal planner remain limits for wider scale. If the provider rejects `onrender.com` as a domain class, register a domain you control, point it to this service, request that **exact** new callback, and update `SMARTPLATE_PUBLIC_URL` only after approval. `smartplate.app` cannot be used without acquiring control of it.
