# live-media-display

A fullscreen kiosk on a Raspberry Pi 4 that shows the UVULITES Instagram follower count and keeps it current on its own.

A Python service polls the Instagram Graph API once a minute and writes the count to a JSON file.
An Odin program built on raylib reads that file and draws the account name, the count in large type, and a countdown to the next update.
When an update is overdue the screen flags the count `STALE` while keeping the last good number on screen.

## Highlights

- The two programs share one atomically written file instead of a socket, so either can crash or restart without the other noticing.
- The display derives its countdown from the data's timestamp rather than a timer, so the number on screen and the countdown under it can never disagree.
- The poller refreshes Instagram's 60-day token by itself and, if a refresh keeps failing, writes the error to the display and exits loudly instead of retrying a dead token forever.
- Rate limits and auth errors back off exponentially, and failure state lives only in memory, so a restart is always a clean retry.
- Three Raspberry Pi 4 fixes: a Mesa override so the V3D driver accepts raylib's OpenGL 3.3 request, a guard against raylib's segfault when window creation fails, and fullscreen deferred until the window manager has mapped the window.

## Stack

Odin, raylib, Python 3 with the standard library only, Instagram Graph API, Raspberry Pi 4.

## License

[MIT](LICENSE).
