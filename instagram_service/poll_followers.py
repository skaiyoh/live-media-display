#!/usr/bin/env python3
"""Poll an Instagram account's follower count via the Instagram Login API.

Reads a long-lived token (seeded once from $IG_TOKEN), persists it to
token.json, and refreshes it before the 60-day expiry so the process can run
indefinitely. Writes the current count atomically to $OUT_PATH for the raylib
display to read. Failures that need a human — refresh dead, polls stuck on
auth errors — exit loudly and leave an "error" field in $OUT_PATH.

Uses only the Python standard library — no dependencies to install.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path: str | None = None) -> None:
    """Populate os.environ from a .env file next to this script.

    Real environment variables take precedence, so an explicit
    `export IG_TOKEN=...` still overrides whatever the file says. A matched
    pair of surrounding quotes is stripped; unquoted values drop trailing
    ` # comments`.
    """
    if path is None:
        path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv()


def env_int(name: str, default: str) -> int:
    """Read an integer env var, failing with a clear message, not a traceback."""
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"ERROR: ${name} must be an integer, got {raw!r}")
    if value <= 0:
        raise SystemExit(f"ERROR: ${name} must be positive, got {value}")
    return value


# --- Config (all overridable via environment) --------------------------------
# Paths default next to this script, not the CWD, so the poller behaves the
# same under launchd/cron as it does when run by hand.
API_VERSION = os.environ.get("IG_API_VERSION", "v22.0")
OUT_PATH = os.environ.get("OUT_PATH", os.path.join(SCRIPT_DIR, "followers.json"))
TOKEN_PATH = os.environ.get("TOKEN_PATH", os.path.join(SCRIPT_DIR, "token.json"))
POLL_INTERVAL = env_int("POLL_INTERVAL", "60")
MAX_INTERVAL = env_int("MAX_INTERVAL", "480")
REFRESH_AFTER = env_int("REFRESH_AFTER_DAYS", "50") * 86_400
REFRESH_RETRY_WAIT = env_int("REFRESH_RETRY_WAIT", "60")
MAX_REFRESH_FAILURES = env_int("MAX_REFRESH_FAILURES", "3")
MAX_AUTH_FAILURES = env_int("MAX_AUTH_FAILURES", "3")

SIXTY_DAYS = 60 * 86_400
GRAPH = "https://graph.instagram.com"
RETRY_AFTER_CAP = 3600  # never let a server's Retry-After header stall us longer


# --- Small helpers ------------------------------------------------------------
def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def mask(token: str) -> str:
    """Never print a full token to logs."""
    return f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "…"


def http_get_json(url: str) -> dict:
    """GET a URL and parse JSON. Raises urllib.error.HTTPError on non-2xx."""
    req = urllib.request.Request(url, headers={"User-Agent": "live-media-display/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def read_error_body(e: urllib.error.HTTPError, limit: int = 300) -> str:
    """Best-effort read of an HTTP error body.

    The socket can die between the raise and the read; letting that escape
    would crash the loop from inside an except block.
    """
    try:
        return e.read().decode(errors="replace")[:limit]
    except Exception:
        return "<unreadable body>"


def write_atomic(path: str, text: str, mode: int) -> None:
    """Write via temp file + rename so readers never see a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- Token lifecycle ----------------------------------------------------------
def save_token(state: dict) -> None:
    write_atomic(TOKEN_PATH, json.dumps(state), mode=0o600)


def read_token_file() -> dict | None:
    """Return persisted token state, or None if missing/empty/corrupt."""
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH) as f:
            content = f.read().strip()
    except OSError as e:
        log(f"{TOKEN_PATH} unreadable ({e}) — re-seeding from $IG_TOKEN")
        return None
    if not content:
        log(f"{TOKEN_PATH} is empty — re-seeding from $IG_TOKEN")
        return None
    try:
        state = json.loads(content)
    except json.JSONDecodeError as e:
        log(f"{TOKEN_PATH} is not valid JSON ({e}) — re-seeding from $IG_TOKEN")
        return None
    if not isinstance(state, dict):
        log(f"{TOKEN_PATH} is not a JSON object — re-seeding from $IG_TOKEN")
        return None
    return state


