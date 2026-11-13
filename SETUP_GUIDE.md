# 💻 Personal Laptop Setup & Operations Guide

This guide walks you through setting up, configuring, and operating the **Autonomous Career Acceleration & Outreach Agent** on any personal laptop (macOS, Linux, or Windows WSL2).

---

## 📑 Table of Contents
1. [Prerequisites](#1-prerequisites)
2. [Cloning the Repository](#2-cloning-the-repository)
3. [Virtual Environment & Dependencies](#3-virtual-environment--dependencies)
4. [Environment Configuration (`.env`)](#4-environment-configuration-env)
5. [Database & Resume Sync](#5-database--resume-sync)
6. [Operating Modes](#6-operating-modes)
7. [Important Assets to Carry & Keep Safe](#7-important-assets-to-carry--keep-safe)

---

## 1. Prerequisites

Before setting up, ensure your personal laptop has:
* **Operating System**: macOS (Intel/Apple Silicon), Ubuntu/Debian Linux, or Windows 10/11 with **WSL2** (Ubuntu).
* **Git**: Installed and connected to your GitHub account (`git --version`).
* **Python**: Python **3.9**, **3.10**, or **3.11** installed (`python3 --version`).
* **SSH Client**: Standard OpenSSH (`ssh -V`).

---

## 2. Cloning the Repository

Clone the private/personal repository from GitHub:

```bash
git clone https://github.com/vishal-g17/career-agent.git
cd career-agent
```

---

## 3. Virtual Environment & Dependencies

Always use an isolated virtual environment to prevent package version conflicts:

```bash
# 1. Create a dedicated virtual environment
python3 -m venv venv

# 2. Activate the virtual environment
# On macOS / Linux:
source venv/bin/activate
# On Windows (cmd):
# venv\Scripts\activate.bat
# On Windows (PowerShell):
# venv\Scripts\Activate.ps1

# 3. Upgrade pip and install all project dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Install Playwright browser binaries (Headless Chromium)
playwright install chromium
```

---

## 4. Environment Configuration (`.env`)

Secrets and API tokens are **strictly excluded from git** for security. Create your local `.env` file from the provided template:

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```ini
# ==============================================================================
# DATABASE PATH
# ==============================================================================
# Path to your SQLite database (leave default or specify absolute path)
DATABASE_PATH=~/.linkedin_agent.db

# ==============================================================================
# AI / LLM CREDENTIALS
# ==============================================================================
GEMINI_API_KEY=your_gemini_api_key_here

# ==============================================================================
# DISCORD COMMAND CENTER (#prompt)
# ==============================================================================
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_PROMPT_CHANNEL_ID=1570342549435187290
DISCORD_NOTIFICATIONS_WEBHOOK_URL=your_webhook_url_here

# ==============================================================================
# COLD EMAIL SENDER 1: NSUT WEBMAIL (Primary / High Trust)
# ==============================================================================
SMTP_SERVER=mail.nsut.ac.in
SMTP_PORT=587
SMTP_USER=vishal90301@nsut.ac.in
SMTP_PASS=your_nsut_webmail_password
SENDER_NAME="Vishal Gupta"

# ==============================================================================
# COLD EMAIL SENDER 2: GMAIL SMTP (Secondary / Failover)
# ==============================================================================
GMAIL_USER=vishal90301@gmail.com
GMAIL_APP_PASS=your_gmail_16_char_app_password

# ==============================================================================
# IMAP INBOX LISTENER (Reply & Bounce Auto-Detection)
# ==============================================================================
IMAP_SERVER=mail.nsut.ac.in
IMAP_PORT=993
IMAP_USER=vishal90301@nsut.ac.in
IMAP_PASS=your_nsut_webmail_password
```

---

## 5. Database & Resume Sync

### A. Candidate Resume
Ensure your ATS resume PDF is present in `data/resume.pdf` or root `Vishal_Gupta_Resume.pdf`:
```bash
ls -lh data/resume.pdf Vishal_Gupta_Resume.pdf
```

### B. Syncing Live Production Data from Cloud VM
If you want to pull the exact live SQLite database with all your historic metrics (1,495 sent emails, 153 applied jobs, 636 tracked jobs) onto your personal laptop, use `scp`:

```bash
# Download production database from Cloud VM to your local machine
scp -i /path/to/agent-vm-key.key opc@130.210.108.55:/home/opc/.linkedin_agent.db ~/.linkedin_agent.db
```

---

## 6. Operating Modes

You have **three ways** to interact with the system from your personal laptop:

### Mode 1: Cloud-Only Control (Recommended — 0 Laptop Resource Usage)
Since the system is **already running 24/7 on your Oracle Cloud VM**, you don't even need to run Python on your laptop!
1. Open Discord on your phone or laptop.
2. Go to `#prompt` channel.
3. Type:
   * `!status` — View real-time pipeline, email, and process stats.
   * `!sh <command>` — Run any shell command on the cloud VM (e.g., `!sh git log -1`, `!sh free -m`).
   * Ask questions — The integrated Gemini AI responds with career system updates.

### Mode 2: SSH Direct Cloud VM Management
Log into your cloud VM from any terminal:
```bash
ssh -i /path/to/agent-vm-key.key opc@130.210.108.55
```
Useful commands on the VM:
```bash
# Check status of the 3 continuous daemons
systemctl status linkedin-portal-worker.service linkedin-email-worker.service linkedin-discord-listener.service

# View live portal worker logs (scraping & applying)
journalctl -u linkedin-portal-worker.service -f

# View live email worker logs
journalctl -u linkedin-email-worker.service -f

# Restart a service
sudo systemctl restart linkedin-portal-worker.service
```

### Mode 3: Running Locally on Your Personal Laptop
If you want to run scraping or tests locally:
```bash
# 1. Run a one-off job hunt across LinkedIn, Indeed, Naukri:
python3 -c "from src.agents.job_hunter import run_job_hunt; print(run_job_hunt())"

# 2. Run the continuous portal applicant locally:
python3 scripts/run_portal_worker.py

# 3. Run the cold email dispatcher locally:
python3 scripts/run_email_worker.py

# 4. Run the local Discord listener:
python3 src/agents/discord_listener.py
```

---

## 7. Important Assets to Carry & Keep Safe

Store the following critical items in a safe, personal location (e.g., a personal Google Drive, password manager, or secure thumb drive):

| Item | Description | Where to Find |
| :--- | :--- | :--- |
| **`agent-vm-key.key`** | Private SSH Key for Cloud VM (`130.210.108.55`) | Download folder or your backup drive. |
| **`.env` File** | All SMTP, Gmail App Passwords, and Discord tokens | `/home/opc/career-agent/.env` on VM. |
| **GitHub Repo** | Codebase repository | `https://github.com/vishal-g17/career-agent.git` |
| **Database File** | SQLite database (`.linkedin_agent.db`) | `/home/opc/.linkedin_agent.db` on VM. |
| **Resume PDF** | Latest LaTeX-compiled ATS CV | `Vishal_Gupta_Resume.pdf` |
