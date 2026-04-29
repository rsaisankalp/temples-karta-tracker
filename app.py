#!/usr/bin/env python3
"""VDS Karta tracker - per-karta temple status + photo upload + WhatsApp share."""
import json, os, urllib.request, urllib.parse, urllib.error, time, mimetypes
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

SHEET_ID = '1PZ4c2rHmfa6dHKWIJ4SzkmN7qvL5xftv_6hfF0imRbo'
GOOGLE_CONFIG = '/var/www/saints/google_config.json'

ROUTES = [
    {'sheet': 'KA Route', 'state_code': 'ka', 'state_name': 'Karnataka',
     'karta_col': 5, 'district_col': 6, 'state_col': 7, 'arrive_col': 11, 'depart_col': 12},
    {'sheet': 'TN Route', 'state_code': 'tn', 'state_name': 'Tamil Nadu',
     'karta_col': 5, 'district_col': 7, 'state_col': 8, 'arrive_col': 12, 'depart_col': 13},
    {'sheet': 'AP Route', 'state_code': 'ap', 'state_name': 'Andhra Pradesh',
     'karta_col': 5, 'district_col': 6, 'state_col': 7, 'arrive_col': 11, 'depart_col': 12},
    {'sheet': 'TG Route', 'state_code': 'tg', 'state_name': 'Telangana',
     'karta_col': 5, 'district_col': 6, 'state_col': 7, 'arrive_col': 11, 'depart_col': 12},
]


BAILEYS_API = 'https://wa1.vaidicpujas.in'
BAILEYS_SESSION_ID = '919620515656'

def normalize_phone(phone_str):
    """+91 99942 04614 -> 919994204614"""
    if not phone_str: return None
    digits = ''.join(c for c in str(phone_str) if c.isdigit())
    if not digits: return None
    if len(digits) == 10: digits = '91' + digits
    return digits

def get_karta_directory():
    """Returns dict: slug -> {name, phone (normalized), notes}"""
    try:
        data = sheets_get("'Karta Directory'!A:E")
    except Exception:
        return {}
    out = {}
    for row in data[1:]:
        if not row or len(row) < 3: continue
        name, phone, slug = row[0], row[1] if len(row)>1 else '', row[2] if len(row)>2 else ''
        if not slug: continue
        out[slug] = {'name': name, 'phone_raw': phone, 'phone': normalize_phone(phone)}
    return out

def baileys_send(receiver, text):
    """Send via Baileys. receiver = digits only (e.g. '919994204614'). Returns (ok, msg)"""
    try:
        digits = ''.join(c for c in str(receiver) if c.isdigit())
        if len(digits) == 10:
            digits = '91' + digits
        jid = digits + '@s.whatsapp.net'
        url = f'{BAILEYS_API}/chats/send?id={BAILEYS_SESSION_ID}'
        body = json.dumps({'receiver': jid, 'message': {'text': text}}).encode()
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read())
        return (bool(res.get('success')), res.get('message', ''))
    except Exception as e:
        return (False, str(e))

_tok = {'t': None, 'exp': 0}
def get_token():
    if _tok['t'] and time.time() < _tok['exp'] - 60:
        return _tok['t']
    with open(GOOGLE_CONFIG) as f:
        cfg = json.load(f)
    body = urllib.parse.urlencode({
        'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
        'refresh_token': cfg['refresh_token'], 'grant_type': 'refresh_token'
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(
            'https://oauth2.googleapis.com/token', data=body, method='POST')) as r:
        res = json.loads(r.read())
    _tok['t'] = res['access_token']; _tok['exp'] = time.time() + res.get('expires_in', 3600)
    return _tok['t']

def slugify(s):
    out = []; prev = False
    for c in s.lower():
        if c.isalnum(): out.append(c); prev = False
        elif not prev: out.append('-'); prev = True
    return ''.join(out).strip('-')

def col_letter(i):
    s=''; n=i
    while True:
        s = chr(ord('A') + n % 26) + s
        n = n // 26 - 1
        if n < 0: break
    return s

def sheets_get(range_):
    t = get_token()
    url = (f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'
           f'{urllib.parse.quote(range_)}')
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={'Authorization': f'Bearer {t}'})) as r:
        return json.loads(r.read()).get('values', [])

def sheets_update(range_, values):
    t = get_token()
    url = (f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'
           f'{urllib.parse.quote(range_)}?valueInputOption=USER_ENTERED')
    body = json.dumps({'values': values}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method='PUT', headers={
            'Authorization': f'Bearer {t}', 'Content-Type': 'application/json'})) as r:
        return json.loads(r.read())

def drive_find(name, parent=None):
    t = get_token()
    q = "name='" + name.replace("'", "\\'") + "' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent: q += f" and '{parent}' in parents"
    url = (f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(q)}"
           f"&fields=files(id,name)")
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={'Authorization': f'Bearer {t}'})) as r:
        return json.loads(r.read()).get('files', [])

def drive_create_folder(name, parent=None):
    t = get_token()
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent: meta['parents'] = [parent]
    H = {'Authorization': f'Bearer {t}', 'Content-Type': 'application/json'}
    with urllib.request.urlopen(urllib.request.Request(
            'https://www.googleapis.com/drive/v3/files',
            data=json.dumps(meta).encode(), method='POST', headers=H)) as r:
        f = json.loads(r.read())
    perm = {'role': 'reader', 'type': 'anyone'}
    urllib.request.urlopen(urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{f['id']}/permissions",
        data=json.dumps(perm).encode(), method='POST', headers=H))
    return f['id']

