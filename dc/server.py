from flask import Flask, request, jsonify, send_from_directory
import requests, os, time, threading

app = Flask(__name__, static_folder='static')
DISCORD_API = "https://discord.com/api/v10"

# in-memory typing tracker: { channel_id: [ {id, name, ts} ] }
typing_store = {}
typing_lock = threading.Lock()

def dh(token):
    return { "Authorization": token, "User-Agent": "DiscordBot (local, 1.0)" }

def clean(t):
    return "".join(c for c in t if ord(c) < 128).strip()

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/login", methods=["POST"])
def login():
    raw = clean(request.get_json().get("token",""))
    if not raw: return jsonify({"error":"No token"}), 400
    bot_token = ("Bot "+raw) if not raw.startswith("Bot ") else raw
    r = requests.get(f"{DISCORD_API}/users/@me/guilds?limit=200", headers=dh(bot_token), timeout=10)
    if r.status_code == 200:
        return jsonify({"ok":True,"token":bot_token,"user":{"username":"Bot","id":"0","avatar":None},"guilds":r.json()})
    r2 = requests.get(f"{DISCORD_API}/users/@me", headers=dh(raw), timeout=10)
    if r2.status_code == 200:
        r3 = requests.get(f"{DISCORD_API}/users/@me/guilds?limit=200", headers=dh(raw), timeout=10)
        return jsonify({"ok":True,"token":raw,"user":r2.json(),"guilds":r3.json() if r3.status_code==200 else []})
    return jsonify({"error":f"Rejected: {r.text[:200]}"}), 401

@app.route("/api/guilds/<gid>/channels")
def get_channels(gid):
    r = requests.get(f"{DISCORD_API}/guilds/{gid}/channels", headers=dh(request.headers.get("Authorization")), timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/guilds/<gid>/roles")
def get_roles(gid):
    r = requests.get(f"{DISCORD_API}/guilds/{gid}/roles", headers=dh(request.headers.get("Authorization")), timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/guilds/<gid>/members")
def get_members(gid):
    token = request.headers.get("Authorization")
    r = requests.get(f"{DISCORD_API}/guilds/{gid}/members?limit=1000", headers=dh(token), timeout=15)
    if r.status_code != 200:
        return jsonify(r.json()), r.status_code
    members = r.json()
    roles_r = requests.get(f"{DISCORD_API}/guilds/{gid}/roles", headers=dh(token), timeout=10)
    role_map = {}
    if roles_r.status_code == 200:
        for role in roles_r.json():
            role_map[role["id"]] = {
                "name": role.get("name",""),
                "color": "#{:06x}".format(role["color"]) if role.get("color") else None,
                "position": role.get("position", 0)
            }
    result = []
    for m in members:
        # find top-colored role by position
        top_color = None
        top_pos = -1
        role_names = []
        for rid in m.get("roles", []):
            if rid in role_map:
                rm = role_map[rid]
                role_names.append(rm["name"])
                if rm["color"] and rm["position"] > top_pos:
                    top_color = rm["color"]
                    top_pos = rm["position"]
        result.append({
            "user": m["user"],
            "nick": m.get("nick"),
            "roles": m.get("roles", []),
            "role_names": role_names,
            "color": top_color,
            "status": "offline"  # bots can't get presence without gateway
        })
    return jsonify(result)

@app.route("/api/channels/<cid>/messages", methods=["GET"])
def get_messages(cid):
    token = request.headers.get("Authorization")
    limit = request.args.get("limit","50")
    before = request.args.get("before","")
    url = f"{DISCORD_API}/channels/{cid}/messages?limit={limit}"
    if before: url += f"&before={before}"
    after = request.args.get("after","")
    if after: url += f"&after={after}"
    r = requests.get(url, headers=dh(token), timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/channels/<cid>/messages", methods=["POST"])
def send_message(cid):
    token = request.headers.get("Authorization")
    data = request.get_json()
    r = requests.post(f"{DISCORD_API}/channels/{cid}/messages", headers=dh(token),
                      json={"content": data.get("content","")}, timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/channels/<cid>/upload", methods=["POST"])
def upload_file(cid):
    token = request.headers.get("Authorization")
    files = {}
    data = {}
    if 'files[0]' in request.files:
        f = request.files['files[0]']
        files['files[0]'] = (f.filename, f.stream, f.content_type)
    if 'payload_json' in request.form:
        import json
        data = json.loads(request.form['payload_json'])
    headers = dh(token)
    headers.pop("Content-Type", None)
    r = requests.post(f"{DISCORD_API}/channels/{cid}/messages",
                      headers=headers,
                      data={"payload_json": request.form.get("payload_json","{}") } if data else {},
                      files=files, timeout=30)
    return jsonify(r.json()), r.status_code

@app.route("/api/channels/<cid>/typing", methods=["POST"])
def typing(cid):
    token = request.headers.get("Authorization")
    requests.post(f"{DISCORD_API}/channels/{cid}/typing", headers=dh(token), timeout=5)
    name = request.headers.get("X-Display-Name", "Someone")
    uid = request.headers.get("X-User-Id", "0")
    with typing_lock:
        if cid not in typing_store:
            typing_store[cid] = []
        existing = [u for u in typing_store[cid] if u["id"] == uid]
        if existing:
            existing[0]["ts"] = time.time() * 1000
        else:
            typing_store[cid].append({"id": uid, "name": name, "ts": time.time() * 1000})
    return jsonify({"ok": True})

@app.route("/api/channels/<cid>/typing-status")
def typing_status(cid):
    with typing_lock:
        users = typing_store.get(cid, [])
        now = time.time() * 1000
        active = [u for u in users if (now - u["ts"]) < 9000]
        typing_store[cid] = active
    return jsonify({"users": active})

@app.route("/api/dms")
def get_dms():
    r = requests.get(f"{DISCORD_API}/users/@me/channels", headers=dh(request.headers.get("Authorization")), timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/dms/open", methods=["POST"])
def open_dm():
    token = request.headers.get("Authorization")
    data = request.get_json()
    r = requests.post(f"{DISCORD_API}/users/@me/channels", headers=dh(token),
                      json={"recipient_id": data.get("recipient_id")}, timeout=10)
    return jsonify(r.json()), r.status_code

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    app.run(host="0.0.0.0", port=5055, debug=False)
