# Development and technical notes

## Local installation

From the project directory:

```sh
omarchy plugin validate .
mkdir -p ~/.config/omarchy/plugins/reinno.omarchy-litellm-usage
cp manifest.json Service.qml collector.py LICENSE ~/.config/omarchy/plugins/reinno.omarchy-litellm-usage/
omarchy-shell shell rescanPlugins
```

Configure credentials below, then enable:

```sh
omarchy plugin enable reinno.omarchy-litellm-usage
```

## Diagnostics and data

```sh
omarchy-shell reinno.omarchy-litellm-usage refresh
omarchy-shell reinno.omarchy-litellm-usage status
```

Run the collector directly to inspect its display-safe JSON:

```sh
python3 ~/.config/omarchy/plugins/reinno.omarchy-litellm-usage/collector.py
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

- Spend comes from LiteLLM, in USD. Today's spend covers the current key;
  `this budget period` shows the user's `spend` from `/user/info`, matching
  the budget meter. It includes all keys charged to that user budget and is
  omitted when the user budget is unavailable.
- The budget meter uses the LiteLLM **user's** `max_budget` versus `spend` from
  `GET /user/info`, not a prepaid balance. Its period label and reset countdown
  come from `budget_duration` and `budget_reset_at`. No budget is invented when
  the server reports `max_budget` as `null`, and a key-level `max_budget` is not
  currently read.
- Daily buckets use LiteLLM's UTC dates. The native panel labels dates in local
  time, so near midnight its “Today” label may differ from the UTC spend heading.
- Model totals cover the same seven-day window. Cached reads and cache creation
  are split out of prompt tokens to avoid counting them twice.
- API requests are not labeled as human prompts or coding sessions.
- Account scope tells native sync aggregation not to sum replicated usage from
  several machines. Use the same LiteLLM key on synced machines: the native
  panel merges by provider ID and cannot distinguish different LiteLLM accounts.

## Tests

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
