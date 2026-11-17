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
{% for href,label in [('/','Overview'),('/beats','Beats'),('/queue','Calendar'),('/research','Research'),('/contacts','Contacts'),('/events','Log'),('/settings','Settings')] %}
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
<p>Upload beats (.mp3 / .wav). Choose a <b>folder</b>: sub-folder names become the genre (e.g. <code>detroit/</code>, <code>rage/</code>).
Tag producers in file names with <code>@name</code>, BPM with <code>140bpm</code>.</p>
<div class="row"><input type="file" id="dir" webkitdirectory multiple><span class="mut">or</span><input type="file" id="files" multiple accept=".mp3,.wav">
<input id="genre" placeholder="genre for loose files (optional)"><button onclick="up()">Upload</button></div>
<p id="prog" class="mut"></p></div>
<h2>All beats</h2>
<table><tr><th>#</th><th>Name</th><th>Genre</th><th>BPM</th><th>Key</th><th>Hz</th><th>Producers</th><th>Status</th><th></th></tr>
{% for b in beats %}<tr><td class="mut">{{b.id}}</td><td>{{b.name or b.original_name}}{% if b.needs_review %}<div class="warn">{{b.review_note}}</div>{% endif %}</td>
<td>{{b.genre or ''}}</td><td>{{b.bpm or ''}}</td><td>{{b.musical_key or ''}}<div class="mut">{{b.key_alt or ''}}</div></td><td>{{b.tuning_hz or ''}}</td>
<td>{{(b.producers or [])|join(' x ')}}</td><td><span class="pill">{{b.status}}</span></td>
<td><form method="post" action="/beats/{{b.id}}/edit" class="row"><input name="genre" value="{{b.genre or ''}}" size=7><input name="bpm" value="{{b.bpm or ''}}" size=4><input name="key" value="{{b.musical_key or ''}}" size=8><input name="bs" value="{{b.beatstars_url or ''}}" placeholder="BeatStars link" size=14><button class="sec">Save</button></form></td></tr>{% endfor %}</table>
<script>
async function up(){const d=document.getElementById('dir').files,f=document.getElementById('files').files,g=document.getElementById('genre').value;
const all=[...d,...f];const p=document.getElementById('prog');let ok=0,bad=0;
for(const file of all){if(!/\\.(mp3|wav)$/i.test(file.name))continue;const fd=new FormData();fd.append('file',file);
fd.append('relpath',file.webkitRelativePath||'');fd.append('genre',g);
const r=await fetch('/api/upload',{method:'POST',body:fd});r.ok?ok++:bad++;p.textContent=`Uploaded ${ok}, failed ${bad} of ${all.length}`;}
p.textContent+=' — done';setTimeout(()=>location.reload(),1200);}
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
<form class="row card" method="get">
<input name="q" value="{{q}}" placeholder="artist name or tag (detroit, flint, plugg…)">
<label class="mut">max uploads/30d <input name="maxsup" value="{{maxsup}}" size=3></label>
<label class="mut">min views/day <input name="minvpd" value="{{minvpd}}" size=4></label>
<select name="sort">{% for k,l in [('opp','Opportunity'),('vpd','Demand'),('sup','Least competition'),('name','Name')] %}<option value="{{k}}" {{'selected' if sort==k}}>{{l}}</option>{% endfor %}</select>
<button>Search</button></form>
<p class="mut">Demand = median views/day of that artist's type beat videos (<1y). Competition = type beats uploaded in the last 7 / 30 days (search page shows up to 20).</p>
<table><tr><th>Artist</th><th>Tags</th><th>Views/day</th><th>Uploads 7d / 30d</th><th>Median views (this month)</th><th>Top-5 avg</th><th>Measured</th></tr>
{% for r in rows %}<tr><td>{{r.name}}</td><td class="mut">{{r.tags or ''}}</td><td>{{'%.0f'|format(r.vpd_med or 0)}}</td><td>{{r.sup7}} / {{r.sup30}}</td><td>{{r.med_month}}</td><td>{{r.top5_avg}}</td><td class="mut">{{r.measured.strftime('%d %b') if r.measured}}</td></tr>
{% else %}<tr><td colspan=7 class="mut">No results</td></tr>{% endfor %}</table>
<h2>Title formulas</h2><p class="mut">Edit in Settings.</p><pre class="card">{{formulas}}</pre>
{% endblock %}"""

CONTACTS = """{% extends 'base' %}{% block body %}
<h1>Contacts</h1>
<div class="grid">{% for s,n in stats %}<div class="card"><div class="mut">{{s}}</div><div class="big">{{n}}</div></div>{% endfor %}</div>
<h2>Import</h2>
<form method="post" action="/contacts/import" class="card">
<p class="mut">Paste anything: CSV, a list, text with emails. Optional: one line per contact as <code>email, name, role</code>.</p>
<textarea name="text"></textarea><div class="row" style="margin-top:8px"><input name="role" placeholder="default role (artist / a&r / manager / producer)"><button>Import</button></div></form>
<h2>List</h2>
<table><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Sent</th><th>Opened</th><th>Replied</th><th>Source</th></tr>
{% for c in rows %}<tr><td>{{c.email}}</td><td>{{c.name or ''}}</td><td>{{c.role or ''}}</td><td><span class="pill {{'ok' if c.status=='valid' else 'bad' if c.status in ('invalid','bounced') else ''}}">{{c.status}}</span></td>
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
<label>Max emails per day <input name="email_daily_limit" value="{{s.email_daily_limit}}" size=4></label>
<label>Title formulas (JSON)<textarea name="formulas">{{formulas}}</textarea></label>
<button>Save</button></form>{% endblock %}"""

ALL = {"base": BASE, "login": LOGIN, "overview": OVERVIEW, "beats": BEATS, "queue": QUEUE,
       "research": RESEARCH, "contacts": CONTACTS, "events": EVENTS, "settings": SETTINGS}
