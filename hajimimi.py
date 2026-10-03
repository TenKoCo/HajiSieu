#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HajiSieu - Roblox Auto Rejoin Tool
Target platform : Termux on rooted Android 10 (needs `su`)
Language        : Python 3

Chuc nang:
  1. Auto Rejoin  - dong tat ca package -> (auto block) -> mo & vao game/SVV,
                    giam sat va tu dong rejoin khi mat ket noi.
  2. Nhap Game ID / ServerVip link
  3. Nhap package (quet theo tien to, vd: com -> com.roblox.*, noka -> noka.*)
  4. Auto Block   - block cac account voi nhau de khong trung server
  10. Config      - Method (Execute/Online), thoi gian dong roblox,
                    delay giua cac tab, auto sort tab, auto grid tab.

LUU Y: Tu dong hoa Roblox co the vi pham Dieu khoan dich vu cua Roblox.
Chi su dung tren tai khoan / thiet bi cua chinh ban, tu chiu rui ro.
"""

import os
import re
import sys
import json
import math
import time
import shutil
import signal
import subprocess
import threading
from datetime import datetime

# --------------------------------------------------------------------------- #
#  Paths & constants
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "logs", "rejoin.log")
COOKIE_PATH = os.path.join(BASE_DIR, "cookie.txt")  # moi dong 1 cookie .ROBLOSECURITY
# Executor (Execute method) heartbeat files are read from here.
# Script injected tren Roblox (Delta/Codex...) nen "touch" file nay moi vai giay:
#   /sdcard/HajiSieu/heartbeat/<package>.hb
HEARTBEAT_DIR = "/sdcard/HajiSieu/heartbeat"
# Execute method: cac thu muc workspace pho bien cua executor Android.
# Script heartbeat.lua ghi file vao <workspace>/HajiSieu/<username>.hb
# Co the ghi de bang config "executor_workspaces".
EXECUTOR_WORKSPACES_DEFAULT = [
    "/sdcard/Delta/Workspace",
    "/sdcard/Delta/workspace",
    "/sdcard/Codex/workspace",
    "/sdcard/Codex",
    "/sdcard/Fluxus/workspace",
    "/sdcard/Krnl",
    "/sdcard/Hydrogen/workspace",
    "/sdcard/Arceus X/workspace",
]

# Cac chuoi/error code bao hieu bi mat ket noi -> can rejoin
DISCONNECT_PATTERNS = [
    "connection lost",
    "lost connection",
    "error code 277", "error code: 277",
    "error code 279", "error code: 279",
    "error code 268", "error code: 268",
    "error code 273", "error code: 273",
    "disconnected for being idle",
    "idled for 20 minutes",
    "you were kicked from this experience",
    "disconnect reason received",
    "game disconnected",
]
_DC_REGEX = re.compile("|".join(re.escape(p) for p in DISCONNECT_PATTERNS), re.IGNORECASE)

HEARTBEAT_STALE_SECONDS = 35   # Execute method: heartbeat cu hon nguong -> coi nhu chet
MONITOR_REFRESH_SECONDS = 3    # chu ky lam moi dashboard
REJOIN_COOLDOWN_SECONDS = 25   # tranh rejoin lien tuc 1 package


# --------------------------------------------------------------------------- #
#  Colors
# --------------------------------------------------------------------------- #
class C:
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    red = "\033[91m"
    grn = "\033[92m"
    yel = "\033[93m"
    blu = "\033[94m"
    mag = "\033[95m"
    cyn = "\033[96m"
    gry = "\033[90m"


def clear():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def hr(ch="-"):
    try:
        w = shutil.get_terminal_size((60, 20)).columns
    except Exception:
        w = 60
    return ch * max(20, min(w, 80))


# --------------------------------------------------------------------------- #
#  Shell / root helpers
# --------------------------------------------------------------------------- #
def run(cmd, timeout=20):
    """Run a plain shell command. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(["sh", "-c", cmd], capture_output=True,
                            text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa
        return 1, "", str(e)


def run_root(cmd, timeout=20):
    """Run a command as root via `su -c`. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(["su", "-c", cmd], capture_output=True,
                            text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "su-not-found"
    except Exception as e:  # noqa
        return 1, "", str(e)


def have_root():
    rc, out, _ = run_root("id -u")
    return rc == 0 and out.strip() == "0"


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = {
    "game_target": "",          # (2) Game ID hoac VIP/ServerVip link
    "package_prefix": "",       # (3) tien to da nhap
    "packages": [],             # (3) danh sach package da quet duoc
    "auto_block": False,        # (4)
    "method": "Execute",        # (10.1) Execute | Online
    "execute_check_seconds": 35,  # (10.4) Time check execute: heartbeat cu hon se rejoin
    "close_minutes": 0,         # (10.2) 0 = khong tu dong dong
    "tab_delay_seconds": 20,    # (10.3)
    "auto_sort": False,         # (10.8) thu nho cac tab (freeform nho)
    "auto_grid": False,         # (10.9) sap xep dang luoi
    "grid_cols": 0,             # (10.7) so cot luoi (0 = tu tinh)
    "rejoin_cooldown_seconds": 25,  # (10.5) cooldown giua 2 lan rejoin cung 1 app
    "usernames": {},            # map package -> username Roblox (hien thi + Execute heartbeat)
    "accounts": [],             # cho Auto Block: [{name,user_id,cookie}]
    "executor_workspaces": [],  # Execute method: thu muc workspace executor (rong = dung mac dinh)
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in DEFAULT_CONFIG:
                if k in data:
                    cfg[k] = data[k]
        except Exception as e:  # noqa
            print(C.red + "Khong doc duoc config.json: %s" % e + C.R)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa
        print(C.red + "Khong luu duoc config.json: %s" % e + C.R)


def log_event(kind, message):
    """Ghi su kien (disconnect / rejoin / block / cycle) ra logs/rejoin.log."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %-11s %s\n" % (ts, kind, message))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Package scanning / Roblox control
