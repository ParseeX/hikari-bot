# Remove Blog Bot Integration Design

## Goal

Remove the bot commands that update or deploy the blog, because blog publishing is no longer performed through the bot.

## Scope

- Delete `hikari_bot/plugins/blog.py`, which registers the `更新博客`/`blog` and `发布`/`deploy` commands.
- Remove `blog_deploy_script` and `blog_update_script` from `hikari_bot/core/config.py`.
- Remove `BLOG_DEPLOY_SCRIPT` and `BLOG_UPDATE_SCRIPT` from `.env.example` and `README.md`.
- Add contract coverage ensuring the plugin and settings are removed.
- Keep `update.sh` and the GitHub Actions deployment documentation, since they are repository deployment infrastructure rather than bot blog commands.

## Non-goals

- Do not remove unrelated web routes, monitors, or bot commands.
- Do not change the standalone deployment workflow.
- Do not alter unrelated configuration fields.

## Acceptance criteria

1. The blog plugin file no longer exists, so NoneBot cannot load the blog commands.
2. The settings object has no blog script fields.
3. Public configuration examples do not advertise either blog script variable.
4. Existing tests pass, and the new contract test fails if the plugin or settings are reintroduced.
