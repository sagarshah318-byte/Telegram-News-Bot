# REDBOXINDIA → Telegram

This bot checks the public X account [@REDBOXINDIA](https://x.com/REDBOXINDIA) hourly from 8:00 AM to 11:00 PM India time and forwards new feed entries to Telegram.

## Free feed method

The bot uses public RSSHub instances instead of the paid X API. It tries these public feed providers in order:

1. https://rsshub.app
2. https://rsshub.stsecurity.moe
3. https://rsshub.axisnetcloud.com

Public RSS services can be rate-limited, unavailable, or blocked by X. RSS may omit some posts or media, and the feed may expose only a limited recent history. This is a free best-effort method, not a guarantee that every X post will always be captured.

## GitHub Actions secrets

In **Settings → Secrets and variables → Actions**, add:

- `TELEGRAM_BOT_TOKEN`: token from Telegram's @BotFather
- `TELEGRAM_CHAT_ID`: your private Telegram chat ID

No X API token is required.

## Schedule

The workflow runs hourly from 08:00 through 23:00 India time. Posts published overnight are picked up at the next 08:00 run, provided the RSS feed still contains them.

## First run

The first run saves the currently visible feed entries as a baseline and does not send older posts. New entries on later runs are forwarded oldest-first. Successfully delivered entry IDs are saved in `redbox-bot/state.json` to reduce duplicate messages.

You can also run it manually from **Actions → REDBOXINDIA to Telegram → Run workflow**.