def get_or_create_folder(name, parent=None):
    found = drive_find(name, parent)
    if found: return found[0]['id']
    return drive_create_folder(name, parent)

def drive_upload(data, fname, mime, parent):
    t = get_token()
    boundary = f'----TempleB{int(time.time()*1000)}'
    meta = json.dumps({'name': fname, 'parents': [parent]})
    body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n'
            f'--{boundary}\r\nContent-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    with urllib.request.urlopen(urllib.request.Request(
            'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink',
            data=body, method='POST', headers={
                'Authorization': f'Bearer {t}',
                'Content-Type': f'multipart/related; boundary={boundary}'})) as r:
        return json.loads(r.read())

def find_status_columns(header):
    """Find Status, Prasaad, Photos columns independently (need not be consecutive)."""
    s_col = p_col = ph_col = None
    for i, h in enumerate(header):
        hh = (h or '').strip()
        if hh.lower() == 'status' and s_col is None:
            s_col = i
        elif hh == 'Prasaad' and p_col is None:
            p_col = i
        elif hh == 'Photos' and ph_col is None:
            ph_col = i
    return s_col, p_col, ph_col

def list_kartas(state_filter=None):
    by_slug = {}
    for r in ROUTES:
        if state_filter and r['state_code'] != state_filter: continue
        data = sheets_get("'" + r['sheet'] + "'!A:F")
        for row in data[1:]:
            if not row or not row[0] or not row[0].isdigit(): continue
            kc = r['karta_col']
            if len(row) <= kc: continue
            k = (row[kc] or '').strip()
            if not k: continue
            slug = slugify(k)
            e = by_slug.setdefault(slug, {'name': k, 'routes': set(), 'count': 0})
            e['routes'].add(r['sheet']); e['count'] += 1
    return [(s, e['name'], sorted(e['routes']), e['count'])
            for s, e in sorted(by_slug.items(), key=lambda x: -x[1]['count'])]

def get_karta_temples(slug):
    karta = None; rows = []
    for r in ROUTES:
        data = sheets_get("'" + r['sheet'] + "'!A:Z")
        if not data: continue
        s_col, p_col, ph_col = find_status_columns(data[0])
        kc = r['karta_col']
        for ridx, row in enumerate(data[1:], 2):
            if not row or not row[0] or not row[0].isdigit(): continue
            if len(row) <= kc: continue
            k = (row[kc] or '').strip()
            if slugify(k) != slug: continue
            karta = karta or k
            def cell(i):
                return row[i] if (i is not None and len(row) > i) else ''
            rows.append({
                'sheet': r['sheet'], 'row': ridx,
                'day': cell(0), 'stop': cell(1), 'type': cell(2), 'name': cell(3),
                'district': cell(r['district_col']), 'state': cell(r['state_col']),
                'arrive': cell(r['arrive_col']), 'depart': cell(r['depart_col']),
                's_col': s_col, 'p_col': p_col, 'ph_col': ph_col,
                'status': cell(s_col), 'prasaad': cell(p_col), 'photos': cell(ph_col),
            })
    return karta, rows

# ============== TEMPLATES ==============
PAGE_CSS = """<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:14px;background:#faf7f1;color:#222}
h1{color:#b8860b;margin-bottom:4px}h2{font-size:13px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-top:24px}
.bk{color:#888;text-decoration:none;font-size:13px}
.stats{background:#fff;padding:14px;border-radius:8px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.bar{background:#eee;height:8px;border-radius:4px;overflow:hidden;margin-top:8px}
.fill{background:linear-gradient(90deg,#b8860b,#6cb56c);height:100%;transition:.4s}
.share-btn{margin-top:12px;padding:9px 14px;background:#25d366;color:#fff;border:0;border-radius:6px;font-weight:600;cursor:pointer;font-size:14px}
.share-btn:hover{background:#1ea954}
.temple{background:#fff;padding:14px;border-radius:8px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.06);border-left:4px solid #ddd;transition:.2s}
.temple.done{border-left-color:#6cb56c;background:#f7fff5}
.temple.amman{background:#fff5fa}
.temple.amman.done{background:#f5fff5}
.tn{font-weight:600;font-size:15px}.tm{color:#888;font-size:12px;margin-top:3px}
.b{display:inline-block;padding:1px 7px;font-size:10px;border-radius:3px;background:#eee;color:#666;margin-left:6px;vertical-align:middle;font-weight:500}
.b.shiva{background:#ffe9c8;color:#a05a00}.b.vishnu{background:#d6e6ff;color:#1e3a8a}.b.amman{background:#ffd0e0;color:#8b1a4d}
.act{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.act select,.act button{padding:6px 9px;font-size:13px;border:1px solid #ddd;border-radius:5px;background:#fff;cursor:pointer}
.act select:hover,.act button:hover{border-color:#b8860b}
.wa{background:#25d366!important;color:#fff;border:0!important}
.photos{margin-top:8px;font-size:12px}.photos a{color:#b8860b}
ul.kartas{list-style:none;padding:0}
ul.kartas li{background:#fff;padding:14px;margin-bottom:8px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
ul.kartas a{color:#b8860b;text-decoration:none;font-weight:600;font-size:16px}
.meta{color:#888;font-size:13px;margin-top:4px}
.tabs{display:flex;gap:8px;margin:12px 0}
.tabs a{padding:6px 14px;background:#eee;border-radius:18px;text-decoration:none;color:#444;font-size:13px}
.tabs a.active{background:#b8860b;color:#fff}
.toggle-btn{padding:8px 14px;font-size:14px;font-weight:600;border:0;border-radius:6px;cursor:pointer;transition:.2s}
.toggle-btn.off{background:#fff;color:#444;border:1.5px solid #ddd}
.toggle-btn.off:hover{border-color:#b8860b;background:#fff8e8}
.toggle-btn.on{background:#6cb56c;color:#fff;border:0}
.toggle-btn.on:hover{background:#5aa05a}
</style>"""

