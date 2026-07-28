# Single-Channel Ratio Refresh Fix Design

## Problem

The Channels page labels each row action as “刷新倍率”, but the manual click path
currently calls `matchChannelUpstreamBinding(..., true)`. The final boolean adds
`?refresh=1` to the request. The backend interprets that query parameter as
`force_refresh=True` for the protected main-site channel-key read.

As a result, clicking a single row does target only that channel, but it bypasses the
channel's persisted key and in-memory key value. It then calls the main site's protected
`POST /api/channel/:id/key` endpoint. An expired browser session or 2FA proof therefore
turns a normal ratio refresh into a key-read error even when a valid key is already
stored locally.

## Intended Behavior

- “刷新倍率” operates on the clicked channel only.
- A persisted channel key is reused when available.
- The protected main-site key endpoint is called only when that channel has no persisted
  or otherwise usable key.
- Upstream user-token matching and group/ratio retrieval still run on every click, so
  channel group and ratio data remain real-time.
- No new business cache is introduced.
- The existing two-second safety interval for protected main-site key reads remains in
  place.
- A failed refresh keeps the most recent successful ratio and reports the error on the
  affected row.

## Design

### Frontend Request

Change the manual row action in `ChannelsPage.tsx` to call
`api.matchChannelUpstreamBinding(siteId, channelId)` without the force-refresh boolean.
The endpoint remains channel-scoped because both the admin-site ID and channel ID stay in
the URL.

The existing API helper retains its optional `forceRefresh` argument for explicit key
rotation or future security-verification workflows, but the ratio-refresh button must not
use it.

### Backend Data Flow

No backend behavior change is required. With `force_refresh=False`, the current backend
already follows the required order:

1. Use the main channel detail when it contains an unmasked key.
2. Otherwise reuse the key persisted in `admin_channel_keys`.
3. Otherwise reuse the short-lived in-process key value when available.
4. Only then refresh the browser session if needed and call the protected main-site key
   endpoint.
5. Use the resulting key to query the configured upstream account and fetch current group
   and ratio data.

This keeps key acquisition stable while leaving group and ratio requests real-time.

### Error Handling

The existing row-scoped busy state and fallback behavior remain unchanged. If a refresh
fails and the row has a previously matched group, the UI continues displaying that group
and ratio while showing a refresh failure message for the clicked row. A failure for one
channel must not overwrite another channel's binding state.

## Testing

- Add a frontend regression proving that the manual “刷新倍率” path does not request
  forced key refresh.
- Retain backend regressions proving a persisted admin channel key bypasses the protected
  endpoint and missing keys can still reach that endpoint.
- Run the complete Python tests, frontend automatic-refresh test, TypeScript/Vite build,
  Python compile check, Docker Compose validation, and `git diff --check`.
- Restart the local service and verify the clicked channel refresh endpoint succeeds while
  the required console routes remain healthy.

## Scope

This fix does not change channel CRUD, automatic page-entry refresh, 2FA verification,
session refresh, rate limiting, upstream matching rules, API response shapes, or visual
styling.
