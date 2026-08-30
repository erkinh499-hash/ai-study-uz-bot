"""Python Telegram bot with Gemini-powered Uzbek responses."""

from __future__ import annotations

import json
import logging
import os
import secrets
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("telegram_bot")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = Path(__file__).with_name("bridge.mjs")
TELEGRAM_MESSAGE_LIMIT = 4096


class TelegramError(RuntimeError):
    """A Telegram connector or Bot API error."""


class GeminiError(RuntimeError):
    """A Gemini API error."""


class WebhookError(RuntimeError):
    """A webhook server configuration or runtime error."""


class ConnectorBridge:
    """Start one local process that owns Replit connector authentication."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.port: int | None = None

    def start(self) -> None:
        environment = os.environ.copy()
        environment["TELEGRAM_BRIDGE_PORT"] = "0"
        self.process = subprocess.Popen(
            ["node", str(BRIDGE_PATH)],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        if self.process.stdout is None:
            raise TelegramError("Connector bridge did not open its output stream")

        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                ready = selector.select(timeout=max(0, deadline - time.monotonic()))
                if not ready:
                    break
                line = self.process.stdout.readline().strip()
                if line.startswith("READY "):
                    self.port = int(line.split(maxsplit=1)[1])
                    return
                if line:
                    LOGGER.debug("connector bridge: %s", line)
        finally:
            selector.close()

        exit_code = self.process.poll()
        self.stop()
        if exit_code is not None:
            raise TelegramError(f"Connector bridge exited during startup (code {exit_code})")
        raise TelegramError("Timed out waiting for the Telegram connector bridge")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        self.port = None

    def __enter__(self) -> ConnectorBridge:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


class TelegramClient:
    """Typed-enough JSON client for the local Telegram connector bridge."""

    def __init__(self, bridge: ConnectorBridge) -> None:
        if bridge.port is None:
            raise TelegramError("Telegram connector bridge is not running")
        self.url = f"http://127.0.0.1:{bridge.port}/call"

    def call(
        self,
        method_name: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 45,
    ) -> Any:
        request_body = json.dumps(
            {
                "methodName": method_name,
                "httpMethod": "POST",
                "body": body or {},
            }
        ).encode()
        request = Request(
            self.url,
            data=request_body,
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as error:
            payload = self._error_payload(error)
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise TelegramError(f"Telegram transport failed: {error}") from error

        if not payload.get("ok"):
            raise TelegramError(str(payload.get("description", "Telegram request failed")))
        return payload.get("result")

    @staticmethod
    def _error_payload(error: HTTPError) -> dict[str, Any]:
        try:
            return json.loads(error.read().decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"ok": False, "description": f"Telegram connector HTTP {error.code}"}


class GeminiClient:
    """Direct Gemini REST client using the GEMINI_API_KEY Replit Secret."""

    SYSTEM_INSTRUCTION = (
        "Siz o'zbek tilida javob beradigan foydali Telegram yordamchi botsiz. "
        "Har qanday savolga aniq, tushunarli va do'stona javob bering. "
        "Asosiy til sifatida o'zbek tilining lotin yozuvidan foydalaning. "
        "Savol boshqa tilda berilsa ham, javobni o'zbek tilida yozing. "
        "Agar savol noaniq bo'lsa, aniqlashtiruvchi savol bering. "
        "Kod, ro'yxat yoki bosqichma-bosqich ko'rsatmalar kerak bo'lsa, "
        "o'qishga qulay shakldan foydalaning. Bilmagan narsangizni uydirmang."
    )

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key.strip() if api_key else ""
        self.model = model

    def answer(self, question: str, history: list[dict[str, Any]]) -> str:
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not configured")

        contents = [*history, {"role": "user", "parts": [{"text": question}]}]
        request_body = {
            "systemInstruction": {
                "parts": [{"text": self.SYSTEM_INSTRUCTION}],
            },
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 8192,
            },
        }
        request = Request(
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            ),
            data=json.dumps(request_body).encode(),
            method="POST",
            headers={
                "content-type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )

        last_error: GeminiError | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode())
                return self._extract_answer(payload)
            except HTTPError as error:
                payload = self._read_error(error)
                message = payload.get("error", {}).get("message", "Gemini request failed")
                last_error = GeminiError(str(message))
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = GeminiError(f"Gemini transport failed: {error}")

            if attempt < 2:
                time.sleep(2**attempt)

        raise last_error or GeminiError("Gemini request failed")

    @staticmethod
    def _read_error(error: HTTPError) -> dict[str, Any]:
        try:
            return json.loads(error.read().decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": {"message": f"Gemini HTTP {error.code}"}}

    @staticmethod
    def _extract_answer(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates", [])
        if not candidates:
            feedback = payload.get("promptFeedback", {})
            raise GeminiError(
                str(feedback.get("blockReason", "Gemini returned no answer"))
            )
        parts = candidates[0].get("content", {}).get("parts", [])
        answer = "".join(part.get("text", "") for part in parts).strip()
        if not answer:
            raise GeminiError("Gemini returned an empty answer")
        return answer


@dataclass(frozen=True)
class BotConfig:
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8787
    webhook_path: str = "/telegram/webhook"
    webhook_url: str = ""
    webhook_secret: str = ""
    retry_delay: int = 5

    @classmethod
    def from_environment(cls) -> BotConfig:
        path = os.getenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook").strip()
        if not path.startswith("/"):
            path = f"/{path}"
        path = "/" + path.strip("/")
        if path == "/":
            path = "/telegram/webhook"

        configured_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip().rstrip("/")
        if configured_url:
            webhook_url = configured_url
        elif os.getenv("REPLIT_DEPLOYMENT") == "1":
            domains = [
                domain.strip()
                for domain in os.getenv("REPLIT_DOMAINS", "").split(",")
                if domain.strip()
            ]
            if not domains:
                raise WebhookError(
                    "Published deployment has no REPLIT_DOMAINS; "
                    "set TELEGRAM_WEBHOOK_URL explicitly"
                )
            domain = domains[0]
            if "://" not in domain:
                domain = f"https://{domain}"
            webhook_url = f"{domain.rstrip('/')}{path}"
        else:
            webhook_url = ""

        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        return cls(
            webhook_host=os.getenv("TELEGRAM_WEBHOOK_HOST", "0.0.0.0"),
            webhook_port=max(
                1,
                int(
                    os.getenv(
                        "PORT",
                        os.getenv("TELEGRAM_WEBHOOK_PORT", "8787"),
                    )
                ),
            ),
            webhook_path=path,
            webhook_url=webhook_url,
            webhook_secret=secret or secrets.token_urlsafe(32),
            retry_delay=max(1, int(os.getenv("TELEGRAM_RETRY_DELAY", "5"))),
        )


class TelegramBot:
    def __init__(
        self,
        telegram: TelegramClient,
        gemini: GeminiClient,
        config: BotConfig,
    ) -> None:
        self.telegram = telegram
        self.gemini = gemini
        self.config = config
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.username = ""

    def run(self) -> None:
        profile = self.telegram.call("getMe")
        self.username = profile.get("username", "")
        LOGGER.info(
            "Connected to Telegram as %s (@%s)",
            profile.get("first_name", "bot"),
            self.username,
        )

        self.telegram.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "Botni ishga tushirish"},
                    {"command": "help", "description": "Buyruqlar ro'yxati"},
                    {"command": "ping", "description": "Bot holatini tekshirish"},
                    {"command": "about", "description": "Bot haqida"},
                    {"command": "reset", "description": "AI suhbatini tozalash"},
                ]
            },
        )

        if self.config.webhook_url:
            self.telegram.call(
                "setWebhook",
                {
                    "url": self.config.webhook_url,
                    "secret_token": self.config.webhook_secret,
                    "allowed_updates": ["message"],
                    "drop_pending_updates": False,
                },
            )
            LOGGER.info(
                "Telegram webhook registered at %s",
                self.config.webhook_url,
            )
        else:
            LOGGER.warning(
                "No public webhook URL is configured. "
                "Publish the app or set TELEGRAM_WEBHOOK_URL."
            )

        server = TelegramWebhookServer(
            (self.config.webhook_host, self.config.webhook_port),
            self,
            self.config.webhook_path,
            self.config.webhook_secret,
        )
        LOGGER.info(
            "Webhook server listening on %s:%s%s",
            self.config.webhook_host,
            self.config.webhook_port,
            self.config.webhook_path,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if not isinstance(text, str) or chat_id is None:
            return

        chat_type = chat.get("type", "unknown")
        LOGGER.info("Received text message in %s chat", chat_type)
        command, argument = self.parse_command(text)

        if command == "start":
            reply = (
                "Assalomu alaykum! Men o'zbek tilida savollarga javob beradigan "
                "AI yordamchiman.\n\nSavolingizni yozing yoki /help buyrug'idan foydalaning."
            )
        elif command == "help":
            reply = (
                "Buyruqlar:\n"
                "/start — botni ishga tushirish\n"
                "/help — yordam\n"
                "/ping — bot holati\n"
                "/about — bot haqida\n"
                "/reset — AI suhbatini tozalash"
            )
        elif command == "ping":
            reply = "pong — bot ishlayapti."
        elif command == "about":
            reply = "Men Python va Gemini yordamida ishlaydigan o'zbek tilidagi AI yordamchiman."
        elif command == "reset":
            self.history.pop(str(chat_id), None)
            reply = "Suhbat konteksti tozalandi."
        elif command:
            reply = "Bu buyruq tanilmadi. /help buyrug'ini sinab ko'ring."
        elif chat_type == "private":
            reply = self.answer_question(chat_id, text)
        else:
            return

        self.send_message(chat_id, reply)
        if command == "start" and argument:
            LOGGER.debug("Start parameter received: %s", argument)

    @staticmethod
    def parse_command(text: str) -> tuple[str | None, str]:
        if not text.startswith("/"):
            return None, ""
        first, *rest = text.split(maxsplit=1)
        command = first[1:].split("@", maxsplit=1)[0].lower()
        return command, rest[0] if rest else ""

    def answer_question(self, chat_id: int | str, question: str) -> str:
        key = str(chat_id)
        previous = self.history.get(key, [])
        try:
            answer = self.gemini.answer(question, previous)
        except GeminiError as error:
            LOGGER.warning("Gemini failed for chat %s: %s", chat_id, error)
            return "Kechirasiz, hozir AI javobini tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring."

        self.history[key] = (
            previous
            + [
                {"role": "user", "parts": [{"text": question}]},
                {"role": "model", "parts": [{"text": answer}]},
            ]
        )[-8:]
        LOGGER.info("Gemini answer generated for chat %s", chat_id)
        return answer

    def send_message(self, chat_id: int | str, text: str) -> None:
        chunks = [
            text[index : index + TELEGRAM_MESSAGE_LIMIT]
            for index in range(0, len(text), TELEGRAM_MESSAGE_LIMIT)
        ] or [""]
        for index, chunk in enumerate(chunks, start=1):
            try:
                self.telegram.call("sendMessage", {"chat_id": chat_id, "text": chunk})
                LOGGER.info("Sent reply %s/%s", index, len(chunks))
            except TelegramError as error:
                LOGGER.warning("Telegram sendMessage failed: %s", error)
                return


class TelegramWebhookServer(ThreadingHTTPServer):
    """HTTP server that receives Telegram webhook updates."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        bot: TelegramBot,
        webhook_path: str,
        webhook_secret: str,
    ) -> None:
        super().__init__(address, TelegramWebhookRequestHandler)
        self.bot = bot
        self.webhook_path = webhook_path
        self.webhook_secret = webhook_secret


