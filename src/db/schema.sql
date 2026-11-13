-- Database Schema for LinkedIn 24/7 Multi-Agent System

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    hook_id TEXT NOT NULL,
    content TEXT NOT NULL,
    human_score REAL NOT NULL,
    discord_thread_id TEXT,
    folio_preview_url TEXT,
    status TEXT DEFAULT 'draft', -- 'draft', 'approved', 'published', 'rejected'
    published_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, -- Hash of company + title + location for strict deduplication
    job_id TEXT UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    posted_at DATETIME,
    url TEXT NOT NULL,
    match_score INTEGER NOT NULL,
    scorecard JSON NOT NULL,
    discord_thread_id TEXT,
    status TEXT DEFAULT 'new', -- 'new', 'alerted', 'applied', 'saved', 'dismissed'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT REFERENCES posts(id),
    comment_id TEXT UNIQUE NOT NULL,
    author_name TEXT NOT NULL,
    author_headline TEXT,
    content TEXT NOT NULL,
    category TEXT NOT NULL, -- 'praise', 'question', 'disagree', 'recruiter', 'spam'
    reply_draft TEXT,
    reply_status TEXT DEFAULT 'pending', -- 'auto_posted', 'approved', 'skipped'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata JSON,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outreach_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT UNIQUE NOT NULL,
    company TEXT,
    role_target TEXT DEFAULT 'Product / Analyst Roles',
    sender_email TEXT,
    status TEXT DEFAULT 'pending', -- 'pending', 'sent', 'replied', 'bounced', 'unresponsive', 'paused'
    first_sent_at DATETIME,
    followup_1_at DATETIME,
    followup_2_at DATETIME,
    last_sent_at DATETIME,
    followup_count INTEGER DEFAULT 0,
    last_subject TEXT,
    last_message_id TEXT,
    reply_snippet TEXT,
    reply_received_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
