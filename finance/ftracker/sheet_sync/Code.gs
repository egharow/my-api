// Finance tracker: receives numbers from the app, keeps this spreadsheet up to date, and serves a phone page.
// Pasted into Extensions > Apps Script and deployed as a web app (see the Google Sheet section in the app).
// Only a request carrying the secret below can change anything here, and the phone page needs the PIN from the app.
const TOKEN = '__TOKEN__';
const PAGE = __PAGE__;

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (!body || body.token !== TOKEN) return reply_({ ok: false, error: 'wrong token' });
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let count = 0;
    (body.sheets || []).forEach(function (t) {
      const sheet = ss.getSheetByName(t.name) || ss.insertSheet(t.name);
      const data = [t.header].concat(t.rows);
      const width = Math.max.apply(null, data.map(function (r) { return r.length; }));
      const padded = data.map(function (r) {
        const row = r.map(function (v) { return v === null || v === undefined ? '' : v; });
        while (row.length < width) row.push('');
        return row;
      });
      sheet.clearContents();
      sheet.getRange(1, 1, padded.length, width).setValues(padded);
      sheet.getRange(1, 1, 1, width).setFontWeight('bold');
      sheet.setFrozenRows(1);
      count++;
    });
    if (body.snapshot) storeSnapshot_(ss, JSON.stringify(body.snapshot), body.pin_hash);
    return reply_({ ok: true, sheets: count, snapshot: !!body.snapshot });
  } catch (err) {
    return reply_({ ok: false, error: String(err) });
  }
}

// The phone page. Opening the web app address shows it; ?check=1 answers with a short message instead.
function doGet(e) {
  if (e && e.parameter && e.parameter.check) return reply_({ ok: true, hello: 'finance tracker sheet link is deployed' });
  return HtmlService.createHtmlOutput(PAGE).setTitle('Finance')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// Called by the phone page. Returns the saved numbers only for the right PIN; five wrong tries lock it for 15 minutes.
function getSnapshot(pin) {
  const cache = CacheService.getScriptCache();
  const fails = Number(cache.get('fails') || 0);
  if (fails >= 5) throw new Error('Too many wrong PINs. Try again in 15 minutes.');
  const want = PropertiesService.getScriptProperties().getProperty('PIN_HASH');
  if (!want || hash_(String(pin)) !== want) {
    cache.put('fails', String(fails + 1), 900);
    throw new Error('Wrong PIN.');
  }
  cache.remove('fails');
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('_phone');
  if (!sheet || sheet.getLastRow() < 1) throw new Error('Nothing has been sent from the app yet.');
  return sheet.getRange(1, 1, sheet.getLastRow(), 1).getValues().map(function (r) { return r[0]; }).join('');
}

function storeSnapshot_(ss, text, pinHash) {
  const sheet = ss.getSheetByName('_phone') || ss.insertSheet('_phone');
  sheet.clearContents();
  const rows = [];
  for (let i = 0; i < text.length; i += 40000) rows.push([text.substr(i, 40000)]);
  sheet.getRange(1, 1, rows.length, 1).setValues(rows);
  sheet.hideSheet();
  if (pinHash) PropertiesService.getScriptProperties().setProperty('PIN_HASH', pinHash);
}

function hash_(pin) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, TOKEN + ':' + pin);
  return bytes.map(function (b) { return ('0' + (b < 0 ? b + 256 : b).toString(16)).slice(-2); }).join('');
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
