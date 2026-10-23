# Agent installation

Install for a new owner, using this checkout and that owner's accounts. Read this document and [the runtime runbook](../agent/RUNBOOK.md). Ask for the CV link, target roles/countries, work authorization, compensation, work format and selected services together. Use the host's built-in browser for required sign-ins. Never copy another person's Notion workspace, CV, contacts, email, secrets or ledger.

A compatible agent host must provide terminal execution, Notion MCP and a browser. Telegram and Gmail are optional. Python modules plan and guard work; the host supplies real MCP/browser callbacks. There is no standalone background scraper or one-click cloud deployment.

## 1. Local runtime

Requirements: Python 3.11+, Node.js 22+ for adapter tests, Git, and an agent client with terminal, Notion MCP and browser/CUA access. LinkedIn or ATS authentication uses the owner's browser session where necessary.

Windows PowerShell, from the repository directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m crm.setup init
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/channels.test.js tests/gmail.test.mjs
```

macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m crm.setup init
.venv/bin/python -m unittest discover -s tests -v
node --test tests/channels.test.js tests/gmail.test.mjs
```

Use the environment's Python for subsequent commands. Windows installs tzdata automatically. `setup init` creates private config.local.json, .local/ and outputs/ and refuses to overwrite existing config. These paths are excluded from Git.

## 2. Connect the owner's services

