#!/usr/bin/env bun
/**
 * Log a skill execution.
 * Usage: bun scripts/log-run.ts <skill> <status> [options]
 * 
 * Options:
 *   --task "description"    What the skill was used for
 *   --error "error msg"     Error message (if status=failure)
 *   --duration <ms>         Execution time in ms
 *   --session <key>         Session identifier
 *   --feedback "feedback"   User feedback on the run
 * 
 * Examples:
 *   bun scripts/log-run.ts deep-research-pro success --task "Research Cognee" --duration 45000
 *   bun scripts/log-run.ts skill-monitor failure --error "DB connection refused"
 *   bun scripts/log-run.ts deep-research-pro success --feedback "Good analysis, too verbose"
 */

import { Database } from "bun:sqlite";

const DB_PATH = process.env.SKILL_MONITOR_DB || 
  `${process.env.HOME}/.openclaw/workspace/data/skill-monitor/skill-runs.db`;

const args = process.argv.slice(2);

if (args.length < 2) {
  console.error("Usage: bun log-run.ts <skill> <success|failure|partial> [options]");
  process.exit(1);
}

const skill = args[0];
const status = args[1];

// Parse optional flags
function getFlag(name: string): string | undefined {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && idx + 1 < args.length ? args[idx + 1] : undefined;
}

const task = getFlag("task");
const error = getFlag("error");
const duration = getFlag("duration");
const session = getFlag("session");
const feedback = getFlag("feedback");

let db: Database;
try {
  db = new Database(DB_PATH);
} catch (e) {
  console.error(`❌ Cannot open DB at ${DB_PATH}. Run setup.ts first.`);
  process.exit(1);
}

// Log the run
db.prepare(`
  INSERT INTO skill_runs (skill, task, status, error, duration_ms, session)
  VALUES (?, ?, ?, ?, ?, ?)
`).run(skill, task, status, error || null, duration ? parseInt(duration) : null, session || null);

// Log feedback if provided
if (feedback) {
  db.prepare(`
    INSERT INTO skill_feedback (skill, feedback, source)
    VALUES (?, ?, 'user')
  `).run(skill, feedback);
}

// Get stats for this skill
const stats = db.prepare(`
  SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
    SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END) as failures
  FROM skill_runs WHERE skill = ?
`).get(skill) as any;

db.close();

const rate = stats.total > 0 
  ? ((stats.successes / stats.total) * 100).toFixed(1) 
  : "N/A";

console.log(`📊 ${skill}: ${status} (overall: ${rate}% success, ${stats.total} runs)`);
