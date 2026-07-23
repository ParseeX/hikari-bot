# Import Normalization Design

## Goal

Normalize imports across the current codebase without changing runtime behavior or replacing NoneBot's directory-based plugin discovery.

## Scope

- Replace every remaining wildcard import with an explicit dependency.
- Import a small number of stable functions directly.
- Import service modules under descriptive namespaces when a consumer uses many of their functions.
- Add direct imports for names that are currently referenced without being defined in the module.
- Remove imports that are demonstrably unused.
- Preserve matcher, scheduler, lifecycle-hook, and web-route registration behavior.

## Import Rules

Imports remain grouped in this order:

1. Python standard library
2. Third-party packages
3. Project-local modules

Groups are separated by one blank line.

Consumers that use only a few names import those names explicitly:

```python
from hikari_bot.core.whitelist import (
    add_group_to_whitelist,
    get_whitelist,
    is_allowed_group,
)
```

Consumers that use a broad service API import the module under a domain-specific namespace:

```python
from hikari_bot.services import mycard as mycard_service
```

Call sites then retain the namespace:

```python
records = await mycard_service.fetch_player_history(user_id)
```

Aliases are used only to describe a service role or avoid a local name collision. Wildcard imports are not permitted.

## Files and Responsibilities

- `hikari_bot/plugins/base.py`: explicitly import the whitelist operations it uses.
- `hikari_bot/plugins/mycard_query.py`: consume MyCard through `mycard_service`.
- `hikari_bot/plugins/ygocard_query.py`: consume card operations through `card_service`.
- `hikari_bot/plugins/ygomatch_query.py`: consume tournament and deck operations through `match_service` and `deck_service`.
- `hikari_bot/services/ygodeck.py`: directly import its constants, logger, and card-image dependency; consistently use `io.BytesIO`.
- `hikari_bot/plugins/web/routes/sms.py`: directly import `log_message`.
- Other touched modules: remove only imports proven unused by static analysis.

`hikari_bot/plugins/monitors/__init__.py` remains unchanged. Its imports intentionally execute plugin modules so their matchers, scheduled jobs, and lifecycle hooks are registered.

## Plugin Registration

The existing `plugin_dirs = ["hikari_bot/plugins"]` configuration remains in place. Explicit plugin registration is outside this change because the project expects to add plugins frequently and directory discovery reduces registration maintenance.

## Verification

The change is complete when:

- No Python source file contains `from ... import *`.
- `pyflakes` reports no undefined names or wildcard-import warnings.
- `compileall` succeeds for `bot.py`, `hikari_bot`, and `scripts`.
- Existing tests pass.

The existing failing `pyflakes` result is the regression baseline for this
import-only change; no custom AST style test is added.

## Non-goals

- No command, scheduler, route, or application behavior changes.
- No plugin directory reorganization.
- No explicit plugin registry.
- No service API redesign.
- No dependency-injection framework.
