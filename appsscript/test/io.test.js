// Apps Script I/O glue, exercised with fake globals. Run: node --test appsscript/test
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const code = require(path.join(__dirname, '..', 'Code.gs'));

const blob = (bytes, type = 'image/jpeg') => ({
  getBytes: () => [...bytes],
  getDataAsString: () => Buffer.from(bytes).toString('utf8'),
  getContentType: () => type,
});

global.Utilities = {
  newBlob: (data) => blob(typeof data === 'string' ? Buffer.from(data, 'utf8') : Buffer.from(data)),
  base64Encode: (bytes) => Buffer.from(bytes).toString('base64'),
  computeDigest: (_alg, text) => [...require('node:crypto').createHash('sha256').update(text, 'utf8').digest()],
  DigestAlgorithm: { SHA_256: 'sha256' },
  Charset: { UTF_8: 'utf8' },
  base64Decode: (text) => [...Buffer.from(text, 'base64')],
  base64DecodeWebSafe: (text) => {
    if (text.length % 4) throw new Error('無法解碼字串。');  // like Apps Script: needs padding
    return [...Buffer.from(text, 'base64url')];
  },
};
global.ScriptApp = { getOAuthToken: () => 'oauth' };
const sleeps = [];
global.Utilities.sleep = (ms) => sleeps.push(ms);
global.Logger = { log: () => {} };

function response(code, body) {
  return { getResponseCode: () => code, getContentText: () => (typeof body === 'string' ? body : JSON.stringify(body)) };
}

/** Fake GitHub contents API: existing paths answer GET with a sha; PUTs are recorded. */
function fakeGithub(existing = []) {
  const puts = [];
  global.UrlFetchApp = {
    fetch: (url, options = {}) => {
      const filePath = decodeURIComponent(url.split('/contents/')[1].split('?')[0]);
      if (options.method === 'put') {
        const body = JSON.parse(options.payload);
        puts.push({ path: filePath, sha: body.sha, data: JSON.parse(Buffer.from(body.content, 'base64').toString('utf8')) });
        return response(201, { content: {} });
      }
      return existing.includes(filePath) ? response(200, { sha: `sha-${filePath}` }) : response(404, { message: 'Not Found' });
    },
  };
  return puts;
}

function fakeProperties() {
  const store = {};
  return { getProperty: (k) => store[k] ?? null, setProperty: (k, v) => { store[k] = v; }, store };
}

const LIST = {
  slug: 'weekly-8585-2026-w43', kind: '8585', label: '本週精選書單', title: 't',
  period_start: '2026-10-19', period_end: '2026-10-25', promo_code: 'WK1',
  books: [{ title: 'A', kobo_url: 'https://www.kobo.com/tw/zh/ebook/A', sale_price: 1, rank: 1 }],
};

test('publishInbox commits the parsed list once, then skips identical reruns', () => {
  const props = fakeProperties();
  const puts = fakeGithub();
  assert.equal(code.publishInbox(LIST, 'tok', props), true);
  assert.deepEqual(puts.map((p) => [p.path, p.sha]), [['data/inbox/weekly-8585-2026-w43.json', undefined]]);
  assert.deepEqual(puts[0].data, LIST);
  assert.equal(code.publishInbox(LIST, 'tok', props), false);
  assert.equal(puts.length, 1);
});

test('publishInbox resends when the list changed and reuses an unprocessed inbox sha', () => {
  const props = fakeProperties();
  fakeGithub();
  code.publishInbox(LIST, 'tok', props);
  const puts = fakeGithub(['data/inbox/weekly-8585-2026-w43.json']);
  assert.equal(code.publishInbox({ ...LIST, promo_code: 'WK2' }, 'tok', props), true);
  assert.equal(puts[0].sha, 'sha-data/inbox/weekly-8585-2026-w43.json');
});

test('GitHub errors surface with status and do not record the fingerprint', () => {
  const props = fakeProperties();
  global.UrlFetchApp = { fetch: () => response(401, { message: 'Bad credentials' }) };
  assert.throws(() => code.publishInbox(LIST, 'tok', props), /GitHub 401/);
  assert.deepEqual(props.store, {});
});

test('findHtml walks multipart payloads', () => {
  const html = Buffer.from('<p>哈囉</p>').toString('base64url');
  const payload = { mimeType: 'multipart/alternative', parts: [
    { mimeType: 'text/plain', body: { data: 'eA' } },
    { mimeType: 'multipart/related', parts: [{ mimeType: 'text/html', body: { data: html } }] },
  ] };
  assert.equal(code.findHtml(payload), '<p>哈囉</p>');
  assert.equal(code.findHtml({ mimeType: 'text/plain', body: { data: 'eA' } }), '');
});

test('findHtml accepts decoded byte arrays (advanced service) and unpadded base64url', () => {
  const bytes = [...Buffer.from('<p>位元組</p>')];
  assert.equal(code.findHtml({ mimeType: 'text/html', body: { data: bytes } }), '<p>位元組</p>');
  const unpadded = Buffer.from('<p>ab</p>').toString('base64url');
  assert.ok(!unpadded.endsWith('='));
  assert.equal(code.findHtml({ mimeType: 'text/html', body: { data: unpadded } }), '<p>ab</p>');
});

