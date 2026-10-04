"""网易云音乐接口客户端

自行实现 weapi / eapi 两套加密（PyPI 上的 pyncm 已下架，不依赖第三方网易云库），
只用到镜像里已有的 httpx 与 cryptography。

登录方式：
- 扫码登录（网页端 weapi）
- 手机号 + 短信验证码 / 手机号 + 密码（先走 weapi，遇到风控码自动改走 eapi 客户端通道）
- 直接粘贴 Cookie（MUSIC_U），作为兜底

登录态保存在 data/ncm_cookie.json。
"""

import base64
import hashlib
import json
import os
import random
import string
import time
from typing import Optional

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DATA_DIR = "data"
COOKIE_FILE = os.path.join(DATA_DIR, "ncm_cookie.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------- 加密参数（网易云网页端 / PC 客户端公开算法） ----------------
_WEAPI_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
_WEAPI_IV = b"0102030405060708"
_WEAPI_PUB_EXP = 0x010001
_WEAPI_MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629"
    "ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424"
    "d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
    16,
)
_EAPI_KEY = b"e82ckenh8dichen8"

_UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.164 NeteaseMusicDesktop/3.0.18.203152 Safari/537.36"
)
_PC_APPVER = "3.0.18.203152"

# eapi 客户端身份：不同客户端的风控策略不同（实测：PC 发短信可过，安卓验证码登录可过）
CLIENT_PROFILES = {
    "pc": {
        "ua": _UA_PC,
        "header": {"os": "pc", "appver": _PC_APPVER, "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
                   "channel": "netease"},
    },
    "android": {
        "ua": "NeteaseMusic/8.20.20.231215173437(8020020);Dalvik/2.1.0 (Linux; U; Android 14; 23013RK75C Build/UKQ1.230804.001)",
        "header": {"os": "android", "appver": "8.20.20.231215173437", "osver": "14", "versioncode": "140",
                   "buildver": "1702629277", "resolution": "1920x1080", "mobilename": "23013RK75C",
                   "channel": "xiaomi"},
    },
}

# 登录 / 风控相关的返回码
RISK_CODES = {8821, -462, 10004}
LOGIN_MESSAGES = {
    400: "参数错误",
    501: "该手机号未注册网易云账号",
    502: "密码错误",
    503: "验证码错误",
    509: "尝试次数过多，请稍后再试",
    8821: "触发网易云风控（需要行为验证），请稍后重试或改用 Cookie 登录",
    -462: "触发网易云安全验证，请稍后重试或改用 Cookie 登录",
    10004: "请求过于频繁，请稍后再试",
}

# 下载音质（从高到低）；请求高等级时服务端会自动降级到账号可用的最高音质
LEVELS = {
    "jymaster": "超清母带",
    "sky": "沉浸环绕声",
    "jyeffect": "高清环绕声",
    "hires": "Hi-Res",
    "lossless": "无损",
    "exhigh": "极高 (320k)",
    "higher": "较高 (192k)",
    "standard": "标准 (128k)",
}


