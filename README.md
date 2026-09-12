# live-media-display

A Raspberry Pi kiosk that shows an Instagram account's follower count fullscreen and keeps the number current on its own.
It was built for the UVULITES Instagram account.

Two small programs share one file:

- `instagram_service/poll_followers.py` asks the Instagram Graph API for the follower count every 60 seconds and writes it to `instagram_service/followers.json`.
  Python 3, standard library only.
- `main.odin` reads that file once a second and draws the username, the count, and a countdown to the next update.
  Odin and raylib.

The display never touches the network and the poller never draws anything.
If the poller dies, the display keeps the last count on screen and marks it `STALE` in red.
If the display dies, the poller keeps the file current for whoever restarts it.

## Try it without an Instagram token

You need the [Odin compiler](https://odin-lang.org/), which bundles raylib.
Write a fake `followers.json` with the current time as `ts`, then run the display:

```sh
printf '{"followers_count": 12345, "username": "uvulites", "ts": %d}\n' "$(date +%s)" > instagram_service/followers.json
odin run .
```

The window goes borderless fullscreen and shows `uvulites`, `12345`, and a countdown from 60 seconds.
Leave it running.
When the countdown reaches zero the footer changes to `update due...`.
Ten seconds later it changes to `update overdue` in orange, with a red `STALE` flag in the top-left corner.
That is what the kiosk shows when the poller has stopped writing.
Press Escape to quit.

## Run it for real

### Get a token

The poller uses the Instagram API with Instagram Login, which needs an Instagram professional account and a Meta app.
In the Meta app dashboard, open **Instagram**, then **API setup with Instagram business login**, and generate a long-lived access token for the account.

### Start the poller

Put the token in `instagram_service/.env`:

```sh
IG_TOKEN=your-long-lived-token
```

Then start the poller:

```sh
python3 instagram_service/poll_followers.py
```

On first run the poller copies the token into `instagram_service/token.json` with mode `0600`.
From then on `token.json` is the source of truth, and `IG_TOKEN` is read again only if `token.json` is missing or unreadable.
The poller refreshes the token itself after 50 days, inside Instagram's 60-day expiry, so it runs for months without a human.

Every poll logs one line:

```
[2026-09-12 13:22:41] uvulites: 14 followers
```

### Start the display

On the Raspberry Pi:

```sh
./run.sh
```

Anywhere else, `odin run .` is enough.
`run.sh` adds the Mesa overrides the Pi needs (see the next section) and changes into the repository directory so `odin run` builds there.

Both programs resolve their files relative to their own location, not the working directory, so they behave the same under a service manager, cron, or a terminal.

## Raspberry Pi 4 notes

**OpenGL version.**
The Pi 4's V3D driver exposes desktop OpenGL 3.1.
raylib asks for a 3.3 core context, so window creation fails with `GLX: Failed to create context: GLXBadFBConfig`.
`run.sh` sets `MESA_GL_VERSION_OVERRIDE=3.3` and `MESA_GLSL_VERSION_OVERRIDE=330` so Mesa advertises 3.3.
The gap between 3.1 and 3.3 is covered by V3D's GLES 3.1 support.

**Startup crash guard.**
raylib's `InitWindow` does not abort when it cannot create a GL context, and calling `CloseWindow` afterwards segfaults on an uninitialized render batch.
`main.odin` checks `IsWindowReady` first and exits with a message that points at `run.sh`.

**Fullscreen timing.**
Toggling fullscreen at `InitWindow` time is silently ignored by the Pi's window manager, and the window stays under the desktop panel.
The display waits until the second frame, once the window is mapped, and then calls `ToggleBorderlessWindowed`.

**Kiosk behavior.**
The cursor is hidden.
The layout is designed in a 1920x515 space, and every size and position scales with the real screen height, so it keeps its proportions on any panel.

## The shared file

`followers.json` is the whole contract between the poller and the display:

```json
{"followers_count": 14, "username": "uvulites", "ts": 1783291297}
```

`ts` is the Unix time of the fetch.
The poller writes through a temporary file and `os.replace`, so the display never reads a half-written file.
Before the first successful read the display shows `Waiting for data...`.
After that it accepts an update only when `ts` is newer than what it already shows.
The countdown is derived from `ts` plus the poll interval on every frame, so a fresh write resets it and the two can never drift apart.

When the poller exits on a fatal error it merges an `error` message into the file and keeps the last count and its `ts`.
The display keeps the last good number on screen and shows the `STALE` flag.

`POLL_INTERVAL` is defined in both programs: the Python default and the Odin constant.
Keep them equal or the countdown will be wrong.

## What the poller does when a request fails

- **Rate limit (429).**
  Double the interval, up to 8 minutes.
  Honor `Retry-After` up to an hour.
- **Auth error (400 or 401).**
  Force a token refresh, double the interval, and count the failure.
  After three consecutive auth-failed polls the poller writes the error to `followers.json` and exits, so the display never sits silently on a dead token.
- **Refresh failure.**
  Retry after 60 seconds, then 120.
  After three consecutive failures, write the error and exit.
- **Network error or timeout.**
  Log it and keep the current interval.

A successful poll resets the cadence and clears the auth incident.
Failure counts live in memory only, never in `token.json`, so a restart retries immediately.

Exiting loudly is deliberate.
A process supervisor or a person notices an exit.
A poller that retries a dead token forever looks healthy from the outside while the display shows a number from last week.

## Configuration

Every setting is an environment variable.
The real environment wins, then `instagram_service/.env`, which is one `KEY=VALUE` per line with optional `export`, quotes, and `#` comments.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IG_TOKEN` | | Long-lived token used to seed `token.json`. Needed on first run only. |
| `IG_API_VERSION` | `v22.0` | Graph API version in the request path. |
| `OUT_PATH` | `instagram_service/followers.json` | Where the count is written. |
| `TOKEN_PATH` | `instagram_service/token.json` | Where the token is persisted. |
| `POLL_INTERVAL` | `60` | Seconds between polls. Must match `POLL_INTERVAL` in `main.odin`. |
| `MAX_INTERVAL` | `480` | Ceiling for the backed-off interval, in seconds. |
| `REFRESH_AFTER_DAYS` | `50` | Token age that triggers a refresh. |
| `REFRESH_RETRY_WAIT` | `60` | First retry delay after a failed refresh, in seconds. Doubles each failure. |
| `MAX_REFRESH_FAILURES` | `3` | Consecutive refresh failures before the poller exits. |
| `MAX_AUTH_FAILURES` | `3` | Consecutive auth-failed polls before the poller exits. |

`OUT_PATH` moves the file for the poller only.
The display always reads `instagram_service/followers.json` next to its own executable.

## Files

| Path | What |
| --- | --- |
| `main.odin` | The display. |
| `run.sh` | Pi launcher with the Mesa overrides. |
| `instagram_service/poll_followers.py` | The poller. |
| `instagram_service/.env` | Your token. Not committed. |
| `instagram_service/token.json` | Persisted, auto-refreshed token. Not committed. |
| `instagram_service/followers.json` | Runtime output. Not committed. |

## License

[MIT](LICENSE).
