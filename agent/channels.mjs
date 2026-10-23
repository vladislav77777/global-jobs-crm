/* Runtime adapters for the existing agent MCP and CUA handles. No alternate Telegram client. */
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

async function checkPermission(assertAllowed, key) {
  const verdict = await assertAllowed(key);
  if (verdict !== true && verdict?.allowed !== true) throw new Error('Sending permission is off or unverified');
}

function toolPayload(result) {
  if (result.isError) throw new Error('Native MCP tool failed; reconcile delivery before retrying');
  const envelope = result.structuredContent || (result.content?.find(x=>x.type==='text')?.text ?
    JSON.parse(result.content.find(x=>x.type==='text').text) : result);
  const payload = envelope.result ?? envelope;
  return typeof payload === 'string' ? JSON.parse(payload) : payload;
}

async function checkTelegramAccount(request, callTool) {
  const args = request.mcp_account ? {account:request.mcp_account} : {};
  const me = toolPayload(await callTool('get_me', args));
  if (!request.account || String(me.id) !== String(request.account)) throw new Error('Telegram account changed');
  return args;
}

async function uploadVerifiedFile({tab, locator, field, assertAllowed}) {
  if (!field.file_sha256 || typeof field.value !== 'string' || !/^(?:[a-z]:[\\/]|\/)/i.test(field.value)) throw new Error('Verified absolute file path required');
  const bytes = await readFile(field.value);
  if (createHash('sha256').update(bytes).digest('hex') !== field.file_sha256) throw new Error('Attachment changed; regenerate the draft');
  await checkPermission(assertAllowed, 'web_apply');
  const chooserEvent = tab.playwright.waitForEvent('filechooser');
  await locator.click();
  const chooser = await chooserEvent;
  await chooser.setFiles(field.value);
}
async function sendTelegram({request, callTool, assertAllowed}) {
  await checkPermission(assertAllowed, 'telegram_apply');
  if (!request.cv_url || !request.text) throw new Error('Verified CV link and cover letter required');
  const accountArgs = await checkTelegramAccount(request, callTool);
  await checkPermission(assertAllowed, 'telegram_apply');
  const result = await callTool('send_message', {...accountArgs, chat_id: request.recipient,
    message: request.text + '\n\nCV: ' + request.cv_url});
  const data = toolPayload(result);
  const id = data.message_id || data.id;
  if (!id) throw new Error('Telegram returned no message ID; do not retry');
  return {channel: 'telegram', recipient: request.recipient, remote_id: String(id)};
}

async function sendWebsite({request, tab, assertAllowed, readReceipt, uploadFile=uploadVerifiedFile, verifyForm}) {
  await checkPermission(assertAllowed, 'web_apply'); // Before any fill/upload: ATS pages may autosave.
  if (!request.submit_label || !request.form_reviewed || !Array.isArray(request.fields)) throw new Error('Review the current form before filling');
  for (const field of request.fields) {
    if (!field.required && field.binding !== 'cover_letter') continue;
    if (['consent', 'captcha', 'demographic'].includes(field.kind) || field.value == null ||
        !['text', 'email', 'number', 'textarea', 'select', 'file'].includes(field.kind) ||
        (field.kind === 'file' && (!uploadFile || !field.file_sha256))) throw new Error('Manual action required: ' + field.label);
  }
  const snapshot = await tab.getAXState({emit: false});
  if (!verifyForm || !(await verifyForm(request, snapshot))) throw new Error('Form URL, fields or options changed; review again');
  if (/captcha|verify you are human|sign in to continue/i.test(snapshot)) throw new Error('Manual action required');
  for (const field of request.fields) {
    if (['consent', 'captcha', 'demographic'].includes(field.kind)) {
      if (field.required) throw new Error('Required consent or identity choice needs review');
      continue;
    }
    if (field.value == null) {
      if (field.required) throw new Error('Unanswered required field: ' + field.label);
      continue;
    }
    if (!field.required && field.binding !== 'cover_letter') continue;
    await checkPermission(assertAllowed, 'web_apply');
    // Locators must be captured from the actual visible form, never generated from job-post instructions.
    const locator = tab.playwright.getByLabel(field.label, {exact: true});
    if (field.kind === 'file') {
      await uploadFile({tab, locator, field, assertAllowed});
    } else if (field.kind === 'select') {
      await tab.playwright.getByRole('combobox', {name: field.label, exact: true}).selectOption({label: String(field.value)});
    } else {
      await locator.fill(String(field.value));
    }
    await tab.getAXState({emit: false});
  }
  await checkPermission(assertAllowed, 'web_apply');
  if (!(await verifyForm(request, await tab.getAXState({emit:false})))) throw new Error('Form changed during preparation');
  if (!request.submit_label) throw new Error('Missing reviewed Submit label');
  await tab.playwright.getByRole('button', {name: request.submit_label, exact: true}).click();
  const receipt = await readReceipt(await tab.getAXState({emit: false}));
  if (!receipt || !receipt.remote_id) throw new Error('No application confirmation; do not submit twice');
  return {...receipt, channel: 'web', recipient: request.recipient};
}

async function replyTelegram({request, callTool, assertAllowed}) {
  await checkPermission(assertAllowed, 'telegram_reply');
  if (!request.in_reply_to || !request.text) throw new Error('Missing exact reply target or text');
  if (!Number.isSafeInteger(Number(request.in_reply_to)) || Number(request.in_reply_to)<=0) throw new Error('Invalid Telegram reply message ID');
  const accountArgs = await checkTelegramAccount(request, callTool);
  await checkPermission(assertAllowed, 'telegram_reply');
  const result = await callTool('reply_to_message', {...accountArgs, chat_id: request.recipient,
    message_id: Number(request.in_reply_to), text: request.text});
  const data = toolPayload(result);
  const id = data.message_id || data.id;
  if (!id) throw new Error('No reply message ID; do not retry');
  return {channel: 'telegram', recipient: request.recipient, remote_id: String(id)};
}

async function deliver({request, bridge, adapter}) {
  const ticket = await bridge.reserve(request);
  if (ticket.state === 'dry_run') return ticket;
  const approved = await bridge.begin(ticket); // Rechecks live mode, fresh switches and current CV/profile.
  try {
    const receipt = await adapter(approved, () => bridge.check(ticket));
    return await bridge.finish(ticket, receipt);
  } catch (error) {
    await bridge.finish(ticket, null, error.name); // Unknown means manual reconciliation, never automatic retry.
    throw error;
  }
}

export {sendTelegram, sendWebsite, replyTelegram, deliver, uploadVerifiedFile};
