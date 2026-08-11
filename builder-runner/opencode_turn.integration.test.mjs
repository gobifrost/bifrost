import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { executeTurn } from "./opencode_turn.mjs";

const enabled = process.env.BIFROST_OPENCODE_INTEGRATION === "1";

test(
  "typed SDK starts OpenCode and completes through the compatible gateway",
  { skip: !enabled },
  async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "bifrost-opencode-sdk-"));
    const workspace = path.join(root, "workspace");
    const home = path.join(root, "home");
    await mkdir(workspace);
    await mkdir(home);
    const previousEnv = {
      HOME: process.env.HOME,
      XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
      XDG_DATA_HOME: process.env.XDG_DATA_HOME,
      XDG_CACHE_HOME: process.env.XDG_CACHE_HOME,
      OPENCODE_DISABLE_AUTOUPDATE: process.env.OPENCODE_DISABLE_AUTOUPDATE,
    };
    Object.assign(process.env, {
      HOME: home,
      XDG_CONFIG_HOME: path.join(home, ".config"),
      XDG_DATA_HOME: path.join(home, ".local", "share"),
      XDG_CACHE_HOME: path.join(home, ".cache"),
      OPENCODE_DISABLE_AUTOUPDATE: "true",
    });

    let gatewayRequests = 0;
    let activeRuntime;
    let runtimeStarts = 0;
    const gateway = http.createServer((request, response) => {
      if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
        response.writeHead(404).end();
        return;
      }
      request.resume();
      request.on("end", () => {
        gatewayRequests += 1;
        if (gatewayRequests === 1) {
          activeRuntime?.server.close();
          request.socket.destroy();
          return;
        }
        response.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
        });
        const base = {
          id: "chatcmpl-smoke",
          object: "chat.completion.chunk",
          created: 1,
          model: "smoke-model",
        };
        response.write(
          `data: ${JSON.stringify({
            ...base,
            choices: [
              {
                index: 0,
                delta: { role: "assistant", content: "Builder SDK is ready." },
                finish_reason: null,
              },
            ],
          })}\n\n`,
        );
        response.write(
          `data: ${JSON.stringify({
            ...base,
            choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
            usage: { prompt_tokens: 8, completion_tokens: 5, total_tokens: 13 },
          })}\n\n`,
        );
        response.end("data: [DONE]\n\n");
      });
    });
    await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
    const address = gateway.address();
    assert(address && typeof address === "object");

    try {
      const result = await executeTurn({
        directory: workspace,
        prompt: "Confirm the harness is ready without editing files.",
        model: "smoke-model",
        title: "Bifrost SDK smoke",
        sessionMarkerPath: path.join(root, "session.json"),
        timeoutSeconds: 60,
        config: {
          share: "disabled",
          autoupdate: false,
          enabled_providers: ["bifrost"],
          model: "bifrost/smoke-model",
          default_agent: "bifrost-builder",
          provider: {
            bifrost: {
              name: "Bifrost test gateway",
              npm: "@ai-sdk/openai-compatible",
              options: {
                apiKey: "test-capability",
                baseURL: `http://127.0.0.1:${address.port}/v1`,
              },
              models: {
                "smoke-model": {
                  tool_call: true,
                  limit: { context: 64_000, output: 16_384 },
                },
              },
            },
          },
          agent: {
            "bifrost-builder": {
              mode: "primary",
              model: "bifrost/smoke-model",
              steps: 2,
              prompt: "Return a concise readiness confirmation.",
              permission: { "*": "deny" },
            },
          },
          compaction: { auto: true, prune: true },
          experimental: { chatMaxRetries: 0 },
        },
      }, {
        onRuntimeStarted: (runtime) => {
          activeRuntime = runtime;
          runtimeStarts += 1;
        },
      });

      assert.equal(result.status, "succeeded");
      assert.equal(result.final_text, "Builder SDK is ready.");
      assert.match(result.harness_session_id, /^ses_/);
      assert.equal(gatewayRequests, 2);
      assert.equal(runtimeStarts, 2);
    } finally {
      await new Promise((resolve) => gateway.close(resolve));
      for (const [key, value] of Object.entries(previousEnv)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
      await rm(root, { recursive: true, force: true });
    }
  },
);
