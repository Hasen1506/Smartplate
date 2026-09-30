# SmartPlate private production pilot

## Current gate

On 30 September 2026, Swiggy MCP Support explicitly confirmed that
`https://smartplate-xgxd.onrender.com/swiggy/callback` is whitelisted and the old
proposed `smartplate.app` URI was removed. The owner then tried the deployed app
again and still received “Onrender isn't whitelisted.” Approval is confirmed by
the supplied support message; successful gateway authentication remains unverified.
Keep the approved callback. Ask support to investigate the mismatch using a fresh
attempt time and the exact callback, without sending tokens or full OAuth URLs.
The app reuses dynamic clients by their exact redirect URI; the proposed domain's
registration cannot be reused for this Render URI. Do not buy or configure the
proposed domain to work around this mismatch.

See [live-core-walkthrough.md](live-core-walkthrough.md) for the implemented
real-menu week, multi-item variants/add-ons, durable reminders and local stub.

## Deployment steps

1. Review the draft PR and CI. The existing `render.yaml` remains a free, disposable demo. Select `render.production.yaml` **explicitly** as the Blueprint file when ready. Its service name is `smartplate`, so Render will attempt to apply it to the existing service. Check the target service and billed plan before syncing.
2. The production Blueprint selects one always-on paid instance with a 1 GB persistent disk at `/var/data`, SQLite at `/var/data/smartplate.db`, the exact current public URL/callback, `SMARTPLATE_SWIGGY=live`, and `SMARTPLATE_LIVE_ORDERS=off`. Preserve the current `SMARTPLATE_SECRET` on an existing service; a rotated secret will make encrypted Swiggy tokens unreadable. Set `SMARTPLATE_PUSH_CONTACT` to a real contact in Render's Environment settings; `sync: false` prompts only when a Blueprint is first created.
3. Before attaching a disk, decide whether any existing profiles matter. The current free instance's database is ephemeral; no migration is implied by the Blueprint. Export or migrate user data using an approved, private route before changing storage. Confirm the application starts with `/readyz` returning 200, then redeploy once and verify profiles, favourites, connection state and reminders survive. The disk is tied to one instance; do not scale horizontally with this SQLite design.
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
Never rotate the global `SMARTPLATE_SECRET` as an account recovery action.

Keep uncertain order attempts blocked until the support owner obtains provider
evidence of acceptance or non-placement. The recent-orders view and stored order
IDs aid investigation. There is no automatic matching by names/totals and no
public "retry anyway" control. A record without a provider ID requires provider
support reconciliation before any manual database correction. Preserve an incident
record and backup before correcting financial attempt state.

## Release boundary

This release prepares a small supervised pilot. Real-account payloads and an
actual supervised purchase remain unverified. The connected planner uses provider
menus; the older sample planner remains an explicitly labelled optional demo.
Persistent reminder jobs improve restart behaviour but still need an awake host.
Single-process SQLite, browser-held profile codes, hosted backup restoration and
support for uncertain purchases remain constraints on a wider public launch.
Do not enable live placement solely because the callback is approved.
