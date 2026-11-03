# REDBOXINDIA → Telegram

This bot checks the public X account [@REDBOXINDIA](https://x.com/REDBOXINDIA) hourly from 8:00 AM to 11:00 PM India time and forwards new posts to Telegram.

## Free feed method

The bot uses the public FxEmbed RSS profile feed, with the FixupX host as a fallback. No paid X API token is required.

Free third-party feeds are not guaranteed to be real-time or complete. They may be delayed, rate-limited, or temporarily unavailable. The bot records successfully delivered post IDs in `redbox-bot/state.json` to reduce duplicates.

## GitHub Actions secrets

In **Settings → Secrets and variables → Actions**, add:

- `TELEGRAM_BOT_TOKEN`: token from Telegram's @BotFather
- `TELEGRAM_CHAT_ID`: your private Telegram chat ID

No X API token is required.

## Schedule

The workflow runs hourly from 08:00 through 23:00 India time. Posts made overnight are picked up at the next 08:00 run, provided they are still present in the RSS feed.

You can also run it manually from **Actions → REDBOXINDIA to Telegram → Run workflow**.
