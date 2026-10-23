/* Read-only bridge to the host's official Gmail MCP. No sending API is exposed. */
function payload(result) {
  if (result?.isError) throw new Error('Gmail MCP read failed');
  let value = result?.structuredContent ?? result;
  if (!result?.structuredContent && result?.content) {
    const text = result.content.find(x => x.type === 'text')?.text;
    if (!text) throw new Error('Missing Gmail MCP payload');
    value = JSON.parse(text);
  }
  return value;
}

function mimeParts(part) {
  // Never import attached files or attached emails as the message body.
  if (!part || part.filename || /attachment/i.test(
    (part.headers || []).find(h => h.name.toLowerCase() === 'content-disposition')?.value || '')) return [];
  return [part, ...(part.parts || []).flatMap(mimeParts)];
}

function normalizeMessage(message, account) {
  if (!message?.id || !/^[a-f0-9]+$/i.test(message.id)) throw new Error('Missing immutable Gmail message ID');
  const headers = Object.fromEntries((message.payload?.headers || []).map(h => [h.name.toLowerCase(), h.value]));
  const parts = mimeParts(message.payload);
  const plain = parts.filter(p => p.mime_type === 'text/plain' && p.body?.content).map(p => p.body.content);
  const html = parts.filter(p => p.mime_type === 'text/html' && p.body?.content).map(p => p.body.content);
  // HTML stays inert evidence; no URLs, pixels or embedded instructions are executed.
  const text = (plain.length ? plain : html).join('\n');
  const timestamp = Number(message.internal_date) / 1000;
  const from = headers.from || '';
  const sender = (from.match(/<([^<>\s]+@[^<>\s]+)>/)?.[1] || from.trim()).toLowerCase();
  if (!text || !Number.isFinite(timestamp) || timestamp <= 0 || !/^[^\s<>@]+@[^\s<>@]+$/.test(sender)) {
    throw new Error('Message body, sender or provider timestamp is missing; review original');
  }
  return {channel: 'email', account, remote_id: 'gmail:' + message.id,
    gmail_id: message.id, thread_id: message.thread_id, message_id: headers['message-id'] || '',
    sender, subject: headers.subject || '', text, occurred_at: timestamp,
    in_reply_to: ((headers.references || '') + ' ' + (headers['in-reply-to'] || '')).match(/<[^<>]+>/g) || [],
    url: 'https://mail.google.com/mail/u/?authuser=' + encodeURIComponent(account) + '#all/' + message.id};
}

async function readGmail({account, query, callTool, assertReadable, maxPages = 5, pageSize = 50}) {
  if (!account || !query || !Number.isInteger(maxPages) || maxPages < 1 ||
      !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new Error('Explicit account, query and bounded page sizes required');
  async function check() {
    const allowed = await assertReadable('email_connect');
    if (allowed !== true && allowed?.allowed !== true) throw new Error('Email reading is Off or unverified');
  }
  await check();
  const profile = payload(await callTool('gmail_get_profile', {}));
  if ((profile.email || profile.emailAddress || '').toLowerCase() !== account.toLowerCase()) throw new Error('Gmail account changed');
  let next, pages = 0;
  const messages = new Map(), tokens = new Set();
  do {
    await check();
    const list = payload(await callTool('gmail_search_emails', {query, max_results: pageSize,
      ...(next ? {next_page_token: next} : {})}));
    if (!Array.isArray(list.emails)) throw new Error('Incomplete Gmail search response');
    const ids = [...new Set(list.emails.map(x => x.id))].filter(x => !messages.has(x));
    if (ids.length) {
      await check();
      const batch = payload(await callTool('gmail_batch_read_email', {message_ids: ids}));
      if (!Array.isArray(batch.responses) || batch.responses.length !== ids.length ||
          new Set(batch.responses.map(x => x.id)).size !== ids.length ||
          batch.responses.some(x => !ids.includes(x.id))) throw new Error('Incomplete Gmail batch; do not advance checkpoint');
      for (const message of batch.responses) messages.set(message.id, normalizeMessage(message, account));
    }
    next = list.next_page_token;
    if (next && tokens.has(next)) throw new Error('Repeated Gmail page token');
    if (next) tokens.add(next);
    pages++;
  } while (next && pages < maxPages);
  return {account, messages: [...messages.values()], complete: !next, pages, next_page_token: next || null};
}

export {readGmail, normalizeMessage};
