/**
 * Kobo weekly lists → GitHub Pages, run by Apps Script time triggers (Thu/Mon 10:30 Asia/Taipei).
 *
 * Reads the latest 【今日 99】/【本週精選】 newsletter from Gmail (read-only), reads the 99 email's
 * weekday banners with Drive OCR and commits the parsed list to data/inbox/<slug>.json.
 * A GitHub Actions workflow (Python) then merges it into data/ and fetches ratings.
 * Each parsed list is committed once: a fingerprint per slug is kept in script properties.
 *
 * Setup: see README "Apps Script". Script property GITHUB_TOKEN = fine-grained PAT with
 * Contents read/write on GITHUB_REPO only.
 *
 * Pure helpers (parse/date) have no Apps Script dependencies and are unit-tested in Node
 * (appsscript/test).
 */

const GITHUB_REPO = 'zapion/kobo99-digest';
const GITHUB_BRANCH = 'main';
const GMAIL_QUERY = 'from:news@shopjp.kobo.com newer_than:8d';
const GMAIL_MAX_RESULTS = 30;
const TRIGGER_HOUR = 10;
const TRIGGER_MINUTE = 30;
const TAIPEI_OFFSET_MS = 8 * 3600 * 1000; // Asia/Taipei has no DST.

const SENDER_DOMAIN_RE = /@(?:[\w-]+\.)*kobo\.com>?$/i;
const SUBJECT_KINDS = [['【本週精選】', '8585'], ['【今日 99】', 'dd99'], ['【今日99】', 'dd99']];
const KIND_LABELS = { dd99: '一週99書單', 8585: '本週精選書單' };
const COVER_RE = /^https:\/\/cdn\.kobo\.com\/book-images\/[\w-]+\/\d+\/\d+\/\w+\/([A-Za-z0-9_-]+)\.(?:jpg|png)$/;
const COVER_SIZE = 300;
const BAR_HOSTS = ['shopjp.kobo.com', 'static.cdn.responsys.net'];
const BAR_PATH_RE = /\/images\/bar\d+\.(?:jpg|png)$/;
const PERIOD_RE = /(\d{1,2})\/(\d{1,2})\s*[-－~～]\s*(\d{1,2})\/(\d{1,2})/;
const PROMO_RE = /輸入\s*([A-Za-z]+\d+)/;
const WEEKDAY_RE = /星期([一二三四五六日天])/g;
const WEEKDAYS = { 一: 0, 二: 1, 三: 2, 四: 3, 五: 4, 六: 5, 日: 6, 天: 6 };

// ── Entry points ─────────────────────────────────────────────────────────────

/** Trigger target. Throws on failure so Apps Script emails you (set "notify immediately"). */
function runIngest() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('Script property GITHUB_TOKEN is missing.');
  const latest = latestPerKind(fetchKoboEmails());
  const kinds = Object.keys(latest);
  if (!kinds.length) throw new Error(`No Kobo 精選/99 email matched: ${GMAIL_QUERY}`);
  const errors = [];
  kinds.sort().forEach((kind) => {
    try {
      const bookList = parseEmail(latest[kind], ocrBanners);
      const changed = publishInbox(bookList, token, PropertiesService.getScriptProperties());
      Logger.log('%s: %s (%s books)', bookList.slug, changed ? 'sent to inbox' : 'unchanged', bookList.books.length);
    } catch (error) {
      errors.push(`${kind}: ${error.message}`);
    }
  });
  if (errors.length) throw new Error(`Kobo ingest failed — use the bookmarklet backup.\n${errors.join('\n')}`);
}

/** Run once by hand: (re)creates the Thu/Mon 10:30 triggers. */
function installTriggers() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'runIngest')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  [ScriptApp.WeekDay.THURSDAY, ScriptApp.WeekDay.MONDAY].forEach((day) => {
    ScriptApp.newTrigger('runIngest').timeBased().onWeekDay(day)
      .atHour(TRIGGER_HOUR).nearMinute(TRIGGER_MINUTE).everyWeeks(1).create();
  });
  Logger.log('Triggers: %s', ScriptApp.getProjectTriggers().map((t) => t.getHandlerFunction()).join(', '));
}

// ── Gmail (read-only via the Gmail advanced service) ─────────────────────────

