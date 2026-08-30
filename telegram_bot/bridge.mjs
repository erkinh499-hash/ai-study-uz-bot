import { createServer } from "node:http";
import { ReplitConnectors } from "@replit/connectors-sdk";

const connectors = new ReplitConnectors();
const port = Number(process.env.TELEGRAM_BRIDGE_PORT ?? 0);

// Long polling can legitimately keep a request open for more than a minute.
// Disable Node's server-side request timers so the connector controls timeout.
const server = createServer({ requestTimeout: 0 }, async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    return sendJson(response, 200, { ok: true });
  }

  if (request.method !== "POST" || request.url !== "/call") {
    return sendJson(response, 404, { ok: false, description: "Not found" });
  }

  try {
    const payload = await readJson(request);
    const apiMethod = payload.methodName;
    const apiMethodType = payload.httpMethod === "GET" ? "GET" : "POST";

    if (typeof apiMethod !== "string" || !/^[A-Za-z][A-Za-z0-9]*$/.test(apiMethod)) {
      return sendJson(response, 400, { ok: false, description: "Invalid Telegram API method" });
    }

    const options = {
      method: apiMethodType,
      headers: { "content-type": "application/json" },
    };
    if (apiMethodType === "POST") {
      options.body = JSON.stringify(payload.body ?? {});
    }

    const telegramResponse = await connectors.proxy("telegram", `/${apiMethod}`, options);
    const responseText = await telegramResponse.text();
    response.writeHead(telegramResponse.status, {
      "content-type": telegramResponse.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    });
    response.end(responseText);
  } catch (error) {
    sendJson(response, 502, {
      ok: false,
      description: error instanceof Error ? error.message : "Telegram connector request failed",
    });
  }
});

server.timeout = 0;
server.keepAliveTimeout = 0;
server.headersTimeout = 0;

function readJson(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) {
        reject(new Error("Request body is too large"));
        request.destroy();
      }
    });
    request.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        reject(new Error(`Invalid JSON request: ${error.message}`));
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(payload));
}

server.listen(port, "127.0.0.1", () => {
  const address = server.address();
  if (address && typeof address === "object") {
    process.stdout.write(`READY ${address.port}\n`);
  }
});