JS_BLOCK = r"""<script>
async function tg(btn,sh,rng,kind){
  const isOn=btn.classList.contains('on');
  const newVal=kind==='status'?(isOn?'To Go':'Done'):(isOn?'Pending':'Collected');
  btn.disabled=true;
  try{
    const r=await fetch('/k/api/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sheet:sh,range:rng,value:newVal})});
    if(r.ok){
      btn.classList.toggle('on');btn.classList.toggle('off');
      if(kind==='status'){
        btn.textContent=isOn?'⏳ Mark Done':'✅ Done — undo?';
        btn.closest('.temple').classList.toggle('done',!isOn);
      } else {
        btn.textContent=isOn?'🍞 Mark Prasaad':'🍞 Collected ✓';
      }
    } else alert('Save failed');
  } finally { btn.disabled=false; }
}
async function upd(el,sh,rng,kind){
  el.disabled=true;
  try{
    const r=await fetch('/k/api/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sheet:sh,range:rng,value:el.value})});
    if(r.ok){
      const c=el.closest('.temple');
      if(kind==='status') c.classList.toggle('done',el.value==='Done');
    } else alert('Failed to save');
  } finally { el.disabled=false; }
}
function ph(btn,sh,row,rng,karta){
  const inp=btn.parentElement.querySelector('input[type=file]');
  inp.onchange=async()=>{
    if(!inp.files.length) return;
    btn.disabled=true; const orig=btn.textContent; btn.textContent='Uploading…';
    const fd=new FormData();
    fd.append('sheet',sh); fd.append('row',row); fd.append('range',rng); fd.append('karta',karta);
    for(const f of inp.files) fd.append('photo',f);
    try{
      const r=await fetch('/k/api/upload',{method:'POST',body:fd});
      const j=await r.json();
      if(j.ok){
        btn.textContent='✓ Uploaded';
        const card=btn.closest('.temple');
        const ex=card.querySelector('.photos');
        const html='📁 <a href="'+j.folder_url+'" target=_blank>View photos</a>';
        if(ex) ex.innerHTML=html;
        else card.insertAdjacentHTML('beforeend','<div class=photos>'+html+'</div>');
        setTimeout(function(){btn.textContent=orig;},2000);
      } else { alert('Upload failed: '+(j.error||'')); btn.textContent=orig; }
    } catch(e){ alert('Error: '+e); btn.textContent=orig; }
    finally { btn.disabled=false; }
  };
  inp.click();
}
function shareAllWA(){
  const cards=document.querySelectorAll('.temple');
  let done=[],togo=[],pcoll=0;
  cards.forEach(function(c){
    const name=c.querySelector('.tn').firstChild.textContent.trim();
    const meta=c.querySelector('.tm').textContent;
    const dayStop=meta.split('·').slice(0,2).join(' ').trim();
    const status=c.querySelectorAll('.toggle-btn')[0].classList.contains('on')?'Done':'To Go';
    const prasaad=c.querySelectorAll('.toggle-btn')[1].classList.contains('on')?'Collected':'Pending';
    if(prasaad==='Collected') pcoll++;
    if(status==='Done') done.push('• '+name);
    else togo.push('• '+name+' — '+dayStop);
  });
  let msg='🕉️ Temple Yatra — '+KARTA+'\n';
  msg+='Progress: '+done.length+'/'+cards.length+' temples done\n';
  msg+='Prasaad: '+pcoll+'/'+cards.length+' collected\n\n';
  if(done.length){ msg+='✅ Done:\n'+done.join('\n')+'\n\n'; }
  if(togo.length){ msg+='⏳ Pending:\n'+togo.join('\n')+'\n\n'; }
  msg+='🔗 https://temples.vaidicpujas.in/k/'+SLUG;
  window.open('https://wa.me/?text='+encodeURIComponent(msg),'_blank');
}
function shareTempleWA(btn){
  const card=btn.closest('.temple');
  const name=card.querySelector('.tn').firstChild.textContent.trim();
  const meta=card.querySelector('.tm').textContent;
  const status=card.querySelectorAll('.toggle-btn')[0].classList.contains('on')?'Done':'To Go';
  const prasaad=card.querySelectorAll('.toggle-btn')[1].classList.contains('on')?'Collected':'Pending';
  const photoLink=card.querySelector('.photos a');
  let msg='🕉️ '+KARTA+' — '+name+'\n';
  msg+=meta+'\n';
  msg+=(status==='Done'?'✅ Completed':'⏳ Pending')+' · Prasaad: '+prasaad;
  if(photoLink) msg+='\n📁 '+photoLink.href;
  window.open('https://wa.me/?text='+encodeURIComponent(msg),'_blank');
}
</script>"""

# ============== ROUTES ==============