def load_token() -> dict:
    """Prefer the persisted token; fall back to seeding from $IG_TOKEN once."""
    state = read_token_file()
    if state is not None:
        token = state.get("access_token")
        if isinstance(token, str) and token:
            log(f"loaded token from {TOKEN_PATH} ({mask(token)})")
            return state

    seed = os.environ.get("IG_TOKEN")
    if not seed:
        raise SystemExit(
            f"No usable token in {TOKEN_PATH} and $IG_TOKEN is unset. Generate a "
            "long-lived token from the Meta dashboard (Instagram > API setup with "
            "Instagram business login > Generate token) and export it as IG_TOKEN."
        )
    now = int(time.time())
    state = {"access_token": seed, "refreshed_at": now, "expires_at": now + SIXTY_DAYS}
    save_token(state)
    log(f"seeded token from $IG_TOKEN ({mask(seed)}) -> {TOKEN_PATH}")
    return state


def refresh_token(state: dict) -> dict:
    """Trade the current token for a fresh 60-day one. No app secret required."""
    url = f"{GRAPH}/refresh_access_token?grant_type=ig_refresh_token&access_token={state['access_token']}"
    data = http_get_json(url)
    now = int(time.time())
    new_state = {
        "access_token": data["access_token"],
        "refreshed_at": now,
        "expires_at": now + int(data.get("expires_in", SIXTY_DAYS)),
    }
    save_token(new_state)
    return new_state


def refresh_wait(failures: int) -> int:
    """Seconds before the next refresh attempt: 60s after the first fail,
    doubling each consecutive fail (60, 120, ...)."""
    if failures == 0:
        return 0
    return REFRESH_RETRY_WAIT * 2 ** (failures - 1)


def maybe_refresh(state: dict) -> dict:
    """Refresh once the token is older than REFRESH_AFTER — or right away when
    an auth-failing poll set force_refresh; otherwise no-op.

    Failed refreshes retry quickly (60s, then 120s) and the process exits
    loudly after MAX_REFRESH_FAILURES consecutive failures — with defaults
    the wrapper hears about a dead refresh within ~4 minutes of the first
    fail. Failure tracking is in-memory only (never written to token.json),
    so a restart retries immediately.
    """
    now = int(time.time())
    due = state.get("force_refresh") or now - state.get("refreshed_at", 0) >= REFRESH_AFTER
    if not due:
        return state
    failures = state.get("refresh_failures", 0)
    if now - state.get("refresh_attempted_at", 0) < refresh_wait(failures):
        return state
    try:
        new_state = refresh_token(state)
        log(f"token refreshed, valid ~60d ({mask(new_state['access_token'])})")
        if state.get("auth_failures"):
            # Keep counting auth-failed polls across a rescue refresh — a token
            # that refreshes fine but still can't poll must not loop forever.
            new_state = {**new_state, "auth_failures": state["auth_failures"]}
        return new_state
    except urllib.error.HTTPError as e:
        reason = f"HTTP {e.code}: {read_error_body(e)}"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"

    failures += 1
    if failures >= MAX_REFRESH_FAILURES:
        log(f"token refresh failed {failures} times in a row ({reason}) — exiting")
        write_error(f"token refresh failed {failures} times in a row: {reason}")
        raise SystemExit(
            f"ERROR: token refresh failed {failures} consecutive times. "
            "Regenerate a long-lived token from the Meta dashboard, update "
            "token.json (or delete it and re-seed via $IG_TOKEN), then restart."
        )
    log(
        f"token refresh failed ({reason}) — "
        f"attempt {failures}/{MAX_REFRESH_FAILURES}, retrying in {refresh_wait(failures)}s"
    )
    return {**state, "refresh_attempted_at": now, "refresh_failures": failures}


# --- Poll ---------------------------------------------------------------------
def fetch_count(token: str) -> tuple[int, str | None]:
    url = f"{GRAPH}/{API_VERSION}/me?fields=followers_count,username&access_token={token}"
    data = http_get_json(url)
    return data["followers_count"], data.get("username")


def write_count(count: int, username: str | None) -> None:
    payload = json.dumps(
        {"followers_count": count, "username": username, "ts": int(time.time())}
    )
    write_atomic(OUT_PATH, payload, mode=0o644)


