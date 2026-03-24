import { NextResponse } from "next/server";
import { spawn } from "child_process";
import path from "path";
import { createProviderConnection } from "@/models";

/**
 * POST /api/providers/codex/register
 * Auto-register a free Codex account using Python script
 */
export async function POST(request) {
  try {
    const { count = 1 } = await request.json().catch(() => ({}));
    
    // Path to Python script
    const scriptPath = path.join(process.cwd(), "scripts/openai-register/register.py");
    
    // Spawn Python process
    const pythonProcess = spawn("python3", [
      scriptPath,
      "--count", String(count),
      "--quiet"
    ]);

    let stdout = "";
    let stderr = "";
    const logs = [];

    // Collect stdout (JSON result)
    pythonProcess.stdout.on("data", (data) => {
      stdout += data.toString();
    });

    // Collect stderr (logs)
    pythonProcess.stderr.on("data", (data) => {
      const text = data.toString();
      stderr += text;
      logs.push(text.trim());
    });

    // Wait for process to complete
    const exitCode = await new Promise((resolve) => {
      pythonProcess.on("close", resolve);
    });

    if (exitCode !== 0) {
      return NextResponse.json(
        { 
          success: false, 
          error: "Registration failed", 
          logs,
          stderr 
        },
        { status: 500 }
      );
    }

    // Parse result
    let result;
    try {
      result = JSON.parse(stdout);
    } catch (error) {
      return NextResponse.json(
        { 
          success: false, 
          error: "Failed to parse registration result", 
          logs,
          stdout 
        },
        { status: 500 }
      );
    }

    // Check if registration succeeded
    if (!result.success) {
      return NextResponse.json(
        { 
          success: false, 
          error: result.error_message || "Registration failed", 
          logs: result.logs || logs 
        },
        { status: 500 }
      );
    }

    // Save to database
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

    return NextResponse.json({
      success: true,
      connection: {
        id: connection.id,
        provider: connection.provider,
        email: connection.email,
        displayName: connection.displayName,
      },
      logs: result.logs || logs,
    });
  } catch (error) {
    console.error("Codex registration error:", error);
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}
