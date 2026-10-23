# Global Jobs CRM

An agent-assisted job search workspace in Notion: vacancies, CV-based preparation, employer replies and referral drafts. It uses **your own CV, accounts and preferences**.

## For you

1. Give your agent access to this repository and the setup instruction below.
2. Add your CV link and preferences to **My profile & CV**. Its **Answer bank** holds reusable answers.
3. Choose sources in **Sources**. Review vacancies in **Jobs** and open each job's draft through **Preparation resources**. Referral drafts are in **Referrals**.
4. Optionally connect your own Gmail to track employer responses. A Jobs mailbox label is optional.

**Nothing is sent by default.** Reading, applications and replies have separate switches. Job statuses are editable: Review, Applied, In progress, Offer, Closed and Needs attention. Latest reply shows the most recent matched employer message of any kind.

Search rules:

- Telegram: prefer posts from the last 14 days; never use posts older than 21 days.
- Websites: an older listing is eligible while the employer still has the opening live. Aggregator links must resolve to a direct employer/contact route.
- Referrals: existing first-degree connections whose **current employer matches the selected vacancy's company**. Former employees and unrelated contacts are excluded.
- One job per employer requisition, one active referral contact per company. Reprocessing must not create duplicates.

## Give this to your agent

> Read README.md, docs/INSTALL.md and agent/RUNBOOK.md. Install this project for me using my own Notion workspace and accounts. Ask for my CV link, missing search preferences and selected services together. Create an empty CRM from examples/notion-bootstrap.json and resolve my own page/data-source IDs. Open required sign-ins in the built-in browser. Leave sources and sending Off until I select them; keep live_sending=false. Run the tests and a duplicate-free collection/preparation replay. Do not send applications, messages, referrals or CV uploads during setup. Report what is connected and what still needs my input.

[Detailed installation for agents](docs/INSTALL.md) · [Runtime commands and recovery](agent/RUNBOOK.md) · [Remaining work](ROADMAP.md)

## What is included

Notion schemas and views; Telegram/HiringCafe/LinkedIn/company-site collection planning; answer-bank and short-letter preparation; Gmail response ingestion; referral draft planning; durable duplicate protection; guarded website/Telegram/SMTP adapters.

Requires Python 3.11+, Node.js 22+ for adapter tests, Git and an agent host with Notion MCP, terminal and browser access. Telegram/Gmail require the owner's own connected integrations. Python dependencies are installed from pyproject.toml; no Node package installation is needed.

**Current limits:** referral sending and automatic LinkedIn reply sync are not implemented. Outbound adapters require owner-specific integration and a verified pilot. CAPTCHA and unsupported forms require manual handling. Scheduling depends on the agent host; an independent cloud executor is not included.

Personal config, CVs, mailbox data, contacts and runtime state stay in ignored config.local.json, .local/ and outputs/. Sharing this repository does not share your private Notion workspace or account connections. A private repository requires GitHub access for each recipient; sharing the URL alone does not grant access.