# --------------------------------------------------------------------------- #
def scan_packages(prefix):
    """Quet tat ca package co ten bat dau bang (bat ky) tien to da nhap.
    Ho tro nhieu tien to, ngan cach bang dau phay. Vd: 'com, noka'."""
    prefixes = [p.strip().lower() for p in prefix.split(",") if p.strip()]
    if not prefixes:
        return []
    rc, out, err = run_root("pm list packages")
    if rc != 0:
        print(C.red + "pm list packages loi: %s" % (err or rc) + C.R)
        return []
    names = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            names.append(line[len("package:"):].strip())
    found = [n for n in names if any(n.lower().startswith(pre) for pre in prefixes)]
    if not found:
        # fallback: khong co ten nao bat dau bang tien to -> thu khop "chua" chuoi
        found = [n for n in names if any(pre in n.lower() for pre in prefixes)]
    return sorted(set(found))


def build_deeplink(game_target):
    """Tao deep link tu Game ID hoac VIP/ServerVip link."""
    g = (game_target or "").strip()
    if not g:
        return ""
    if g.isdigit():
        return "roblox://placeId=%s" % g
    # neu nguoi dung dan placeId=... | accessCode=...
    if g.lower().startswith("roblox://"):
        return g
    # VIP / share link https(...) -> giao cho Roblox tu parse qua VIEW intent
    if g.lower().startswith("http"):
        return g
    # fallback: coi nhu place id
    return "roblox://placeId=%s" % g


def launch(pkg, deeplink, freeform=False):
    """Mo 1 package va vao game qua VIEW intent (gioi han dung package).
    Neu VIEW intent that bai (clone khong dang ky scheme roblox://) ->
    fallback mo app bang launcher qua monkey (khong tu join game)."""
    wm = "--windowingMode 5 " if freeform else ""
    cmd = ('am start %s-a android.intent.action.VIEW -d "%s" %s'
           % (wm, deeplink, pkg))
    rc, out, err = run_root(cmd)
    blob = (out + " " + err).lower()
    if rc != 0 or "error" in blob or "exception" in blob:
        run_root("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    return rc, out, err


def force_stop(pkg):
    return run_root("am force-stop %s" % pkg)


def force_stop_all(pkgs):
    for p in pkgs:
        force_stop(p)


def pid_of(pkg):
    rc, out, _ = run_root("pidof %s" % pkg)
    if rc == 0 and out.strip():
        return out.strip().split()[0]
    return None


def get_username(cfg, pkg):
    """Hien thi username. Uu tien map trong config, neu khong co -> rut gon package."""
    name = cfg.get("usernames", {}).get(pkg)
    if name:
        return name
    short = pkg.split(".")[-1]
    return short[:14]


# --------------------------------------------------------------------------- #
#  CPU / RAM
# --------------------------------------------------------------------------- #
def _cpu_sample():
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()
        nums = [int(x) for x in parts[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        total = sum(nums)
        return total, idle
    except Exception:
        return None, None


def cpu_percent(interval=0.25):
    t1, i1 = _cpu_sample()
    if t1 is None:
        return 0.0
    time.sleep(interval)
    t2, i2 = _cpu_sample()
    if t2 is None or t2 - t1 <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - (i2 - i1) / (t2 - t1)) * 100.0))


def mem_info():
    """Tra ve (used_mb, total_mb)."""
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(0, total - avail)
        return used / 1024.0, total / 1024.0
    except Exception:
        return 0.0, 0.0