class TelegramWebhookRequestHandler(BaseHTTPRequestHandler):
    """Acknowledge Telegram quickly, then process updates in the background."""

    server: TelegramWebhookServer

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"ok": False, "description": "Not found"})

    def do_POST(self) -> None:
        if self.path != self.server.webhook_path:
            self._send_json(404, {"ok": False, "description": "Not found"})
            return

        expected_secret = self.server.webhook_secret
        received_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected_secret or not secrets.compare_digest(
            received_secret,
            expected_secret,
        ):
            self._send_json(403, {"ok": False, "description": "Forbidden"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1_000_000:
                raise ValueError("Invalid webhook body size")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Webhook payload must be an object")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            LOGGER.warning("Invalid Telegram webhook payload: %s", error)
            self._send_json(400, {"ok": False, "description": "Invalid payload"})
            return

        self._send_json(200, {"ok": True})
        Thread(
            target=self.server.bot.handle_update,
            args=(payload,),
            name="telegram-update",
            daemon=True,
        ).start()

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("Webhook HTTP: " + format, *args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)


def run_bot() -> None:
    config = BotConfig.from_environment()
    with ConnectorBridge() as bridge:
        TelegramBot(
            TelegramClient(bridge),
            GeminiClient(
                os.getenv("GEMINI_API_KEY"),
                os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
            ),
            config,
        ).run()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    while True:
        try:
            run_bot()
        except KeyboardInterrupt:
            LOGGER.info("Bot stopped.")
            return
        except (TelegramError, ValueError) as error:
            LOGGER.error("Bot runtime stopped: %s; restarting in 5s", error)
            time.sleep(5)


if __name__ == "__main__":
    main()