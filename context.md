# AICHATBOT – Linux Server Deployment

Server folder: `/opt/AICHATBOT`  ·  Python venv: `venv312`  ·  App port: `8000`  ·  Public URL: **http://124.7.209.74:8000**

## What was changed on the PC (already done)

| Change | Why |
|---|---|
| Downloaded the embedding model into `models/all-MiniLM-L6-v2/` (88 MB) | The server can't reach `us.aws.cdn.hf.co` (firewall), so startup hung (see `errorchatbot.png`) |
| `src/helper.py` now loads the model from `models/all-MiniLM-L6-v2/` when it exists | No Hugging Face download needed on the server |
| Removed the 2 `-e git+https://github.com/...` lines from `requirements.txt` | One had a wrong package name (`pip install` fails); both installed an old copy of `src` |
| Renamed `templates/Record-view.html` → `templates/record-view.html` | `app.py` asks for the lowercase name; Linux treats them as different files |
| `app.py`: port `8000`, `debug=False` | Public URL http://124.7.209.74:8000; debug mode would let visitors run code on the server |

## Files the server must have

Copy these from the PC if they're not on the server yet (git does **not** carry `.env`, and `Jul-2026.xls` is too large for GitHub):

```
/opt/AICHATBOT/
├── .env                              ← 4 keys: PINECONE, GOOGLE, GROQ, TAVILY
├── requirements.txt                  ← updated version
├── src/helper.py                     ← updated version
├── models/all-MiniLM-L6-v2/          ← whole folder, including empty 2_Normalize/
└── data/
    ├── Jul-2026.xls
    └── cm_pincode.xlsx
```

Copy from Windows PowerShell (replace `SERVER_IP`):

```powershell
scp -r "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\models" root@SERVER_IP:/opt/AICHATBOT/
scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\src\helper.py" root@SERVER_IP:/opt/AICHATBOT/src/
scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\app.py" root@SERVER_IP:/opt/AICHATBOT/
scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\requirements.txt" root@SERVER_IP:/opt/AICHATBOT/
scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\templates\record-view.html" root@SERVER_IP:/opt/AICHATBOT/templates/
scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\.env" root@SERVER_IP:/opt/AICHATBOT/
```

---

## Commands to run on the Linux server

### Step 1 – Go to the project and activate the venv

```bash
cd /opt/AICHATBOT
source venv312/bin/activate
```

### Step 2 – Install packages

```bash
pip uninstall -y tci-chatbot medical-chatbot
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

- `not installed` from the uninstall line is fine.
- Torch is installed first from the CPU index so pip doesn't download the multi-GB GPU build.

### Step 3 – Check Python uses the current code

```bash
python -c "import src.helper as h; print(h.__file__)"
```

✅ Must print: `/opt/AICHATBOT/src/helper.py`
❌ If it prints a path inside `venv312/src/...`, the old copy is still installed – repeat Step 2's uninstall line.

### Step 4 – Check the model file copied correctly

```bash
sha256sum models/all-MiniLM-L6-v2/model.safetensors
```

✅ Must print: `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db`
❌ Anything else → copy the `models` folder again.

### Step 5 – Test the model loads without internet

```bash
export HF_HUB_OFFLINE=1
python -c "from src.helper import download_hugging_face_embeddings as d; print(len(d().embed_query('test')))"
```

✅ Must show `[INFO] Loading embedding model from local folder: ...` and then print `384` within a few seconds.
❌ `Local model folder not found` → the `models` folder isn't at `/opt/AICHATBOT/models/all-MiniLM-L6-v2/`.

### Step 6 – Check the files the app needs

```bash
ls -la .env data/Jul-2026.xls data/cm_pincode.xlsx
grep -c "_API_KEY" .env
```

✅ All 3 files listed, and the count is `4`.

### Step 7 – Check the server can reach the other services

The chatbot calls these on every message. The same firewall that blocked Hugging Face may block them.

```bash
for u in https://api.groq.com https://api.pinecone.io https://api.tavily.com https://www.tciexpress.in; do
  curl -4 -s -o /dev/null -w "$u -> %{http_code}\n" --connect-timeout 10 $u || echo "$u -> BLOCKED"
done
```

✅ Any 3-digit code (200, 301, 404…) means it's reachable.
❌ `000` or `BLOCKED` → ask IT to allow that domain on port 443.

### Step 8 – Check the record page file name (Linux is case-sensitive)

Already renamed to `record-view.html` on the PC. Make sure the server has the new name and no old copy:

```bash
ls templates/
```

✅ Shows `record-view.html` (all lowercase).
❌ Still shows `Record-view.html` → copy the `templates` folder again, or run:
`mv templates/Record-view.html templates/record-view.html`

### Step 9 – Start the app

Always start from the project folder.

```bash
cd /opt/AICHATBOT
source venv312/bin/activate
export HF_HUB_OFFLINE=1
python app.py
```

✅ Good startup looks like:

```
[+] Analytics Ready: 441,985 records loaded.
[+] Pincode Master Ready: ... records loaded.
[INFO] Loading embedding model from local folder: /opt/AICHATBOT/models/all-MiniLM-L6-v2
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:8000
 * Running on http://124.7.209.74:8000
