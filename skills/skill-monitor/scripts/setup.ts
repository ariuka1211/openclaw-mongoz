#!/usr/bin/env bun
/**
 * Initialize skill-monitor SQLite database.
 * Run once on first use: bun skills/skill-monitor/scripts/setup.ts
 */

import { Database } from "bun:sqlite";
import { mkdirSync } from "fs";
import { dirname } from "path";

const DB_PATH = process.env.SKILL_MONITOR_DB || 
  `${process.env.HOME}/.openclaw/workspace/data/skill-monitor/skill-runs.db`;

mkdirSync(dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);

db.exec(`
  CREATE TABLE IF NOT EXISTS skill_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill TEXT NOT NULL,
    task TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'failure', 'partial')),
    error TEXT,
    duration_ms INTEGER,
    session TEXT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE INDEX IF NOT EXISTS idx_skill ON skill_runs(skill);
  CREATE INDEX IF NOT EXISTS idx_timestamp ON skill_runs(timestamp);
  CREATE INDEX IF NOT EXISTS idx_status ON skill_runs(status);

  CREATE TABLE IF NOT EXISTS skill_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill TEXT NOT NULL,
    feedback TEXT NOT NULL,
    source TEXT DEFAULT 'user',
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
  );
`);

db.close();
console.log(`✅ Skill monitor DB initialized at ${DB_PATH}`);
