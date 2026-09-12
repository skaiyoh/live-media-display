# live-media-display

A kiosk that shows the UVULITES Instagram follower count fullscreen on a Raspberry Pi 4 and keeps it current on its own.
Two small programs, one shared file, no dependencies beyond the Odin compiler and Python's standard library.

## What it does

A Python service polls the Instagram Graph API once a minute and writes the follower count to a JSON file.
An Odin program built on raylib reads that file once a second and draws the account name, the count in large type, and a countdown to the next update.

The screen has four states.
Before the first successful read it shows `Waiting for data...`.
With fresh data it shows the count and `next update in 42s`.
When the countdown reaches zero the footer changes to `update due...` for a ten-second grace period.
After that it changes to `update overdue` in orange and a red `STALE` flag appears in the corner, while the last good count stays on screen.
The same flag appears the moment the file becomes unreadable.

## Why two processes and a file

The poller and the display share `followers.json` rather than a socket or a shared process.
Each side can crash, restart, or be swapped out without the other noticing.
The display never touches the network, so a slow or failing API call can never stall a frame.
The poller writes through a temporary file and an atomic rename, so the display never reads a half-written payload.

The display trusts the file's timestamp over its own clock.
It accepts an update only when the timestamp is newer than the one it already shows, and it recomputes the countdown from that timestamp every frame.
A fresh write resets the countdown by construction, so the number on screen and the countdown under it can never disagree.

Both programs resolve their files relative to their own location rather than the working directory.
They behave the same under a service manager, cron, or a terminal.

## Keeping the token alive without a human

Instagram's long-lived tokens expire after 60 days.
The poller takes a token once from the environment, persists it to a mode `0600` file, and refreshes it itself after 50 days using the token-refresh grant, which needs no app secret.
A kiosk that works for eight weeks and then goes dark would defeat the purpose, so the refresh path gets the most careful handling in the code.

A failed refresh retries after 60 seconds, then 120.
After three consecutive failures the poller writes the error into `followers.json` and exits with a message that says how to regenerate the token.
Exiting loudly is the point.
A supervisor or a person notices an exit.
A poller that retries a dead token forever looks healthy from the outside while the screen shows a number from last week.

## Handling API failures

- A rate limit doubles the poll interval, up to eight minutes, and honors `Retry-After` up to an hour.
- An auth error forces a token refresh on the next cycle and backs off the same way.
  Three consecutive auth failures that a refresh cannot rescue also write the error and exit.
- A network error or timeout is logged and the poller keeps its current interval.
- A successful poll resets the interval and clears the auth incident.

Failure counts live in memory only and are never written to the token file.
A restart is always a clean retry.

## Making raylib run on a Raspberry Pi 4

Getting the display onto the Pi's screen took three fixes, each of which is documented in the code.

**The GPU driver refuses the OpenGL version raylib asks for.**
The Pi 4's V3D driver exposes desktop OpenGL 3.1, and raylib requests a 3.3 core context.
GLFW fails with `GLXBadFBConfig` and no window appears.
The launcher script sets `MESA_GL_VERSION_OVERRIDE=3.3` and `MESA_GLSL_VERSION_OVERRIDE=330` so Mesa advertises 3.3.
The hardware satisfies the request because V3D's GLES 3.1 support covers the small gap between desktop 3.1 and 3.3.

**A failed window creation segfaults on exit.**
raylib's `InitWindow` does not abort when it cannot create a GL context, and the matching `CloseWindow` then dereferences an uninitialized render batch.
The program checks `IsWindowReady` before anything else and exits with a message that points at the launcher script.

**Fullscreen is ignored at startup.**
The Pi's window manager reserves space for its desktop panel and silently drops a fullscreen request made at window creation.
The window ends up decorated, offset, and clipped at the bottom.
The program waits until the second frame, once the window is mapped, and toggles borderless fullscreen then.

The layout is designed in a 1920x515 space and every size and position scales with the real screen height, so it keeps its proportions on any panel.

## Stack

- Odin with the raylib bindings that ship in the compiler's `vendor` collection.
- Python 3, standard library only.
- Instagram Graph API with Instagram Login.
- Raspberry Pi 4 under X11 with the V3D Mesa driver.

## Running it

Put a long-lived token in `instagram_service/.env` as `IG_TOKEN=...`, start `python3 instagram_service/poll_followers.py`, and launch the display with `./run.sh` on the Pi or `odin run .` anywhere else.
To see the display without a token, write a `followers.json` with the current time as `ts` and watch it count down into the stale state:

```sh
printf '{"followers_count": 12345, "username": "uvulites", "ts": %d}\n' "$(date +%s)" > instagram_service/followers.json
odin run .
```

Every poller setting is an environment variable with a sensible default.
The names and defaults are at the top of `instagram_service/poll_followers.py`.

## License

[MIT](LICENSE).
