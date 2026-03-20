#!/usr/bin/env bun
/**
 * Audit skill execution history for degradation patterns.
 * Usage: bun scripts/audit.ts [--skill <name>] [--days <n>] [--format text|json]
 */

import { Database } from "bun:sqlite";

const DB_PATH = process.env.SKILL_MONITOR_DB || 
  `${process.env.HOME}/.openclaw/workspace/data/skill-monitor/skill-runs.db`;

const args = process.argv.slice(2);
function getFlag(name: string): string | undefined {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && idx + 1 < args.length ? args[idx + 1] : undefined;
}

const filterSkill = getFlag("skill");
const days = parseInt(getFlag("days") || "30");
const format = getFlag("format") || "text";

let db: Database;
try {
  db = new Database(DB_PATH, { readonly: true });
} catch (e) {
  console.error(`❌ Cannot open DB at ${DB_PATH}. Run setup.ts first.`);
  process.exit(1);
}

interface SkillStats {
  skill: string;
  total: number;
  successes: number;
  failures: number;
  partials: number;
  success_rate: number;
  avg_duration_ms: number | null;
  last_run: string;
  last_failure: string | null;
  common_errors: string;
  trend: string;
}

// Get overall stats per skill
const skillClause = filterSkill ? "WHERE skill = ?" : "";
const skillParams = filterSkill ? [filterSkill] : [];

const overallStats = db.prepare(`
  SELECT 
    skill,
    COUNT(*) as total,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
    SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END) as failures,
    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) as partials,
    ROUND(100.0 * SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate,
    ROUND(AVG(duration_ms)) as avg_duration_ms,
    MAX(timestamp) as last_run,
    MAX(CASE WHEN status = 'failure' THEN timestamp END) as last_failure
  FROM skill_runs ${skillClause}
  GROUP BY skill
  ORDER BY total DESC
`).all(...skillParams) as SkillStats[];

// Get common errors per skill
for (const stat of overallStats) {
  const errors = db.prepare(`
    SELECT error, COUNT(*) as count
    FROM skill_runs
    WHERE skill = ? AND error IS NOT NULL
    GROUP BY error
    ORDER BY count DESC
    LIMIT 3
  `).all(stat.skill) as { error: string; count: number }[];
  
  stat.common_errors = errors.map(e => `"${e.error}" (×${e.count})`).join(", ") || "none";

  // Trend: compare last 7 days vs previous period
  const recent = db.prepare(`
    SELECT 
      ROUND(100.0 * SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) / COUNT(*), 1) as rate
    FROM skill_runs
    WHERE skill = ? AND timestamp > datetime('now', '-7 days')
  `).get(stat.skill) as any;

  const previous = db.prepare(`
    SELECT 
      ROUND(100.0 * SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) / COUNT(*), 1) as rate
    FROM skill_runs
    WHERE skill = ? 
      AND timestamp <= datetime('now', '-7 days')
      AND timestamp > datetime('now', '-14 days')
  `).get(stat.skill) as any;

  if (recent?.rate !== null && previous?.rate !== null) {
    const diff = recent.rate - previous.rate;
    if (diff > 5) stat.trend = `📈 improving (+${diff.toFixed(1)}%)`;
    else if (diff < -5) stat.trend = `📉 degrading (${diff.toFixed(1)}%)`;
    else stat.trend = `➡️ stable (${recent.rate}%)`;
  } else {
    stat.trend = `— insufficient data`;
  }
}

// Get recent failures (last 10)
const recentFailures = db.prepare(`
  SELECT skill, task, error, timestamp
  FROM skill_runs
  WHERE status = 'failure'
    ${filterSkill ? "AND skill = ?" : ""}
    AND timestamp > datetime('now', '-${days} days')
  ORDER BY timestamp DESC
  LIMIT 10
`).all(...skillParams) as any[];

// Get unlogged skills (skills in workspace but no runs recorded)
const fs = require("fs");
const path = require("path");
const skillsDir = path.join(process.env.HOME!, ".openclaw/workspace/skills");
const allSkills = fs.readdirSync(skillsDir).filter((f: string) => 
  fs.statSync(path.join(skillsDir, f)).isDirectory()
);
const loggedSkills = new Set(overallStats.map(s => s.skill));
const untracked = allSkills.filter((s: string) => !loggedSkills.has(s));

db.close();

// Output
if (format === "json") {
  console.log(JSON.stringify({ overallStats, recentFailures, untracked }, null, 2));
} else {
  console.log("# 🔍 Skill Monitor Audit Report\n");
  console.log(`Period: last ${days} days | Generated: ${new Date().toISOString()}\n`);

  if (overallStats.length === 0) {
    console.log("No skill runs recorded yet. Start logging with `bun log-run.ts <skill> <status>`\n");
  } else {
    console.log("## Skill Health Overview\n");
    console.log("| Skill | Runs | Success | Rate | Trend | Last Run |");
    console.log("|-------|------|---------|------|-------|----------|");
    for (const s of overallStats) {
      const ago = s.last_run ? timeAgo(s.last_run) : "never";
      console.log(`| ${s.skill} | ${s.total} | ${s.successes} | ${s.success_rate}% | ${s.trend} | ${ago} |`);
    }
    console.log("");

    // Flag skills needing attention
    const degraded = overallStats.filter(s => s.trend.includes("degrading"));
    const lowRate = overallStats.filter(s => s.success_rate < 70 && s.total >= 3);
    const problems = [...new Set([...degraded, ...lowRate])];

    if (problems.length > 0) {
      console.log("## ⚠️ Skills Needing Attention\n");
      for (const p of problems) {
        console.log(`**${p.skill}**: ${p.success_rate}% success, ${p.trend}`);
        if (p.common_errors !== "none") {
          console.log(`  Errors: ${p.common_errors}`);
        }
        if (p.avg_duration_ms) {
          console.log(`  Avg duration: ${(p.avg_duration_ms / 1000).toFixed(1)}s`);
        }
        console.log("");
      }
    }

    if (recentFailures.length > 0) {
      console.log("## ❌ Recent Failures\n");
      for (const f of recentFailures) {
        console.log(`- **${f.skill}** (${timeAgo(f.timestamp)}): ${f.error || f.task || "no details"}`);
      }
      console.log("");
    }
  }

  if (untracked.length > 0) {
    console.log("## 📝 Untracked Skills (no runs logged)\n");
    for (const s of untracked) {
      console.log(`- ${s}`);
    }
    console.log("");
  }

  console.log("---\n💡 Add `--skill <name>` to focus, `--days <n>` for different period, `--format json` for machine output.");
}

function timeAgo(timestamp: string): string {
  const diff = Date.now() - new Date(timestamp + "Z").getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
