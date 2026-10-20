# kAIm56 multi-user + OIDC (Rauthy) — design

Date: 2026-10-07. Status: agreed (sections 1–2 reviewed; 3–4 decided at implementation start).

## Decisions
- Model A: shared household/team. All users see all instances; roles admin|user.
- IdP: Rauthy, external; issuer `https://auth.example.com/auth/v1/` (path + trailing slash, endpoints via discovery:
  device `/auth/v1/oidc/device`, end_session `/auth/v1/oidc/logout`).
- Native clients (APK, CLI, voice): OIDC device flow, proxied through the manager (works over iroh).
- Roles from Rauthy `roles` claim (a claim, not a scope): `kaim56_admin`, `kaim56_user`; neither -> login refused.
  Display name: `preferred_username`, else `name`, else `email`.
- user = use, not configure: chat, start/stop instances, apps/voice/tasks use, read logs.
  admin-only: create/delete instances, settings/policies/tools, plugin approval,
  Cloudflare/app delete+rename, keys/passwords. Default deny (unknown route -> admin).
- Attribution: chat messages and tasks carry the user's name; agent sees who speaks. Plus audit log.
- Basic auth (MANAGER_USER/PASS) only during migration, then removed.
- Architecture: manager is the OIDC broker and issues its own sessions (stdlib only, no JWT crypto).

## 1. Components
- Rauthy client `kaim56`: confidential; grants authorization_code (PKCE S256), refresh_token,
  device_code; redirect `https://<PUBLIC_HOST>/auth/callback`; scopes `openid profile email`
  (Rauthy has no `roles` scope; `roles` is in claims_supported).
  Env (/etc/kaim56.env): OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET. OIDC off when unset.
- mgr/oidc.py: cached discovery; web start/callback; device start/poll; refresh; userinfo ->
  Principal(sub, name, email, role).
- mgr/sessions.py (SQLite): token = 32 random bytes, stored as sha256 only; fields sub, name,
  role, kind web|device, device_name, rauthy refresh token, expires, last_check.
- mgr/auth.py: authenticate(req) -> Principal|None from cookie `kaim56_sid`
  (HttpOnly, Secure, SameSite=Strict) or `Authorization: Bearer`; Basic -> Principal(admin,"basic")
  while MANAGER_PASS set.
- Role check: regex table of user-allowed routes (like guestgrants); rest admin-only.
- Unchanged: guests by VM IP, app bearer tokens, lockout, origin checks.

## 2. Flows
- Web: GET /auth/login -> Rauthy authorize (state/nonce/PKCE in memory, 10 min) ->
  /auth/callback (state check, code exchange, userinfo, role) -> session + cookie -> `/`.
  /auth/logout deletes session -> Rauthy end_session.
- Device: POST /api/auth/device {device_name} -> {user_code, verification_uri_complete, handle};
  device_code stays in manager. POST /api/auth/device/poll {handle} -> pending | {token} (once).
- Lifetime: web 12 h, device 90 d, sliding. On use with last_check > 15 min: refresh at Rauthy
  (rotate), re-read userinfo/role; failure or role gone -> delete session -> 401.
- Public login/device endpoints go through lockout + rate limit; handle expires with device_code.

## 3. UI, attribution, audit
- Web UI: login page/redirect when unauthenticated; header shows user + logout.
- Admin "Sessions" view: list (user, kind, device, last use), revoke.
- Chat/task records get `by` (name; `user` is already the boolean "sent by the user" in chat messages);
  the message sent to the agent is prefixed `[name] `.
- Audit log: append-only JSONL (ts, sub, name, role, method, path, status) for every write (non-GET)
  and every login/logout/revoke. Lives next to the per-instance tool trails as `audit/access.log`
  (`.log`, so the trail readers and saddler do not count it as an instance).

## 4. Rollout and testing
- Phase 1 (this step): manager only. Basic keeps working; OIDC active only when OIDC_* set.
- Phase 2: clients (CLI `kaim56 login`, APK device login, voice `login`), then Basic removal.
- Tests: fake OIDC provider (stdlib http.server) covering discovery, code+PKCE, device pending/ok,
  refresh rotation, role missing, refresh failure -> 401, role table (user denied admin route),
  cookie flags, attribution, audit lines.
