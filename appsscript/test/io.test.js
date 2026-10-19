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
  base64Decode: (text) => [...Buffer.from(text, 'base64')],
  base64DecodeWebSafe: (text) => [...Buffer.from(text, 'base64url')],
};
global.ScriptApp = { getOAuthToken: () => 'oauth' };
global.Logger = { log: () => {} };

function response(code, body) {
  return { getResponseCode: () => code, getContentText: () => (typeof body === 'string' ? body : JSON.stringify(body)) };
}

/** Fake GitHub contents API backed by an in-memory repo; records PUTs. */
function fakeGithub(files) {
  const puts = [];
  global.UrlFetchApp = {
    fetch: (url, options = {}) => {
      const filePath = decodeURIComponent(url.split('/contents/')[1].split('?')[0]);
      if (options.method === 'put') {
        const body = JSON.parse(options.payload);
        puts.push({ path: filePath, sha: body.sha, data: JSON.parse(Buffer.from(body.content, 'base64').toString('utf8')) });
        return response(201, { content: {} });
      }
      if (!(filePath in files)) return response(404, { message: 'Not Found' });
      const content = Buffer.from(`${JSON.stringify(files[filePath], null, 2)}\n`).toString('base64');
      return response(200, { content, sha: `sha-${filePath}` });
    },
  };
  return puts;
}

const LIST = {
  slug: 'weekly-8585-2026-w43', kind: '8585', label: '本週精選書單', title: 't',
  period_start: '2026-10-19', period_end: '2026-10-25', promo_code: 'WK1',
  books: [{ title: 'A', kobo_url: 'https://www.kobo.com/tw/zh/ebook/A', sale_price: 1, rank: 1 }],
};

test('publishList creates list + manifest when the repo has neither', () => {
  const puts = fakeGithub({});
  assert.equal(code.publishList(LIST, 'tok'), true);
  assert.deepEqual(puts.map((p) => [p.path, p.sha]), [['data/weekly-8585-2026-w43.json', undefined], ['data/manifest.json', undefined]]);
  assert.equal(puts[1].data.lists[0].slug, LIST.slug);
});

test('publishList is a no-op when merged data is unchanged', () => {
  const manifest = code.updateManifest({}, LIST);
  const puts = fakeGithub({ 'data/weekly-8585-2026-w43.json': LIST, 'data/manifest.json': manifest });
  assert.equal(code.publishList(LIST, 'tok'), false);
  assert.deepEqual(puts, []);
});

test('publishList updates existing file with its sha and keeps blog fields', () => {
  const existing = { ...LIST, books: [{ ...LIST.books[0], title: 'A longer blog title', author: 'X', sale_price: undefined }] };
  const puts = fakeGithub({ 'data/weekly-8585-2026-w43.json': existing, 'data/manifest.json': code.updateManifest({}, LIST) });
  assert.equal(code.publishList(LIST, 'tok'), true);
  assert.equal(puts.length, 1);
  assert.equal(puts[0].sha, 'sha-data/weekly-8585-2026-w43.json');
  assert.deepEqual([puts[0].data.books[0].title, puts[0].data.books[0].author, puts[0].data.books[0].sale_price], ['A longer blog title', 'X', 1]);
});

test('GitHub errors surface with status', () => {
  global.UrlFetchApp = { fetch: () => response(401, { message: 'Bad credentials' }) };
  assert.throws(() => code.publishList(LIST, 'tok'), /GitHub 401/);
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

test('ocrImage still trashes the temp file when export fails', () => {
  const methods = [];
  global.UrlFetchApp = { fetch: (url, options = {}) => {
    methods.push(options.method || 'get');
    if (!options.method) return { getBlob: () => blob([1]) };
    if (options.method === 'post') return response(200, { id: 'doc1' });
    if (url.includes('/export')) return response(500, 'boom');
    return response(200, {});
  } };
  assert.throws(() => code.ocrImage('https://shopjp.kobo.com/x/images/bar1.jpg'), /Drive 500/);
  assert.equal(methods.at(-1), 'patch');
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