@app.route('/k/')
def k_index():
    items_html = ''
    for slug, name, routes, count in list_kartas():
        items_html += ('<li><a href="/k/' + slug + '">' + name + '</a>'
                       '<div class=meta>' + str(count) + ' temples · ' + ', '.join(routes) + '</div></li>')
    html = ('<!doctype html><html><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>Temple Yatra — Kartas</title>' + PAGE_CSS + '</head><body>'
            '<h1>🕉️ Temple Yatra — Kartas</h1>'
            '<div class=tabs>'
            '<a href="/k/" class=active>All</a>'
            '<a href="/k/state/ka">Karnataka</a>'
            '<a href="/k/state/tn">Tamil Nadu</a>'
            '<a href="/k/state/ap">Andhra Pradesh</a>'
            '<a href="/k/state/tg">Telangana</a>'
            '</div>'
            '<p>Choose your name to see your assigned temples.</p>'
            '<ul class=kartas>' + items_html + '</ul></body></html>')
    return Response(html, mimetype='text/html')

@app.route('/k/state/<state>')
def k_state(state):
    state = state.lower()
    state_names = {'ka': 'Karnataka', 'tn': 'Tamil Nadu', 'ap': 'Andhra Pradesh', 'tg': 'Telangana'}
    if state not in state_names:
        return '<h1>Unknown state</h1>', 404
    items_html = ''
    for slug, name, routes, count in list_kartas(state_filter=state):
        items_html += ('<li><a href="/k/' + slug + '">' + name + '</a>'
                       '<div class=meta>' + str(count) + ' temples</div></li>')
    if not items_html:
        items_html = '<li class=meta>No Kartas assigned to ' + state_names[state] + ' yet.</li>'
    tabs = '<div class=tabs><a href="/k/">All</a>'
    for c, n in [('ka', 'Karnataka'), ('tn', 'Tamil Nadu'), ('ap', 'Andhra Pradesh'), ('tg', 'Telangana')]:
        cls = ' class=active' if c == state else ''
        tabs += '<a href="/k/state/' + c + '"' + cls + '>' + n + '</a>'
    tabs += '</div>'
    html = ('<!doctype html><html><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>' + state_names[state] + ' — Kartas</title>' + PAGE_CSS + '</head><body>'
            '<h1>🕉️ ' + state_names[state] + ' Route — Kartas</h1>' + tabs +
            '<p>Tap your name to update your assigned temples.</p>'
            '<ul class=kartas>' + items_html + '</ul></body></html>')
    return Response(html, mimetype='text/html')