function fetchKoboEmails() {
  const listing = Gmail.Users.Messages.list('me', { q: GMAIL_QUERY, maxResults: GMAIL_MAX_RESULTS });
  return (listing.messages || []).map(({ id }) => {
    const message = Gmail.Users.Messages.get('me', id, { format: 'full' });
    const header = (name) => (message.payload.headers.find((h) => h.name.toLowerCase() === name) || {}).value || '';
    return {
      subject: header('subject'),
      from: header('from'),
      sentMs: Number(message.internalDate),
      html: findHtml(message.payload),
    };
  });
}

/** The advanced service may return body.data as decoded bytes or as unpadded base64url text. */
function decodeBody(data) {
  if (typeof data !== 'string') return Utilities.newBlob(data).getDataAsString('UTF-8');
  const padded = data + '='.repeat((4 - (data.length % 4)) % 4);
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(padded)).getDataAsString('UTF-8');
}

function findHtml(part) {
  if (part.mimeType === 'text/html' && part.body && part.body.data) {
    return decodeBody(part.body.data);
  }
  for (const child of part.parts || []) {
    const html = findHtml(child);
    if (html) return html;
  }
  return '';
}

// ── Drive OCR (drive.file scope: only touches the temp docs it creates) ─────

const DRIVE_API = 'https://www.googleapis.com/drive/v3/files';
const DRIVE_UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart';
const BOUNDARY = 'kobo99-ocr-boundary';

const DRIVE_MAX_ATTEMPTS = 4;
const DRIVE_RETRY_BASE_MS = 1000;

function isRetryable(code) {
  return code === 429 || code >= 500;
}

/** Drive REST call with retries on 429/5xx (Drive OCR returns sporadic 500s). */
function driveFetch(url, options, step) {
  let last = '';
  for (let attempt = 1; attempt <= DRIVE_MAX_ATTEMPTS; attempt += 1) {
    const response = UrlFetchApp.fetch(url, {
      ...options,
      headers: { Authorization: `Bearer ${ScriptApp.getOAuthToken()}` },
      muteHttpExceptions: true,
    });
    const code = response.getResponseCode();
    if (code >= 200 && code < 300) return response;
    last = `Drive ${step} ${code}: ${response.getContentText().slice(0, 300)}`;
    if (!isRetryable(code) || attempt === DRIVE_MAX_ATTEMPTS) break;
    Logger.log('%s (attempt %s/%s), retrying', last.split(':')[0], attempt, DRIVE_MAX_ATTEMPTS);
    Utilities.sleep(DRIVE_RETRY_BASE_MS * 2 ** (attempt - 1));
  }
  throw new Error(last);
}

function ocrImage(url) {
  const image = UrlFetchApp.fetch(url).getBlob();
  const head = `--${BOUNDARY}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n`
    + JSON.stringify({ name: 'kobo99-ocr-tmp', mimeType: 'application/vnd.google-apps.document' })
    + `\r\n--${BOUNDARY}\r\nContent-Type: ${image.getContentType()}\r\n\r\n`;
  const payload = Utilities.newBlob(head).getBytes()
    .concat(image.getBytes())
    .concat(Utilities.newBlob(`\r\n--${BOUNDARY}--`).getBytes());
  const fileId = JSON.parse(driveFetch(DRIVE_UPLOAD_URL, {
    method: 'post', contentType: `multipart/related; boundary=${BOUNDARY}`, payload,
  }, 'upload').getContentText()).id;
  try {
    return driveFetch(`${DRIVE_API}/${fileId}/export?mimeType=text/plain`, { method: 'get' }, 'export').getContentText();
  } finally {
    try {
      driveFetch(`${DRIVE_API}/${fileId}`, { method: 'patch', contentType: 'application/json', payload: '{"trashed":true}' }, 'trash');
    } catch (error) {
      Logger.log('Warning: could not trash temp OCR doc %s: %s', fileId, error.message);
    }
  }
}

function ocrBanners(urls) {
  return urls.map(ocrImage);
}

// ── GitHub contents API ──────────────────────────────────────────────────────