def _pkcs7(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def _aes_cbc_b64(data: bytes, key: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(_WEAPI_IV)).encryptor()
    return base64.b64encode(enc.update(_pkcs7(data)) + enc.finalize())


def weapi_encrypt(payload: dict) -> dict:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sec_key = "".join(random.choices(string.ascii_letters + string.digits, k=16)).encode()
    params = _aes_cbc_b64(_aes_cbc_b64(text, _WEAPI_PRESET_KEY), sec_key).decode()
    rs = pow(int(sec_key[::-1].hex(), 16), _WEAPI_PUB_EXP, _WEAPI_MODULUS)
    return {"params": params, "encSecKey": format(rs, "x").zfill(256)}


def eapi_encrypt(url_path: str, payload: dict) -> dict:
    """url_path 形如 /api/w/login/cellphone"""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.md5(f"nobody{url_path}use{text}md5forencrypt".encode("utf-8")).hexdigest()
    data = f"{url_path}-36cd479b6b5-{text}-36cd479b6b5-{digest}".encode("utf-8")
    enc = Cipher(algorithms.AES(_EAPI_KEY), modes.ECB()).encryptor()
    return {"params": (enc.update(_pkcs7(data)) + enc.finalize()).hex().upper()}


class NeteaseError(Exception):
    pass


def _rand(chars: str, n: int) -> str:
    return "".join(random.choices(chars, k=n))


class NeteaseClient:
    def __init__(self):
        self.cookies: dict = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._load()

    # ---------- cookie 持久化 ----------
    def _load(self):
        try:
            if os.path.exists(COOKIE_FILE):
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    self.cookies = json.load(f) or {}
        except Exception as e:
            print(f"[网易云] 读取 cookie 失败: {e}")
            self.cookies = {}
        changed = False
        defaults = {
            "_ntes_nuid": lambda: _rand("0123456789abcdef", 32),
            "NMTID": lambda: "00O" + _rand(string.ascii_letters + string.digits, 40),
            "deviceId": lambda: _rand("0123456789ABCDEF", 32),
        }
        for key, make in defaults.items():
            if key not in self.cookies:
                self.cookies[key] = make()
                changed = True
        if changed:
            self._save()

    def _save(self):
        try:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cookies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[网易云] 保存 cookie 失败: {e}")

    def set_cookie_string(self, raw: str):
        raw = (raw or "").strip()
        if not raw:
            raise NeteaseError("Cookie 为空")
        parsed = {}
        if "=" not in raw:
            parsed["MUSIC_U"] = raw
        else:
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k:
                        parsed[k] = v
        if not parsed.get("MUSIC_U"):
            raise NeteaseError("Cookie 中没有 MUSIC_U")
        self.cookies.update(parsed)
        self._save()

    def logout(self):
        keep = {k: self.cookies[k] for k in ("_ntes_nuid", "NMTID", "deviceId") if k in self.cookies}
        self.cookies = keep
        self._save()

    @property
    def has_login_cookie(self) -> bool:
        return bool(self.cookies.get("MUSIC_U"))

    # ---------- HTTP ----------
    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _absorb_cookies(self, resp: httpx.Response):
        changed = False
        for header in resp.headers.get_list("set-cookie"):
            first = header.split(";", 1)[0]
            if "=" not in first:
                continue
            k, v = first.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in ("MUSIC_U", "__csrf", "MUSIC_A_T", "MUSIC_R_T", "NMTID") and v and self.cookies.get(k) != v:
                self.cookies[k] = v
                changed = True
        if changed:
            self._save()

    @staticmethod
    def _json(resp: httpx.Response) -> dict:
        try:
            return resp.json()
        except Exception:
            raise NeteaseError(f"接口返回异常 HTTP {resp.status_code}: {resp.text[:200]}")

    async def request(self, path: str, data: Optional[dict] = None) -> dict:
        """网页端 weapi 请求，path 形如 /song/lyric"""
        data = dict(data or {})
        data["csrf_token"] = self.cookies.get("__csrf", "")
        cookies = {"__remember_me": "true", "os": "pc", **{k: v for k, v in self.cookies.items() if k != "deviceId"}}
        headers = {
            "User-Agent": _UA_WEB,
            "Referer": "https://music.163.com/",
            "Origin": "https://music.163.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        }
        client = await self._http()
        resp = await client.post(f"https://music.163.com/weapi{path}", data=weapi_encrypt(data), headers=headers)
        self._absorb_cookies(resp)
        return self._json(resp)

    async def eapi_request(self, api_path: str, data: Optional[dict] = None, profile: str = "pc") -> dict:
        """客户端 eapi 请求，api_path 形如 /api/w/login/cellphone；profile 为 pc / android"""
        prof = CLIENT_PROFILES.get(profile, CLIENT_PROFILES["pc"])
        header = dict(prof["header"])
        header.update({
            "deviceId": self.cookies.get("deviceId", ""),
            "requestId": f"{int(time.time() * 1000)}_{random.randint(0, 999):04d}",
            "__csrf": self.cookies.get("__csrf", ""),
        })
        if self.cookies.get("MUSIC_U"):
            header["MUSIC_U"] = self.cookies["MUSIC_U"]
        payload = dict(data or {})
        payload["header"] = header
        payload["e_r"] = False
        cookie = {**header, "NMTID": self.cookies.get("NMTID", ""), "_ntes_nuid": self.cookies.get("_ntes_nuid", "")}
        headers = {
            "User-Agent": prof["ua"],
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookie.items() if v),
        }
        url = "https://interface.music.163.com/eapi" + api_path[len("/api"):]
        client = await self._http()
        resp = await client.post(url, data=eapi_encrypt(api_path, payload), headers=headers)
        self._absorb_cookies(resp)
        return self._json(resp)

    async def _with_fallback(self, weapi_path: str, eapi_path: str, data: dict,
                             order=("web", "pc", "android")) -> dict:
        """按顺序尝试各个通道（网页 / PC 客户端 / 安卓客户端），遇到风控码就换下一个"""
        res = {"code": -1}
        for channel in order:
            try:
                if channel == "web":
                    res = await self.request(weapi_path, data)
                else:
                    res = await self.eapi_request(eapi_path, data, profile=channel)
            except Exception as e:
                print(f"[网易云] {channel} 通道 {eapi_path} 请求失败: {e}")
                res = {"code": -1, "message": str(e)}
                continue
            code = res.get("code")
            if code not in RISK_CODES and code != -1:
                if channel != order[0]:
                    print(f"[网易云] {eapi_path} 通过 {channel} 通道完成（code={code}）")
                return res
            print(f"[网易云] {channel} 通道 {eapi_path} 触发风控 code={code}，尝试下一个通道")
        return res

    # ---------- 账号 ----------
    async def get_account(self) -> Optional[dict]:
        if not self.has_login_cookie:
            return None
        res = await self.request("/w/nuser/account/get")
        profile = res.get("profile")
        if res.get("code") != 200 or not profile:
            return None
        account = res.get("account") or {}
        return {
            "uid": profile.get("userId"),
            "nickname": profile.get("nickname", ""),
            "avatar": profile.get("avatarUrl", ""),
            "vip_type": account.get("vipType", 0),
        }

    async def qr_create(self) -> str:
        res = await self._with_fallback("/login/qrcode/unikey", "/api/login/qrcode/unikey", {"type": 3},
                                        order=("web", "pc"))
        key = res.get("unikey")
        if not key:
            raise NeteaseError(LOGIN_MESSAGES.get(res.get("code")) or res.get("message")
                               or f"获取二维码失败（code={res.get('code')}）")
        return key

    async def qr_check(self, key: str) -> dict:
        return await self._with_fallback("/login/qrcode/client/login", "/api/login/qrcode/client/login",
                                         {"key": key, "type": 3}, order=("web", "pc"))

    async def send_sms(self, phone: str, ctcode: str = "86") -> dict:
        return await self._with_fallback(
            "/sms/captcha/sent", "/api/sms/captcha/sent",
            {"cellphone": str(phone).strip(), "ctcode": str(ctcode or "86")},
            order=("pc", "web", "android"),
        )

    async def login_cellphone(self, phone: str, ctcode: str = "86",
                              captcha: str = "", password: str = "") -> dict:
        data = {
            "type": "1",
            "https": "true",
            "phone": str(phone).strip(),
            "countrycode": str(ctcode or "86"),
            "remember": "true",
        }
        if captcha:
            data["captcha"] = str(captcha).strip()
        elif password:
            data["password"] = hashlib.md5(password.encode("utf-8")).hexdigest()
        else:
            raise NeteaseError("请填写验证码或密码")
        # 实测安卓客户端通道的验证码登录最不容易被风控
        order = ("android", "pc", "web") if captcha else ("web", "pc", "android")
        res = await self._with_fallback("/w/login/cellphone", "/api/w/login/cellphone", data, order=order)
        # 有的返回把 cookie 放在响应体里
        raw = res.get("cookie") or ""
        if isinstance(raw, str) and "MUSIC_U=" in raw:
            self.absorb_cookie_text(raw)
        token = res.get("token")
        if res.get("code") == 200 and token and not self.cookies.get("MUSIC_U"):
            self.cookies["MUSIC_U"] = token
            self._save()
        return res

    def absorb_cookie_text(self, raw: str):
        parts = {}
        for p in str(raw).split(";"):
            if "=" in p:
                k, v = p.split("=", 1)
                k = k.strip()
                if k in ("MUSIC_U", "__csrf", "MUSIC_A_T", "MUSIC_R_T"):
                    parts[k] = v.strip()
        if parts:
            self.cookies.update(parts)
            self._save()

    # ---------- 歌单 / 歌曲 ----------
    async def user_playlists(self, uid) -> list:
        res = await self.request("/user/playlist", {"uid": uid, "limit": 1000, "offset": 0, "includeVideo": True})
        out = []
        for pl in res.get("playlist", []) or []:
            creator = pl.get("creator") or {}
            out.append({
                "id": str(pl.get("id")),
                "name": pl.get("name", ""),
                "count": pl.get("trackCount", 0),
                "cover": (pl.get("coverImgUrl") or "").replace("http://", "https://", 1),
                "subscribed": bool(pl.get("subscribed")) or str(creator.get("userId")) != str(uid),
                "special": pl.get("specialType", 0),
            })
        return out

    async def playlist_detail(self, pid) -> dict:
        res = await self.request("/v6/playlist/detail", {"id": str(pid), "n": 100000, "s": 0})
        if res.get("code") != 200:
            raise NeteaseError(res.get("message") or res.get("msg") or f"获取歌单失败 code={res.get('code')}")
        pl = res.get("playlist") or {}
        ids = [str(t["id"]) for t in pl.get("trackIds", []) or []]
        return {
            "name": pl.get("name", ""),
            "cover": (pl.get("coverImgUrl") or "").replace("http://", "https://", 1),
            "ids": ids,
        }

    async def song_details(self, ids: list) -> list:
        songs = []
        for i in range(0, len(ids), 400):
            batch = ids[i:i + 400]
            res = await self.request("/v3/song/detail", {"c": json.dumps([{"id": int(x)} for x in batch])})
            priv = {str(p.get("id")): p for p in res.get("privileges", []) or []}
            for s in res.get("songs", []) or []:
                songs.append(_song_to_dict(s, priv.get(str(s.get("id")))))
        return songs

    async def playlist_songs(self, pid) -> tuple:
        detail = await self.playlist_detail(pid)
        songs = await self.song_details(detail["ids"]) if detail["ids"] else []
        return detail, songs

    async def search(self, keyword: str, page: int = 1, num: int = 30) -> list:
        res = await self.request(
            "/cloudsearch/pc",
            {"s": keyword, "type": 1, "limit": num, "offset": (page - 1) * num, "total": True},
        )
        songs = (res.get("result") or {}).get("songs") or []
        return [_song_to_dict(s, s.get("privilege")) for s in songs]

    async def song_url(self, song_id, level: str = "lossless") -> Optional[dict]:
        res = await self.request(
            "/song/enhance/player/url/v1",
            {"ids": json.dumps([int(song_id)]), "level": level, "encodeType": "flac"},
        )
        data = (res.get("data") or [None])[0]
        if not data or not data.get("url"):
            return None
        return {
            "url": data["url"],
            "ext": "." + (data.get("type") or "mp3").lower(),
            "level": data.get("level") or level,
            "br": data.get("br", 0),
            "size": data.get("size", 0),
            "trial": bool(data.get("freeTrialInfo")),
        }

    async def lyric(self, song_id) -> dict:
        res = await self.request("/song/lyric", {"id": str(song_id), "lv": -1, "tv": -1, "kv": -1, "rv": -1})
        return {
            "lyric": ((res.get("lrc") or {}).get("lyric")) or "",
            "trans": ((res.get("tlyric") or {}).get("lyric")) or "",
        }


def _song_to_dict(s: dict, privilege: Optional[dict] = None) -> dict:
    al = s.get("al") or {}
    publish = s.get("publishTime") or 0
    year = ""
    if publish and publish > 0:
        try:
            year = time.strftime("%Y", time.localtime(publish / 1000))
        except Exception:
            year = ""
    playable = True
    if privilege is not None:
        playable = (privilege.get("st", 0) or 0) >= 0
    return {
        "id": str(s.get("id")),
        "name": s.get("name", ""),
        "singer": [{"name": a.get("name", "")} for a in (s.get("ar") or [])],
        "album": al.get("name", ""),
        "cover": (al.get("picUrl") or "").replace("http://", "https://", 1),
        "track": s.get("no") or 0,
        "year": year,
        "interval": int((s.get("dt") or 0) / 1000),
        "fee": s.get("fee", 0),
        "playable": playable,
    }


client = NeteaseClient()
