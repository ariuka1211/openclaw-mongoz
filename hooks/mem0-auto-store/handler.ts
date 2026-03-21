/**
 * mem0-auto-store hook handler
 *
 * Stores conversation context to MEM0 semantic memory when /new or /reset is triggered.
 * Reads the session JSONL transcript and POSTs recent messages to MEM0 for auto-extraction.
 */
import { readFile, readdir } from "node:fs/promises";
import { join, dirname, basename } from "node:path";
import os from "node:os";

const MEM0_API_BASE = "https://api.mem0.ai";

/** Resolve MEM0 config from env or plugin config */
function resolveMem0Config(cfg: any): { apiKey: string; userId: string } {
  const pluginCfg = cfg?.plugins?.entries?.mem0?.config ?? {};
  return {
    apiKey: process.env.MEM0_API_KEY || pluginCfg.apiKey || "",
    userId: process.env.MEM0_USER_ID || pluginCfg.userId || "john",
  };
}

/** Read recent user/assistant messages from a JSONL session file */
async function readSessionMessages(sessionFilePath: string, maxMessages: number): Promise<Array<{ role: string; content: string }>> {
  try {
    const raw = await readFile(sessionFilePath, "utf-8");
    const lines = raw.trim().split("\n");
    const messages: Array<{ role: string; content: string }> = [];

    for (const line of lines) {
      try {
        const entry = JSON.parse(line);
        if (entry.type === "message" && entry.message) {
          const msg = entry.message;
          if ((msg.role === "user" || msg.role === "assistant") && msg.content) {
            const text = Array.isArray(msg.content)
              ? msg.content.find((c: any) => c.type === "text")?.text
              : msg.content;
            if (text && typeof text === "string" && !text.startsWith("/")) {
              messages.push({ role: msg.role, content: text.slice(0, 2000) }); // cap per message
            }
          }
        }
      } catch {}
    }

    return messages.slice(-maxMessages);
  } catch (err) {
    console.error("[mem0-auto-store] Failed to read session file:", err);
    return [];
  }
}

/** Try the active transcript; fallback to .reset.* sibling */
async function resolveSessionFile(sessionFile: string, sessionsDir: string, sessionId?: string): Promise<string | null> {
  // Try the provided file first
  try {
    await readFile(sessionFile, "utf-8");
    return sessionFile;
  } catch {}

  // Try reset fallbacks
  try {
    const files = await readdir(sessionsDir);
    const resetPrefix = `${basename(sessionFile)}.reset.`;
    const resetCandidates = files.filter((n) => n.startsWith(resetPrefix)).sort();
    if (resetCandidates.length > 0) {
      return join(sessionsDir, resetCandidates[resetCandidates.length - 1]);
    }
  } catch {}

  // Try canonical session file
  if (sessionId) {
    const canonical = join(sessionsDir, `${sessionId}.jsonl`);
    try {
      await readFile(canonical, "utf-8");
      return canonical;
    } catch {}
  }

  return null;
}

/** Send messages to MEM0 API for auto-extraction */
async function storeToMem0(messages: Array<{ role: string; content: string }>, userId: string, apiKey: string): Promise<string> {
  if (messages.length === 0) return "(no messages to store)";

  try {
    const res = await fetch(`${MEM0_API_BASE}/v1/memories/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Token ${apiKey}`,
      },
      body: JSON.stringify({
        messages: messages.map((m) => ({ role: m.role, content: m.content })),
        user_id: userId,
        version: "v2",
      }),
    });

    if (!res.ok) {
      const body = await res.text();
      return `MEM0 store error: HTTP ${res.status} — ${body}`;
    }

    const data = await res.json() as any[];
    if (!Array.isArray(data) || data.length === 0) return "(MEM0 returned no memories)";
    return data.map((m: any) => `  • [${m.id}] ${m.memory}`).join("\n");
  } catch (err) {
    return `MEM0 store error: ${String(err)}`;
  }
}

// ── Hook handler ──
const mem0AutoStore = async (event: any, context: any) => {
  if (event.type !== "command" || (event.action !== "new" && event.action !== "reset")) return;

  const cfg = context?.cfg;
  const mem0 = resolveMem0Config(cfg);
  if (!mem0.apiKey) {
    console.warn("[mem0-auto-store] MEM0_API_KEY not configured — skipping");
    return;
  }

  // Resolve session file
  const sessionEntry = context?.previousSessionEntry || context?.sessionEntry || {};
  const sessionFile = sessionEntry.sessionFile;
  const sessionId = sessionEntry.sessionId;
  const homeDir = os.homedir();
  
  console.log(`[mem0-auto-store] DEBUG: sessionFile=${sessionFile}, sessionId=${sessionId}`);
  console.log(`[mem0-auto-store] DEBUG: context keys=${Object.keys(context || {}).join(",")}`);
  console.log(`[mem0-auto-store] DEBUG: sessionEntry keys=${Object.keys(sessionEntry || {}).join(",")}`);

  // Sessions live in ~/.openclaw/agents/main/sessions/, not workspace/sessions/
  const defaultSessionsDir = join(homeDir, ".openclaw", "agents", "main", "sessions");
  const sessionsDir = sessionFile ? dirname(sessionFile) : defaultSessionsDir;

  let resolvedFile: string | null = sessionFile || null;
  if (!resolvedFile || resolvedFile.includes(".reset.")) {
    resolvedFile = await resolveSessionFile(resolvedFile || "", sessionsDir, sessionId);
    // Fallback: if no sessionFile was provided and nothing found in default dir,
    // try the most recent reset file across all sessions
    if (!resolvedFile && !sessionFile) {
      try {
        const files = await readdir(defaultSessionsDir);
        console.log(`[mem0-auto-store] DEBUG: fallback dir=${defaultSessionsDir}, files=${files.length}`);
        const resetFiles = files
          .filter((n) => n.includes(".reset.") && n.endsWith(".jsonl"))
          .map((n) => ({ name: n, path: join(defaultSessionsDir, n) }));
        console.log(`[mem0-auto-store] DEBUG: reset candidates=${resetFiles.length}`);
        // Sort by timestamp embedded in filename (newest last)
        resetFiles.sort((a, b) => a.name.localeCompare(b.name));
        if (resetFiles.length > 0) {
          resolvedFile = resetFiles[resetFiles.length - 1].path;
          console.log(`[mem0-auto-store] Using most recent reset file: ${resolvedFile}`);
        }
      } catch (e) { console.log(`[mem0-auto-store] DEBUG: fallback error: ${e}`); }
    }
  }

  if (!resolvedFile) {
    console.warn(`[mem0-auto-store] No session file found — skipping (sessionFile=${sessionFile}, defaultDir=${defaultSessionsDir})`);
    return;
  }

  // Read messages
  const maxMessages = 15;
  const messages = await readSessionMessages(resolvedFile, maxMessages);
  if (messages.length === 0) {
    console.log("[mem0-auto-store] No messages in session — skipping");
    return;
  }

  // Store to MEM0
  const result = await storeToMem0(messages, mem0.userId, mem0.apiKey);
  console.log(`[mem0-auto-store] Stored ${messages.length} messages → MEM0:\n${result}`);

  // Notify user
  event.messages = event.messages || [];
  event.messages.push(`🧠 Session context stored to MEM0 (${messages.length} messages processed)`);
};

export default mem0AutoStore;
