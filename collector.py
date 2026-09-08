#!/usr/bin/env python3
"""Collect one LiteLLM virtual key into the native Omarchy Agents contract."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PLUGIN_ID = "reinno.litellm-usage"
MAX_PAGES = 100


class UsageError(Exception):
    """Only fixed, display-safe messages may be passed to this exception."""


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "omarchy/agents/litellm.json"


def record_path():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "omarchy/agents/usage/litellm.json"


def read_settings(path):
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        raise UsageError("Cannot read LiteLLM configuration; check the JSON file.") from None
    if not isinstance(config, dict):
        raise UsageError("LiteLLM configuration must be a JSON object.")
    url = config.get("url") or os.environ.get("LITELLM_URL", "")
    if not isinstance(url, str) or not url:
        raise UsageError("Set url in litellm.json or LITELLM_URL in the shell environment.")
    url = url.rstrip("/")
    try:
        parts = urlsplit(url)
        valid = parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        valid = valid and (parts.scheme == "https" or (parts.scheme == "http" and local))
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise UsageError("Use an HTTPS server URL without credentials, query, or fragment; HTTP is allowed only on loopback.")
    env_name = config.get("tokenEnv", "LITELLM_TOKEN")
    if not isinstance(env_name, str) or not env_name:
        raise UsageError("tokenEnv must name an environment variable.")
    token = os.environ.get(env_name, "").strip()
    if not token and config.get("tokenFile"):
        try:
            path = Path(config["tokenFile"]).expanduser()
            if not path.is_absolute():
                raise UsageError("tokenFile must be an absolute path or start with ~/.")
            with path.open() as file:
                mode = os.fstat(file.fileno()).st_mode
                if not stat.S_ISREG(mode) or mode & 0o077:
                    raise UsageError("tokenFile must be a regular file with permissions 600 or 400.")
                token = file.read(16385).strip()
        except (OSError, TypeError):
            raise UsageError("Cannot read tokenFile.") from None
    if not token:
        raise UsageError("Set the token environment variable in the desktop session, or configure a private tokenFile.")
    if len(token) > 16384 or any(c.isspace() for c in token):
        raise UsageError("LiteLLM token must be a single value without whitespace.")
    return url, token


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib can forward Authorization on redirects. Require the final URL.
        raise UsageError("LiteLLM redirected the request; configure the final server URL.")


class Client:
    def __init__(self, url, token):
        self.url, self.token = url, token
        self.opener = build_opener(NoRedirects())

    def get(self, path, **query):
        url = self.url + path + ("?" + urlencode(query) if query else "")
        request = Request(url, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=15) as response:
                data = json.load(response)
        except HTTPError as error:
            if error.code in (401, 403):
                raise UsageError("The LiteLLM token cannot read usage (HTTP 401/403); check its permissions.") from None
            if error.code == 429:
                raise UsageError("LiteLLM rate limited the usage request; retry later.") from None
            raise UsageError("LiteLLM usage request failed (HTTP " + str(error.code) + ").") from None
        except (URLError, TimeoutError, OSError):
            raise UsageError("Cannot reach LiteLLM; check the network and server URL.") from None
        except (ValueError, UnicodeError):
            raise UsageError("LiteLLM returned an invalid JSON response.") from None
        if not isinstance(data, dict):
            raise UsageError("LiteLLM returned an unexpected response format.")
        return data


def number(value):
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise UsageError("LiteLLM returned an invalid usage number.") from None


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_record():
    return dict(schemaVersion=1, id="litellm", name="LiteLLM", scope="account",
                collector=PLUGIN_ID, ready=False, hasLocalStats=False, hasPromptStats=False,
                updatedAt=timestamp(), usageStatusText="", authHelpText="", tierLabel="",
                limits=[], recentDays=[], modelUsage={}, todayTokensByModel={},
                todayTotalTokens=0, activeDates=[], activeDays=0)


def collect(client, token, today=None):
    today = today or datetime.now(timezone.utc).date()
    days = [str(today - timedelta(days=offset)) for offset in range(6, -1, -1)]
    info = client.get("/key/info").get("info")
    if not isinstance(info, dict) or not info.get("user_id"):
        raise UsageError("LiteLLM must associate this virtual key with a user to query daily activity.")
    spend = number(info.get("spend"))
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    totals = {day: 0 for day in days}
    models, today_models = {}, {}
    today_spend = 0.0
    for page in range(1, MAX_PAGES + 1):
        data = client.get("/user/daily/activity", user_id=info["user_id"],
                          start_date=days[0], end_date=days[-1], page=page, page_size=100)
        rows = data.get("results")
        if not isinstance(rows, list):
            raise UsageError("LiteLLM daily activity is unavailable or has an unsupported format.")
        for row in rows:
            day = str(row.get("date", ""))[:10]
            if day not in totals:
                continue
            breakdown = row.get("breakdown", {})
            if "api_keys" not in breakdown:
                raise UsageError("LiteLLM daily activity does not include per-key usage.")
            metrics = breakdown["api_keys"].get(key_hash, {}).get("metrics", {})
            totals[day] += int(number(metrics.get("total_tokens", 0)))
            if day == days[-1]:
                today_spend += number(metrics.get("spend", 0))
            for model, entry in breakdown.get("models", {}).items():
                metrics = entry.get("api_key_breakdown", {}).get(key_hash, {}).get("metrics", {})
                total = int(number(metrics.get("total_tokens", 0)))
                if not total:
                    continue
                prompt = int(number(metrics.get("prompt_tokens", 0)))
                cached = min(prompt, int(number(metrics.get("cache_read_input_tokens", 0))))
                created = min(prompt - cached, int(number(metrics.get("cache_creation_input_tokens", 0))))
                bucket = models.setdefault(model, dict(inputTokens=0, outputTokens=0,
                    cacheReadInputTokens=0, cacheCreationInputTokens=0))
                bucket["inputTokens"] += prompt - cached - created
                bucket["outputTokens"] += int(number(metrics.get("completion_tokens", 0)))
                bucket["cacheReadInputTokens"] += cached
                bucket["cacheCreationInputTokens"] += created
                if day == days[-1]:
                    today_models[model] = today_models.get(model, 0) + total
        # Current LiteLLM nests pagination; accept older flat metadata too.
        meta = data.get("metadata", data)
        total_pages = int(number(meta.get("total_pages", 1)))
        if not meta.get("has_more", False) and page >= total_pages:
            break
        if not rows:
            raise UsageError("LiteLLM returned an incomplete page of usage.")
    else:
        raise UsageError("LiteLLM usage exceeded the pagination safety limit.")
    record = empty_record()
    active = [day for day in days if totals[day] > 0]
    record.update(ready=True, hasLocalStats=True,
        tierLabel=f"${today_spend:,.2f} today · ${spend:,.2f} key spend",
        todayTotalTokens=totals[days[-1]], todayTokensByModel=today_models,
        recentDays=[dict(date=day, messageCount=totals[day]) for day in days],
        activeDates=active, activeDays=len(active), modelUsage=models)
    budget = info.get("max_budget")
    if budget is not None:
        budget = number(budget)
        record["tierLabel"] += f" / ${budget:,.2f} budget"
        if budget > 0:
            record["limits"] = [dict(label="Key budget", title="Key budget", percent=spend / budget,
                                     resetsAt=str(info.get("budget_reset_at") or ""))]
    return record


def write_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".litellm-", delete=False) as file:
            temporary = file.name
            json.dump(record, file, allow_nan=False)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def cached_record(path, source):
    try:
        record = json.loads(path.read_text())
        if source and record.get("_source") == source and record.get("collector") == PLUGIN_ID:
            return record
    except (OSError, ValueError, AttributeError):
        pass
    return empty_record()


def refresh(config, target=None):
    source = None
    try:
        url, token = read_settings(config)
        source = hashlib.sha256((url + "\0" + token).encode()).hexdigest()
        record = collect(Client(url, token), token)
        record["_source"] = source
        status = 0
    except Exception as error:
        message = str(error) if isinstance(error, UsageError) else "LiteLLM returned an unexpected usage response."
        record = cached_record(target, source) if target else empty_record()
        stale = record.get("ready") or record.get("stale")
        record.update(ready=False, stale=bool(stale), usageStatusText="LiteLLM unavailable" + (" — cached usage" if stale else ""),
                      authHelpText=message + (" Last success: " + record["updatedAt"] if stale else ""))
        status = 1
    if target:
        write_record(target, record)
    return status, record


@contextmanager
def writer_lock(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / ".litellm.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UsageError("A LiteLLM refresh is already running.") from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config_path())
    parser.add_argument("--write", action="store_true", help="Atomically update the native Agents record")
    parser.add_argument("--remove-record", action="store_true", help="Remove this plugin's generated usage record")
    args = parser.parse_args()
    try:
        if args.write or args.remove_record:
            path = record_path()
            with writer_lock(path):
                if args.remove_record:
                    if path.exists():
                        record = json.loads(path.read_text())
                        if record.get("collector") != PLUGIN_ID:
                            raise UsageError("Refusing to remove a record owned by another collector.")
                        path.unlink()
                    print("LiteLLM usage record removed")
                    return 0
                status, record = refresh(args.config, path)
        else:
            status, record = refresh(args.config)
        if args.write:
            print("LiteLLM usage updated" if status == 0 else record["authHelpText"])
        else:
            print(json.dumps(record, allow_nan=False))
        return status
    except Exception as error:
        print(str(error) if isinstance(error, UsageError) else "Cannot update the LiteLLM usage record.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
