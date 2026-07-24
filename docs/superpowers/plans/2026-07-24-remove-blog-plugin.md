# Remove Blog Bot Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the bot's blog update/deploy plugin and its dedicated configuration while preserving standalone repository deployment.

**Architecture:** The change removes the auto-discovered `hikari_bot/plugins/blog.py` module and deletes only the two settings fields consumed by it. Public environment documentation is kept aligned with the remaining settings; `update.sh` and GitHub deployment documentation remain untouched.

**Tech Stack:** Python 3.12, pytest, dataclass-based settings, NoneBot plugin auto-discovery.

## Global Constraints

- Do not remove `update.sh` or the GitHub Actions deployment workflow.
- Do not alter unrelated settings, plugins, routes, monitors, or services.
- The regression contract must cover the plugin file, settings fields, and public configuration examples.

---

### Task 1: Add the removal contract test

**Files:**
- Modify: `tests/test_removed_features_contract.py`

**Interfaces:**
- Consumes: `_settings_fields()` and `_source()` helpers already defined in the test module.
- Produces: `test_blog_plugin_and_settings_are_removed()` as the regression contract for the cleanup.

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_removed_features_contract.py`:

```python
def test_blog_plugin_and_settings_are_removed():
    assert not (ROOT / "hikari_bot/plugins/blog.py").exists()
    assert {"blog_deploy_script", "blog_update_script"}.isdisjoint(_settings_fields())

    env_example = _source(".env.example")
    readme = _source("README.md")
    for removed_setting in ("BLOG_DEPLOY_SCRIPT", "BLOG_UPDATE_SCRIPT"):
        assert removed_setting not in env_example
        assert removed_setting not in readme
```

- [ ] **Step 2: Run the contract test to verify it fails for the expected reason**

Run:

```powershell
uv run pytest tests/test_removed_features_contract.py::test_blog_plugin_and_settings_are_removed -q
```

Expected: `1 failed`, with the first assertion reporting that `hikari_bot/plugins/blog.py` still exists.

### Task 2: Remove the bot integration and public configuration

**Files:**
- Delete: `hikari_bot/plugins/blog.py`
- Modify: `hikari_bot/core/config.py:133-138`
- Modify: `.env.example:13-14`
- Modify: `README.md:67-68`

**Interfaces:**
- Consumes: The failing contract from Task 1.
- Produces: A plugin directory without blog commands and a settings object without blog-only fields.

- [ ] **Step 1: Delete the auto-discovered blog plugin**

Remove `hikari_bot/plugins/blog.py` entirely. Do not replace its commands with aliases or no-op handlers.

- [ ] **Step 2: Remove the two blog settings fields**

Delete these dataclass fields from `hikari_bot/core/config.py`:

```python
    blog_deploy_script: Path | None = field(
        default_factory=lambda: _optional_path("BLOG_DEPLOY_SCRIPT")
    )
    blog_update_script: Path | None = field(
        default_factory=lambda: _optional_path("BLOG_UPDATE_SCRIPT")
    )
```

Keep neighboring `public_group_id` and `jm_data_dir` fields unchanged.

- [ ] **Step 3: Remove the public environment entries**

Delete only these lines from `.env.example`:

```dotenv
BLOG_DEPLOY_SCRIPT=/srv/blog/deploy.sh
BLOG_UPDATE_SCRIPT=/srv/blog/update.sh
```

Delete only the corresponding two lines from the optional configuration block in `README.md`.

- [ ] **Step 4: Run the focused contract test**

Run:

```powershell
uv run pytest tests/test_removed_features_contract.py::test_blog_plugin_and_settings_are_removed -q
```

Expected: `1 passed`.

### Task 3: Verify the complete cleanup

**Files:**
- Inspect: all tracked files for remaining runtime/config references

**Interfaces:**
- Consumes: The cleaned plugin, settings, and documentation from Task 2.
- Produces: Verified repository state with no remaining bot blog integration references.

- [ ] **Step 1: Search for runtime and public-config references**

Run:

```powershell
rg -n -i --hidden --glob '!.git/**' --glob '!docs/superpowers/**' --glob '!*.pyc' "blog|BLOG_DEPLOY_SCRIPT|BLOG_UPDATE_SCRIPT|更新博客|deploy" .
```

Expected: no output from application code or public configuration for the removed bot integration; unrelated `update.sh`/GitHub deployment references may remain.

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
uv run pytest -q
```

Expected: exit code `0` and zero failures.

- [ ] **Step 3: Inspect the final diff**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only the planned plugin, settings, docs, test, and plan changes are present.

- [ ] **Step 4: Commit the implementation**

Run:

```powershell
git add .env.example README.md hikari_bot/core/config.py hikari_bot/plugins/blog.py tests/test_removed_features_contract.py docs/superpowers/plans/2026-07-24-remove-blog-plugin.md
git commit -m "chore: remove blog bot integration"
```

- [ ] **Step 5: Push `main`**

Run:

```powershell
git push origin main
```
