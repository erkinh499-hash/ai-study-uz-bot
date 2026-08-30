# Python Telegram Bot

A small Python Telegram bot that responds to commands and private messages through the Replit-managed Telegram connection.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string
- `python main.py` — run the Telegram bot

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.12 standard library for bot logic
- Replit connector SDK bridge for authenticated Telegram Bot API requests
- Gemini REST API for optional Uzbek-language AI responses
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `main.py` — root launcher
- `telegram_bot/bot.py` — webhook server and message handlers
- `telegram_bot/bridge.mjs` — local authenticated Telegram connector bridge
- `telegram_bot/README.md` — run instructions and supported commands

## Architecture decisions

- Telegram webhooks are used so the bot can run from a published deployment
  without keeping the workspace open.
- Python owns all bot behavior; the small Node bridge is limited to authenticated Telegram API calls.
- Gemini responses are optional until `GEMINI_API_KEY` is added as a Replit Secret.
- Telegram API errors are logged and retried without exposing credentials.

## Product

The bot welcomes users, lists available commands, responds to health checks, and answers private-chat questions in Uzbek with Gemini when configured.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- The Telegram connector must remain attached to this environment for `python main.py` to authenticate successfully.
- AI replies require a `GEMINI_API_KEY` Replit Secret; never place the key in source code or chat.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