**Notion:** official HTTP MCP `https://mcp.notion.com/mcp`. Authorize your workspace and destination page. Discover actual tool schemas; names vary by client. Verify fetch, database/page creation, update and read-back. [Official setup](https://developers.notion.com/guides/mcp/get-started-with-mcp).

**HiringCafe:** official read-only HTTP MCP `https://hiringcafe.com/api/mcp`; no account required. Verify initialization, tool listing, search and full descriptions. Independently check dates, countries and the final employer route. [Connector documentation](https://hiringcafe.com/documentation/hiringcafe-claude-connector).

**Telegram, optional:** discover the owner's connected personal Telegram MCP and verify the account with a read-only call. Select the owner's Global Jobs folder and record its actual ID. Never copy another owner's sessions, credentials, SSH target or folder ID. If unavailable, report the source as blocked; do not silently start another Telegram client. No alternate Telegram library is bundled.

**LinkedIn/company sites:** use the host's supported browser. Login/CAPTCHA, inaccessible personal feeds and incomplete catalogues mean partial coverage. Optional Referral Connections discovery uses an authenticated first-degree/current-company search. Verify the owner's account, each connection and current employer; keep referral sending Off.

**Gmail, optional:** install the official Gmail plugin from your agent host's plugin directory and connect your own Google account. Complete the account connection through the host's plugin settings and built-in browser; installation commands vary by host. Rediscover available tools after authorization. Verify `gmail_get_profile`, a bounded `gmail_search_emails` query and `gmail_batch_read_email` before marking Email connection & reading On/Connected. Record `email.sender`, the actual host MCP server name and `email.read_transport: "gmail_mcp"` in private config. Bind `agent/gmail.mjs` to those three native tools and a fresh Notion reading switch. It exposes no mail-writing API. Installation alone does not establish account access. [Plugin setup](https://learn.chatgpt.com/docs/plugins).

**Other email providers:** optional SMTP/TLS 465 and read-only IMAP/TLS 993 remain available. Configure hostnames, sender, mailbox and credential environment-variable names privately. Keep secrets out of Notion/Git/logs. Gmail OAuth is independent of SMTP credentials; a working Gmail read connector does not verify SMTP sending. Do not send a test email without authorization.

**Gmail organization, optional:** if the owner requests a folder such as Jobs, use the official Gmail MCP label tools separately from the read-only bridge. Search both that label and new unlabeled hiring mail; review each message before adding the label and verify it afterward. Preserve Inbox/unread state and deleted mail. Exclude newsletters, alerts and unrelated messages. Persist label retries separately from response deduplication. Keep this choice in the owner's private schedule; no mailbox mutation is enabled for a fresh installation.

## 3. Create an empty Notion CRM

Use Notion MCP and examples/notion-bootstrap.json, containing English schemas, five source cards, ten Off channel controls and an empty profile. There is no Python Notion provisioning script.

1. Create Global Jobs CRM with My profile & CV and Run log child pages. Create Answer bank under My profile & CV. A separate global preparation page is not needed for a new workspace.
2. Create Jobs, Sources, Answer bank and Channel settings databases using the supplied CREATE TABLE schemas. Save their **data source UUIDs**, not view IDs or database URLs, and the root/profile/log page UUIDs in config.local.json.
3. Create source/control rows once, using stable control Key values. Keep every switch Off. Add no sample jobs to production. If Gmail reading is selected, create the optional Email responses database after Jobs, replace its jobs_data_source_id placeholder with the actual Jobs data source UUID, and save responses_data_source_id in private config.
4. Home: Sources gallery showing Name, Collection and Run status; Channel settings table showing Feature, Enabled, What it does and Daily limit, sorted by Order, with wrapped descriptions. Hide Key, Order and Connection status. Use On/Off selects. Connection status is internal adapter availability, not a candidate score. Display `Test mode — no applications or replies are sent.` Link profile and log; keep preparation inside job records. Keep plans/install text in Git.
5. Create one Jobs table view showing Role, Company, editable Status, Latest reply, Preparation resources, Apply URL, Source and Response date. Do not create separate shortlist/application-tracker screens. Preparation resources is a URL to that vacancy's preparation child page, not another database. Keep Cover letter, Preparation status and Input needed on the job record too. Hide internal Stage and Rejection reason from the main table; preserve stored data. Status is a native select with Review, Applied, In progress, Offer, Closed and Needs attention. crm.status and crm.communication respect manual status choices while retaining internal delivery state.
6. Answer bank contains reusable profile/CV answers; job-specific letters and form notes belong to the selected job's preparation page. Reuse the existing Preparation resources URL and verify its parent job. If the link is empty, inspect that job's current child pages before creating at most one Preparation child page. On a timeout, reread children before retrying. Preserve existing drafts/manual text. Include a link back to the job, cover letter, form notes and unresolved questions. Read back the parent and content, pass the observed URL as draft.notion_preparation_url, and apply the planner's job update. Replay must reuse the same page/link. A blank link means no preparation has been linked yet. Legacy Application draft URLs normalize to Preparation resources. Keep a legacy global preparation index under Run log if present; new workspaces need none.
7. For Email responses, show Response, Company, Outcome, Review, Received and Job. Add a Needs matching view filtered by Review. Hide Event key, Account, Message ID and Sync state in every view and the page layout. Each row opens as a useful response page; do not put technical markers or raw JSON in its body.
8. Fetch schemas, rows, filters and views again. Confirm unique control keys and no enabled sends. Run `python -m crm.setup check --require-notion`. Local check validates config only; remote MCP read-back is still required.

Legacy Russian workspaces are supported by crm.schema. Migrate columns and options in **separate requests**: rename columns, fetch schema, add English options, update a complete verified inventory and filters/layouts, then remove unused old options. Combining rename with ALTER of the new name can create duplicate properties. Preserve record IDs, manual stages/history and a private pre-migration snapshot.

For referral preparation, create the optional Referrals database after Jobs; replace jobs_data_source_id in its schema and save referrals_data_source_id privately. Use one Referrals view showing Request, Company, Job, Status, Message draft and Input needed. Hide Request key and Account in the view and page layout. Sources → Referral Connections enables discovery/preparation. Channel settings → Referral requests controls future sending and remains Off/Not implemented. See agent/RUNBOOK.md for inputs and replay checks.

## 4. Add the owner's CV and preferences

Put an accessible CV link in My profile & CV. Read the complete document; save exact exported text bytes privately, e.g. .local/cv-current.txt, and their SHA-256. Extract contact details, dates, technologies, education and languages with exact evidence quotes. Separate CV evidence from candidate confirmations. Never invent experience, work rights, metrics, behavioral stories or past applications.

Record roles/countries, location/timezone, authorization/sponsorship, relocation, work format, contract, availability, compensation including currency/period/gross-or-net, and optional-field/data-retention policy. Ask for missing material facts together; leave unknown required answers unresolved. Reconcile applications outside the CRM before sending.

Create stable-key answer records with the CV version. Prepare private normalized profile/form inputs using examples/preparation-format.json. Reusable candidate answers must match profile.confirmed_answers exactly, including evidence. bank_from_notion normalizes escaped rich text; do not compare raw Notion HTML with plain CV text.

## 5. Verify collection and preparation

Enable selected source cards; Telegram also requires a verified connection switch. Collect a small reviewed batch, then apply it twice using planner/MCP/read-back. The second pass must create zero jobs and preserve manual stages and correspondence.

Safe local planner demo:

```sh
python -m crm.collector --snapshot examples/snapshot.json --candidates examples/candidates.json --sources examples/sources.json --output outputs/demo-plan.json
```

The example is deliberately unreviewed/unverified and is not added. The planner writes only a local plan. Actual runs require fresh **complete** snapshots with has_more false, reviewed candidates and verified direct employer routes. A quota-limited/partial query is not a complete inventory.

Acquire the collection lease with crm.run_state begin, retain its private token file, read switches/CV/all jobs, collect, run crm.collector, apply through MCP and read back. Rerun the planner to verify zero repeated creates. Advance successful source checkpoints only after verified writes; preserve partial progress separately. Renew during long runs and release with your own token. Use `python -m crm.run_state --help` for command arguments.

Collection: prefer Telegram posts from the last 14 days; accept up to 21 days maximum and require a known source posting date. Website jobs remain eligible regardless of original posting date only after verifying the actual employer opening is still active. HiringCafe/LinkedIn leads must resolve to an open employer listing. Set candidate.employer_open_verified=true only after that check. Initial bounded discovery uses 14 days; extend Telegram discovery to 21 days where useful. Resume checkpoints with 72-hour overlap; at most 20 qualified new jobs, no quota to fill. Deduplicate by canonical requisition, merge sources, preserve meaningful URL parameters and date provenance. Find employer forms/ATS, company hiring email or verified recruiter contacts. Advertising aggregators are not final routes; Remote does not prove worldwide eligibility.

Private preparation command:

```sh
python -m crm.preparation --profile outputs/profile.json --bank outputs/answer-bank-notion.json --drafts outputs/drafts.json --cv .local/cv-current.txt --jobs outputs/jobs.json --output outputs/preparation
```

Outputs: TXT letters, packs.json, a local read-only preview.html with networking/submission disabled, and a Notion update/link plan. Reuse existing drafts. Letters: 3–5 relevant sentences, at most 100 words. CV/profile/file changes invalidate old preparations. Unknown required fields block sending; skip optional fields except the letter. Never fill live employer forms in test mode: fields can autosave before Submit.

## Delivery and responses

Follow [runtime runbook](../agent/RUNBOOK.md) for executable bridge commands, request formats, native MCP/CUA bindings, response ingestion and Notion synchronization.

- New applications require master and channel switches, connection where applicable, and explicitly enabled private live policy. Replies have independent switches/limits. Live policy defaults to false.
- The SQLite ledger reserves each canonical job before any side effect. Reserved, sent and uncertain jobs block another automatic application across channels. Missing receipts/timeouts require manual reconciliation. SMTP acceptance is delivery evidence, not an employer reply.
- Incoming messages deduplicate by channel/account/message ID. Match an exact application and trusted sender; otherwise Needs matching. Full evidence stays in the private ledger/mailbox; Notion shows a short quote and source link. Reasons require actual quotes; silence has no invented cause. Older responses cannot overwrite newer summaries.
- The durable outbox separates Notion sync from sending. One response page per hidden event identity; reuse pending pages after interruptions, verify job values and finalize the response receipt before acknowledging. Replays never send another application or create another response page. Preserve manual/advanced stages.
- Web adapters support reviewed labeled fields, native selects and host-provided file uploads. Changed forms, unsupported widgets, login/CAPTCHA, legal agreements and unknown required facts go to manual review. Live provider/ATS delivery has not been validated with a real application in this workspace.
- Tests use synthetic data, temporary databases, fake native tools/SMTP/IMAP and local form fixtures. Each owner must connect and verify their own email account before inbox processing.

## Scheduling and remaining work

Local scheduling can combine job collection with **read-only Gmail/status synchronization** and needs the machine/agent app running. Update the existing schedule through the host's supported tools; keep its cadence/timezone, use one lease, independent checkpoints and fresh switches. Sources Off must not suppress enabled Gmail reading; Gmail Off must prevent mailbox calls/cursor updates. Resume mail windows with 72-hour overlap and stable IDs. Notify only about new actionable results. Scheduled sends remain prohibited. The shared durable cloud executor and live referral delivery remain future work; see [remaining work](../ROADMAP.md).

## Troubleshooting and private data

- Notion query quota/incomplete pagination: save partial progress; do not write from stale/incomplete inventory. Individual fetches may still work.
- Missing/off/duplicate settings, disconnected accounts, stale settings or CV mismatch: block that operation and name the missing prerequisite.
- HiringCafe 429: respect Retry-After and retain continuation instead of immediate repeats.
- Ambiguous delivery: keep Delivery uncertain; reconcile evidence. Never delete ledger rows to bypass deduplication.
- Keep .local/, outputs/, config.local.json, CVs, cookies, sessions and secrets out of Git. Back up the private ledger separately. Do not publish local AGENTS.md or another candidate's data.

Cloud execution is not included. Each owner must create their own host schedule after setup; no schedule, secret or account session is installed by cloning this repository.


## Private configuration map

Write only the current owner's values into config.local.json:

- timezone: the owner's IANA timezone, including seasonal clock changes.
- notion.root_page_id: Global Jobs CRM page; profile_page_id: My profile & CV, including Answer bank; rules_page_id: Run log. Optional preparation_page_id is retained only for a legacy preparation index; leave empty in new workspaces. Each job stores its own Preparation resources URL.
- notion.jobs_data_source_id, sources_data_source_id, answers_data_source_id, actions_data_source_id: the actual collection UUIDs returned by Notion. Optional responses_data_source_id and referrals_data_source_id are empty until those databases are created. A collection UUID is not a database/view UUID.
- telegram.mcp_server and folder_id: the owner's discovered connected personal MCP and selected jobs folder. Never install or borrow another owner's Telegram session.
- email.sender and mcp_server: the verified owner's account and actual Gmail connector. Use read_transport=gmail_mcp for Gmail. SMTP/IMAP host, port, mailbox and credential environment-variable names are needed only if the owner chooses those transports; Gmail OAuth alone does not enable SMTP.
- collection: prefer 14 days, Telegram hard maximum 21 days, overlap 72 hours, at most 20 new jobs.
- paths: workspace-relative private_dir=.local and output_dir=outputs.
- delivery.live_sending=false; every fresh source/control starts Off. Connection controls may turn On only for services explicitly selected and verified.

Do not store provider tokens, cookies or passwords in this config. Record only environment-variable names for SMTP credentials. Optional Gmail labeling and the employer rotation belong to the owner's private schedule/profile, not shared repository defaults.
