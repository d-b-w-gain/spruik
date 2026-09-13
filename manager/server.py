#!/usr/bin/env python3
import hmac
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave


ADMIN_TOKEN = os.getenv("SPRUIK_ADMIN_TOKEN", "")
KOKORO_URL = os.getenv(
    "KOKORO_URL", "http://host.docker.internal:8880/v1/audio/speech"
)
SETTINGS_FILE = Path(
    os.getenv("SPRUIK_SETTINGS_FILE", "/var/lib/spruik/settings.json")
)
VOICEMAIL_DIR = Path(
    os.getenv("SPRUIK_VOICEMAIL_DIR", "/var/spool/asterisk/voicemail")
)
MANAGER_BIND = os.getenv("SPRUIK_MANAGER_BIND", "0.0.0.0")
MANAGER_PORT = int(os.getenv("SPRUIK_MANAGER_PORT", "8088"))
SETTINGS_LOCK = threading.Lock()
EVENTS_LOCK = threading.Lock()
RETRY_LOCK = threading.Lock()
SIGNAL_API_URL = os.getenv("SIGNAL_API_URL", "")
SIGNAL_NUMBER = os.getenv("SIGNAL_NUMBER", "")
SIGNAL_RECIPIENT = os.getenv("SIGNAL_RECIPIENT", SIGNAL_NUMBER)
SIGNAL_CALL_CONTROL_URL = os.getenv("SIGNAL_CALL_CONTROL_URL", "")
DATA_DIR = Path(os.getenv("SPRUIK_DATA_DIR", "/var/lib/spruik"))
CALL_HISTORY_FILE = DATA_DIR / "call-history.jsonl"
DELIVERY_HISTORY_FILE = DATA_DIR / "delivery-history.jsonl"
HEALTH_STATE_FILE = DATA_DIR / "health-state.json"
HEALTH_ALERTS_ENABLED = os.getenv("SPRUIK_HEALTH_ALERTS_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
HEALTH_INTERVAL_SECONDS = max(
    15, int(os.getenv("SPRUIK_HEALTH_INTERVAL_SECONDS", "60"))
)
AUTO_RETRY_ENABLED = os.getenv("SPRUIK_AUTO_RETRY_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
AUTO_RETRY_DELAYS = tuple(
    max(15, int(value.strip()))
    for value in os.getenv("SPRUIK_AUTO_RETRY_DELAYS", "60,300,900,3600").split(",")
    if value.strip()
)
AUTO_RETRY_MAX_ATTEMPTS = min(
    20, max(1, int(os.getenv("SPRUIK_AUTO_RETRY_MAX_ATTEMPTS", "8")))
)
AUTO_RETRY_SCAN_SECONDS = max(
    15, int(os.getenv("SPRUIK_AUTO_RETRY_SCAN_SECONDS", "30"))
)

PROMPTS = {
    "standard": {
        "text_env": "STANDARD_GREETING",
        "voice_env": "STANDARD_VOICE",
        "default_text": "Thanks for calling. Please hold while we connect your call.",
        "default_voice": "af_heart",
        "audio": Path("/var/lib/asterisk/sounds/custom/standard-greeting.sln24"),
    },
    "hold": {
        "text_env": "HOLD_PROMO",
        "voice_env": "HOLD_VOICE",
        "default_text": "While we connect your call, thanks for holding. We will be with you shortly.",
        "default_voice": "af_heart",
        "audio": Path("/var/lib/asterisk/moh/spruik/hold-promotion.sln24"),
    },
    "voicemail": {
        "text_env": "VOICEMAIL_GREETING",
        "voice_env": "VOICEMAIL_VOICE",
        "default_text": "Nobody is available to take your call. Please leave a message after the tone, then hang up when you are finished.",
        "default_voice": "af_heart",
        "audio": Path("/var/lib/asterisk/sounds/custom/voicemail-greeting.sln24"),
    },
    "thank_you": {
        "text_env": "THANK_YOU_MESSAGE",
        "voice_env": "THANK_YOU_VOICE",
        "default_text": "Thank you. Your message has been recorded.",
        "default_voice": "af_heart",
        "audio": Path("/var/lib/asterisk/sounds/custom/thank-you.sln24"),
    },
}


def default_settings():
    return {
        name: {
            "text": os.getenv(specification["text_env"], specification["default_text"]),
            "voice": os.getenv(specification["voice_env"], specification["default_voice"]),
        }
        for name, specification in PROMPTS.items()
    }


def load_settings():
    settings = default_settings()
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return settings
    for name in PROMPTS:
        candidate = saved.get(name)
        if not isinstance(candidate, dict):
            continue
        text = candidate.get("text")
        voice = candidate.get("voice")
        if isinstance(text, str) and text.strip():
            settings[name]["text"] = text.strip()
        if isinstance(voice, str) and voice.strip():
            settings[name]["voice"] = voice.strip()
    return settings


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, SETTINGS_FILE)


def synthesize(text, voice):
    body = json.dumps(
        {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            "response_format": "pcm",
            "speed": 1.0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        KOKORO_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        pcm = response.read()
    if not pcm:
        raise ValueError("Kokoro returned empty audio")
    return pcm


def pcm_to_wav(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    return output.getvalue()


def run_asterisk(command):
    return subprocess.run(
        ["asterisk", "-rx", command],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def check_http(url):
    if not url:
        return "disabled"
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            return "online" if response.status < 400 else "offline"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "offline"


def signal_about_url():
    if not SIGNAL_API_URL:
        return ""
    parsed = urllib.parse.urlsplit(SIGNAL_API_URL)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/v1/about", "", "")
    )


def signal_bridge_health_url():
    if not SIGNAL_CALL_CONTROL_URL:
        return ""
    parsed = urllib.parse.urlsplit(SIGNAL_CALL_CONTROL_URL)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/health", "", "")
    )


def asterisk_status():
    signal_bridge = check_http(signal_bridge_health_url())
    signal_messaging = check_http(signal_about_url())
    try:
        registration = run_asterisk("pjsip show registrations")
        contacts = run_asterisk("pjsip show contacts")
        channels = run_asterisk("core show channels concise")
    except (OSError, subprocess.SubprocessError):
        return {
            "asterisk": "offline",
            "trunkRegistered": False,
            "endpoints": {"101": False, "102": False},
            "activeChannels": 0,
            "signalBridge": signal_bridge,
            "signalMessaging": signal_messaging,
            "primaryRoute": "signal",
            "healthAlerts": HEALTH_ALERTS_ENABLED,
            "automaticRetry": AUTO_RETRY_ENABLED,
        }

    contact_lines = contacts.stdout.splitlines()
    return {
        "asterisk": "online" if registration.returncode == 0 else "offline",
        "trunkRegistered": bool(
            re.search(r"\sRegistered\s", registration.stdout, re.IGNORECASE)
        ),
        "endpoints": {
            extension: any(
                re.search(rf"Contact:\s+{extension}/", line)
                and re.search(r"\bAvail\b", line)
                for line in contact_lines
            )
            for extension in ("101", "102")
        },
        "activeChannels": len(
            [line for line in channels.stdout.splitlines() if line.strip()]
        ),
        "signalBridge": signal_bridge,
        "signalMessaging": signal_messaging,
        "primaryRoute": "signal",
        "healthAlerts": HEALTH_ALERTS_ENABLED,
        "automaticRetry": AUTO_RETRY_ENABLED,
    }


def append_event(destination, outcome, route):
    record = {
        "timestamp": int(time.time()),
        "outcome": re.sub(r"[^a-z0-9_-]", "", str(outcome).lower())[:40]
        or "unknown",
        "route": re.sub(r"[^a-z0-9_-]", "", str(route).lower())[:40]
        or "none",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_LOCK:
        try:
            existing = destination.read_text(encoding="utf-8").splitlines()[-199:]
        except (FileNotFoundError, OSError):
            existing = []
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            "\n".join(existing + [json.dumps(record, separators=(",", ":"))])
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)


def read_events(destination, limit=50):
    try:
        lines = destination.read_text(encoding="utf-8").splitlines()[-limit:]
    except (FileNotFoundError, OSError):
        return []
    events = []
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        try:
            timestamp = int(event.get("timestamp", 0))
        except (TypeError, ValueError):
            continue
        events.append(
            {
                "timestamp": timestamp,
                "outcome": str(event.get("outcome", "unknown")),
                "route": str(event.get("route", "none")),
            }
        )
    return events


def recording_delivery_state(recording):
    metadata = Path(str(recording) + ".delivery.json")
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"attempts": 0, "lastAttempt": None, "lastStatus": "retained"}
    return {
        "attempts": max(0, int(value.get("attempts", 0))),
        "lastAttempt": value.get("lastAttempt"),
        "lastStatus": str(value.get("lastStatus", "retained")),
    }


def save_delivery_state(recording, attempts, status, last_attempt=None):
    metadata = Path(str(recording) + ".delivery.json")
    metadata.write_text(
        json.dumps(
            {
                "attempts": attempts,
                "lastAttempt": int(time.time() if last_attempt is None else last_attempt),
                "lastStatus": status,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def signal_payload(message, recording=None):
    if not SIGNAL_API_URL or not SIGNAL_NUMBER or not SIGNAL_RECIPIENT:
        raise RuntimeError("Signal delivery is not configured")
    payload = {
        "number": SIGNAL_NUMBER,
        "recipients": [SIGNAL_RECIPIENT],
        "message": message,
        "notify_self": SIGNAL_NUMBER == SIGNAL_RECIPIENT,
    }
    if recording is not None:
        encoded = base64.b64encode(recording.read_bytes()).decode("ascii")
        payload["base64_attachments"] = [
            f"data:audio/wav;filename={recording.name};base64,{encoded}"
        ]
    return payload


def send_signal(message, recording=None):
    request = urllib.request.Request(
        SIGNAL_API_URL,
        data=json.dumps(signal_payload(message, recording), separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status >= 300:
            raise RuntimeError(f"Signal returned HTTP {response.status}")


def retry_voicemail(recording, source="manual"):
    with RETRY_LOCK:
        if not recording.is_file():
            raise FileNotFoundError("Voicemail no longer exists")
        state = recording_delivery_state(recording)
        attempts = state["attempts"] + 1
        try:
            send_signal("Retried Spruik voicemail.", recording)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
            save_delivery_state(recording, attempts, "failed")
            append_event(DELIVERY_HISTORY_FILE, "retained", f"signal-{source}")
            raise
        Path(str(recording) + ".delivery.json").unlink(missing_ok=True)
        recording.unlink()
        append_event(DELIVERY_HISTORY_FILE, "delivered", f"signal-{source}")


def automatic_retry_due(recording, state, now=None):
    now = time.time() if now is None else now
    attempts = state["attempts"]
    if attempts >= AUTO_RETRY_MAX_ATTEMPTS:
        return False
    if not AUTO_RETRY_DELAYS:
        return False
    delay_index = max(0, attempts - 1)
    delay = AUTO_RETRY_DELAYS[min(delay_index, len(AUTO_RETRY_DELAYS) - 1)]
    reference = state.get("lastAttempt") or recording.stat().st_mtime
    return now >= float(reference) + delay


def voicemail_retry_monitor():
    while True:
        try:
            VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
            for recording in sorted(VOICEMAIL_DIR.glob("*.wav")):
                if not recording.is_file():
                    continue
                state = recording_delivery_state(recording)
                if state["attempts"] >= AUTO_RETRY_MAX_ATTEMPTS:
                    if state["lastStatus"] != "exhausted":
                        save_delivery_state(
                            recording,
                            state["attempts"],
                            "exhausted",
                            state.get("lastAttempt"),
                        )
                        append_event(
                            DELIVERY_HISTORY_FILE, "exhausted", "signal-auto"
                        )
                    continue
                if not automatic_retry_due(recording, state):
                    continue
                try:
                    retry_voicemail(recording, "auto")
                except (
                    FileNotFoundError,
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                    RuntimeError,
                ):
                    pass
        except OSError:
            pass
        time.sleep(AUTO_RETRY_SCAN_SECONDS)


def health_monitor():
    previous = None
    while True:
        status = asterisk_status()
        current = {
            "trunk": status["trunkRegistered"],
            "signalVoice": status["signalBridge"],
            "signalMessages": status["signalMessaging"],
        }
        if previous is not None and current != previous:
            changes = [
                f"{name}: {previous.get(name)} -> {value}"
                for name, value in current.items()
                if previous.get(name) != value
            ]
            try:
                send_signal("Spruik health changed. " + "; ".join(changes))
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
                pass
        previous = current
        try:
            HEALTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            HEALTH_STATE_FILE.write_text(
                json.dumps(current, separators=(",", ":")) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
        time.sleep(HEALTH_INTERVAL_SECONDS)


def safe_recording(name):
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.wav", name):
        return None
    directory = VOICEMAIL_DIR.resolve()
    candidate = (directory / name).resolve()
    if candidate.parent != directory:
        return None
    return candidate


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Spruik</title>
  <style>
    :root { color-scheme: dark; font: 16px system-ui, sans-serif; background: #101419; color: #edf2f7; }
    body { max-width: 1050px; margin: 0 auto; padding: 28px 18px 60px; }
    h1 { margin: 0 0 4px; font-size: 2.4rem; } h2 { margin-top: 0; }
    .muted { color: #9eabb8; } .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit,minmax(290px,1fr)); }
    section { background: #192129; border: 1px solid #2a3640; border-radius: 12px; padding: 18px; margin-top: 18px; }
    input, textarea, select, button { box-sizing: border-box; width: 100%; font: inherit; border-radius: 7px; border: 1px solid #40505e; padding: 9px; background: #0e1419; color: inherit; }
    textarea { min-height: 105px; resize: vertical; } button { cursor: pointer; background: #1e7f62; border: 0; font-weight: 700; }
    button.secondary { background: #34414c; } button.danger { background: #963e46; width: auto; } button.compact { width: auto; margin-left: 6px; }
    label { display: block; margin: 10px 0 5px; color: #b8c3cd; } .actions { display: flex; gap: 8px; margin-top: 10px; }
    .actions button { flex: 1; } .pill { display: inline-block; padding: 4px 8px; border-radius: 999px; margin: 3px; background: #692f36; }
    .pill.ok { background: #176347; } table { width: 100%; border-collapse: collapse; } td, th { text-align: left; border-bottom: 1px solid #33404a; padding: 9px 5px; }
    audio { width: 100%; margin-top: 10px; } #notice { min-height: 1.5em; color: #62d5a9; }
  </style>
</head>
<body>
  <h1>Spruik</h1><div class="muted">Your small, overly articulate phone system.</div>
  <div id="notice"></div>
  <section id="login"><h2>Connect</h2><label>Admin token</label><input id="token" type="password" autocomplete="off"><button style="margin-top:10px" onclick="connect()">Open Spruik</button></section>
  <main id="app" hidden>
    <section><h2>Status</h2><div id="status">Loading…</div><div class="actions"><button class="secondary" onclick="refresh()">Refresh</button><button class="secondary" onclick="testCall(600)">Test welcome</button><button class="secondary" onclick="testCall(601)">Test hold</button><button class="secondary" onclick="testCall(602)">Test voicemail</button></div></section>
    <section><h2>Messages</h2><div id="voicemails">Loading…</div></section>
    <section><h2>Recent activity</h2><div id="calls">Loading…</div></section>
    <section><h2>Signal delivery history</h2><div id="deliveries">Loading…</div></section>
    <section><h2>Prompts</h2><div class="grid" id="prompts"></div></section>
    <audio id="player" controls hidden></audio>
  </main>
<script>
let token = '';
const labels = {standard:'Standard welcome',hold:'While ringing',voicemail:'Voicemail',thank_you:'Confirmation'};
async function api(path, options={}) {
  options.headers = Object.assign({'Authorization':'Bearer '+token}, options.headers||{});
  const response = await fetch(path, options);
  if (!response.ok) throw new Error((await response.json().catch(()=>({error:response.statusText}))).error);
  return response;
}
function tell(message, bad=false) { const n=document.getElementById('notice'); n.textContent=message; n.style.color=bad?'#ff8e96':'#62d5a9'; }
async function connect(){ token=document.getElementById('token').value; try { await api('/api/status'); document.getElementById('login').hidden=true; document.getElementById('app').hidden=false; await refresh(); } catch(e){ tell(e.message,true); } }
async function refresh(){ try { const [status,prompts,messages,calls,deliveries]=await Promise.all([(await api('/api/status')).json(),(await api('/api/prompts')).json(),(await api('/api/voicemails')).json(),(await api('/api/calls')).json(),(await api('/api/deliveries')).json()]); renderStatus(status); renderPrompts(prompts); renderMessages(messages); renderCalls(calls); renderDeliveries(deliveries); } catch(e){ tell(e.message,true); } }
function renderStatus(s){ document.getElementById('status').innerHTML=`<span class="pill ${s.signalBridge==='online'?'ok':''}">Signal voice ${s.signalBridge}</span><span class="pill ${s.signalMessaging==='online'?'ok':''}">Signal messages ${s.signalMessaging}</span><span class="pill ${s.trunkRegistered?'ok':''}">Trunk ${s.trunkRegistered?'registered':'offline'}</span><span class="pill ${s.endpoints['101']?'ok':''}">PC backup ${s.endpoints['101']?'ready':'offline'}</span><span class="pill ${s.endpoints['102']?'ok':''}">Mobile backup ${s.endpoints['102']?'ready':'offline'}</span><span class="pill ok">${s.activeChannels} active channels</span><span class="pill ${s.healthAlerts?'ok':''}">Alerts ${s.healthAlerts?'on':'off'}</span><span class="pill ${s.automaticRetry?'ok':''}">Auto retry ${s.automaticRetry?'on':'off'}</span>`; }
function renderPrompts(settings){ const root=document.getElementById('prompts'); root.innerHTML=''; Object.entries(settings).forEach(([name,value])=>{ const card=document.createElement('div'); card.innerHTML=`<h3>${labels[name]}</h3><label>Voice</label><input id="${name}-voice" value="${escapeHtml(value.voice)}"><label>Script</label><textarea id="${name}-text">${escapeHtml(value.text)}</textarea><div class="actions"><button class="secondary" onclick="preview('${name}')">Preview</button><button onclick="savePrompt('${name}')">Save live</button></div>`; root.appendChild(card); }); }
function escapeHtml(value){ return value.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function promptBody(name){ return JSON.stringify({voice:document.getElementById(name+'-voice').value,text:document.getElementById(name+'-text').value}); }
async function preview(name){ try { tell('Generating preview…'); const response=await api('/api/prompts/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:promptBody(name)}); const player=document.getElementById('player'); player.src=URL.createObjectURL(await response.blob()); player.hidden=false; await player.play(); tell('Preview ready.'); } catch(e){ tell(e.message,true); } }
async function savePrompt(name){ try { tell('Generating and installing prompt…'); await api('/api/prompts/'+name,{method:'PUT',headers:{'Content-Type':'application/json'},body:promptBody(name)}); tell(labels[name]+' updated.'); } catch(e){ tell(e.message,true); } }
async function testCall(extension){ try { await api('/api/test-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:'101',extension:String(extension)})}); tell('Test call sent to the PC.'); } catch(e){ tell(e.message,true); } }
function renderMessages(data){ const root=document.getElementById('voicemails'); if(!data.length){ root.innerHTML='<span class="muted">No retained messages.</span>'; return; } root.innerHTML='<table><thead><tr><th>Recorded</th><th>Delivery</th><th></th></tr></thead><tbody>'+data.map(v=>`<tr><td><a href="#" onclick="playMessage('${v.name}');return false">${new Date(v.modified*1000).toLocaleString()}</a><div class="muted">${Math.ceil(v.size/1024)} KB</div></td><td>${escapeHtml(v.delivery.lastStatus)}${v.delivery.attempts?' · '+v.delivery.attempts+' attempt'+(v.delivery.attempts===1?'':'s'):''}</td><td><button class="compact" onclick="retryMessage('${v.name}')">Retry Signal</button><button class="danger compact" onclick="deleteMessage('${v.name}')">Delete</button></td></tr>`).join('')+'</tbody></table>'; }
function renderCalls(data){ const root=document.getElementById('calls'); if(!data.length){ root.innerHTML='<span class="muted">No call outcomes recorded yet.</span>'; return; } root.innerHTML='<table><thead><tr><th>When</th><th>Outcome</th><th>Route</th></tr></thead><tbody>'+data.map(v=>`<tr><td>${new Date(v.timestamp*1000).toLocaleString()}</td><td>${escapeHtml(v.outcome)}</td><td>${escapeHtml(v.route)}</td></tr>`).join('')+'</tbody></table>'; }
function renderDeliveries(data){ const root=document.getElementById('deliveries'); if(!data.length){ root.innerHTML='<span class="muted">No delivery retries recorded yet.</span>'; return; } root.innerHTML='<table><thead><tr><th>When</th><th>Result</th><th>Source</th></tr></thead><tbody>'+data.map(v=>`<tr><td>${new Date(v.timestamp*1000).toLocaleString()}</td><td>${escapeHtml(v.outcome)}</td><td>${escapeHtml(v.route)}</td></tr>`).join('')+'</tbody></table>'; }
async function playMessage(name){ try { const response=await api('/api/voicemails/'+encodeURIComponent(name)); const player=document.getElementById('player'); player.src=URL.createObjectURL(await response.blob()); player.hidden=false; await player.play(); } catch(e){ tell(e.message,true); } }
async function retryMessage(name){ try { tell('Retrying Signal delivery…'); await api('/api/voicemails/'+encodeURIComponent(name)+'/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); tell('Voicemail delivered through Signal.'); await refresh(); } catch(e){ tell(e.message,true); await refresh(); } }
async function deleteMessage(name){ if(!confirm('Delete this retained voicemail?')) return; try { await api('/api/voicemails/'+encodeURIComponent(name),{method:'DELETE'}); tell('Voicemail deleted.'); await refresh(); } catch(e){ tell(e.message,true); } }
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SpruikManager/0.1"

    def log_message(self, format_string, *args):
        print(
            "%s - %s" % (self.address_string(), format_string % args), flush=True
        )

    def send_json(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self):
        if not ADMIN_TOKEN:
            self.send_json(503, {"error": "Spruik management is disabled"})
            return False
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {ADMIN_TOKEN}"
        if not hmac.compare_digest(supplied, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        return True

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1_000_000:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length))

    def send_audio(self, audio, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(audio)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            encoded = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src 'self' blob:")
            self.end_headers()
            self.wfile.write(encoded)
            return
        if path == "/healthz":
            self.send_json(200, {"ready": bool(ADMIN_TOKEN)})
            return
        if not self.authorized():
            return
        if path == "/api/status":
            self.send_json(200, asterisk_status())
            return
        if path == "/api/prompts":
            self.send_json(200, load_settings())
            return
        if path == "/api/voicemails":
            VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
            messages = [
                {
                    "name": item.name,
                    "size": item.stat().st_size,
                    "modified": item.stat().st_mtime,
                    "delivery": recording_delivery_state(item),
                }
                for item in sorted(VOICEMAIL_DIR.glob("*.wav"), reverse=True)
                if item.is_file()
            ]
            self.send_json(200, messages)
            return
        if path == "/api/calls":
            self.send_json(200, read_events(CALL_HISTORY_FILE))
            return
        if path == "/api/deliveries":
            self.send_json(200, read_events(DELIVERY_HISTORY_FILE))
            return
        if path.startswith("/api/voicemails/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            recording = safe_recording(name)
            if recording is None or not recording.is_file():
                self.send_json(404, {"error": "Voicemail not found"})
                return
            self.send_audio(recording.read_bytes(), recording.name)
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            return
        try:
            if path.startswith("/api/voicemails/") and path.endswith("/retry"):
                name = urllib.parse.unquote(path.split("/")[-2])
                recording = safe_recording(name)
                if recording is None or not recording.is_file():
                    self.send_json(404, {"error": "Voicemail not found"})
                    return
                retry_voicemail(recording, "manual")
                self.send_json(200, {"delivered": True})
                return
            payload = self.read_json()
            if path == "/api/prompts/preview":
                text, voice = validate_prompt(payload)
                self.send_audio(pcm_to_wav(synthesize(text, voice)), "preview.wav")
                return
            if path == "/api/test-call":
                target = str(payload.get("target", ""))
                extension = str(payload.get("extension", ""))
                if target not in {"101", "102"} or extension not in {"600", "601", "602"}:
                    raise ValueError("Unsupported test call")
                result = run_asterisk(
                    f"channel originate PJSIP/{target} extension {extension}@from-internal"
                )
                if result.returncode != 0:
                    raise RuntimeError("Asterisk rejected the test call")
                self.send_json(202, {"accepted": True})
                return
            self.send_json(404, {"error": "Not found"})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            service = "Signal" if path.endswith("/retry") else "Kokoro"
            self.send_json(502, {"error": f"{service} request failed: {error}"})
        except (OSError, subprocess.SubprocessError) as error:
            self.send_json(500, {"error": str(error)})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            return
        if not path.startswith("/api/prompts/"):
            self.send_json(404, {"error": "Not found"})
            return
        name = path.rsplit("/", 1)[-1]
        if name not in PROMPTS:
            self.send_json(404, {"error": "Unknown prompt"})
            return
        try:
            text, voice = validate_prompt(self.read_json())
            pcm = synthesize(text, voice)
            destination = PROMPTS[name]["audio"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(pcm)
            os.replace(temporary, destination)
            with SETTINGS_LOCK:
                settings = load_settings()
                settings[name] = {"text": text, "voice": voice}
                save_settings(settings)
            if name == "hold":
                run_asterisk("moh reload")
            self.send_json(200, {"saved": True})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except (urllib.error.URLError, TimeoutError) as error:
            self.send_json(502, {"error": f"Kokoro request failed: {error}"})
        except OSError as error:
            self.send_json(500, {"error": str(error)})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            return
        if not path.startswith("/api/voicemails/"):
            self.send_json(404, {"error": "Not found"})
            return
        name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        recording = safe_recording(name)
        if recording is None or not recording.is_file():
            self.send_json(404, {"error": "Voicemail not found"})
            return
        recording.unlink()
        Path(str(recording) + ".delivery.json").unlink(missing_ok=True)
        self.send_json(200, {"deleted": True})


def validate_prompt(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    text = str(payload.get("text", "")).strip()
    voice = str(payload.get("voice", "")).strip()
    if not text or len(text) > 1500:
        raise ValueError("Prompt text must be between 1 and 1500 characters")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", voice):
        raise ValueError("Voice contains unsupported characters")
    return text, voice


if __name__ == "__main__":
    if not ADMIN_TOKEN:
        raise SystemExit("SPRUIK_ADMIN_TOKEN is empty; management UI is disabled")
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
    if HEALTH_ALERTS_ENABLED:
        threading.Thread(target=health_monitor, daemon=True).start()
    if AUTO_RETRY_ENABLED:
        threading.Thread(target=voicemail_retry_monitor, daemon=True).start()
    print(f"Spruik manager listening on {MANAGER_BIND}:{MANAGER_PORT}", flush=True)
    ThreadingHTTPServer((MANAGER_BIND, MANAGER_PORT), Handler).serve_forever()
