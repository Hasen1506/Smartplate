# Invite-beta release contract

SmartPlate's public mode is an invite-only planner. It discovers restaurants and
menus through each user's Swiggy account and selected saved address. The final
order happens in Swiggy. There is no live cart mutation, payment, scheduled order,
or automatic order placement in this release.

## Configure the two services

1. Create a Supabase project. In Authentication, enable email sign-in and change
   the email template to include `{{ .Token }}` so the app's code-entry screen
   receives an OTP. Set the site URL to the Render origin. Keep the invite list
   in `SMARTPLATE_INVITED_EMAILS` **and invite those users in Supabase**. The
   app refuses other emails before asking Supabase to send a code and sets
   `create_user=false` on OTP requests, so the form cannot open signup.
2. Get a PostgreSQL connection string from Supabase **Connect**. For an IPv4
   Render service, use the session pooler URL. Put it in Render's `DATABASE_URL`
   secret. Do not use the service-role key in browser code.
3. Deploy `render.yaml` as a Render Blueprint and provide `DATABASE_URL`,
   `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SMARTPLATE_INVITED_EMAILS`,
   and `SMARTPLATE_PUBLIC_BASE_URL`. The last value must be the exact HTTPS
   origin of this Render service, with no trailing slash. Render generates
   `SMARTPLATE_SESSION_SECRET`; preserve it across deploys because it signs
   sessions and encrypts Swiggy access tokens.
4. Register the callback `https://YOUR-ORIGIN/api/swiggy/callback` with Swiggy
   for production access. Swiggy's delegated OAuth uses dynamic client
   registration and PKCE. Its production endpoint is access-reviewed; request
   staging access and validate there before inviting users.

The app fails at startup when required public configuration is absent. Demo
mode must be selected explicitly with `SMARTPLATE_MODE=demo` and should remain
on a private port.

## Release verification

- An invited user can sign in, reconnect, select a saved address, search open
  restaurants, browse a menu, select restaurants, set budget and safety filters,
  choose meal slots from an empty week, and build a plan. An unconfigured user
  never receives an auto-filled plan.
- A second invited account cannot read the first user's profile, plans or
  schedule. Unauthenticated API calls return 401.
- Closing or changing a Swiggy address clears its cached restaurant choices.
  The app shows no fabricated restaurant if Swiggy responds with none or fails.
- A restricted dish with missing allergen or relevant medical data is excluded.
  Missing dish ratings are not replaced with restaurant ratings; a known
  restaurant rating is still required for the chosen rating floor.
  Listed item prices are labeled as estimates; no tax, fee, surge, nutrition or
  carbon figures are invented from the Swiggy browse response.
- Public execution and simulated-receipt routes do not place or claim orders.
  The handoff opens Swiggy for a fresh review.

The automated tests cover these contracts with mocked provider payloads, but
they cannot prove the live OAuth registration, tool response shape, service
availability, or Render↔Supabase network path without those external accounts.
Keep the invite list small until that staging flow is green.

Source contracts: [Swiggy delegated OAuth](https://mcp.swiggy.com/builders/docs/start/enterprise/delegated-auth/),
[restaurant search](https://mcp.swiggy.com/builders/docs/reference/food/search_restaurants/),
[menu browse](https://mcp.swiggy.com/builders/docs/reference/food/get_restaurant_menu/),
[Supabase OTP](https://supabase.com/docs/guides/auth/auth-email-passwordless),
[Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Render Flask deployment](https://render.com/docs/deploy-flask).