function githubFetch(path, token, options) {
  const response = UrlFetchApp.fetch(`https://api.github.com/repos/${GITHUB_REPO}/contents/${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' },
    muteHttpExceptions: true,
  });
  const code = response.getResponseCode();
  if (code === 404 && (!options || options.method !== 'put')) return null;
  if (code < 200 || code >= 300) throw new Error(`GitHub ${code} ${path}: ${response.getContentText().slice(0, 300)}`);
  return JSON.parse(response.getContentText());
}

function existingSha(path, token) {
  const file = githubFetch(`${path}?ref=${GITHUB_BRANCH}`, token);
  return file ? file.sha : null;
}

function fingerprint(text) {
  return Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8));
}

/** Commit the parsed list to data/inbox/ unless this exact list was already sent. */
function publishInbox(bookList, token, properties) {
  const text = `${JSON.stringify(bookList, null, 2)}\n`;
  const key = `published:${bookList.slug}`;
  const digest = fingerprint(text);
  if (properties.getProperty(key) === digest) return false;
  const path = `data/inbox/${bookList.slug}.json`;
  const body = {
    message: `data: ${bookList.slug} (email)`,
    branch: GITHUB_BRANCH,
    content: Utilities.base64Encode(Utilities.newBlob(text).getBytes()),
  };
  const sha = existingSha(path, token);
  if (sha) body.sha = sha;
  githubFetch(path, token, { method: 'put', contentType: 'application/json', payload: JSON.stringify(body) });
  properties.setProperty(key, digest);
  return true;
}

// ── Pure helpers (unit-tested in Node) ───────────────────────────────────────

function taipeiDate(ms) {
  return new Date(ms + TAIPEI_OFFSET_MS).toISOString().slice(0, 10);
}

function addDays(iso, days) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** Monday = 0 … Sunday = 6, like Python's date.weekday(). */
function weekday(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

function isoWeek(iso) {
  const thursday = addDays(iso, 3 - weekday(iso));
  const year = Number(thursday.slice(0, 4));
  const week = Math.floor((Date.parse(thursday) - Date.parse(`${year}-01-01`)) / (7 * 86400000)) + 1;
  return { year, week };
}

function decodeEntities(text) {
  const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
  return text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, code) => {
    if (code[0] === '#') {
      const value = code[1].toLowerCase() === 'x' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return String.fromCodePoint(value);
    }
    return named[code.toLowerCase()] ?? whole;
  });
}

function textOf(html) {
  return decodeEntities(html.replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, ' '))
    .replace(/\s+/g, ' ').trim();
}

function attrOf(tag, name) {
  const match = tag.match(new RegExp(`\\s${name}="([^"]*)"`, 'i'));
  return match ? decodeEntities(match[1]) : '';
}

/** Inner text of the first element carrying id="…". */
function textById(html, id) {
  const open = html.match(new RegExp(`<(\\w+)[^>]*\\sid="${id}"[^>]*>`, 'i'));
  if (!open) return '';
  const rest = html.slice(open.index + open[0].length);
  const close = rest.search(new RegExp(`</${open[1]}>`, 'i'));
  return textOf(close < 0 ? rest : rest.slice(0, close));
}

function numberOf(text) {
  const match = text.trim().match(/(\d[\d,]*)\s*$/);
  return match ? Number(match[1].replace(/,/g, '')) : null;
}

function classify(email) {
  const sender = (email.from.match(/<([^>]+)>/) || [null, email.from])[1].trim();
  if (!SENDER_DOMAIN_RE.test(sender)) return null;
  const hit = SUBJECT_KINDS.find(([prefix]) => email.subject.startsWith(prefix));
  return hit ? hit[1] : null;
}

function latestPerKind(emails) {
  const newest = {};
  emails.forEach((email) => {
    const kind = classify(email);
    if (kind && (!newest[kind] || email.sentMs > newest[kind].sentMs)) newest[kind] = email;
  });
  return newest;
}

function parseBooks(html) {
  const numbers = [...new Set([...html.matchAll(/\sid="book(\d+)-title"/g)].map((m) => Number(m[1])))]
    .sort((a, b) => a - b);
  if (!numbers.length) throw new Error('No books found; the email template may have changed.');
  return numbers.map((n) => {
    const imgTag = (html.match(new RegExp(`<img[^>]*\\sid="book${n}-img-image-link"[^>]*>`, 'i')) || [''])[0];
    const cover = attrOf(imgTag, 'src');
    const coverMatch = cover.match(COVER_RE);
    const title = textById(html, `book${n}-title`);
    if (!title || !coverMatch) throw new Error(`book${n}: missing title or Kobo cover URL (${cover.slice(0, 80)})`);
    return {
      number: n,
      title,
      description: textById(html, `book${n}-description`),
      kobo_url: `https://www.kobo.com/tw/zh/ebook/${coverMatch[1]}`,
      cover_url: cover.replace(/\/\d+\/\d+\/(\w+)\/([^/]+)$/, `/${COVER_SIZE}/${COVER_SIZE}/$1/$2`),
      list_price: numberOf(textById(html, `book${n}-list-price`)),
      sale_price: numberOf(textById(html, `book${n}-sell-price`)),
    };
  });
}

