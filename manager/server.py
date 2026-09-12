#!/usr/bin/env python3
import hmac
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


def asterisk_status():
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
    }


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
    button.secondary { background: #34414c; } button.danger { background: #963e46; width: auto; }
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
async function refresh(){ try { const [status,prompts,messages]=await Promise.all([(await api('/api/status')).json(),(await api('/api/prompts')).json(),(await api('/api/voicemails')).json()]); renderStatus(status); renderPrompts(prompts); renderMessages(messages); } catch(e){ tell(e.message,true); } }
function renderStatus(s){ document.getElementById('status').innerHTML=`<span class="pill ${s.asterisk==='online'?'ok':''}">Asterisk ${s.asterisk}</span><span class="pill ${s.trunkRegistered?'ok':''}">Trunk ${s.trunkRegistered?'registered':'offline'}</span><span class="pill ${s.endpoints['101']?'ok':''}">PC ${s.endpoints['101']?'ready':'offline'}</span><span class="pill ${s.endpoints['102']?'ok':''}">Mobile ${s.endpoints['102']?'ready':'offline'}</span><span class="pill ok">${s.activeChannels} active channels</span>`; }
function renderPrompts(settings){ const root=document.getElementById('prompts'); root.innerHTML=''; Object.entries(settings).forEach(([name,value])=>{ const card=document.createElement('div'); card.innerHTML=`<h3>${labels[name]}</h3><label>Voice</label><input id="${name}-voice" value="${escapeHtml(value.voice)}"><label>Script</label><textarea id="${name}-text">${escapeHtml(value.text)}</textarea><div class="actions"><button class="secondary" onclick="preview('${name}')">Preview</button><button onclick="savePrompt('${name}')">Save live</button></div>`; root.appendChild(card); }); }
function escapeHtml(value){ return value.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function promptBody(name){ return JSON.stringify({voice:document.getElementById(name+'-voice').value,text:document.getElementById(name+'-text').value}); }
async function preview(name){ try { tell('Generating preview…'); const response=await api('/api/prompts/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:promptBody(name)}); const player=document.getElementById('player'); player.src=URL.createObjectURL(await response.blob()); player.hidden=false; await player.play(); tell('Preview ready.'); } catch(e){ tell(e.message,true); } }
async function savePrompt(name){ try { tell('Generating and installing prompt…'); await api('/api/prompts/'+name,{method:'PUT',headers:{'Content-Type':'application/json'},body:promptBody(name)}); tell(labels[name]+' updated.'); } catch(e){ tell(e.message,true); } }
async function testCall(extension){ try { await api('/api/test-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:'101',extension:String(extension)})}); tell('Test call sent to the PC.'); } catch(e){ tell(e.message,true); } }
function renderMessages(data){ const root=document.getElementById('voicemails'); if(!data.length){ root.innerHTML='<span class="muted">No retained messages.</span>'; return; } root.innerHTML='<table><thead><tr><th>Recorded</th><th>Size</th><th></th></tr></thead><tbody>'+data.map(v=>`<tr><td><a href="#" onclick="playMessage('${v.name}');return false">${new Date(v.modified*1000).toLocaleString()}</a></td><td>${Math.ceil(v.size/1024)} KB</td><td><button class="danger" onclick="deleteMessage('${v.name}')">Delete</button></td></tr>`).join('')+'</tbody></table>'; }
async function playMessage(name){ try { const response=await api('/api/voicemails/'+encodeURIComponent(name)); const player=document.getElementById('player'); player.src=URL.createObjectURL(await response.blob()); player.hidden=false; await player.play(); } catch(e){ tell(e.message,true); } }
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
                {"name": item.name, "size": item.stat().st_size, "modified": item.stat().st_mtime}
                for item in sorted(VOICEMAIL_DIR.glob("*.wav"), reverse=True)
                if item.is_file()
            ]
            self.send_json(200, messages)
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
        except (urllib.error.URLError, TimeoutError) as error:
            self.send_json(502, {"error": f"Kokoro request failed: {error}"})
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
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
    print(f"Spruik manager listening on {MANAGER_BIND}:{MANAGER_PORT}", flush=True)
    ThreadingHTTPServer((MANAGER_BIND, MANAGER_PORT), Handler).serve_forever()