```

(The last line only appears if `124.7.209.74` is set directly on the server's network card. If it's missing, that's fine – it means a router forwards that IP to the server.)

A `HuggingFaceEmbeddings` deprecation warning is normal and harmless.

### Step 10 – Test it from the server

In a second terminal:

```bash
curl -s -X POST -d "msg=hello" http://localhost:8000/get
```

✅ Returns JSON with an `"answer"` field.

Then open **http://124.7.209.74:8000** in a browser on your PC. If the server test works but the browser can't connect, the firewall port is closed (see below).

---

## Still to do before going live

- **Open port 8000 in the firewall** if users can't reach http://124.7.209.74:8000:
  ```bash
  firewall-cmd --permanent --add-port=8000/tcp && firewall-cmd --reload
  firewall-cmd --list-ports          # should show 8000/tcp
  ```
  If it still doesn't open, check port 8000 is free and the app is listening on it:
  ```bash
  ss -tlnp | grep 8000               # should show python listening on 0.0.0.0:8000
  ```
  If a cloud provider or company firewall sits in front of the server, port 8000 must be allowed there too.
- **If you put nginx in front**, set `client_max_body_size 50M;` (uploads over 1 MB fail otherwise) and `proxy_read_timeout 180s;` (slow answers return 504 otherwise).

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `Failed to connect to us.aws.cdn.hf.co` / startup hangs | Model folder missing, app is trying to download | Steps 4–5 |
| `FileNotFoundError: data/Jul-2026.xls` | Data files not in `data/`, or app started from another folder | Step 6, and `cd /opt/AICHATBOT` before starting |
| `Missing required env variables` | `.env` missing or incomplete | Step 6 |
| `The api_key client option must be set` | `GROQ_API_KEY` missing from `.env` | Step 6 |
| `TemplateNotFound: record-view.html` | File name capital letter | Step 8 |
| `inconsistent name: expected 'medical-chatbot'` | Old `requirements.txt` on the server | Copy the updated `requirements.txt`, redo Step 2 |
| Answers fail with connection/timeout errors | Firewall blocks Groq / Pinecone / Tavily | Step 7 |

---

# Session Log – 2026-11-02

## Problem
Full tracking details were not coming in the chatbot, but the same DWB showed full tracking in the browser (e.g. DWB `299106861`).

## Root cause (in `src/tracking_service.py`)
The page fetch was fine – the HTML from `https://www.tciexpress.in/track-your-shipment?trackshipment=<DWB>&dwb=dwb` already contains all data. The **parser** was wrong:

| Bug | Effect |
|---|---|
| `extract_label_value`: site renders `Status:` on one line and the value on the next; code only read text after the colon | `status`, `destination_pincode`, `destination_location` were empty |
| `extract_booking_information`: site renders a header row (Booking Station, Delivery Station, Booking Date, Packets, Weight, Expected Delivery Date) then a value row; code took the first value for every label | `packets`, `weight`, dates all showed "XDPT (DABASPETE)" |

## Fix
1. `extract_label_value`: if nothing follows `Label:`, read the next line via `find_next_real_value`.
2. `extract_booking_information`: find "Booking Information", collect the consecutive header labels, then map each header to the value at the same column position.

Verified on the live page for DWB `299106861`: status `Docket Delivered On 06-AUG-2026`, destination `400703` / `XNRL(NERUL)`, booking `XDPT (DABASPETE)` → `XNRL (NERUL)`, 01-AUG-2026, 1 packet, weight 10, expected 05-AUG-2026. Transhipment rows were already correct.

## Deployment to the server (done)
- Server: `124.7.209.74`, project in `/opt/AICHATBOT`, runs as systemd service **`chatbot.service`** (Flask `app.py`, `venv312`, behind nginx). `/opt/POD` uvicorn (port 8001) is a different app – do not touch.
- Backup made: `src/tracking_service.py.bak-<date_time>`.
- Uploaded fixed file:
  ```powershell
  scp "C:\Users\XCRP-IT-KAPIL\Documents\AICHATBOT\src\tracking_service.py" root@124.7.209.74:/opt/AICHATBOT/src/tracking_service.py
  ```
- Verified with `grep -n "Value is on the next line"` (line 297) and `grep -n "header row (Booking Station"` (line 374).
- Restarted: `systemctl restart chatbot.service` → `active (running)`.
- Rollback (if ever needed): `cp src/tracking_service.py.bak-<actual timestamp> src/tracking_service.py` then restart. (Use the real file name from `ls src/*.bak-*`; typing `<timestamp>` literally makes bash fail.)

## Other finding: Groq rate limit
Server logs show `Error code: 429 ... tokens per day (TPD): Limit 200000, Used ~199700` for model `qwen/qwen3.8-27b`. When this happens `generate_tracking_response` fails and the code uses `generate_fallback_response` (plain short reply). The booking/movement tables and timeline are built from parsed data, so they work regardless. The full AI card returns after the daily limit resets, or after upgrading to Groq Dev Tier.
Optional follow-up: enrich `generate_fallback_response` (booking details + timeline) so it looks good when rate-limited.

## Notes
- Local PC Python (`python`) lacks `langchain_openai`; the project venv is `venv313`. Tests here stubbed that import.
- Pending: confirm in the chatbot that DWB `299106861` now shows full details after restart.