test('fetchKoboEmails maps Gmail API messages', () => {
  global.Gmail = { Users: { Messages: {
    list: (user, opts) => { assert.match(opts.q, /from:news@shopjp\.kobo\.com/); return { messages: [{ id: 'm1' }] }; },
    get: () => ({ internalDate: '1792375220000', payload: {
      mimeType: 'text/html', body: { data: Buffer.from('<b>x</b>').toString('base64url') },
      headers: [{ name: 'Subject', value: '【本週精選】x' }, { name: 'From', value: 'K <news@shopjp.kobo.com>' }],
    } }),
  } } };
  assert.deepEqual(code.fetchKoboEmails(), [{ subject: '【本週精選】x', from: 'K <news@shopjp.kobo.com>', sentMs: 1792375220000, html: '<b>x</b>' }]);
  global.Gmail.Users.Messages.list = () => ({});
  assert.deepEqual(code.fetchKoboEmails(), []);
});

test('ocrImage uploads as Google Doc, exports text, trashes the temp file', () => {
  const calls = [];
  global.UrlFetchApp = { fetch: (url, options = {}) => {
    calls.push([options.method || 'get', url.replace(/\?.*/, '')]);
    if (!options.method) return { getBlob: () => blob([1, 2, 3]) };
    if (options.method === 'post') {
      assert.match(options.contentType, /multipart\/related/);
      return response(200, { id: 'doc1' });
    }
    if (url.includes('/export')) return response(200, '星期四限定');
    return response(200, {});
  } };
  assert.equal(code.ocrImage('https://shopjp.kobo.com/x/images/bar1.jpg'), '星期四限定');
  assert.deepEqual(calls.map((c) => c[0]), ['get', 'post', 'get', 'patch']);
});

function fakeDrive(script) {
  const calls = [];
  global.UrlFetchApp = { fetch: (url, options = {}) => {
    const step = !options.method ? 'image' : options.method === 'post' ? 'upload' : url.includes('/export') ? 'export' : 'trash';
    calls.push(step);
    if (step === 'image') return { getBlob: () => blob([1]) };
    const queue = script[step] || [];
    const [code, body] = queue.length > 1 ? queue.shift() : queue[0] || [200, {}];
    return response(code, body);
  } };
  return calls;
}

test('ocrImage retries Drive 5xx/429 with backoff and succeeds', () => {
  sleeps.length = 0;
  const calls = fakeDrive({
    upload: [[500, 'Internal Error'], [429, 'slow down'], [200, { id: 'doc1' }]],
    export: [[503, 'x'], [200, '星期五加碼']],
  });
  assert.equal(code.ocrImage('https://shopjp.kobo.com/x/images/bar2.jpg'), '星期五加碼');
  assert.deepEqual(calls, ['image', 'upload', 'upload', 'upload', 'export', 'export', 'trash']);
  assert.deepEqual(sleeps, [1000, 2000, 1000]);
});

test('ocrImage gives up after max attempts and names the failing step', () => {
  const calls = fakeDrive({ upload: [[500, 'Internal Error']] });
  assert.throws(() => code.ocrImage('https://shopjp.kobo.com/x/images/bar1.jpg'), /Drive upload 500/);
  assert.equal(calls.filter((c) => c === 'upload').length, 4);
  assert.ok(!calls.includes('trash'), 'nothing to trash when upload failed');
});

test('ocrImage does not retry 4xx and still trashes after export failure', () => {
  const calls = fakeDrive({ upload: [[200, { id: 'doc1' }]], export: [[403, 'forbidden']] });
  assert.throws(() => code.ocrImage('https://shopjp.kobo.com/x/images/bar1.jpg'), /Drive export 403/);
  assert.deepEqual(calls, ['image', 'upload', 'export', 'trash']);
});

test('ocrImage ignores a failed trash (temp doc left behind) and returns the text', () => {
  fakeDrive({ upload: [[200, { id: 'doc1' }]], export: [[200, '星期四']], trash: [[404, 'gone']] });
  assert.equal(code.ocrImage('https://shopjp.kobo.com/x/images/bar1.jpg'), '星期四');
});

test('runIngest requires the GitHub token and reports missing mail', () => {
  global.PropertiesService = { getScriptProperties: () => ({ getProperty: () => null }) };
  assert.throws(() => code.runIngest(), /GITHUB_TOKEN/);
  global.PropertiesService = { getScriptProperties: () => ({ getProperty: () => 'tok' }) };
  global.Gmail = { Users: { Messages: { list: () => ({}) } } };
  assert.throws(() => code.runIngest(), /No Kobo/);
});

test('runIngest collects per-list failures into one error', () => {
  global.PropertiesService = { getScriptProperties: () => ({ getProperty: () => 'tok' }) };
  global.Gmail = { Users: { Messages: {
    list: () => ({ messages: [{ id: 'm1' }] }),
    get: () => ({ internalDate: '1792375220000', payload: {
      mimeType: 'text/html', body: { data: Buffer.from('<p>template changed</p>').toString('base64url') },
      headers: [{ name: 'Subject', value: '【本週精選】x' }, { name: 'From', value: 'K <news@shopjp.kobo.com>' }],
    } }),
  } } };
  assert.throws(() => code.runIngest(), /bookmarklet backup[\s\S]*8585: No books/);
});
