# Python Telegram Bot

This project contains a small Telegram bot written in Python. It uses long
webhooks, so it can receive messages from a published Replit deployment without
the workspace being open.

## Run it

Start the project workflow, or run this from the project root:

```bash
python main.py
```

The bot uses the Replit-managed Telegram connection for authenticated Bot API
requests. No token is stored in the source code or required in a `.env` file.

## Commands

- `/start` — welcome message
- `/help` — list commands
- `/ping` — respond with `pong`
- `/about` — describe the bot
- `/reset` — clear your AI conversation context
- Any regular message in a private chat receives an answer from Gemini in Uzbek

## Enable Gemini AI

Add your key as a Replit Secret named `GEMINI_API_KEY`, then restart the
`Telegram Bot` workflow. The bot uses the Gemini REST API directly and keeps
the key out of the source code.

The default model is `gemini-3.6-flash`. To use another supported Gemini model,
set `GEMINI_MODEL` as a regular environment variable.

## Webhook settings

When the app runs in a published Replit deployment, it automatically builds the
webhook URL from `REPLIT_DOMAINS` and registers it with Telegram. For local
testing, set `TELEGRAM_WEBHOOK_URL` to a public HTTPS callback URL. Telegram
requests must include the configured `TELEGRAM_WEBHOOK_SECRET`; if omitted, a
new secure secret is generated each process start.

## Optional settings

You can tune the webhook server with environment variables:

- `TELEGRAM_WEBHOOK_URL` — full public callback URL; auto-detected in deployment
- `TELEGRAM_WEBHOOK_PATH` — callback path, default `/telegram/webhook`
- `TELEGRAM_WEBHOOK_SECRET` — Telegram secret-token header value
- `TELEGRAM_RETRY_DELAY` — retry delay after an API error, default `5`
- `LOG_LEVEL` — Python log level, default `INFO`
- `GEMINI_MODEL` — Gemini model name, default `gemini-3.6-flash`