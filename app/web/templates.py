BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Beat Manager</title>
<style>
:root{--bg:#0e0f11;--card:#17191c;--line:#26292e;--text:#e8e8e6;--mut:#8b8f96;--acc:#d6ff3a;--warn:#ffb020;--bad:#ff5a5a;--ok:#4cd98a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,Segoe UI,Inter,Roboto,sans-serif}
a{color:var(--text)}nav{display:flex;gap:4px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
nav a{padding:6px 10px;border-radius:8px;text-decoration:none;color:var(--mut)}nav a.on,nav a:hover{background:var(--card);color:var(--text)}
nav .brand{font-weight:700;color:var(--acc);margin-right:12px}main{max-width:1200px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 16px}h2{font-size:15px;margin:24px 0 10px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.big{font-size:26px;font-weight:700}.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600;font-size:12px}
input,select,textarea,button{background:#0b0c0e;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
button{background:var(--acc);color:#111;border:0;font-weight:600;cursor:pointer}button.sec{background:var(--card);color:var(--text);border:1px solid var(--line)}
form.inline{display:inline}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.pill{display:inline-block;padding:2px 8px;border-radius:99px;background:#22252a;font-size:12px}
textarea{width:100%;min-height:220px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
.drop{border:2px dashed var(--line);border-radius:12px;padding:30px;text-align:center;color:var(--mut)}
</style></head><body>
{% if nav %}<nav><span class="brand">BEAT MANAGER</span>
{% for href,label in [('/','Overview'),('/beats','Beats'),('/queue','Calendar'),('/research','Research'),('/emails','Emails'),('/contacts','Contacts'),('/events','Log'),('/settings','Settings')] %}
<a href="{{href}}" class="{{'on' if active==href}}">{{label}}</a>{% endfor %}
<form method="post" action="/logout" class="inline" style="margin-left:auto"><button class="sec">Log out</button></form></nav>{% endif %}
<main>{% block body %}{% endblock %}</main></body></html>"""

LOGIN = """{% extends 'base' %}{% block body %}
<div class="card" style="max-width:360px;margin:60px auto">
<h1>Sign in</h1>
{% if not configured %}<p class="warn">Dashboard is not set up yet. Run the setup command on the server.</p>
{% else %}{% if error %}<p class="bad">{{error}}</p>{% endif %}
<form method="post" action="/login" style="display:grid;gap:10px">
<input type="password" name="password" placeholder="Password" autocomplete="current-password" required>
<input name="code" placeholder="2FA code" inputmode="numeric" autocomplete="one-time-code" required>
<button>Sign in</button></form>{% endif %}</div>{% endblock %}"""

OVERVIEW = """{% extends 'base' %}{% block body %}
<h1>Overview</h1>
<div class="grid">
<div class="card"><div class="mut">State</div><div class="big {{'bad' if killed else 'ok'}}">{{'STOPPED' if killed else 'Running'}}</div></div>
<div class="card"><div class="mut">Artists in DB</div><div class="big">{{a.n}}</div><div class="mut">{{a.s}} measured</div></div>
<div class="card"><div class="mut">Measured last 24h</div><div class="big">{{m24}}</div></div>
<div class="card"><div class="mut">Beats</div><div class="big">{{beats_total}}</div><div class="mut">{% for s,n in beat_status %}{{s}}: {{n}} {% endfor %}</div></div>
<div class="card"><div class="mut">Uploaded</div><div class="big">{{uploaded}}</div></div>
<div class="card"><div class="mut">YouTube uploading</div><div class="big {{'ok' if yt else 'warn'}}">{{'ON' if yt else 'OFF'}}</div></div>
</div>
<h2>Next uploads</h2>
<table><tr><th>When (UTC)</th><th>Title</th><th>Status</th></tr>
{% for u in next %}<tr><td>{{u.scheduled.strftime('%a %d %b %H:%M')}}</td><td>{{u.t}}</td><td><span class="pill">{{u.status}}</span></td></tr>{% else %}<tr><td colspan=3 class="mut">Nothing scheduled</td></tr>{% endfor %}</table>
<h2>Needs your review</h2>
<table>{% for b in review %}<tr><td>{{b.name}}</td><td class="warn">{{b.review_note}}</td></tr>{% else %}<tr><td class="mut">Nothing to review</td></tr>{% endfor %}</table>
<h2>Recent events</h2>
<table>{% for e in events %}<tr><td class="mut">{{e.at.strftime('%d %b %H:%M')}}</td><td class="{{'bad' if e.level=='alert' else 'warn' if e.level=='warn' else ''}}">{{e.kind}}</td><td>{{e.message}}</td></tr>{% endfor %}</table>
{% endblock %}"""

BEATS = """{% extends 'base' %}{% block body %}
<h1>Beats</h1>
<div class="card">
<div class="row" style="margin-bottom:10px"><b>1. Batch genre</b>
<select id="genre">{% for g in genres %}<option {{'selected' if g=='detroit'}}>{{g}}</option>{% endfor %}</select>
<input id="newgenre" placeholder="or new genre" size=14>
<span class="mut">Producers in file names: <code>@conkretus @rsd</code>, BPM: <code>142bpm</code></span></div>
<div id="drop" class="drop"><b>2. Drag &amp; drop beats here</b><br><span class="mut">or</span> <label style="text-decoration:underline;cursor:pointer">choose files<input type="file" id="pick" multiple accept=".mp3,.wav" hidden></label></div>
<div id="list" style="margin-top:10px"></div>
<div class="row" style="margin-top:10px"><button id="go" onclick="up()" disabled>3. Upload</button><span id="prog" class="mut"></span></div>
</div>
<h2>All beats</h2>
<table><tr><th>#</th><th>Name</th><th>Genre</th><th>BPM</th><th>Key</th><th>Hz</th><th>Producers</th><th>Status</th><th>Edit</th></tr>
{% for b in beats %}<tr><td class="mut">{{b.id}}</td><td>{{b.name or b.original_name}}{% if b.needs_review %}<div class="warn">{{b.review_note}}</div>{% endif %}</td>
<td>{{b.genre or ''}}</td><td>{{b.bpm|int if b.bpm else ''}}</td><td>{{b.musical_key or ''}}<div class="mut">{{b.key_alt or ''}}</div></td><td>{{b.tuning_hz|int if b.tuning_hz else ''}}</td>
<td>{{(b.producers or [])|join(' x ')}}</td><td><span class="pill">{{b.status}}</span></td>
<td><form method="post" action="/beats/{{b.id}}/edit" class="row"><input name="genre" value="{{b.genre or ''}}" size=7 title="genre"><input name="bpm" value="{{b.bpm|int if b.bpm else ''}}" size=3 title="BPM"><input name="key" value="{{b.musical_key or ''}}" size=7 title="key"><input name="bs" value="{{b.beatstars_url or ''}}" placeholder="BeatStars link" size=12><button class="sec">Save</button></form></td></tr>{% endfor %}</table>
<script>
let files=[];const genres={{genres|tojson}};
const drop=document.getElementById('drop'),list=document.getElementById('list');
function batchGenre(){return (document.getElementById('newgenre').value.trim()||document.getElementById('genre').value).toLowerCase();}
function add(fl){for(const f of fl){if(/\\.(mp3|wav)$/i.test(f.name))files.push({f,g:null});}render();}
function render(){document.getElementById('go').disabled=!files.length;
 list.innerHTML=files.length?'<table><tr><th>File</th><th>Genre (change single beats if needed)</th><th></th></tr>'+files.map((x,i)=>
 `<tr><td>${x.f.name.replace(/</g,'&lt;')}</td><td><select onchange="files[${i}].g=this.value">${['(batch: '+batchGenre()+')',...genres].map(g=>`<option ${x.g===g?'selected':''} value="${g.startsWith('(')?'':g}">${g}</option>`).join('')}</select></td><td><button class="sec" onclick="files.splice(${i},1);render()">x</button></td></tr>`).join('')+'</table>':'';}
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.style.borderColor='var(--acc)';}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.style.borderColor='';}));
drop.addEventListener('drop',ev=>add(ev.dataTransfer.files));
document.getElementById('pick').addEventListener('change',ev=>add(ev.target.files));
document.getElementById('genre').addEventListener('change',render);document.getElementById('newgenre').addEventListener('input',render);
async function up(){const p=document.getElementById('prog');let ok=0,bad=0;document.getElementById('go').disabled=true;
for(const x of files){const fd=new FormData();fd.append('file',x.f);fd.append('relpath','');fd.append('genre',x.g||batchGenre());
const r=await fetch('/api/upload',{method:'POST',body:fd});r.ok?ok++:bad++;p.textContent=`Uploaded ${ok}, failed ${bad} of ${files.length}`;}
p.textContent+=' — done';files=[];setTimeout(()=>location.reload(),1200);}
</script>{% endblock %}"""

QUEUE = """{% extends 'base' %}{% block body %}
<h1>Upload calendar</h1>
<p class="mut">Titles marked <span class="pill">scheduled</span> are preliminary: the final title is chosen 24h before upload with the latest data.
Edit a title to lock it.</p>
<table><tr><th>When (UTC)</th><th>Beat</th><th>Title</th><th>Formula</th><th>Status</th><th></th></tr>
{% for u in rows %}<tr><td>{{u.scheduled.strftime('%a %d %b %H:%M')}}</td><td>{{u.name}}<div class="mut">{{u.genre}}</div></td>
<td><form method="post" action="/queue/{{u.uid}}/title" class="row"><input name="title" value="{{u.title or ''}}" size=60 placeholder="auto"><button class="sec">{{'Locked' if u.title_locked else 'Lock'}}</button></form>{% if u.error %}<div class="bad">{{u.error}}</div>{% endif %}</td>
<td class="mut">{{u.formula or ''}}{% if u.is_test %} <span class="pill">test</span>{% endif %}</td><td><span class="pill">{{u.status}}</span></td>
<td><form method="post" action="/queue/{{u.uid}}/move" class="row"><input type="datetime-local" name="when"><button class="sec">Move</button></form>
<form method="post" action="/queue/{{u.uid}}/skip" class="inline"><button class="sec">Skip</button></form></td></tr>
{% else %}<tr><td colspan=6 class="mut">Calendar is empty. Beats are scheduled once YouTube uploading is ON.</td></tr>{% endfor %}</table>
<h2>Formula performance (avg views after 7 days)</h2>
<table>{% for p in perf %}<tr><td>{{p.formula}}</td><td>{{p.avg_views|int}} views</td><td class="mut">{{p.videos}} videos</td></tr>{% else %}<tr><td class="mut">Not enough data yet</td></tr>{% endfor %}</table>
<h2>Uploaded</h2>
<table>{% for u in done %}<tr><td>{{u.done.strftime('%d %b %H:%M') if u.done}}</td><td>{{u.title}}</td><td class="mut">{{u.formula}}</td><td>{{u.views if u.views is not none else ''}}</td></tr>{% endfor %}</table>
{% endblock %}"""

RESEARCH = """{% extends 'base' %}{% block body %}
<h1>Research</h1>
<div class="card" style="margin-bottom:12px">
<b>How to read this</b>
<div class="grid" style="margin-top:8px">
<div><span class="pill ok">GOOD NICHE</span><div class="mut">People search for this artist, but few type beats are uploaded. Best for the B (niche) slot.</div></div>
<div><span class="pill">BIG NAME</span><div class="mut">Lots of searches and lots of competition. Use as the A (traffic) name.</div></div>
<div><span class="pill warn">CROWDED</span><div class="mut">Too many type beats for the demand. Avoid or use only as 3rd name.</div></div>
<div><span class="pill mut">LOW DEMAND</span><div class="mut">Few people search for it yet. Watch, don't use.</div></div>
</div>
<p class="mut" style="margin:10px 0 0"><b>Searches</b> = how many views a typical type beat for this artist gets per day (shows real search demand).
<b>Competition</b> = how many new type beats were uploaded in the last 30 days.</p></div>
<form class="row card" method="get">
<input name="q" value="{{q}}" placeholder="genre or artist (detroit, flint, plugg, BabyTron…)" size=34>
<select name="sort">{% for k,l in [('opp','Best niches first'),('vpd','Most searched'),('sup','Least competition'),('name','A–Z')] %}<option value="{{k}}" {{'selected' if sort==k}}>{{l}}</option>{% endfor %}</select>
<label class="mut"><input type="checkbox" name="only" value="niche" {{'checked' if only=='niche'}}> only good niches</label>
<button>Search</button></form>
<table><tr><th>Artist</th><th>Verdict</th><th>Searches</th><th>Competition</th><th>Genres</th><th>Updated</th></tr>
{% for r in rows %}<tr><td><b>{{r.name}}</b></td>
<td><span class="pill {{r.vclass}}">{{r.verdict}}</span></td>
<td>{{r.dlabel}} <span class="mut">· {{'%.0f'|format(r.vpd_med or 0)}} views/day</span></td>
<td>{{r.clabel}} <span class="mut">· {{r.sup30}}{{'+' if r.sup30>=20}} uploads/30d</span></td>
<td class="mut">{{r.tags or ''}}</td><td class="mut">{{r.measured.strftime('%d %b') if r.measured}}</td></tr>
{% else %}<tr><td colspan=6 class="mut">No results</td></tr>{% endfor %}</table>
<h2>Current title formulas</h2><p class="mut">Edit in Settings.</p><pre class="card">{{formulas}}</pre>
{% endblock %}"""

CONTACTS = """{% extends 'base' %}{% block body %}
<h1>Contacts</h1>
<div class="grid">{% for s,n in stats %}<div class="card"><div class="mut">{{s}}</div><div class="big">{{n}}</div></div>{% endfor %}</div>
<h2>Import</h2>
<form method="post" action="/contacts/import" class="card">
<p class="mut">Paste anything: CSV, a list, text with emails. Optional: one line per contact as <code>email, name, role</code>.</p>
<textarea name="text"></textarea><div class="row" style="margin-top:8px"><input name="role" placeholder="default role (artist / a&r / manager / producer)"><button>Import</button></div></form>
<h2>List</h2>
<table><tr><th>Email</th><th>Name</th><th>Role</th><th>Genres</th><th>Status</th><th>Sent</th><th>Opened</th><th>Replied</th><th>Source</th></tr>
{% for c in rows %}<tr><td>{{c.email}}</td><td>{{c.name or ''}}</td><td>{{c.role or ''}}</td><td class="mut">{{(c.tags or [])|join(', ')}}</td><td><span class="pill {{'ok' if c.status=='valid' else 'bad' if c.status in ('invalid','bounced') else ''}}">{{c.status}}</span></td>
<td>{{c.sent_count or 0}}</td><td>{{c.opened_count or 0}}</td><td>{{'yes' if c.replied else ''}}</td><td class="mut">{{c.source or ''}}</td></tr>{% endfor %}</table>
{% endblock %}"""

EVENTS = """{% extends 'base' %}{% block body %}<h1>Log</h1>
<table>{% for e in rows %}<tr><td class="mut">{{e.at.strftime('%d %b %H:%M:%S')}}</td><td class="{{'bad' if e.level=='alert' else 'warn' if e.level=='warn' else 'mut'}}">{{e.level}}</td><td>{{e.kind}}</td><td>{{e.message}}</td></tr>{% endfor %}</table>{% endblock %}"""

SETTINGS = """{% extends 'base' %}{% block body %}
<h1>Settings</h1>{% if saved %}<p class="ok">Saved.</p>{% endif %}{% if err %}<p class="bad">{{err}}</p>{% endif %}
<form method="post" action="/settings" class="card" style="display:grid;gap:10px;max-width:720px">
<label><input type="checkbox" name="yt_enabled" {{'checked' if s.yt_enabled}}> YouTube uploading ON</label>
<label><input type="checkbox" name="tg_channel_enabled" {{'checked' if s.tg_channel_enabled}}> Post beats to Telegram channel</label>
<label>Telegram channel id (e.g. @mychannel) <input name="tg_channel_id" value="{{s.tg_channel_id}}"></label>
<label>Telegram channel link (shown in descriptions) <input name="telegram_channel_url" value="{{s.telegram_channel_url}}" size=40></label>
<label>Instagram link <input name="instagram_url" value="{{s.instagram_url}}" size=40></label>
<label>Uploads per day <input name="uploads_per_day" value="{{s.uploads_per_day}}" size=3></label>
<label>Upload hours (UTC, comma separated) <input name="upload_hours_utc" value="{{s.upload_hours_utc|join(',')}}" size=10></label>
<label>[FREE] in titles <select name="free_tag">{% for v in ['test','on','off'] %}<option {{'selected' if s.free_tag==v}}>{{v}}</option>{% endfor %}</select></label>
<label><input type="checkbox" name="email_enabled" {{'checked' if s.email_enabled}}> Weekly email packs ON</label>
<label>Beats per email pack <input name="email_pack_size" value="{{s.email_pack_size}}" size=3></label>
<label>Emails per day target (starts at 30, +15/day warm-up) <input name="email_daily_limit" value="{{s.email_daily_limit}}" size=4></label>
<label>Email subjects (one per line, picked at random)<textarea name="email_subjects" style="min-height:90px">{{subj}}</textarea></label>
<label>Email text<textarea name="email_body" style="min-height:60px">{{body}}</textarea></label>
<label>Signature + disclaimer<textarea name="email_signature" style="min-height:110px">{{sig}}</textarea></label>
<label>Title formulas (JSON)<textarea name="formulas">{{formulas}}</textarea></label>
<label>Artist BPM ranges (beats outside the range won't get that artist in the title; half/double time counts)
<textarea name="artist_bpm" style="min-height:120px">{{artist_bpm}}</textarea></label>
<button>Save</button></form>{% endblock %}"""

EMAILS = """{% extends 'base' %}{% block body %}
<h1>Emails</h1>
<div class="grid">
<div class="card"><div class="mut">Sent this week</div><div class="big">{{w.sent}}</div></div>
<div class="card"><div class="mut">Opened</div><div class="big">{{w.opened}}</div><div class="mut">{{w.open_rate}}% open rate</div></div>
<div class="card"><div class="mut">Replied</div><div class="big ok">{{w.replied}}</div></div>
<div class="card"><div class="mut">Bounced</div><div class="big {{'bad' if w.bounced}}">{{w.bounced}}</div></div>
<div class="card"><div class="mut">Still to send (current pack)</div><div class="big">{{pending}}</div></div>
<div class="card"><div class="mut">Email packs</div><div class="big {{'ok' if enabled else 'warn'}}">{{'ON' if enabled else 'OFF'}}</div></div>
</div>
<h2>Sent this week</h2>
<table><tr><th>When</th><th>To</th><th>Role</th><th>Pack</th><th>Opened</th><th>Replied</th></tr>
{% for r in week %}<tr><td class="mut">{{r.sent_at.strftime('%a %d %b %H:%M')}}</td><td>{{r.email}}{% if r.name %} <span class="mut">({{r.name}})</span>{% endif %}</td><td>{{r.role or ''}}</td>
<td>#{{r.pack_id}}</td><td>{{'yes' if r.status.startswith('opened') else ''}}</td><td class="ok">{{'yes' if r.replied else ''}}</td></tr>
{% else %}<tr><td colspan=6 class="mut">Nothing sent this week</td></tr>{% endfor %}</table>
<h2>Weekly history</h2>
<table><tr><th>Week</th><th>Sent</th><th>Opened</th><th>Open rate</th></tr>
{% for h in hist %}<tr><td>{{h.wk.strftime('%d %b %Y')}}</td><td>{{h.sent}}</td><td>{{h.opened}}</td><td>{{h.rate}}%</td></tr>{% else %}<tr><td colspan=4 class="mut">No history yet</td></tr>{% endfor %}</table>
<h2>Packs</h2>
<table><tr><th>#</th><th>Started</th><th>Beats</th><th>Recipients</th></tr>
{% for p in packs %}<tr><td>{{p.id}}</td><td>{{p.sent_at.strftime('%d %b') if p.sent_at else 'not sent'}}</td><td>{{p.beats}}</td><td>{{p.n}}</td></tr>{% endfor %}</table>
<h2>Most engaged contacts</h2>
<table><tr><th>Email</th><th>Sent</th><th>Opened</th><th>Replied</th><th>Last sent</th></tr>
{% for c in hot %}<tr><td>{{c.email}}</td><td>{{c.sent_count}}</td><td>{{c.opened_count}}</td><td class="ok">{{'yes' if c.replied else ''}}</td><td class="mut">{{c.last_sent.strftime('%d %b') if c.last_sent}}</td></tr>{% endfor %}</table>
{% endblock %}"""

ALL = {"base": BASE, "login": LOGIN, "overview": OVERVIEW, "beats": BEATS, "queue": QUEUE,
       "research": RESEARCH, "contacts": CONTACTS, "emails": EMAILS, "events": EVENTS, "settings": SETTINGS}