def write_error(message: str) -> None:
    """Surface a fatal poller error to the display via OUT_PATH.

    Merges into the existing payload so the display keeps the last known
    follower count — and its ts, which keeps meaning "when the count was
    fetched"; the error carries its own error_ts. Never raises — this runs
    on the way down and must not mask the exit.
    """
    payload = {}
    try:
        with open(OUT_PATH) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    if not isinstance(payload, dict):
        payload = {}
    payload = {**payload, "error": message, "error_ts": int(time.time())}
    try:
        write_atomic(OUT_PATH, json.dumps(payload), mode=0o644)
    except OSError as e:
        log(f"could not write error to {OUT_PATH}: {e}")


# In-memory incident bookkeeping kept inside the state dict. Never persisted:
# save_token() only ever writes the dicts built in load_token()/refresh_token().
AUTH_INCIDENT_MARKERS = (
    "auth_failures",
    "force_refresh",
    "refresh_failures",
    "refresh_attempted_at",
)


def end_auth_incident(state: dict) -> dict:
    """A successful poll ends an auth incident: drop the auth-failure count and
    any refresh bookkeeping the incident forced. Scheduled-refresh failures
    (no force_refresh) keep their count — polls succeed while a pre-expiry
    refresh is failing, and must not reset that alarm."""
    if not (state.get("auth_failures") or state.get("force_refresh")):
        return state
    return {k: v for k, v in state.items() if k not in AUTH_INCIDENT_MARKERS}


def handle_http_error(
    e: urllib.error.HTTPError, state: dict, interval: int
) -> tuple[dict, int]:
    """Handle a poll HTTP error; return (state, seconds until the next poll).

    429s and auth errors both back off exponentially: rate limits to be a good
    citizen, auth errors because a dead token never self-heals and re-polling
    at full cadence just burns quota and spams logs. An auth error also forces
    a token refresh — that rescues a still-refreshable token — and after
    MAX_AUTH_FAILURES consecutive auth-failed polls the process exits loudly
    so the display never sits silently stale on a dead token.
    """
    if e.code == 429:
        backed_off = min(interval * 2, MAX_INTERVAL)
        retry_after = ((e.headers or {}).get("Retry-After") or "").strip()
        if retry_after.isdigit():
            backed_off = max(backed_off, min(int(retry_after), RETRY_AFTER_CAP))
        log(f"rate limited (429) — backing off to {backed_off}s")
        return state, backed_off
    body = read_error_body(e)
    if e.code in (400, 401):
        failures = state.get("auth_failures", 0) + 1
        if failures >= MAX_AUTH_FAILURES:
            log(f"auth error (HTTP {e.code}) on {failures} consecutive polls ({body}) — exiting")
            write_error(f"polling auth-failed {failures} times in a row: HTTP {e.code}: {body}")
            raise SystemExit(
                f"ERROR: polling hit auth errors {failures} consecutive times and a "
                "token refresh could not rescue it. Regenerate a long-lived token "
                "from the Meta dashboard, update token.json (or delete it and "
                "re-seed via $IG_TOKEN), then restart."
            )
        backed_off = min(interval * 2, MAX_INTERVAL)
        log(
            f"auth error (HTTP {e.code}): {body} — forcing a token refresh "
            f"(poll attempt {failures}/{MAX_AUTH_FAILURES}), backing off to {backed_off}s"
        )
        return {**state, "auth_failures": failures, "force_refresh": True}, backed_off
    log(f"HTTP {e.code}: {body}")
    return state, POLL_INTERVAL


def next_delay(state: dict, interval: int) -> int:
    """Sleep until the next poll — or sooner if a refresh retry is due first,
    so consecutive refresh failures surface within minutes even when polling
    is backed off."""
    failures = state.get("refresh_failures", 0)
    if not failures:
        return interval
    retry_due_in = (
        state.get("refresh_attempted_at", 0) + refresh_wait(failures) - int(time.time())
    )
    return max(1, min(interval, retry_due_in))


def main() -> None:
    state = load_token()
    interval = POLL_INTERVAL
    log(f"polling every {POLL_INTERVAL}s -> {OUT_PATH}")
    while True:
        state = maybe_refresh(state)
        try:
            count, username = fetch_count(state["access_token"])
            write_count(count, username)
            log(f"{username}: {count} followers")
            interval = POLL_INTERVAL  # recovered — reset cadence
            state = end_auth_incident(state)
        except urllib.error.HTTPError as e:
            state, interval = handle_http_error(e, state, interval)
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"network error: {e}")  # transient — keep base cadence
        except Exception as e:
            log(f"poll error: {e}")
        time.sleep(next_delay(state, interval))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