/** Banner images in document order with the book numbers that follow each. */
function barGroups(html) {
  const groups = [];
  const re = /<img[^>]*>|\sid="book(\d+)-title"/gi;
  for (const match of html.matchAll(re)) {
    if (match[1]) {
      if (groups.length) groups[groups.length - 1].books.push(Number(match[1]));
      continue;
    }
    const src = attrOf(match[0], 'src');
    if (!BAR_PATH_RE.test(src.split('?')[0])) continue;
    const host = (src.match(/^https:\/\/([^/]+)\//) || [])[1];
    if (!BAR_HOSTS.includes(host)) throw new Error(`Unexpected banner image host: ${src}`);
    groups.push({ url: src, books: [] });
  }
  return groups;
}

function periodNear(text, sentIso) {
  const match = text.match(PERIOD_RE);
  if (!match) throw new Error('No M/D-M/D period in the 99 email.');
  const [m1, d1, m2, d2] = match.slice(1).map(Number);
  const sentYear = Number(sentIso.slice(0, 4));
  const pad = (n) => String(n).padStart(2, '0');
  const start = [sentYear - 1, sentYear, sentYear + 1]
    .map((y) => `${y}-${pad(m1)}-${pad(d1)}`)
    .sort((a, b) => Math.abs(Date.parse(a) - Date.parse(sentIso)) - Math.abs(Date.parse(b) - Date.parse(sentIso)))[0];
  const startYear = Number(start.slice(0, 4));
  const endYear = m2 * 100 + d2 >= m1 * 100 + d1 ? startYear : startYear + 1;
  return { start, end: `${endYear}-${pad(m2)}-${pad(d2)}` };
}

function saleDates(html, start, end, readBanners) {
  const groups = barGroups(html);
  if (!groups.length) throw new Error('No date banners found in the 99 email.');
  const texts = readBanners(groups.map((g) => g.url));
  const dates = {};
  groups.forEach(({ url, books }, index) => {
    const days = [...(texts[index] || '').matchAll(WEEKDAY_RE)].map((m) => WEEKDAYS[m[1]]);
    if (days.length !== books.length) {
      throw new Error(`Banner ${url.split('/').pop()} read as "${texts[index]}"; expected ${books.length} weekdays`);
    }
    books.forEach((n, i) => {
      const date = addDays(start, (days[i] - weekday(start) + 7) % 7);
      if (date > end) throw new Error(`Weekday ${days[i]} falls outside ${start}..${end}`);
      dates[n] = date;
    });
  });
  return dates;
}

function withoutNumber({ number, ...book }) {
  return book;
}

function makeList(kind, start, end, subject, books, promo) {
  const { year, week } = isoWeek(start);
  const slug = `weekly-${kind}-${year}-w${week}`;
  return {
    slug,
    kind,
    label: KIND_LABELS[kind],
    url: `https://www.kobo.com/zh/blog/${slug}`,
    title: subject,
    period_start: start,
    period_end: end,
    promo_code: promo,
    books,
  };
}

/** email: {subject, from, sentMs, html}; readBanners: urls → OCR text per url. */
function parseEmail(email, readBanners) {
  const kind = classify(email);
  if (!kind) throw new Error(`Not a Kobo list email: ${email.subject}`);
  if (!email.html) throw new Error('Email has no HTML part.');
  const books = parseBooks(email.html);
  const sent = taipeiDate(email.sentMs);
  const text = textOf(email.html);
  if (kind === '8585') {
    const start = addDays(sent, -weekday(sent));
    const promo = text.match(PROMO_RE);
    const ranked = books.map((b) => ({ ...withoutNumber(b), rank: b.number }));
    return makeList(kind, start, addDays(start, 6), email.subject, ranked, promo ? promo[1] : null);
  }
  const { start, end } = periodNear(text, sent);
  const dates = saleDates(email.html, start, end, readBanners);
  const missing = books.filter((b) => !dates[b.number]).map((b) => b.number);
  if (missing.length) throw new Error(`No sale date banner for books ${missing.join(',')}`);
  const dated = books.map((b) => ({ ...withoutNumber(b), sale_date: dates[b.number] }));
  return makeList(kind, start, end, email.subject, dated, null);
}

if (typeof module !== 'undefined') {
  module.exports = {
    taipeiDate, addDays, weekday, isoWeek, decodeEntities, textById, classify, latestPerKind,
    parseBooks, barGroups, periodNear, parseEmail, findHtml, publishInbox, runIngest, fetchKoboEmails, ocrImage,
  };
}
