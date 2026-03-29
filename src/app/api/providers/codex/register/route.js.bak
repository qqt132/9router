import { NextResponse } from "next/server";
import { spawn } from "child_process";
import path from "path";
import { createProviderConnection } from "@/models";

/**
 * POST /api/providers/codex/register
 * Auto-register a free Codex account using Python script
 * Returns a streaming SSE response with real-time logs
 */
export async function POST(request) {
  const { count = 1 } = await request.json().catch(() => ({}));

  const scriptPath = path.join(process.cwd(), "scripts/openai-register/register.py");

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      const send = (data) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
      };

      let stdout = "";
      const pythonProcess = spawn("python3", [
        scriptPath,
        "--count", String(count),
      ]);

      pythonProcess.stderr.on("data", (data) => {
        const lines = data.toString().split("\n").filter(Boolean);
        for (const line of lines) {
          send({ type: "log", message: line });
        }
      });

      pythonProcess.stdout.on("data", (data) => {
        stdout += data.toString();
      });

      const exitCode = await new Promise((resolve) => {
        pythonProcess.on("close", resolve);
      });

      if (exitCode !== 0) {
        send({ type: "error", message: "Registration script failed" });
        controller.close();
        return;
      }

      let result;
      try {
        result = JSON.parse(stdout);
      } catch {
        send({ type: "error", message: "Failed to parse registration result" });
        controller.close();
        return;
      }

      if (!result.success) {
        send({ type: "error", message: result.error_message || "Registration failed" });
        controller.close();
        return;
      }

      // Save to database
      try {
        const connection = await createProviderConnection({
          provider: "codex",
          authType: "oauth",
          email: result.email,
          displayName: result.email,
          accessToken: result.access_token,
          refreshToken: result.refresh_token,
          idToken: result.id_token,
          expiresIn: result.expires_in,
          expiresAt: result.expires_in
            ? new Date(Date.now() + result.expires_in * 1000).toISOString()
            : null,
          testStatus: "active",
        });

        send({
          type: "success",
          connection: {
            id: connection.id,
            provider: connection.provider,
            email: connection.email,
            displayName: connection.displayName,
          },
        });
      } catch (err) {
        send({ type: "error", message: `Failed to save account: ${err.message}` });
      }

      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
