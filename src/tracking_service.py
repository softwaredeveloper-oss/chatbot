import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


class TCITrackingService:

    def __init__(self):
        load_dotenv()
        self.tracking_base_url = "https://www.tciexpress.in/track-your-shipment"
        self.api_key = os.getenv("GROQ_API_KEY")

        self.chat_model = ChatOpenAI(
            model="qwen/qwen3.8-27b",
            temperature=0.3,
            max_tokens=600,
            api_key=self.api_key,
            base_url="https://api.groq.com/openai/v1",
            model_kwargs={
                "reasoning_effort": "none"
            }
        )

    # ============================================================
    # 1. EXTRACT DWB NUMBER
    # ============================================================
    def extract_dwb_no(self, message):
        if not message:
            return None

        # Clean trailing timestamps (e.g., "29910686103:23 PM" -> "299106861")
        cleaned_msg = re.sub(
            r"\d{1,2}:\d{2}\s*(?:AM|PM)?",
            "",
            message,
            flags=re.IGNORECASE
        )

        patterns = [
            r"\bDWB[_\s-]*NO[_\s:-]*(\d{8,15})\b",
            r"\bDWB[_\s-]*(\d{8,15})\b",
            r"\bDOCKET[_\s-]*(?:NUMBER)?[_\s:-]*(\d{8,15})\b",
            r"\b([2-9]\d{8})\b",  # Standard 9-digit DWB
            r"\b(\d{8,15})\b"
        ]

        for pattern in patterns:
            match = re.search(pattern, cleaned_msg, re.IGNORECASE)
            if match:
                return match.group(1)

        # Standalone numeric match
        match = re.fullmatch(r"\s*(\d{8,15})\s*", cleaned_msg)
        if match:
            return match.group(1)

        return None

    # ============================================================
    # 2. CHECK TRACKING QUESTION
    # ============================================================
    def is_tracking_question(self, message):
        if not message:
            return False

        keywords = [
            # English
            "track", "tracking", "track shipment", "track my shipment",
            "shipment status", "docket status", "delivery status",
            "current status", "current location", "shipment location",
            "where is my shipment", "where is my docket", "shipment delivered",
            "delivery information", "tracking details", "shipment details",
            "shipment movement",
            # Hinglish
            "kaha hai", "kahaan hai", "kahan hai", "status batao",
            "status kya hai", "tracking batao", "tracking dikhao",
            "track karo", "track karna hai", "shipment check karo",
            "delivery hua", "delivery hui", "delivery kab",
            "docket check karo", "docket details", "shipment details",
            # Hindi
            "कहां है", "कहाँ है", "कहा है", "स्थिति", "स्टेटस",
            "ट्रैक", "ट्रैकिंग", "डिलीवरी", "डिलीवर", "शिपमेंट"
        ]

        message_lower = message.lower()
        return any(keyword.lower() in message_lower for keyword in keywords)

    # ============================================================
    # 3. BUILD TRACKING URL
    # ============================================================
    def build_tracking_url(self, dwb_no):
        return f"{self.tracking_base_url}?trackshipment={dwb_no}&dwb=dwb"

    # ============================================================
    # 4. LIVE TRACK DWB
    # ============================================================
    def track_dwb(self, dwb_no):
        if not dwb_no:
            return {
                "success": False,
                "error": "DWB number is required."
            }

        dwb_no = str(dwb_no).strip()
        if not re.fullmatch(r"\d{8,15}", dwb_no):
            return {
                "success": False,
                "error": "Invalid DWB number."
            }

        url = self.build_tracking_url(dwb_no)

        print("\n==============================================")
        print("[TRACKING] TCI Express Live Tracking")
        print(f"[TRACKING] DWB: {dwb_no}")
        print(f"[TRACKING] URL: {url}")
        print("==============================================")

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9"
            }

            response = requests.get(url, headers=headers, timeout=30)
            print(f"[TRACKING] HTTP Status: {response.status_code}")
            response.raise_for_status()

            html = response.text
            if not html.strip():
                return {
                    "success": False,
                    "error": "TCI Express tracking page returned empty data."
                }

            # Parse HTML
            tracking = self.parse_tracking_html(html, requested_dwb=dwb_no)

            if not tracking:
                return {
                    "success": False,
                    "error": f"No tracking information found for DWB {dwb_no}."
                }

            # AI Summary
            try:
                ai_response = self.generate_tracking_response(tracking)
            except Exception as ai_error:
                print(f"[WARNING] Tracking AI response failed: {ai_error}")
                ai_response = self.generate_fallback_response(tracking)

            booking_table = self.create_booking_table(
                tracking.get("booking_information", {})
            )
            movement_table = self.create_movement_table(
                tracking.get("transhipment_information", [])
            )
            shipment_timeline = self.create_shipment_timeline(tracking)

            print("[TRACKING] Tracking successful.")

            return {
                "success": True,
                "dwb_no": dwb_no,
                "url": url,
                "tracking": tracking,
                "answer": ai_response,
                "booking_table": booking_table,
                "movement_table": movement_table,
                "shipment_timeline": shipment_timeline
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "TCI Express tracking website took too long to respond."
            }
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Tracking request failed: {e}")
            return {
                "success": False,
                "error": f"Unable to access TCI Express tracking service: {str(e)}"
            }
        except Exception as e:
            print(f"[ERROR] Tracking service failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # ============================================================
    # 5. PARSE TRACKING HTML
    # ============================================================
    def parse_tracking_html(self, html, requested_dwb=None):
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        # Add spaces to table cells before extraction to prevent glued text
        for cell in soup.find_all(["td", "th", "div", "p"]):
            cell.append(" ")

        text = soup.get_text("\n", strip=True)

        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in text.splitlines()
            if line.strip()
        ]

        clean_lines = []
        for line in lines:
            if not clean_lines or clean_lines[-1] != line:
                clean_lines.append(line)
        lines = clean_lines

        tracking = {
            "docket_number": requested_dwb or "",
            "status": "",
            "destination_pincode": "",
            "destination_location": "",
            "delivery_information": [],
            "booking_information": {},
            "transhipment_information": []
        }

        tracking["docket_number"] = self.extract_docket_number(lines, requested_dwb)
        tracking["status"] = self.extract_label_value(
            lines, ["status", "shipment status", "current status"]
        )
        tracking["destination_pincode"] = self.extract_label_value(
            lines, ["destination pin", "destination pincode", "destination pin code"]
        )
        tracking["destination_location"] = self.extract_label_value(
            lines, ["destination location", "destination"]
        )
        tracking["delivery_information"] = self.extract_delivery_information(lines)
        tracking["booking_information"] = self.extract_booking_information(lines)
        tracking["transhipment_information"] = self.parse_transhipment(lines)

        return tracking

    # ============================================================
    # 6. EXTRACT DOCKET NUMBER
    # ============================================================
    def extract_docket_number(self, lines, requested_dwb=None):
        if requested_dwb:
            return str(requested_dwb).strip()

        patterns = [
            r"docket\s*(?:number|no)\s*[:\-]?\s*(\d{8,15})",
            r"dwb\s*(?:number|no)?\s*[:\-]?\s*(\d{8,15})",
            r"ref\s*no\.?\s*[:\-]?\s*(\d{8,15})"
        ]

        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    return match.group(1)

        for line in lines:
            match = re.search(r"\b\d{8,15}\b", line)
            if match:
                value = match.group(0)
                if len(value) >= 8:
                    return value

        return ""

    # ============================================================
    # 7. EXTRACT LABEL VALUE
    # ============================================================
    def extract_label_value(self, lines, labels):
        for i, line in enumerate(lines):
            clean_line = re.sub(r"\s+", " ", line).strip()
            lower_line = clean_line.lower()

            for label in labels:
                label_lower = label.lower()

                if lower_line.startswith(label_lower + ":"):
                    value = clean_line.split(":", 1)[1].strip()
                    if not value:
                        # Value is on the next line ("Status:" / "Docket Delivered...")
                        value = self.find_next_real_value(lines, i)
                    if value:
                        return value

                if lower_line == label_lower:
                    value = self.find_next_real_value(lines, i)
                    if value:
                        return value

        return ""

    # ============================================================
    # 8. FIND NEXT REAL VALUE
    # ============================================================
    def find_next_real_value(self, lines, index):
        ignored = {
            "status", "destination", "destination location", "destination pin",
            "destination pincode", "destination pin code", "booking station",
            "delivery station", "booking date", "packets", "packet", "weight",
            "expected delivery date", "from station", "to station", "despatch date",
            "despatch time", "arrival date", "arrival time"
        }

        for next_index in range(index + 1, min(index + 5, len(lines))):
            value = lines[next_index].strip()
            if not value:
                continue
            if value.lower() in ignored:
                continue
            return value

        return ""

    # ============================================================
    # 9. DELIVERY INFORMATION
    # ============================================================
    def extract_delivery_information(self, lines):
        information = []
        started = False
        stop_keywords = [
            "booking information", "booking station",
            "transhipment information", "transshipment information"
        ]

        for line in lines:
            lower = line.lower()
            if "delivery information" in lower or lower == "delivery":
                started = True
                continue

            if not started:
                continue

            if any(keyword in lower for keyword in stop_keywords):
                break

            if line:
                information.append(line)

        return information

    # ============================================================
    # 10. BOOKING INFORMATION
    # ============================================================
    def extract_booking_information(self, lines):
        booking = {}
        booking_mapping = {
            "booking station": "booking_station",
            "delivery station": "delivery_station",
            "booking date": "booking_date",
            "packets": "packets",
            "packet": "packets",
            "weight": "weight",
            "expected delivery date": "expected_delivery_date"
        }

        # The site renders a header row (Booking Station, Delivery Station, ...)
        # followed by a value row, so map headers to values by column position.
        start = None
        for i, line in enumerate(lines):
            if "booking information" in line.lower():
                start = i + 1
                break

        if start is None:
            return booking

        headers = []
        i = start
        while i < len(lines) and lines[i].strip().lower() in booking_mapping:
            headers.append(booking_mapping[lines[i].strip().lower()])
            i += 1

        for offset, key in enumerate(headers):
            if i + offset < len(lines):
                booking[key] = lines[i + offset].strip()

        return booking

    # ============================================================
    # 11. TRANSHIPMENT INFORMATION
    # ============================================================
    def parse_transhipment(self, lines):
        movements = []
        start_index = None

        for i, line in enumerate(lines):
            lower = line.lower()
            if "transhipment information" in lower or "transshipment information" in lower:
                start_index = i
                break

        if start_index is None:
            return movements

        header_index = None
        for i in range(start_index, min(start_index + 30, len(lines))):
            if lines[i].lower() == "from station":
                header_index = i
                break

        if header_index is None:
            return movements

        data_start = header_index + 6
        date_pattern = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}$", re.IGNORECASE)
        time_pattern = re.compile(r"^\d{1,2}:\d{2}$")

        stop_words = {
            "footer", "contact us", "about us", "customer care",
            "terms and conditions", "privacy policy", "copyright",
            "quick links", "important links", "follow us",
            "download app", "get in touch"
        }

        i = data_start
        while i + 5 < len(lines):
            row = [
                lines[i].strip(),
                lines[i + 1].strip(),
                lines[i + 2].strip(),
                lines[i + 3].strip(),
                lines[i + 4].strip(),
                lines[i + 5].strip()
            ]

            if any(value.lower() in stop_words for value in row):
                break

            if not date_pattern.match(row[2]) or not time_pattern.match(row[3]) or \
               not date_pattern.match(row[4]) or not time_pattern.match(row[5]):
                i += 1
                continue

            movements.append({
                "from_station": row[0],
                "to_station": row[1],
                "despatch_date": row[2],
                "despatch_time": row[3],
                "arrival_date": row[4],
                "arrival_time": row[5]
            })
            i += 6

        return movements

    # ============================================================
    # 12. GENERATE AI CUSTOMER RESPONSE
    # ============================================================
    def generate_tracking_response(self, tracking):
        prompt = f"""
You are a TCI Express shipment tracking assistant.

You have received LIVE shipment tracking information.

IMPORTANT RULES:
0. Reply in English only, even if the user wrote in Hindi or Hinglish.
1. Use ONLY the supplied shipment data. Never invent information.
2. Structure the reply in a modern, easy-to-read chatbot card.
3. If delivered, start with: "✅ **Shipment Delivered**"
4. If in-transit or other status, start with: "🚚 **Shipment Status: [Status]**"
5. Do NOT include markdown code fences (```).
6. Convert 24-hr times (e.g., 18:08) to 12-hr AM/PM format (e.g., 06:08 PM).
7. List the transit timeline in REVERSE chronological order (most recent first).

SHIPMENT DATA:
{tracking}

FORMAT TO PRODUCE:
[Status Header with Emoji]
**DWB / Docket No:** `[docket_number]`

* **Status:** [Current status with date if available]
* **Destination:** [Destination location and pincode]
* **Route:** [Origin Station] ➔ [Destination Station]
* **Package Details:** [Packets] Pkg | Weight: [Weight] (omit if missing)

---
**📍 Transit Timeline (Most Recent First):**
* 🟢 **[Latest Date & Time]:** [Event description] at [Location]
* ⚪ **[Previous Date & Time]:** [Event description] at [Location]
"""

        response = self.chat_model.invoke(prompt)
        return self.clean_answer(response.content)

    # ============================================================
    # 13. FALLBACK RESPONSE
    # ============================================================
    def generate_fallback_response(self, tracking):
        dwb = tracking.get("docket_number", "")
        status = tracking.get("status", "")
        destination = tracking.get("destination_location", "")
        pincode = tracking.get("destination_pincode", "")

        is_delivered = "DELIVER" in (status or "").upper()
        emoji = "✅" if is_delivered else "🚚"

        answer = f"{emoji} **Shipment Status: {status or 'In Transit'}**\n\n"

        if dwb:
            answer += f"**DWB Number:** `{dwb}`\n"
        if destination:
            answer += f"* **Destination:** {destination}"
            if pincode:
                answer += f" - {pincode}"
            answer += "\n"

        booking = tracking.get("booking_information", {})
        if booking.get("booking_station") and booking.get("delivery_station"):
            answer += f"* **Route:** {booking['booking_station']} ➔ {booking['delivery_station']}\n"

        return answer.strip()

    # ============================================================
    # 14. CREATE BOOKING TABLE
    # ============================================================
    def create_booking_table(self, booking):
        return {
            "headers": ["Field", "Details"],
            "rows": [
                ["Booking Station", booking.get("booking_station", "")],
                ["Delivery Station", booking.get("delivery_station", "")],
                ["Booking Date", booking.get("booking_date", "")],
                ["Packets", booking.get("packets", "")],
                ["Weight", booking.get("weight", "")],
                ["Expected Delivery Date", booking.get("expected_delivery_date", "")]
            ]
        }

    # ============================================================
    # 15. CREATE MOVEMENT TABLE
    # ============================================================
    def create_movement_table(self, movements):
        rows = []
        for movement in movements:
            dispatch = f"{movement.get('despatch_date', '')} {movement.get('despatch_time', '')}".strip()
            arrival = f"{movement.get('arrival_date', '')} {movement.get('arrival_time', '')}".strip()
            rows.append([
                movement.get("from_station", ""),
                movement.get("to_station", ""),
                dispatch,
                arrival
            ])
        return {
            "headers": ["From", "To", "Dispatch", "Arrival"],
            "rows": rows
        }

    # ============================================================
    # 16. CREATE SHIPMENT TIMELINE
    # ============================================================
    def create_shipment_timeline(self, tracking):
        timeline = []
        booking = tracking.get("booking_information", {})
        movements = tracking.get("transhipment_information", [])

        booking_station = booking.get("booking_station", "")
        booking_date = booking.get("booking_date", "")

        if booking_station:
            timeline.append({
                "type": "booking",
                "title": "Shipment Booked",
                "location": booking_station,
                "date": booking_date,
                "time": "",
                "status": "completed"
            })

        for movement in movements:
            from_station = movement.get("from_station", "")
            to_station = movement.get("to_station", "")

            if from_station:
                timeline.append({
                    "type": "dispatch",
                    "title": "Dispatched",
                    "location": from_station,
                    "date": movement.get("despatch_date", ""),
                    "time": movement.get("despatch_time", ""),
                    "status": "completed"
                })

            if to_station:
                timeline.append({
                    "type": "arrival",
                    "title": "Arrived",
                    "location": to_station,
                    "date": movement.get("arrival_date", ""),
                    "time": movement.get("arrival_time", ""),
                    "status": "completed"
                })

        status = (tracking.get("status", "") or "").upper()
        delivery_info = tracking.get("delivery_information", [])
        delivery_date = ""

        for item in delivery_info:
            match = re.search(r"\d{2}-[A-Z]{3}-\d{4}", item, re.IGNORECASE)
            if match:
                delivery_date = match.group(0)
                break

        if "DELIVER" in status or delivery_date:
            timeline.append({
                "type": "delivered",
                "title": "Delivered",
                "location": tracking.get("destination_location", ""),
                "date": delivery_date,
                "time": "",
                "status": "completed"
            })

        return timeline

    # ============================================================
    # 17. CLEAN AI RESPONSE
    # ============================================================
    def clean_answer(self, text):
        if not text:
            return ""

        # Remove Qwen thinking tags
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        if "<think>" in text.lower():
            text = re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove markdown fences and raw URLs
        text = re.sub(r"```markdown|```", "", text)
        text = re.sub(r"https?://\S+", "", text)

        return text.strip()