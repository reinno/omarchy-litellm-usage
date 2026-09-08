# LiteLLM Usage for Omarchy

Adds a **LiteLLM tab to the native Agents panel** with today's spend, key spend,
key budget when configured, seven days of tokens, and a model breakdown.

This is a background service plugin. It does not add another bar widget or
replace the native Agents panel. Python collects usage and the shell service
refreshes it every five minutes while Omarchy is running.

## Requirements

- Omarchy Shell with `omarchy.agents` and the schema-version-1 usage-record interface.
- Python 3.10+ (standard library only).
- A LiteLLM virtual key associated with a user, with access to `GET /key/info`
  and `GET /user/daily/activity`. Daily activity must expose per-key breakdowns.
- The native Agents widget enabled in your bar.

The plugin reads the current key's usage. It never substitutes another key's
activity or the whole team's spend when per-key data is unavailable.

## Install from this checkout

From the project directory:

```sh
omarchy plugin validate .
mkdir -p ~/.config/omarchy/plugins/reinno.litellm-usage
cp manifest.json Service.qml collector.py LICENSE ~/.config/omarchy/plugins/reinno.litellm-usage/
omarchy-shell shell rescanPlugins
```

Configure credentials below, then enable:

```sh
omarchy plugin enable reinno.litellm-usage
```

The repository is prepared for git-based plugin installation. Once it has a
public repository URL, users can install it using
`omarchy plugin add <repository-url> --enable` and update using
`omarchy plugin update reinno.litellm-usage --yes`. Marketplace listing is a
separate publication step; this checkout is not a published marketplace entry.

## Configuration

Settings live in `~/.config/omarchy/agents/litellm.json`, outside the plugin's
checkout, so plugin updates preserve them. `XDG_CONFIG_HOME` is respected.

```json
{
  "url": "https://litellm.example.com",
  "tokenEnv": "LITELLM_TOKEN",
  "tokenFile": "",
  "refreshIntervalSec": 300
}
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `url` | `LITELLM_URL` environment variable | LiteLLM proxy base URL, including any path prefix |
| `tokenEnv` | `LITELLM_TOKEN` | Name of the environment variable holding the virtual key |
| `tokenFile` | Empty | Fallback file containing only the key; absolute path or `~/…` |
| `refreshIntervalSec` | `300` | Refresh interval, clamped to 60–3600 seconds |

An explicit URL wins over `LITELLM_URL`. A nonempty token environment variable
wins over `tokenFile`. The token is never stored in plugin settings, command-line
arguments, logs, or usage records. HTTPS is required except for loopback servers.
HTTP redirects are refused to prevent forwarding credentials to another host.

**Environment variables must be visible to `omarchy-shell`.** Exporting a value
only in an interactive terminal's `.zshrc` does not update an already-running
desktop process. The plugin deliberately does not source shell startup scripts.
Use your desktop session's environment mechanism or a private token file:

```sh
mkdir -p ~/.config/omarchy/agents
(umask 077; printf '%s' "$LITELLM_TOKEN" > ~/.config/omarchy/agents/litellm.token)
chmod 600 ~/.config/omarchy/agents/litellm.token
```

Then set `tokenFile` to `~/.config/omarchy/agents/litellm.token` and provide the
server URL in `litellm.json`. The file must have mode `600` or `400`. Do not
commit the actual configuration or token to the project. Configuration edits
trigger a refresh; token-file changes are picked up on the next refresh.

## Refresh and diagnostics

```sh
omarchy-shell reinno.litellm-usage refresh
omarchy-shell reinno.litellm-usage status
```

Run the collector directly to inspect its display-safe JSON:

```sh
python3 ~/.config/omarchy/plugins/reinno.litellm-usage/collector.py
```

The record lives at `~/.local/state/omarchy/agents/usage/litellm.json`
(`XDG_STATE_HOME` is respected). Writes are atomic and private. Overlapping
writers are locked out. The service requests native provider discovery after
its first successful refresh, and subsequent updates are watched by the panel.
The native panel's own refresh shortcut runs built-in collectors; use the
plugin's refresh command above to fetch LiteLLM immediately.

If a request fails, the same key's previous record is retained and visibly
marked as cached, with the last successful timestamp. A changed server or key
never reuses another account's cached numbers. Before the first successful
collection, use the status command for errors; the native panel hides providers
without activity or a budget meter. Hiding the LiteLLM tab in the native panel
does not stop this service from collecting; disable the plugin to stop requests.

## Data interpretation

- Spend comes from LiteLLM, in USD. `key spend` means the server's current
  `spend` field, which may reset with a configured budget period; it is not
  necessarily lifetime billing.
- Budget meters use the key's `max_budget`, not a prepaid balance. No budget
  is invented when the server reports `null`.
- Daily buckets use LiteLLM's UTC dates. The native panel labels dates in local
  time, so near midnight its “Today” label may differ from the UTC spend heading.
- Model totals cover the same seven-day window. Cached reads and cache creation
  are split out of prompt tokens to avoid counting them twice.
- API requests are not labeled as human prompts or coding sessions.
- Account scope tells native sync aggregation not to sum replicated usage from
  several machines. Use the same LiteLLM key on synced machines: the native
  panel merges by provider ID and cannot distinguish different LiteLLM accounts.

## Disable or remove

Disable first to stop network requests. The cached tab persists until its
generated record is removed:

```sh
omarchy plugin disable reinno.litellm-usage
python3 ~/.config/omarchy/plugins/reinno.litellm-usage/collector.py --remove-record
omarchy-shell omarchy.agents refresh
omarchy plugin remove reinno.litellm-usage --yes
```

Configuration and the token file are user-owned and remain for future installs.

If migrating from the earlier standalone collector/timer, disable that timer
before enabling this plugin to avoid two writers:

```sh
systemctl --user disable --now omarchy-litellm-usage.timer
systemctl --user stop omarchy-litellm-usage.service
```

## Development

```sh
python3 -m unittest discover -s tests -v
omarchy plugin validate .
```

Tests use synthetic records for pagination, budgets, cache accounting,
per-key filtering, credential precedence, and stale-state behavior. When
Quickshell is installed, an offscreen smoke test runs the service with fake
commands and isolated configuration/state directories. Tests never call LiteLLM.

The plugin uses the native Agents JSON interface documented in
`/usr/share/omarchy/shell/plugins/agents/README.md`. A future incompatible
interface change may require a plugin update. No packaged Omarchy files are modified.

MIT licensed.