@app.route('/k/<slug>')
def k_karta(slug):
    karta, temples = get_karta_temples(slug)
    if not karta:
        return '<h1>Unknown karta: ' + slug + '</h1><a href="/k/">← All kartas</a>', 404

    by_sheet = {}
    for t in temples: by_sheet.setdefault(t['sheet'], []).append(t)
    total = len(temples)
    done = sum(1 for t in temples if t['status'] == 'Done')
    pcoll = sum(1 for t in temples if t['prasaad'] == 'Collected')
    pct = (done * 100 // total) if total else 0

    sections = ''
    for sheet_name in sorted(by_sheet):
        sections += '<h2>' + sheet_name + '</h2>'
        for t in by_sheet[sheet_name]:
            cls = 'temple'
            if t['status'] == 'Done': cls += ' done'
            if t['type'] == 'Amman': cls += ' amman'
            tcls = t['type'].lower() if t['type'].lower() in ('shiva','vishnu','amman') else ''
            s_let = col_letter(t['s_col']) if t['s_col'] is not None else ''
            p_let = col_letter(t['p_col']) if t['p_col'] is not None else ''
            ph_let = col_letter(t['ph_col']) if t['ph_col'] is not None else ''
            row_n = str(t['row'])
            karta_attr = karta.replace('"', '&quot;')
            photo_div = ('<div class=photos>📁 <a href="' + t['photos'] + '" target=_blank>View photos</a></div>'
                         if t['photos'] else '')
            is_done = t['status'] == 'Done'
            is_collected = t['prasaad'] == 'Collected'
            sel_status = ('<button class="toggle-btn ' + ('on' if is_done else 'off') + '" '
                          'onclick="tg(this,\'' + t['sheet'] + '\',\'' + s_let + row_n + '\',\'status\')">'
                          + ('✅ Done — undo?' if is_done else '⏳ Mark Done') + '</button>')
            sel_pr = ('<button class="toggle-btn ' + ('on' if is_collected else 'off') + '" '
                      'onclick="tg(this,\'' + t['sheet'] + '\',\'' + p_let + row_n + '\',\'prasaad\')">'
                      + ('🍞 Collected ✓' if is_collected else '🍞 Mark Prasaad') + '</button>')
            upload_btn = ('<button onclick="ph(this,\'' + t['sheet'] + '\',\'' + row_n + '\',\'' + ph_let + row_n + '\',\'' + karta_attr + '\')">📷 Upload</button>'
                          '<input type=file accept="image/*" multiple style=display:none>')
            wa_btn = '<button class=wa onclick="shareTempleWA(this)" title="Share on WhatsApp">📲 Share</button>'
            sections += ('<div class="' + cls + '">'
                         '<div class=th><div class=tn>' + t['name'] + ' <span class="b ' + tcls + '">' + t['type'] + '</span></div>'
                         '<div class=tm>Day ' + str(t['day']) + ' · Stop ' + str(t['stop']) + ' · ' + t['district'] + ', ' + t['state'] + ' · ' + t['arrive'] + '–' + t['depart'] + '</div></div>'
                         '<div class=act>' + sel_status + sel_pr + upload_btn + wa_btn + '</div>' + photo_div + '</div>')

    karta_js = karta.replace('\\', '\\\\').replace('"', '\\"')
    head_js = '<script>const SLUG="' + slug + '";const KARTA="' + karta_js + '";</script>'

    html = ('<!doctype html><html><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>' + karta + ' — Temple Yatra</title>' + PAGE_CSS + '</head><body>'
            '<a href="/k/" class=bk>← All kartas</a>'
            '<h1>' + karta + '</h1>'
            '<div class=stats>'
            '<b>' + str(done) + '/' + str(total) + '</b> done · '
            '<b>' + str(pcoll) + '/' + str(total) + '</b> prasaad collected'
            '<div class=bar><div class=fill style="width:' + str(pct) + '%"></div></div>'
            '<button class=share-btn onclick="shareAllWA()">📲 Share status on WhatsApp</button>'
            '</div>' + sections + head_js + JS_BLOCK + '</body></html>')
    return Response(html, mimetype='text/html')

@app.route('/k/api/update', methods=['POST'])
def api_update():
    data = request.json
    sheets_update("'" + data['sheet'] + "'!" + data['range'], [[data['value']]])
    return jsonify({'ok': True})

@app.route('/k/api/upload', methods=['POST'])
def api_upload():
    sheet = request.form['sheet']; range_ = request.form['range']; karta = request.form['karta']
    files = request.files.getlist('photo')
    if not files: return jsonify({'ok': False, 'error': 'no files'}), 400
    parent = get_or_create_folder('Temple Yatra Photos')
    folder = get_or_create_folder(karta, parent=parent)
    for f in files:
        body = f.read()
        mime = f.mimetype or mimetypes.guess_type(f.filename)[0] or 'application/octet-stream'
        ts = time.strftime('%Y%m%d-%H%M%S')
        drive_upload(body, ts + '-' + f.filename, mime, folder)
    folder_url = 'https://drive.google.com/drive/folders/' + folder
    sheets_update("'" + sheet + "'!" + range_, [[folder_url]])
    return jsonify({'ok': True, 'folder_url': folder_url, 'count': len(files)})


@app.route('/k/api/state/<state>')
def api_state(state):
    state = state.lower()
    karta_map = {}
    for r in ROUTES:
        if r['state_code'] != state: continue
        data = sheets_get("'" + r['sheet'] + "'!A:H")
        for row in data[1:]:
            if not row or not row[0] or not row[0].isdigit(): continue
            kc = r['karta_col']
            if len(row) <= kc: continue
            k = (row[kc] or '').strip()
            if not k: continue
            slug = slugify(k)
            tname = row[3] if len(row) > 3 else ''
            ttype = row[2] if len(row) > 2 else ''
            entry = karta_map.setdefault(slug, {'slug': slug, 'name': k, 'temples': []})
            entry['temples'].append({'name': tname, 'type': ttype})
    kartas = sorted(karta_map.values(), key=lambda x: -len(x['temples']))
    res = jsonify({'kartas': kartas})
    res.headers['Access-Control-Allow-Origin'] = '*'
    return res


@app.route('/followup')
def followup():
    rows = []
    for r in ROUTES:
        data = sheets_get("'" + r['sheet'] + "'!A:Z")
        if not data: continue
        s_col, p_col, ph_col = find_status_columns(data[0])
        kc = r['karta_col']
        for ridx, row in enumerate(data[1:], 2):
            if not row or not row[0] or not row[0].isdigit(): continue
            def cell(i): return row[i] if (i is not None and len(row) > i) else ''
            rows.append({
                'sheet': r['sheet'], 'state_code': r['state_code'], 'row': ridx,
                'day': cell(0), 'stop': cell(1), 'type': cell(2), 'name': cell(3),
                'karta': cell(kc), 'district': cell(r['district_col']),
                's_col': s_col, 'p_col': p_col, 'ph_col': ph_col,
                'status': cell(s_col), 'prasaad': cell(p_col), 'photos': cell(ph_col),
            })

    karta_dir = get_karta_directory()
    total = len(rows)
    done_count = sum(1 for t in rows if t['status'] == 'Done')
    pcoll = sum(1 for t in rows if t['prasaad'] == 'Collected')
    pct = (done_count * 100 // total) if total else 0

    by_state = {}
    for t in rows:
        s = t['state_code'].upper()
        e = by_state.setdefault(s, {'total':0,'done':0,'pcoll':0})
        e['total'] += 1
        if t['status']=='Done': e['done'] += 1
        if t['prasaad']=='Collected': e['pcoll'] += 1

    by_karta = {}
    for t in rows:
        if not t['karta']: continue
        slug = slugify(t['karta'])
        e = by_karta.setdefault(slug, {'name': t['karta'], 'slug': slug, 'total':0,'done':0,'pcoll':0, 'states':set()})
        e['total'] += 1
        e['states'].add(t['state_code'].upper())
        if t['status']=='Done': e['done'] += 1
        if t['prasaad']=='Collected': e['pcoll'] += 1

    state_cards = ''
    for s in sorted(by_state):
        e = by_state[s]
        p = (e['done']*100//e['total']) if e['total'] else 0
        state_cards += ('<div class=stat-card><div class=stat-state>' + s + '</div>'
                        '<div class=stat-num>' + str(e['done']) + '<span style=color:#888;font-size:14px>/' + str(e['total']) + '</span></div>'
                        '<div class=stat-meta>' + str(p) + '% done · ' + str(e['pcoll']) + ' prasaad</div>'
                        '<div class=mini-bar><div class=mini-fill style="width:' + str(p) + '%"></div></div></div>')

    karta_cards = ''
    karta_list_sorted = sorted(by_karta.values(), key=lambda x: (x['done']/x['total'] if x['total'] else 0))
    for k in karta_list_sorted:
        p = (k['done']*100//k['total']) if k['total'] else 0
        d = karta_dir.get(k['slug'], {})
        phone_raw = d.get('phone_raw', '')
        phone_n = d.get('phone', '')
        states_str = ', '.join(sorted(k['states']))
        if phone_n:
            wa_btn = '<button class="btn btn-wa" onclick="sendKartaWA(\'' + k['slug'] + '\',this)">📲 Send via WhatsApp</button>'
        else:
            wa_btn = '<span style="color:#888;font-size:12px">no phone</span>'
        wa_click_btn = '<button class="btn btn-wa-click" onclick="clickWA(\'' + k['slug'] + '\',this)">📲 Click-to-WhatsApp</button>'
        karta_cards += ('<div class=karta-card>'
                        '<div style="display:flex;justify-content:space-between;align-items:start;gap:10px;flex-wrap:wrap">'
                        '<div><a href="/k/' + k['slug'] + '" target=_blank class=karta-name>' + k['name'] + '</a>'
                        '<div class=stat-meta>' + states_str + ' · ' + (phone_raw or '<em>no phone</em>') + '</div></div>'
                        '<div style="text-align:right"><b>' + str(k['done']) + '/' + str(k['total']) + '</b> · ' + str(p) + '%<br>'
                        '<small style=color:#888>' + str(k['pcoll']) + ' prasaad</small></div></div>'
                        '<div class=mini-bar><div class=mini-fill style="width:' + str(p) + '%"></div></div>'
                        '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">' + wa_btn + ' ' + wa_click_btn + '</div>'
                        '</div>')

    table_rows = ''
    for t in rows:
        s_let = col_letter(t['s_col']) if t['s_col'] is not None else ''
        p_let = col_letter(t['p_col']) if t['p_col'] is not None else ''
        photo_cell = ('<a href="' + t['photos'] + '" target=_blank>📁</a>' if t['photos'] else '—')
        done_cls = ' class=done-row' if t['status'] == 'Done' else ''
        table_rows += ('<tr' + done_cls + ' data-state="' + t['state_code'] + '" data-karta="' + (t['karta'] or '') + '" data-status="' + (t['status'] or 'To Go') + '" data-type="' + t['type'] + '">'
                       '<td><input type=checkbox class=row-chk data-sheet="' + t['sheet'] + '" data-srng="' + s_let + str(t['row']) + '" data-prng="' + p_let + str(t['row']) + '"></td>'
                       '<td>' + t['state_code'].upper() + '</td><td>D' + str(t['day']) + '/' + str(t['stop']) + '</td><td>' + t['type'] + '</td>'
                       '<td>' + t['name'] + '<br><small style="color:#888">' + t['district'] + '</small></td>'
                       '<td><small>' + (t['karta'] or '<em>—</em>') + '</small></td>'
                       '<td><select class=st-sel onchange="updOne(this,\'' + t['sheet'] + '\',\'' + s_let + str(t['row']) + '\',\'status\')">'
                       '<option' + (' selected' if t['status']!='Done' else '') + '>To Go</option>'
                       '<option' + (' selected' if t['status']=='Done' else '') + '>Done</option></select></td>'
                       '<td><select class=pr-sel onchange="updOne(this,\'' + t['sheet'] + '\',\'' + p_let + str(t['row']) + '\',\'prasaad\')">'
                       '<option' + (' selected' if t['prasaad']!='Collected' else '') + '>Pending</option>'
                       '<option' + (' selected' if t['prasaad']=='Collected' else '') + '>Collected</option></select></td>'
                       '<td>' + photo_cell + '</td></tr>')

    html = ('<!doctype html><html><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>Admin Followup</title><style>'
            'body{font-family:system-ui,-apple-system,sans-serif;margin:0 auto;padding:14px;background:#fafafa;color:#222;max-width:1100px}'
            'h1{color:#b8860b;margin:6px 0;font-size:20px}h2{font-size:14px;color:#666;text-transform:uppercase;letter-spacing:.5px;margin:18px 0 8px}'
            '.bar{background:#fff;padding:12px 14px;border-radius:8px;margin:8px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}'
            '.fbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px}'
            '.fbar select,.fbar input{padding:5px 8px;border:1px solid #ddd;border-radius:4px;font-size:13px}'
            '.btn{padding:6px 12px;border:0;border-radius:4px;cursor:pointer;font-size:13px;font-weight:600}'
            '.btn-done{background:#6cb56c;color:#fff}.btn-pcoll{background:#b8860b;color:#fff}.btn-reset{background:#aaa;color:#fff}'
            '.btn-wa{background:#25d366;color:#fff}.btn-wa-click{background:#fff;color:#25d366;border:1.5px solid #25d366}'
            '.btn:disabled{opacity:.6;cursor:wait}'
            '.bigstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:10px 0}'
            '.stat-card{background:#fff;padding:12px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}'
            '.stat-state{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;font-weight:600}'
            '.stat-num{font-size:30px;font-weight:700;color:#b8860b;line-height:1}'
            '.stat-meta{color:#666;font-size:12px;margin-top:3px}'
            '.mini-bar{background:#eee;height:5px;border-radius:3px;overflow:hidden;margin-top:8px}'
            '.mini-fill{background:linear-gradient(90deg,#b8860b,#6cb56c);height:100%}'
            '.karta-card{background:#fff;padding:14px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:8px}'
            '.karta-name{color:#b8860b;text-decoration:none;font-weight:600;font-size:15px}'
            '.progress{flex:1;background:#eee;height:8px;border-radius:4px;overflow:hidden}'
            '.fill{background:linear-gradient(90deg,#b8860b,#6cb56c);height:100%;width:' + str(pct) + '%}'
            'table{width:100%;border-collapse:collapse;background:#fff;font-size:13px;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}'
            'th{background:#f3eee0;padding:8px;text-align:left;font-weight:600;font-size:12px;color:#666}'
            'td{padding:8px;border-top:1px solid #f0f0f0}'
            'tr.done-row{background:#f4faf3}tr.hidden{display:none}'
            '.row-chk{cursor:pointer;transform:scale(1.2)}'
            '#log{position:fixed;bottom:20px;right:20px;background:#222;color:#fff;padding:10px 14px;border-radius:6px;display:none;z-index:1000;font-size:13px;max-width:300px}'
            '</style></head><body>'
            '<h1>🛠️ Admin Followup</h1>'
            '<div class=bar><b>' + str(done_count) + '/' + str(total) + '</b> temples done · '
            '<b>' + str(pcoll) + '/' + str(total) + '</b> prasaad collected · '
            '<b>' + str(pct) + '%</b> overall<div class=progress style="margin-top:6px"><div class=fill></div></div></div>'
            '<h2>Per state</h2><div class=bigstats>' + state_cards + '</div>'
            '<h2>Per Karta — sorted by least progress</h2>' + karta_cards +
            '<h2>All temples</h2>'
            '<div class="bar fbar">'
            '<label>State: <select id=fState><option value="">All</option><option value=ka>KA</option><option value=tn>TN</option><option value=ap>AP</option><option value=tg>TG</option></select></label>'
            '<label>Status: <select id=fStatus><option value="">All</option><option>To Go</option><option>Done</option></select></label>'
            '<label>Type: <select id=fType><option value="">All</option><option>Shiva</option><option>Vishnu</option><option>Amman</option></select></label>'
            '<input id=fSearch placeholder="Search temple/karta/district…" style="flex:1;min-width:160px">'
            '<span id=cnt style="color:#666"></span></div>'
            '<div class="bar fbar">'
            '<button class="btn btn-done" onclick="bulkSet(\'status\',\'Done\')">✅ Mark selected Done</button>'
            '<button class="btn btn-reset" onclick="bulkSet(\'status\',\'To Go\')">↩ To Go</button>'
            '<button class="btn btn-pcoll" onclick="bulkSet(\'prasaad\',\'Collected\')">🍞 Prasaad Collected</button>'
            '<button class="btn btn-reset" onclick="bulkSet(\'prasaad\',\'Pending\')">↩ Pending</button>'
            '<label style="margin-left:auto"><input type=checkbox id=selAll onchange="toggleAll(this)"> Select all visible</label></div>'
            '<table><thead><tr><th></th><th>St</th><th>Day/Stop</th><th>Type</th><th>Temple</th><th>Karta</th><th>Status</th><th>Prasaad</th><th>📁</th></tr></thead>'
            '<tbody id=tb>' + table_rows + '</tbody></table>'
            '<div id=log></div>'
            '<script>'
            'function showLog(msg,err){const l=document.getElementById("log");l.style.display="block";l.style.background=err?"#c0392b":"#222";l.textContent=msg;setTimeout(()=>l.style.display="none",4000);}'
            'function applyFilters(){const st=document.getElementById("fState").value,sts=document.getElementById("fStatus").value,tp=document.getElementById("fType").value,q=document.getElementById("fSearch").value.toLowerCase();let shown=0;document.querySelectorAll("#tb tr").forEach(r=>{let h=false;if(st&&r.dataset.state!==st)h=true;if(sts&&r.dataset.status!==sts)h=true;if(tp&&r.dataset.type!==tp)h=true;if(q&&!r.textContent.toLowerCase().includes(q))h=true;r.classList.toggle("hidden",h);if(!h)shown++;});document.getElementById("cnt").textContent=shown+" rows shown";}'
            '["fState","fStatus","fType","fSearch"].forEach(id=>document.getElementById(id).addEventListener("input",applyFilters));applyFilters();'
            'function toggleAll(el){document.querySelectorAll("#tb tr:not(.hidden) .row-chk").forEach(c=>c.checked=el.checked);}'
            'async function updOne(sel,sh,rng,kind){sel.disabled=true;const r=await fetch("/followup/api/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sheet:sh,range:rng,value:sel.value})});if(r.ok){const tr=sel.closest("tr");if(kind==="status"){tr.dataset.status=sel.value;tr.classList.toggle("done-row",sel.value==="Done");}}sel.disabled=false;}'
            'async function bulkSet(kind,value){const checks=document.querySelectorAll("#tb tr:not(.hidden) .row-chk:checked");if(!checks.length){alert("Select rows first");return;}if(!confirm("Apply "+kind+"="+value+" to "+checks.length+" temples?"))return;const updates=[];checks.forEach(c=>{const tr=c.closest("tr");const rng=kind==="status"?c.dataset.srng:c.dataset.prng;updates.push({sheet:c.dataset.sheet,range:rng,value:value});});const r=await fetch("/followup/api/bulk",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({updates:updates})});const j=await r.json();if(j.ok){checks.forEach(c=>{const tr=c.closest("tr");const sel=tr.querySelector(kind==="status"?".st-sel":".pr-sel");sel.value=value;if(kind==="status"){tr.dataset.status=value;tr.classList.toggle("done-row",value==="Done");}c.checked=false;});showLog("Updated "+j.count+" cells");}else showLog("Failed: "+(j.error||""),true);}'
            'async function sendKartaWA(slug,btn){btn.disabled=true;btn.textContent="Sending...";try{const r=await fetch("/followup/api/wa-send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({karta_slug:slug})});const j=await r.json();if(j.ok){btn.textContent="✓ Sent";showLog("WhatsApp sent to "+j.to);}else{btn.textContent="📲 Send via WhatsApp";showLog("Failed: "+(j.error||"unknown"),true);}}catch(e){btn.textContent="📲 Send via WhatsApp";showLog("Error: "+e,true);}finally{btn.disabled=false;}}'
            'async function clickWA(slug,btn){btn.disabled=true;try{const r=await fetch("/followup/api/wa-link?karta_slug="+slug);const j=await r.json();if(j.ok){window.open(j.wa_url,"_blank");}else showLog("No phone for this karta",true);}catch(e){showLog("Error: "+e,true);}finally{btn.disabled=false;}}'
            '</script></body></html>')
    return Response(html, mimetype='text/html')


@app.route('/followup/api/update', methods=['POST'])
def followup_update():
    d = request.json
    sheets_update("'" + d['sheet'] + "'!" + d['range'], [[d['value']]])
    return jsonify({'ok': True})

@app.route('/followup/api/bulk', methods=['POST'])
def followup_bulk():
    d = request.json
    updates = d.get('updates', [])
    if not updates: return jsonify({'ok': False, 'error': 'no updates'}), 400
    t = get_token()
    data_arr = [{'range': "'" + u['sheet'] + "'!" + u['range'], 'values': [[u['value']]]} for u in updates]
    body = json.dumps({'valueInputOption': 'USER_ENTERED', 'data': data_arr}).encode()
    url = 'https://sheets.googleapis.com/v4/spreadsheets/' + SHEET_ID + '/values:batchUpdate'
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method='POST', headers={
            'Authorization': 'Bearer ' + t, 'Content-Type': 'application/json'})) as r:
        res = json.loads(r.read())
    return jsonify({'ok': True, 'count': res.get('totalUpdatedCells', 0)})


def build_karta_progress_message(slug):
    """Generate a progress message for a karta."""
    karta_name = None
    done = []; togo = []; pcoll = 0
    for r in ROUTES:
        data = sheets_get("'" + r['sheet'] + "'!A:Z")
        if not data: continue
        s_col, p_col, ph_col = find_status_columns(data[0])
        kc = r['karta_col']
        for row in data[1:]:
            if not row or not row[0] or not row[0].isdigit(): continue
            if len(row) <= kc: continue
            k = (row[kc] or '').strip()
            if slugify(k) != slug: continue
            karta_name = karta_name or k
            name = row[3] if len(row) > 3 else ''
            day = row[0]; stop = row[1] if len(row)>1 else ''
            status = row[s_col] if s_col is not None and len(row) > s_col else ''
            prasaad = row[p_col] if p_col is not None and len(row) > p_col else ''
            district = row[r['district_col']] if len(row) > r['district_col'] else ''
            if prasaad == 'Collected': pcoll += 1
            line = '• ' + name + ' (' + district + ')'
            if status == 'Done': done.append(line)
            else: togo.append('• ' + name + ' — D' + str(day) + '/' + str(stop))
    total = len(done) + len(togo)
    name_for_greeting = karta_name or slug
    msg = '🙏🏼 Pranam ' + name_for_greeting + ',\n\n'
    msg += 'Hope you are doing well. Sharing your Temple Yatra status:\n\n'
    msg += '📊 Progress: ' + str(len(done)) + ' / ' + str(total) + ' temples · '
    msg += str(pcoll) + ' / ' + str(total) + ' prasaad collected\n\n'
    if togo:
        msg += '⏳ Yet to visit:\n' + '\n'.join(togo[:30]) + '\n\n'
    if done:
        msg += '✅ Already completed:\n' + '\n'.join(done[:30]) + '\n\n'
    msg += 'Kindly mark each temple as *Done* and prasaad as *Collected* on your tracker as you complete them. '
    msg += 'Please also share your experience and photos when possible 🙌🏼\n\n'
    msg += '🔗 Your tracker:\nhttps://temples.vaidicpujas.in/k/' + slug + '\n\n'
    msg += 'With gratitude,\nVDS Team 🕉️'
    return karta_name, msg

@app.route('/followup/api/wa-send', methods=['POST'])
def followup_wa_send():
    d = request.json
    slug = d.get('karta_slug')
    if not slug: return jsonify({'ok': False, 'error': 'karta_slug required'}), 400
    kdir = get_karta_directory()
    info = kdir.get(slug)
    if not info or not info.get('phone'):
        return jsonify({'ok': False, 'error': 'no phone in Karta Directory for ' + slug}), 400
    karta_name, msg = build_karta_progress_message(slug)
    ok, err = baileys_send(info['phone'], msg)
    if ok:
        return jsonify({'ok': True, 'to': info['phone_raw'] or info['phone']})
    return jsonify({'ok': False, 'error': err or 'send failed'}), 500

@app.route('/followup/api/wa-link')
def followup_wa_link():
    slug = request.args.get('karta_slug')
    if not slug: return jsonify({'ok': False, 'error': 'karta_slug required'}), 400
    kdir = get_karta_directory()
    info = kdir.get(slug, {})
    karta_name, msg = build_karta_progress_message(slug)
    phone = info.get('phone') or ''
    wa_url = 'https://wa.me/' + phone + '?text=' + urllib.parse.quote(msg)
    return jsonify({'ok': True, 'wa_url': wa_url})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8001)
