// Finance tracker: receives the submitted numbers and keeps this spreadsheet up to date.
// Pasted into Extensions > Apps Script and deployed as a web app (see the Sheet page in the app).
// Only a request carrying the secret below can change anything here.
const TOKEN = '__TOKEN__';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (!body || body.token !== TOKEN) return reply_({ ok: false, error: 'wrong token' });
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    body.sheets.forEach(function (t) {
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
    });
    return reply_({ ok: true, sheets: body.sheets.length });
  } catch (err) {
    return reply_({ ok: false, error: String(err) });
  }
}

// Opening the web app address in a browser answers this, so you can check it is deployed.
function doGet() {
  return reply_({ ok: true, hello: 'finance tracker sheet link is deployed' });
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
