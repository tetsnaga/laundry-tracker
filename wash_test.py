"""One-shot WASH login/status test. Credentials and cookies stay in memory."""
import getpass
import http.cookiejar
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

BASE = "https://www.getwashconnect.com"


class RoomParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_room = False
        self.options = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("id") == "room":
            self.in_room = True
        if tag == "option" and self.in_room:
            self.options.append((attrs.get("value", ""), "selected" in attrs))

    def handle_endtag(self, tag):
        if tag == "select":
            self.in_room = False

    def selected_room(self):
        chosen = [value for value, selected in self.options if selected]
        if chosen and chosen[0]:
            return chosen[0]
        if self.options and self.options[0][0]:
            return self.options[0][0]
        raise ValueError("No selected room found; login or page parsing failed.")


def parse_time(value):
    # Match the numeric date/time components used by WASH's JavaScript.
    parts = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T ](\d{1,2}):(\d{1,2}):(\d{1,2})", str(value))
    if not parts:
        raise ValueError("Unrecognized server timestamp")
    return datetime(*map(int, parts.groups()))


def display_status(machine, timestamp):
    """Mirror the washer/dryer display logic, retaining fallback explanations."""
    code = machine["LMCStatus"]
    root = code[:1]
    if root in ("7", "8"):
        return "Available", "", "website maps this code to Available"
    if root in ("0", "1", "2", "3", "4", "5"):
        try:
            age = (parse_time(timestamp) - parse_time(machine.get("SeverTime"))).total_seconds()
        except ValueError:
            return "Unknown", "", "cannot interpret report time"
        if root in ("1", "4"):
            try:
                seconds = float(machine["RemainingMin"]) * 60 - age
                if not math.isfinite(seconds):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                return "Unknown", "", "invalid remaining time"
            if seconds < 0:
                return "Available", "", "website fallback: countdown expired"
            return ("Running (door open)" if root == "1" else "Running",
                    str(math.ceil(seconds / 60)), "")
        if age > 3600:
            return "Available", "", "website fallback: report over 1 hour old"
    labels = {
        "0": "Start", "2": "Door Closed", "3": "Waiting for Start Button",
        "5": "Finished", "6": "Error", "9": "Manual Mode",
        "A": "Door Unlock Mode", "B": "Partial Vend Mode", "C": "Pause Mode",
        "D": "Start Mode After PF", "E": "Door Lock Mode",
    }
    return labels.get(root, "Offline"), "", ""


def main():
    email = input("WASH email: ").strip()
    password = getpass.getpass("WASH password (hidden): ")
    if not email or not password:
        raise ValueError("Email and password are required.")
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, fields=None, ajax=False):
        headers = {"User-Agent": "WASH-personal-collector-test/2.0"}
        if ajax:
            headers.update({"X-Requested-With": "XMLHttpRequest",
                            "Referer": BASE + "/tenant/laundry"})
        data = None if fields is None else urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(BASE + path, data=data, headers=headers)
        with client.open(req, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")

    request("/")
    request("/", {"login": email, "password": password, "next": ""})
    del password
    parser = RoomParser()
    parser.feed(request("/tenant/laundry"))
    room = parser.selected_room()
    try:
        result = json.loads(request("/tenant/update_machine_status", {"room": room}, ajax=True))
    except json.JSONDecodeError:
        raise ValueError("Status endpoint did not return JSON; session may be invalid.") from None
    if not isinstance(result, dict):
        raise ValueError("Unexpected status response structure.")
    rows = []
    for kind in ("washers", "dryers"):
        group = result.get(kind)
        if isinstance(group, dict):
            group = list(group.values())
        if not isinstance(group, list):
            raise ValueError("Status response is missing a machine group.")
        for machine in group:
            if not isinstance(machine, dict) or not machine.get("SerialNumber"):
                raise ValueError("Machine record is missing its ID.")
            if not isinstance(machine.get("LMCStatus"), str) or not machine["LMCStatus"]:
                raise ValueError("Machine record is missing its raw status code.")
            rows.append((kind, machine))
    if not rows:
        raise ValueError("Status response contains no machines.")

    print("\nVERIFIED: fresh login and machine status JSON retrieval.")
    print("Observed (UTC):", datetime.now(timezone.utc).isoformat())
    print("Server timestamp:", result.get("timestamp", "missing"))
    print(f"\n{'Type':<9} {'Machine ID':<12} {'Raw code':<16} {'Website state':<26} Minutes left")
    for kind, machine in rows:
        status, minutes, note = display_status(machine, result.get("timestamp"))
        print(f"{kind:<9} {str(machine['SerialNumber']):<12} {machine['LMCStatus']:<16} {status:<26} {minutes}")
        if note:
            print("  Note:", note)
    print(f"\nTotal: {len(rows)} machine status records.")
    print("Credentials and cookies were not saved. No monitoring is running.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
    except urllib.error.HTTPError as error:
        print(f"\nFAILED: HTTP {error.code}.")
        sys.exit(1)
    except urllib.error.URLError:
        print("\nFAILED: network connection error.")
        sys.exit(1)
    except ValueError as error:
        print(f"\nFAILED: {error}")
        sys.exit(1)
