# REDBOXINDIA → Telegram

This GitHub Actions bot checks the X account [@REDBOXINDIA](https://x.com/REDBOXINDIA) hourly from 8:00 AM to 11:00 PM India time and forwards new posts to a Telegram chat.

Posts made overnight are picked up at the next 8:00 AM run. The bot stores the last successfully delivered post ID in `redbox-bot/state.json` to avoid normal duplicate deliveries.

## Required GitHub Actions secrets

In **Settings → Secrets and variables → Actions → New repository secret**, add:

- `X_BEARER_TOKEN`: X API v2 Bearer Token
- `TELEGRAM_BOT_TOKEN`: token from Telegram's @BotFather
- `TELEGRAM_CHAT_ID`: your private Telegram chat ID

Never put these credentials in source files or commit them to this repository.

## First run

The first run records the latest available post as the starting point. It does not send old timeline posts. Every newer post is forwarded on later runs.

You can also start a run manually from **Actions → REDBOXINDIA to Telegram → Run workflow**.

## Access requirements

The X API app must have read access to the user timeline endpoint. X API plan limits and pricing are controlled by X. The repository's GitHub Actions workflow needs permission to write repository contents so it can save the delivery cursor.