# --------------------------------------------------------------------------- #
#  Window arrange (freeform / grid) - best effort, phu thuoc ROM
# --------------------------------------------------------------------------- #
def screen_size():
    rc, out, _ = run_root("wm size")
    m = re.search(r"(\d+)\s*x\s*(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 1920


def find_task_id(pkg):
    """Tim taskId cua package tu dumpsys (best effort cho Android 10)."""
    rc, out, _ = run_root("dumpsys activity activities | grep -i %s" % pkg)
    m = re.search(r"Task(?:Record)?\{[^ ]*\s+#?(\d+)", out)
    if m:
        return m.group(1)
    m = re.search(r"taskId=(\d+)", out)
    if m:
        return m.group(1)
    return None


def resize_task(task_id, l, t, r, b):
    run_root("am task resize %s %d %d %d %d" % (task_id, l, t, r, b))


def arrange_tabs(cfg, pkgs):
    """Config 8/9: thu nho (sort) hoac sap xep luoi cac cua so Roblox."""
    if not (cfg.get("auto_sort") or cfg.get("auto_grid")):
        return
    w, h = screen_size()
    n = len(pkgs)
    if n == 0:
        return
    if cfg.get("auto_grid"):
        gc = int(cfg.get("grid_cols", 0) or 0)
        cols = gc if gc > 0 else int(math.ceil(math.sqrt(n)))
        cols = max(1, min(cols, n))
        rows = int(math.ceil(n / float(cols)))
    else:  # auto_sort = thu nho -> xep hang nho o nua tren man hinh
        cols = min(n, 3)
        rows = int(math.ceil(n / float(cols)))
    cw = w // cols
    ch = h // rows
    for idx, pkg in enumerate(pkgs):
        task = find_task_id(pkg)
        if not task:
            continue
        col = idx % cols
        row = idx // cols
        l = col * cw
        t = row * ch
        r = l + cw - 4
        b = t + ch - 4
        if cfg.get("auto_sort") and not cfg.get("auto_grid"):
            # thu nho ~45% kich thuoc o
            r = l + int(cw * 0.9)
            b = t + int(ch * 0.9)
        resize_task(task, l, t, r, b)
        time.sleep(0.2)


# --------------------------------------------------------------------------- #
#  Auto Block (Roblox API) - can accounts[{name,user_id,cookie}] trong config
# --------------------------------------------------------------------------- #
def _http_post(url, cookie, csrf=None, body=b"{}"):
    import urllib.request
    import urllib.error
    headers = {
        "Cookie": ".ROBLOSECURITY=%s" % cookie,
        "Content-Type": "application/json",
        "Referer": "https://www.roblox.com/",
        "User-Agent": "HajiSieu-Rejoin",
    }
    if csrf:
        headers["X-CSRF-TOKEN"] = csrf
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.getcode(), resp.headers, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa
        return 0, None, str(e)


def _block_user(cookie, csrf_box, target_user_id):
    """Block 1 user. Lay X-CSRF-TOKEN tu phan hoi 403 roi thu lai (khong logout)."""
    url = ("https://apis.roblox.com/user-blocking-api/v1/users/%s/block-user"
           % target_user_id)
    code, headers, _ = _http_post(url, cookie, csrf_box.get("t"))
    if code == 403 and headers is not None and headers.get("x-csrf-token"):
        csrf_box["t"] = headers.get("x-csrf-token")
        code, headers, _ = _http_post(url, cookie, csrf_box["t"])
    return code in (200, 201), "HTTP %s" % code


def run_auto_block(cfg):
    accounts = cfg.get("accounts", [])
    valid = [a for a in accounts if a.get("user_id") and a.get("cookie")]
    if len(valid) < 2:
        print(C.yel + "Auto Block: can >= 2 account (user_id + cookie) trong "
              "config.json ('accounts'). Bo qua." + C.R)
        return
    print(C.cyn + "Auto Block: dang block %d account voi nhau..." % len(valid) + C.R)
    for a in valid:
        csrf_box = {}
        for b in valid:
            if a is b:
                continue
            ok, msg = _block_user(a["cookie"], csrf_box, b["user_id"])
            na = a.get("name", a["user_id"])
            nb = b.get("name", b["user_id"])
            tag = (C.grn + "OK" + C.R) if ok else (C.red + "FAIL (%s)" % msg + C.R)
            print("  - %s -> block %s : %s" % (na, nb, tag))
            log_event("block", "%s -> %s : %s" % (na, nb, "OK" if ok else msg))


# --------------------------------------------------------------------------- #
#  Tu dong do username Roblox tu du lieu app (can root)
# --------------------------------------------------------------------------- #
def _http_get(url, cookie):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={
        "Cookie": ".ROBLOSECURITY=%s" % cookie,
        "User-Agent": "HajiSieu-Rejoin",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.getcode(), resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa
        return 0, str(e)


def _copy_cookiedb(src):
    """Copy DB cookies (chi root doc duoc) ra noi python doc duoc. Tra ve path local."""
    tmp = "/sdcard/HajiSieu/tmp"
    run_root("rm -rf %s; mkdir -p %s" % (tmp, tmp))
    run_root("cp '%s' %s/Cookies 2>/dev/null" % (src, tmp))
    run_root("cp '%s-wal' %s/Cookies-wal 2>/dev/null" % (src, tmp))
    run_root("cp '%s-shm' %s/Cookies-shm 2>/dev/null" % (src, tmp))
    run_root("chmod -R 666 %s/Cookies* 2>/dev/null" % tmp)
    return "%s/Cookies" % tmp


def _extract_roblosecurity(pkg):
    """Lay cookie .ROBLOSECURITY tu webview cookie DB cua package (neu co)."""
    import sqlite3
    srcs = [
        "/data/data/%s/app_webview/Default/Cookies" % pkg,
        "/data/data/%s/app_webview/Cookies" % pkg,
    ]
    for src in srcs:
        rc, out, _ = run_root("ls '%s' 2>/dev/null" % src)
        if rc != 0 or not out.strip():
            continue
        local = _copy_cookiedb(src)
        try:
            con = sqlite3.connect(local)
            cur = con.cursor()
            cur.execute("SELECT value FROM cookies "
                        "WHERE name='.ROBLOSECURITY' AND host_key LIKE '%roblox%'")
            rows = cur.fetchall()
            con.close()
            for (value,) in rows:
                if value and value.startswith("_"):
                    return value
        except Exception:
            pass
    return None


def _username_from_cookie(cookie):
    code, body = _http_get("https://users.roblox.com/v1/users/authenticated", cookie)
    if code == 200:
        try:
            d = json.loads(body)
            return d.get("name") or d.get("displayName")
        except Exception:
            return None
    return None


def detect_usernames(cfg, pkgs, verbose=True):
    """Tu dong do username Roblox cho tung package va luu vao config['usernames'].
    Co che: root doc cookie .ROBLOSECURITY tu app_webview -> goi API users/authenticated.
    """
    found = {}
    for p in pkgs:
        name = None
        cookie = _extract_roblosecurity(p)
        if cookie:
            name = _username_from_cookie(cookie)
        if name:
            found[p] = name
            if verbose:
                print(C.grn + "  - %s -> %s" % (p, name) + C.R)
        elif verbose:
            print(C.yel + "  - %s -> khong do duoc (chua dang nhap / ROM khac?)"
                  % p + C.R)
    if found:
        cfg.setdefault("usernames", {}).update(found)
        save_config(cfg)
    run_root("rm -rf /sdcard/HajiSieu/tmp 2>/dev/null")
    return found


# --------------------------------------------------------------------------- #
#  Login bang cookie (.ROBLOSECURITY) - ghi vao webview cookie DB cua app
# --------------------------------------------------------------------------- #
def _parse_cookie_line(s):
    """Ho tro 2 dang: 'cookie' HOAC 'tk:mk:cookie'. Tra ve phan cookie.
    Cookie .ROBLOSECURITY luon bat dau bang _|WARNING va co chua dau ':',
    nen uu tien lay tu vi tri _|WARNING tro di de khong lam vo cookie.
    """
    idx = s.find("_|WARNING")
    if idx >= 0:
        return s[idx:].strip()
    # fallback: dang tk:mk:cookie (cookie khong co tien to warning)
    parts = s.split(":", 2)
    if len(parts) == 3:
        return parts[2].strip()
    return s.strip()


def load_cookies():
    """Doc cookie.txt. Moi dong: 'cookie' hoac 'tk:mk:cookie'. Tao mau neu chua co."""
    if not os.path.exists(COOKIE_PATH):
        try:
            with open(COOKIE_PATH, "w", encoding="utf-8") as f:
                f.write("# Moi dong 1 cookie. Ho tro 2 dang:\n"
                        "#   <cookie>                 (bat dau bang _|WARNING:-DO-NOT-SHARE...)\n"
                        "#   taikhoan:matkhau:<cookie>  (cookie van la _|WARNING...)\n")
        except Exception:
            pass
        return []
    out = []
    try:
        with open(COOKIE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    c = _parse_cookie_line(s)
                    if c:
                        out.append(c)
    except Exception:
        pass
    return out


def _find_cookie_db(pkg):
    for src in [
        "/data/data/%s/app_webview/Default/Cookies" % pkg,
        "/data/data/%s/app_webview/Cookies" % pkg,
    ]:
        rc, out, _ = run_root("ls '%s' 2>/dev/null" % src)
        if rc == 0 and out.strip():
            return src
    return None


def _inject_cookie(pkg, src, cookie):
    """Ghi cookie .ROBLOSECURITY vao DB cookies cua webview (giu owner + context)."""
    import sqlite3
    _, uidgid, _ = run_root("stat -c '%%u:%%g' '%s'" % src)
    uidgid = uidgid.strip()
    tmp = "/sdcard/HajiSieu/tmp"
    run_root("rm -rf %s; mkdir -p %s" % (tmp, tmp))
    run_root("cp '%s' %s/Cookies 2>/dev/null" % (src, tmp))
    run_root("cp '%s-wal' %s/Cookies-wal 2>/dev/null" % (src, tmp))
    run_root("cp '%s-shm' %s/Cookies-shm 2>/dev/null" % (src, tmp))
    run_root("chmod -R 666 %s/Cookies* 2>/dev/null" % tmp)
    local = "%s/Cookies" % tmp
    try:
        con = sqlite3.connect(local)
        con.execute("PRAGMA journal_mode=DELETE")
        cur = con.cursor()
        cur.execute("PRAGMA table_info(cookies)")
        cols = [r[1] for r in cur.fetchall()]
        if not cols:
            con.close()
            return False, "DB cookies khong hop le"
        now_c = int((time.time() + 11644473600) * 1000000)
        exp_c = int((time.time() + 400 * 24 * 3600 + 11644473600) * 1000000)
        cur.execute("DELETE FROM cookies WHERE name='.ROBLOSECURITY'")
        row = {
            "creation_utc": now_c, "host_key": ".roblox.com",
            "top_frame_site_key": ".roblox.com", "name": ".ROBLOSECURITY",
            "value": cookie, "encrypted_value": b"", "path": "/",
            "expires_utc": exp_c, "is_secure": 1, "is_httponly": 1,
            "last_access_utc": now_c, "has_expires": 1, "is_persistent": 1,
            "priority": 1, "samesite": 0, "source_scheme": 2,
            "source_port": 443, "is_same_party": 0, "last_update_utc": now_c,
            "source_type": 0, "has_cross_site_ancestor": 0,
        }
        use = [c for c in cols if c in row]
        cur.execute("INSERT INTO cookies (%s) VALUES (%s)"
                    % (",".join(use), ",".join("?" for _ in use)),
                    [row[c] for c in use])
        con.commit()
        con.close()
    except Exception as e:  # noqa
        return False, "sqlite loi: %s" % e
    rc, _, err = run_root("cp %s/Cookies '%s'" % (tmp, src))
    if rc != 0:
        return False, "ghi lai DB that bai: %s" % (err or rc)
    run_root("rm -f '%s-wal' '%s-shm' 2>/dev/null" % (src, src))
    if uidgid and ":" in uidgid:
        run_root("chown %s '%s'" % (uidgid, src))
    run_root("restorecon '%s' 2>/dev/null" % src)
    run_root("rm -rf %s 2>/dev/null" % tmp)
    return True, "ok"


def login_with_cookie(pkg, cookie):
    """Login 1 app bang cookie: dam bao webview -> stop -> ghi cookie -> mo lai."""
    src = _find_cookie_db(pkg)
    if not src:
        open_app(pkg)  # mo 1 lan de webview tao file Cookies
        for _ in range(10):
            time.sleep(1)
            src = _find_cookie_db(pkg)
            if src:
                break
    if not src:
        return False, "khong thay webview Cookies (mo app 1 lan truoc roi thu lai)"
    force_stop(pkg)
    time.sleep(1)
    ok, msg = _inject_cookie(pkg, src, cookie)
    if not ok:
        return False, msg
    open_app(pkg)
    return True, "da ghi cookie & mo app"


# --------------------------------------------------------------------------- #
#  Logcat monitor (bat chuoi mat ket noi theo pid cua tung package)
# --------------------------------------------------------------------------- #
class LogcatMonitor(threading.Thread):
    def __init__(self, pid_to_pkg, disconnect_flags, pending_dc, lock):
        super().__init__(daemon=True)
        self.pid_to_pkg = pid_to_pkg      # dict pid(str) -> package
        self.disconnect_flags = disconnect_flags  # dict package -> bool
        self.pending_dc = pending_dc      # dict pid(str) -> reason (chua map duoc)
        self.lock = lock
        self._stop = threading.Event()
        self.proc = None

    def stop(self):
        self._stop.set()
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass

    def run(self):
        try:
            self.proc = subprocess.Popen(
                ["su", "-c", "logcat -v threadtime -T 1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1)
        except Exception:
            return
        for line in self.proc.stdout:
            if self._stop.is_set():
                break
            m = _DC_REGEX.search(line)
            if not m:
                continue
            reason = m.group(0)
            # format threadtime: MM-DD HH:MM:SS.mmm  PID  TID  LEVEL TAG: msg
            parts = line.split()
            pid = parts[2] if len(parts) > 2 and parts[2].isdigit() else ""
            if not pid:
                continue
            with self.lock:
                pkg = self.pid_to_pkg.get(pid)
                if pkg:
                    self.disconnect_flags[pkg] = True
                    log_event("disconnect", "%s: %s" % (pkg, reason))
                else:
                    # chua map duoc pid -> ghi tam, evaluate() se gan dung package
                    self.pending_dc[pid] = reason


def _mtime(path):
    rc, out, _ = run_root("stat -c %%Y '%s' 2>/dev/null" % path)
    if rc == 0 and out.strip().isdigit():
        return int(out.strip())
    return None


def heartbeat_fresh(cfg, pkg):
    """Execute method: kiem tra heartbeat do script Lua (heartbeat.lua) ghi ra.
    File nam o <workspace_executor>/HajiSieu/<username>.hb.
    Can map package -> username trong config['usernames'] de khop dung instance.
    """
    now = time.time()
    threshold = cfg.get("execute_check_seconds", HEARTBEAT_STALE_SECONDS)
    workspaces = cfg.get("executor_workspaces") or EXECUTOR_WORKSPACES_DEFAULT
    uname = cfg.get("usernames", {}).get(pkg)
    candidates = []

    if uname:
        safe = re.sub(r"[^0-9A-Za-z_\-]", "_", uname)
        for ws in workspaces:
            candidates.append("%s/HajiSieu/%s.hb" % (ws.rstrip("/"), safe))
        candidates.append(os.path.join(HEARTBEAT_DIR, pkg + ".hb"))
        candidates.append(os.path.join(HEARTBEAT_DIR, safe + ".hb"))
    else:
        # Khong biet username -> coi la song neu co BAT KY file .hb nao con moi.
        # (Nen set config['usernames'] khi chay nhieu acc o Execute method.)
        for ws in workspaces:
            rc, out, _ = run_root("ls -1 '%s/HajiSieu/'*.hb 2>/dev/null" % ws.rstrip("/"))
            if rc == 0 and out.strip():
                candidates.extend([ln.strip() for ln in out.splitlines() if ln.strip()])
        candidates.append(os.path.join(HEARTBEAT_DIR, pkg + ".hb"))

    for path in candidates:
        mt = _mtime(path)
        if mt is not None and (now - mt) <= threshold:
            return True
    return False


# --------------------------------------------------------------------------- #
#  Core: start auto rejoin
# --------------------------------------------------------------------------- #
STOP_FLAG = threading.Event()


def _sig(_s, _f):
    STOP_FLAG.set()


def start_rejoin(cfg):
    if not cfg["game_target"]:
        print(C.red + "Chua nhap Game ID / ServerVip (menu 2)." + C.R)
        input("Enter de quay lai...")
        return
    if not cfg["packages"]:
        print(C.red + "Chua co package nao (menu 3)." + C.R)
        input("Enter de quay lai...")
        return

    # Hoi/cap nhat thoi gian dong roblox (Config 2)
    cur = cfg.get("close_minutes", 0)
    print(C.cyn + "Thoi gian dong roblox (phut): %s"
          % (cur if cur else "chua dat") + C.R)
    raw = input("Nhap thoi gian dong roblox (Enter de giu nguyen): ").strip()
    if raw.isdigit():
        cfg["close_minutes"] = int(raw)
        save_config(cfg)
    close_minutes = cfg.get("close_minutes", 0)

    deeplink = build_deeplink(cfg["game_target"])
    pkgs = list(cfg["packages"])
    freeform = cfg.get("auto_sort") or cfg.get("auto_grid")

    # Tu dong do username con thieu (de dien cot Username + khop heartbeat Execute)
    missing = [p for p in pkgs if not cfg.get("usernames", {}).get(p)]
    if missing:
        print(C.cyn + "[*] Tu dong do username (%d app)..." % len(missing) + C.R)
        detect_usernames(cfg, missing, verbose=False)

    disconnect_flags = {p: False for p in pkgs}
    pid_to_pkg = {}
    pending_dc = {}
    launch_time = {p: 0 for p in pkgs}
    last_rejoin = {p: 0 for p in pkgs}
    status = {p: "Idle" for p in pkgs}
    lock = threading.Lock()

    STOP_FLAG.clear()
    signal.signal(signal.SIGINT, _sig)

    monitor = LogcatMonitor(pid_to_pkg, disconnect_flags, pending_dc, lock)
    monitor.start()

    def refresh_pids():
        with lock:
            pid_to_pkg.clear()
            for p in pkgs:
                pid = pid_of(p)
                if pid:
                    pid_to_pkg[pid] = p

    def launch_all():
        print(C.yel + "\n[*] Dong tat ca package..." + C.R)
        log_event("cycle", "dong tat ca & mo lai (%d package)" % len(pkgs))
        force_stop_all(pkgs)
        with lock:
            pending_dc.clear()
        time.sleep(2)
        if cfg.get("auto_block"):
            run_auto_block(cfg)
        delay = max(0, int(cfg.get("tab_delay_seconds", 20)))
        for i, p in enumerate(pkgs):
            if STOP_FLAG.is_set():
                return
            print(C.grn + "[*] Mo tab %d/%d: %s" % (i + 1, len(pkgs), p) + C.R)
            launch(p, deeplink, freeform=freeform)
            with lock:
                disconnect_flags[p] = False
                status[p] = "Launching"
                launch_time[p] = time.time()
                last_rejoin[p] = time.time()
            if i < len(pkgs) - 1 and delay > 0:
                time.sleep(delay)
        time.sleep(3)
        refresh_pids()
        if freeform:
            print(C.cyn + "[*] Sap xep cac tab..." + C.R)
            arrange_tabs(cfg, pkgs)

    def evaluate(p):
        pid = pid_of(p)
        alive = pid is not None
        with lock:
            if pid:
                pid_to_pkg[pid] = p
                if pid in pending_dc:
                    disconnect_flags[p] = True
                    log_event("disconnect", "%s: %s" % (p, pending_dc.pop(pid)))
            dced = disconnect_flags.get(p, False)
        if not alive:
            return "Dead", True
        if dced:
            return "Disconnected", True
        if cfg.get("method") == "Execute":
            if not heartbeat_fresh(cfg, p):
                return "No-Heartbeat", True
        return "Running", False

    def rejoin(p):
        now = time.time()
        cooldown = int(cfg.get("rejoin_cooldown_seconds", REJOIN_COOLDOWN_SECONDS))
        if now - last_rejoin.get(p, 0) < cooldown:
            return
        with lock:
            status[p] = "Rejoining"
            last_rejoin[p] = now
        log_event("rejoin", "%s (%s)" % (p, get_username(cfg, p)))
        force_stop(p)
        time.sleep(1.5)
        launch(p, deeplink, freeform=freeform)
        with lock:
            disconnect_flags[p] = False
            launch_time[p] = now
        if freeform:
            arrange_tabs(cfg, [p])

    def dashboard(cycle_start):
        clear()
        cpu = cpu_percent(0.2)
        used_mb, total_mb = mem_info()
        c = C
        print(c.B + c.mag + "  HajiSieu - Roblox Auto Rejoin  " + c.R)
        print(c.gry + hr("=") + c.R)
        mid = ("CPU: %s%5.1f%%%s    |    RAM: %s%.0f/%.0f MB%s"
               % (c.cyn, cpu, c.R, c.cyn, used_mb, total_mb, c.R))
        print("   " + mid)
        if close_minutes > 0:
            left = max(0, close_minutes * 60 - (time.time() - cycle_start))
            print(c.gry + "   Chu ky dong roblox sau: %d phut %02d giay"
                  % (left // 60, left % 60) + c.R)
        print(c.gry + "   Method: %s | AutoBlock: %s | Sort: %s | Grid: %s"
              % (cfg.get("method"),
                 "ON" if cfg.get("auto_block") else "OFF",
                 "ON" if cfg.get("auto_sort") else "OFF",
                 "ON" if cfg.get("auto_grid") else "OFF") + c.R)
        print(c.gry + hr("-") + c.R)
        print(c.B + "   %-22s %-14s %-14s" % ("Package", "Username", "Status") + c.R)
        print(c.gry + hr("-") + c.R)
        for p in pkgs:
            st = status.get(p, "?")
            col = c.grn
            if st in ("Dead", "Disconnected", "No-Heartbeat"):
                col = c.red
            elif st in ("Rejoining", "Launching"):
                col = c.yel
            uname = get_username(cfg, p)
            print("   %-22s %-14s %s%-14s%s"
                  % (p[:22], uname[:14], col, st, c.R))
        print(c.gry + hr("=") + c.R)
        print(c.gry + "   Ctrl+C de dung va quay lai menu." + c.R)

    # ---- bat dau ----
    launch_all()
    cycle_start = time.time()
    try:
        while not STOP_FLAG.is_set():
            for p in pkgs:
                if STOP_FLAG.is_set():
                    break
                st, need = evaluate(p)
                with lock:
                    status[p] = st
                if need:
                    rejoin(p)
            dashboard(cycle_start)
            # chu ky dong roblox
            if close_minutes > 0 and (time.time() - cycle_start) >= close_minutes * 60:
                launch_all()
                cycle_start = time.time()
            for _ in range(MONITOR_REFRESH_SECONDS):
                if STOP_FLAG.is_set():
                    break
                time.sleep(1)
    finally:
        monitor.stop()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        print(C.yel + "\nDa dung auto rejoin." + C.R)
        input("Enter de quay lai menu...")


# --------------------------------------------------------------------------- #
#  Menu actions
# --------------------------------------------------------------------------- #
def menu_set_game(cfg):
    clear()
    print(C.B + "2. Nhap Game ID / ServerVip" + C.R)
    print(C.gry + "Hien tai: %s" % (cfg["game_target"] or "chua dat") + C.R)
    val = input("Nhap Game ID or ServerVip Link: ").strip()
    if val:
        cfg["game_target"] = val
        save_config(cfg)
        print(C.grn + "Da luu." + C.R)
    time.sleep(0.8)


def menu_set_packages(cfg):
    clear()
    print(C.B + "3. Nhap Package" + C.R)
    print(C.gry + "Nhap tien to (vd: com -> com.roblox.*, noka -> noka.*)."
          " Nhieu tien to cach nhau dau phay." + C.R)
    pre = input("Nhap ten package (tien to): ").strip()
    if not pre:
        return
    print(C.cyn + "Dang quet..." + C.R)
    pkgs = scan_packages(pre)
    if not pkgs:
        print(C.red + "Khong tim thay package nao khop." + C.R)
        time.sleep(1.2)
        return
    cfg["package_prefix"] = pre
    cfg["packages"] = pkgs
    save_config(cfg)
    print(C.grn + "Tim thay %d package:" % len(pkgs) + C.R)
    for p in pkgs:
        print("   - " + p)
    print(C.cyn + "\n[*] Tu dong do username Roblox..." + C.R)
    detect_usernames(cfg, pkgs, verbose=True)
    input("\nEnter de tiep tuc...")


def menu_auto_block(cfg):
    clear()
    print(C.B + "4. Auto Block" + C.R)
    cur = "ON" if cfg.get("auto_block") else "OFF"
    print("Auto Block (tu dong block cac account da dang nhap voi nhau de khong "
          "bi join trung server roblox): [trang thai hien tai: %s%s%s]"
          % (C.grn if cfg.get("auto_block") else C.red, cur, C.R))
    ans = input("Nhap y/n de bat hoac tat (y=On, n=Off): ").strip().lower()
    if ans == "y":
        cfg["auto_block"] = True
        save_config(cfg)
        print(C.grn + "Auto Block: ON" + C.R)
    elif ans == "n":
        cfg["auto_block"] = False
        save_config(cfg)
        print(C.red + "Auto Block: OFF" + C.R)
    time.sleep(0.9)


def open_app(pkg):
    """Chi mo app bang launcher (khong vao game, khong lam gi them)."""
    rc, out, err = run_root("monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg)
    blob = (out + " " + err).lower()
    if rc != 0 or "no activities found" in blob or "aborted" in blob:
        # fallback: resolve launcher activity roi am start
        run_root("am start -n \"$(cmd package resolve-activity --brief %s "
                 "| tail -n 1)\"" % pkg)
    return rc


def menu_open_all(cfg):
    """5. Open All Tabs - chi mo tat ca app da chon (khong vao game, khong sort)."""
    clear()
    print(C.B + "5. Open All Tabs" + C.R)
    pkgs = list(cfg.get("packages", []))
    if not pkgs:
        print(C.red + "Chua co package nao (menu 3)." + C.R)
        input("Enter de quay lai...")
        return
    print(C.cyn + "Mo %d app (chi mo len, khong lam gi them)..." % len(pkgs) + C.R)
    for i, p in enumerate(pkgs):
        print(C.grn + "[*] Mo %d/%d: %s" % (i + 1, len(pkgs), p) + C.R)
        open_app(p)
        log_event("open", "mo app: %s" % p)
        time.sleep(1)
    print(C.grn + "Da mo xong %d app." % len(pkgs) + C.R)
    input("Enter de quay lai menu...")


def _cookie_label(c):
    return (c[:22] + "...") if len(c) > 22 else c


def check_cookie(cookie):
    """Kiem tra 1 cookie. Tra ve (ok, mo_ta)."""
    code, body = _http_get("https://users.roblox.com/v1/users/authenticated", cookie)
    if code == 200:
        try:
            d = json.loads(body)
            return True, "%s (id %s)" % (d.get("name") or d.get("displayName"), d.get("id"))
        except Exception:
            return True, "live"
    if code == 401:
        return False, "het han / sai cookie (401)"
    if code == 0:
        return False, "loi mang: %s" % body[:40]
    return False, "HTTP %s" % code


def _check_all_cookies(cookies):
    clear()
    print(C.B + "Kiem tra tat ca cookie" + C.R)
    if not cookies:
        print(C.red + "cookie.txt trong." + C.R)
        print(C.gry + COOKIE_PATH + C.R)
        input("Enter...")
        return
    print(C.cyn + "Dang kiem tra %d cookie..." % len(cookies) + C.R)
    print(C.gry + hr() + C.R)
    live = 0
    for i, c in enumerate(cookies):
        ok, info = check_cookie(c)
        if ok:
            live += 1
        tag = (C.grn + "LIVE" + C.R) if ok else (C.red + "DEAD" + C.R)
        print("  %2d. [%s] %-25s | %s" % (i + 1, tag, _cookie_label(c), info))
    print(C.gry + hr() + C.R)
    print("Tong: %d  |  LIVE: %s%d%s  |  DEAD: %s%d%s"
          % (len(cookies), C.grn, live, C.R, C.red, len(cookies) - live, C.R))
    input("\nEnter de quay lai...")


def menu_login_cookie(cfg):
    """6. Login with cookie - login app bang cookie trong cookie.txt."""
    while True:
        clear()
        cookies = load_cookies()
        pkgs = list(cfg.get("packages", []))
        print(C.B + "Login with cookie" + C.R)
        print(C.gry + "File: %s" % COOKIE_PATH + C.R)
        print(C.gry + "Cookie: %d  |  Package: %d" % (len(cookies), len(pkgs)) + C.R)
        print(C.gry + hr() + C.R)
        print("1. Choose app to login")
        print("2. Login all package")
        print("3. Kiem tra tat ca cookie (trang thai)")
        print("0. Quay lai")
        print(C.gry + hr() + C.R)
        ch = input("Chon: ").strip()
        if ch == "1":
            _login_choose(cfg, pkgs, cookies)
        elif ch == "2":
            _login_all(cfg, pkgs, cookies)
        elif ch == "3":
            _check_all_cookies(cookies)
        elif ch == "0":
            return


def _login_choose(cfg, pkgs, cookies):
    clear()
    print(C.B + "1. Choose app to login" + C.R)
    if not pkgs:
        print(C.red + "Chua co package (menu 3)." + C.R)
        input("Enter...")
        return
    if not cookies:
        print(C.red + "cookie.txt trong. Them cookie (1 dong 1 cookie) vao:" + C.R)
        print(C.gry + COOKIE_PATH + C.R)
        input("Enter...")
        return
    for i, p in enumerate(pkgs):
        print("  %d. %s" % (i + 1, p))
    s = input("Chon app (so): ").strip()
    if not (s.isdigit() and 1 <= int(s) <= len(pkgs)):
        return
    pkg = pkgs[int(s) - 1]
    print()
    for i, c in enumerate(cookies):
        print("  %d. %s" % (i + 1, _cookie_label(c)))
    sc = input("Chon cookie (so): ").strip()
    if not (sc.isdigit() and 1 <= int(sc) <= len(cookies)):
        return
    cookie = cookies[int(sc) - 1]
    print(C.cyn + "Dang login %s ..." % pkg + C.R)
    ok, msg = login_with_cookie(pkg, cookie)
    print(((C.grn + "OK") if ok else (C.red + "FAIL")) + C.R + " : " + msg)
    log_event("login", "%s : %s" % (pkg, "OK" if ok else msg))
    input("Enter de quay lai...")


def _login_all(cfg, pkgs, cookies):
    clear()
    print(C.B + "2. Login all package" + C.R)
    if not pkgs:
        print(C.red + "Chua co package (menu 3)." + C.R)
        input("Enter...")
        return
    if not cookies:
        print(C.red + "cookie.txt trong. Them cookie roi thu lai:" + C.R)
        print(C.gry + COOKIE_PATH + C.R)
        input("Enter...")
        return
    n = min(len(pkgs), len(cookies))
    if len(cookies) < len(pkgs):
        print(C.yel + "Chi co %d cookie cho %d package -> login %d app dau."
              % (len(cookies), len(pkgs), n) + C.R)
    for i in range(n):
        pkg = pkgs[i]
        cookie = cookies[i]
        print(C.grn + "[*] Login %d/%d: %s" % (i + 1, n, pkg) + C.R)
        ok, msg = login_with_cookie(pkg, cookie)
        print("   " + (((C.grn + "OK") if ok else (C.red + "FAIL " + msg)) + C.R))
        log_event("login", "%s : %s" % (pkg, "OK" if ok else msg))
    print(C.grn + "Xong." + C.R)
    input("Enter de quay lai...")


def menu_config(cfg):
    while True:
        clear()
        print(C.B + C.mag + "10. Config" + C.R)
        print(C.gry + hr() + C.R)
        print("1. [%s%s%s] Doi Method  (1=Execute, 2=Online)"
              % (C.cyn, cfg.get("method"), C.R))
        print("2. Thoi gian dong roblox: %s%d phut%s"
              % (C.cyn, cfg.get("close_minutes", 0), C.R))
        print("3. Delay khi mo giua cac tab: %s%d giay%s"
              % (C.cyn, cfg.get("tab_delay_seconds", 20), C.R))
        print("4. Time check execute: %s%d giay%s"
              % (C.cyn, cfg.get("execute_check_seconds", 35), C.R))
        print("5. Cooldown rejoin: %s%d giay%s"
              % (C.cyn, cfg.get("rejoin_cooldown_seconds", 25), C.R))
        print("7. So cot luoi (grid): %s%s%s"
              % (C.cyn, (cfg.get("grid_cols", 0) or "tu tinh"), C.R))
        print("8. Auto sort tab (thu nho tab): %s"
              % (C.grn + "ON" + C.R if cfg.get("auto_sort") else C.red + "OFF" + C.R))
        print("9. Auto sap xep tab (dang luoi): %s"
              % (C.grn + "ON" + C.R if cfg.get("auto_grid") else C.red + "OFF" + C.R))
        print("0. Quay lai")
        print(C.gry + hr() + C.R)
        ch = input("Chon: ").strip()
        if ch == "1":
            m = input("Chon method (1=Execute, 2=Online): ").strip()
            if m == "1":
                cfg["method"] = "Execute"
            elif m == "2":
                cfg["method"] = "Online"
            save_config(cfg)
        elif ch == "2":
            v = input("Thoi gian dong roblox (phut, 0=khong tu dong): ").strip()
            if v.lstrip("-").isdigit():
                cfg["close_minutes"] = max(0, int(v))
                save_config(cfg)
        elif ch == "3":
            v = input("Delay giua cac tab (giay): ").strip()
            if v.isdigit():
                cfg["tab_delay_seconds"] = int(v)
                save_config(cfg)
        elif ch == "4":
            v = input("Time check execute (giay, heartbeat cu hon se rejoin): ").strip()
            if v.isdigit() and int(v) > 0:
                cfg["execute_check_seconds"] = int(v)
                save_config(cfg)
        elif ch == "5":
            v = input("Cooldown rejoin (giay): ").strip()
            if v.isdigit() and int(v) > 0:
                cfg["rejoin_cooldown_seconds"] = int(v)
                save_config(cfg)
        elif ch == "7":
            v = input("So cot luoi grid (0 = tu tinh): ").strip()
            if v.isdigit():
                cfg["grid_cols"] = int(v)
                save_config(cfg)
        elif ch == "8":
            cfg["auto_sort"] = not cfg.get("auto_sort")
            save_config(cfg)
        elif ch == "9":
            cfg["auto_grid"] = not cfg.get("auto_grid")
            save_config(cfg)
        elif ch == "0":
            return


def main_menu():
    cfg = load_config()
    # chuan bi thu muc heartbeat (Execute method)
    run_root("mkdir -p %s" % HEARTBEAT_DIR)

    rooted = have_root()
    if not rooted:
        print(C.yel + "[!] Khong phat hien quyen root (su). Mot so chuc nang "
              "se khong hoat dong. Hay cap quyen root cho Termux." + C.R)
        time.sleep(1.5)

    while True:
        clear()
        print(C.B + C.mag + "   HajiSieu - Roblox Auto Rejoin (Termux/Root)" + C.R)
        print(C.gry + hr("=") + C.R)
        print("   Game/SVV : %s%s%s"
              % (C.cyn, cfg["game_target"] or "chua dat", C.R))
        print("   Packages : %s%d app%s"
              % (C.cyn, len(cfg["packages"]), C.R))
        print("   Method   : %s%s%s   Root: %s"
              % (C.cyn, cfg.get("method"), C.R,
                 C.grn + "OK" + C.R if rooted else C.red + "NO" + C.R))
        print("   AutoBlock: %s | Sort: %s | Grid: %s"
              % ("ON" if cfg.get("auto_block") else "OFF",
                 "ON" if cfg.get("auto_sort") else "OFF",
                 "ON" if cfg.get("auto_grid") else "OFF"))
        print(C.gry + hr("-") + C.R)
        print("   1.  Auto Rejoin")
        print("   2.  Nhap Game ID / ServerVip")
        print("   3.  Nhap Package")
        print("   4.  Auto Block")
        print("   5.  Open All Tabs")
        print("   6.  Login with cookie")
        print("   10. Config")
        print("   0.  Thoat")
        print(C.gry + hr("=") + C.R)
        ch = input("   Chon: ").strip()
        if ch == "1":
            start_rejoin(cfg)
        elif ch == "2":
            menu_set_game(cfg)
        elif ch == "3":
            menu_set_packages(cfg)
        elif ch == "4":
            menu_auto_block(cfg)
        elif ch == "5":
            menu_open_all(cfg)
        elif ch == "6":
            menu_login_cookie(cfg)
        elif ch == "10":
            menu_config(cfg)
        elif ch == "0":
            clear()
            print(C.grn + "Tam biet!" + C.R)
            return
        else:
            # ho tro go 'config'
            if ch.lower() == "config":
                menu_config(cfg)


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(C.yel + "\nThoat." + C.R)
