"""网易云音乐模块：登录 / 歌单 / 搜索 / 播放代理 / 下载队列 / 歌单监控

与 QQ 音乐部分相互独立（独立的任务表、监控表、配置），但共享：
- 标签 / 封面 / 歌词写入开关（config.json 的 download.*）
- 通知渠道（notification.py）
- 下载根目录（download.downloads_root）
数据文件：data/ncm_cookie.json、ncm_tasks.json、ncm_monitored.json、ncm_config.json
"""
import asyncio
import os
import re
import time
from typing import Optional

import aiofiles
import httpx
import orjson
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from netease_api import LEVELS, LOGIN_MESSAGES, RISK_CODES, NeteaseError, client

router = APIRouter(prefix="/api/ncm")

DATA_DIR = "data"
TASKS_FILE = os.path.join(DATA_DIR, "ncm_tasks.json")
MONITOR_FILE = os.path.join(DATA_DIR, "ncm_monitored.json")
AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".wav", ".ape")

# 下载音质固定请求最高档（超清母带），服务端会自动降到账号可用的最高音质，因此界面上不再提供选择
DOWNLOAD_LEVEL = "jymaster"

tasks: dict = {}                  # key: 网易云歌曲 id(str)
queue: asyncio.Queue = asyncio.Queue()
_bg: list = []
_wake = asyncio.Event()
_save_lock = asyncio.Lock()
_check_lock = asyncio.Lock()
_account_cache = {"t": 0.0, "v": None}
_avatar_cache: dict = {}


# ---------------- JSON / 配置 ----------------
def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                content = f.read()
            if content.strip():
                return orjson.loads(content)
    except Exception as e:
        print(f"[网易云] 读取 {path} 失败: {e}")
    return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    os.replace(tmp, path)


def _cfg(key, default=None):
    from config import config as app_config
    return app_config.get(key, default)


def _downloads_root() -> str:
    from download_paths import downloads_root
    return downloads_root()


def _sanitize(name: str) -> str:
    from download_paths import sanitize_component
    return sanitize_component(name)


def single_dir() -> str:
    """单曲 / 搜索下载目录：与 QQ 音乐共用「单曲下载目录」"""
    from download_paths import default_song_dir
    return default_song_dir()


def playlist_dir(title: str, override: str = "") -> str:
    from download_paths import playlist_subfolder_enabled
    if (override or "").strip():
        return override.strip()
    if not playlist_subfolder_enabled():
        return _downloads_root()
    return os.path.join(_downloads_root(), _sanitize(title or "网易云歌单"))


async def _save_tasks():
    async with _save_lock:
        try:
            data = orjson.dumps(tasks, option=orjson.OPT_INDENT_2)
            async with aiofiles.open(TASKS_FILE + ".tmp", "wb") as f:
                await f.write(data)
            os.replace(TASKS_FILE + ".tmp", TASKS_FILE)
        except Exception as e:
            print(f"[网易云] 保存任务失败: {e}")


# ---------------- 本地文件匹配 ----------------
def _norm(s: str) -> str:
    return re.sub(r"[\s,，&、/\\·._\-()（）\[\]【】'\"!！?？:：]", "", (s or "")).lower()


def song_title(song: dict) -> str:
    singers = ", ".join(a["name"] for a in song.get("singer", []) if a.get("name"))
    return f"{song.get('name', '未知歌曲')} - {singers or '未知歌手'}"


class LocalIndex:
    """目录（递归）里音频文件的归一化索引"""

    def __init__(self, directory: str):
        self.full = {}
        if not directory or not os.path.isdir(directory):
            return
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                base, ext = os.path.splitext(fn)
                if ext.lower() in AUDIO_EXTS and not fn.startswith("."):
                    self.full.setdefault(_norm(base), os.path.join(root, fn))

    def find(self, song: dict) -> Optional[str]:
        key = _norm(song_title(song))
        if key in self.full:
            return self.full[key]
        name = _norm(song.get("name", ""))
        first = _norm((song.get("singer") or [{}])[0].get("name", "")) if song.get("singer") else ""
        if len(name) >= 2:
            for k, path in self.full.items():
                if k.startswith(name) and (not first or first in k[len(name):]):
                    return path
        return None


def _file_info(path: str) -> dict:
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return {"filename": os.path.basename(path), "path": path, "size": size,
            "ext": os.path.splitext(path)[1].lstrip(".").upper()}


def annotate(songs: list, directory: str) -> list:
    idx = LocalIndex(directory)
    for s in songs:
        t = tasks.get(s["id"])
        s["status"] = t.get("status") if t else None
        s["progress"] = t.get("progress", 0) if t else 0
        s["quality"] = t.get("quality", "") if t else ""
        local = idx.find(s)
        if local:
            s["local"] = _file_info(local)
            if s["status"] in (None, "completed", "failed", "cancelled"):
                s["status"] = "local"
    return songs


# ---------------- 账号 ----------------
async def account(force: bool = False):
    now = time.time()
    if not force and now - _account_cache["t"] < 60:
        return _account_cache["v"]
    try:
        v = await client.get_account()
    except Exception as e:
        print(f"[网易云] 获取账号失败: {e}")
        v = _account_cache["v"] if now - _account_cache["t"] < 600 else None
    _account_cache.update(t=now, v=v)
    return v


async def require_login():
    acc = await account()
    if not acc:
        raise HTTPException(status_code=401, detail="网易云未登录或登录已失效")
    return acc


async def require_download_ready():
    """下载类接口的门槛：网易云已登录，或者有可用的自定义下载源"""
    try:
        from lx_source import manager as source_manager
        if source_manager.has_usable("netease"):
            return None
    except Exception:
        pass
    return await require_login()


# ---------------- 通知 ----------------
async def _notify(kind: str, *args):
    try:
        from notification import notification_manager as nm
        if kind == "ok":
            await nm.send_download_complete_notification(*args)
        elif kind == "fail":
            await nm.send_download_failed_notification(*args)
        elif kind == "playlist":
            await nm.send_playlist_update_notification(*args)
        elif kind == "text":
            await nm.send_notification(*args)
    except Exception as e:
        print(f"[网易云] 通知发送失败: {e}")


# ---------------- 下载 ----------------
async def enqueue(song: dict, target_dir: str = "", force: bool = False, source: str = "",
                  date_folder=None) -> bool:
    sid = song["id"]
    t = tasks.get(sid)
    if t and not force and t.get("status") in ("queued", "downloading", "completed"):
        return False
    tasks[sid] = {
        "status": "queued",
        "song_name": song_title(song),
        "song": song,
        "quality": "",
        "progress": 0,
        "error": None,
        "download_dir": target_dir or (t or {}).get("download_dir", ""),
        "force": force,
        "source": source or (t or {}).get("source", ""),
        "date_folder": bool((t or {}).get("date_folder")) if date_folder is None else bool(date_folder),
        "created": int(time.time()),
    }
    await _save_tasks()
    await queue.put(sid)
    return True


async def _resolve_by_sources(sid: str, song: dict):
    """用自定义下载源获取网易云歌曲直链；返回与 client.song_url 相同结构的 dict，失败返回 None"""
    try:
        from lx_source import manager as source_manager
    except Exception:
        return None
    if not source_manager.list():
        return None
    music_info = {
        "songmid": str(sid),
        "name": song.get("name", ""),
        "singer": "、".join(a.get("name", "") for a in song.get("singer", [])),
        "albumName": song.get("album", ""),
        "img": song.get("cover", ""),
        "interval": song.get("interval", 0),
        "source": "wy",
    }
    try:
        r = await source_manager.resolve("netease", music_info)
    except Exception as e:
        print(f"[网易云] 下载源解析失败，回退官方渠道: {e}")
        return None
    if not r:
        return None
    return {"url": r["url"], "ext": r["extension"], "level": r["level"], "size": 0,
            "trial": False, "quality_label": r["quality"]}


async def _download(sid: str):
    task = tasks[sid]
    song = task["song"]
    name = "[网易云] " + task["song_name"]
    base_dir = task.get("download_dir") or single_dir()
    target_dir = base_dir
    if task.get("date_folder"):
        from download_paths import date_folder_name
        target_dir = os.path.join(base_dir, date_folder_name())
    os.makedirs(target_dir, exist_ok=True)

    if not task.get("force"):
        existing = LocalIndex(base_dir).find(song)
        if existing:
            info = _file_info(existing)
            task.update(status="completed", progress=100, file_path=existing, file_size=info["size"],
                        quality=task.get("quality") or info["ext"], error=None, skipped=True)
            print(f"[网易云] 本地已存在，跳过: {existing}")
            return

    # 先试自定义下载源（洛雪格式），全部失败再回退账号官方渠道
    info = await _resolve_by_sources(sid, song)
    if not info:
        try:
            info = await client.song_url(sid, DOWNLOAD_LEVEL)
        except Exception as e:
            info = None
            print(f"[网易云] 获取链接异常 {sid}: {e}")
    if not info:
        msg = "无法获取下载链接（无版权 / 需要 VIP / 登录失效）"
        if client.has_login_cookie and not await account(force=True):
            msg = "网易云登录已失效，请重新登录"
            await _notify("text", "网易云音乐登录已失效，请到网页「网易云」页重新登录。", "网易云登录已失效")
        task.update(status="failed", error=msg)
        await _notify("fail", name, msg)
        return
    if info["trial"]:
        msg = "仅能获取试听片段（该歌曲需要 VIP 或单曲购买）"
        task.update(status="failed", error=msg)
        await _notify("fail", name, msg)
        return

    safe = re.sub(r'[\\/*?:"<>|]', "", task["song_name"]).strip().rstrip(".")
    path = os.path.join(target_dir, safe + info["ext"])
    tmp = path + ".part"
    quality = info.get("quality_label") or LEVELS.get(info["level"], info["level"])
    task.update(status="downloading", quality=quality, progress=0)
    await _save_tasks()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as http:
            async with http.stream("GET", info["url"], headers={"User-Agent": "Mozilla/5.0"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0)) or info.get("size") or 0
                done = 0
                async with aiofiles.open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(256 * 1024):
                        if tasks.get(sid, {}).get("status") == "cancelled":
                            raise asyncio.CancelledError()
                        await f.write(chunk)
                        done += len(chunk)
                        if total:
                            task["progress"] = min(99, int(done * 100 / total))
        # 覆盖同一首歌的旧文件（含不同格式的副本）
        for old in [p for p in [LocalIndex(base_dir).find(song)] if p and p != path]:
            try:
                os.remove(old)
                lrc = os.path.splitext(old)[0] + ".lrc"
                if os.path.exists(lrc):
                    os.remove(lrc)
            except OSError:
                pass
        os.replace(tmp, path)
    except asyncio.CancelledError:
        if os.path.exists(tmp):
            os.remove(tmp)
        task.update(status="cancelled", error="用户手动取消")
        return
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        msg = f"下载出错: {e}"
        task.update(status="failed", error=msg)
        await _notify("fail", name, msg)
        return

    size = os.path.getsize(path) if os.path.exists(path) else 0
    task.update(status="completed", progress=100, file_path=path, file_size=size, error=None, skipped=False)
    print(f"[网易云] 下载完成: {task['song_name']} ({quality})")

    try:
        from config import config as app_config
        song_info = {
            "name": song.get("name", ""),
            "singer": [a["name"] for a in song.get("singer", [])],
            "album": song.get("album", ""),
            "track": song.get("track") or "",
            "year": song.get("year", ""),
            "cover_url": (song.get("cover") + "?param=800y800")
            if song.get("cover") and app_config.get("download.write_cover", True) else "",
        }
        if app_config.get("download.write_lyrics", True):
            ly = await client.lyric(sid)
            if app_config.get("download.lyric_include_normal", True):
                song_info["lyrics"] = ly.get("lyric", "")
            if app_config.get("download.lyric_include_trans", True):
                song_info["lyrics_trans"] = ly.get("trans", "")
        if app_config.get("download.write_tags", True):
            tag_info = dict(song_info)
            if not app_config.get("download.lyric_write_tag", True):
                tag_info.pop("lyrics", None)
                tag_info.pop("lyrics_trans", None)
            from tagging import write_tags_async
            await write_tags_async(path, tag_info)
        if app_config.get("download.write_lyrics", True) and app_config.get("download.lyric_write_lrc", True):
            from tagging import write_lrc_async
            await write_lrc_async(path, song_info)
    except Exception as e:
        print(f"[网易云] 写入标签/歌词失败: {e}")

    from local_files import human_size
    await _notify("ok", name, quality, human_size(size), os.path.dirname(path))


async def _process(sid):
    t = tasks.get(sid)
    if not t or t.get("status") != "queued":
        return
    try:
        await _download(sid)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[网易云] 下载异常: {e}")
        t.update(status="failed", error=str(e))
    await _save_tasks()


from worker_pool import WorkerPool  # noqa: E402
pool = WorkerPool("网易云", queue, _process)


def apply_config():
    """config.json 保存后调用：按共用的「最大并发下载数」调整网易云工作者"""
    from tasks import max_concurrent
    pool.resize(max_concurrent())


def wake_now():
    _wake.set()


# ---------------- 监控 ----------------
def _load_monitor() -> dict:
    return _read_json(MONITOR_FILE, {})


def _save_monitor(data: dict):
    _write_json(MONITOR_FILE, data)


async def check_playlists() -> int:
    if _check_lock.locked():
        return 0
    found = 0
    async with _check_lock:
        mons = _load_monitor()
        if not mons:
            return 0
        if not await account(force=True):
            print("[网易云] 未登录，跳过歌单监控检查")
            return 0
        for pid, info in mons.items():
            try:
                detail, songs = await client.playlist_songs(pid)
                name = detail["name"] or info.get("title", "")
                known = set(info.get("known_ids", []))
                current = {s["id"] for s in songs}
                new_songs = [s for s in songs if s["id"] not in known]
                if new_songs:
                    found += len(new_songs)
                    print(f"[网易云] 歌单「{name}」发现 {len(new_songs)} 首新歌")
                    target = playlist_dir(name, info.get("download_dir", ""))
                    for s in new_songs:
                        await enqueue(s, target, source=name, date_folder=bool(info.get("date_folder")))
                    await _notify("playlist", f"[网易云] {name}",
                                  [{"name": s["name"], "singer": s["singer"]} for s in new_songs], len(songs))
                info["known_ids"] = sorted(current)
                info["title"] = name
                info["cover"] = detail.get("cover") or info.get("cover", "")
                info["last_check"] = int(time.time())
            except Exception as e:
                print(f"[网易云] 检查歌单 {pid} 失败: {e}")
        _save_monitor(mons)
    return found


async def _monitor_loop():
    from monitor import check_interval_seconds
    try:
        await asyncio.wait_for(_wake.wait(), timeout=30)
    except asyncio.TimeoutError:
        pass
    while True:
        _wake.clear()
        try:
            await check_playlists()
        except Exception as e:
            print(f"[网易云] 监控循环异常: {e}")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=check_interval_seconds())
        except asyncio.TimeoutError:
            pass


# ---------------- 生命周期 ----------------
async def startup():
    persisted = _read_json(TASKS_FILE, {})
    for t in persisted.values():
        if t.get("status") in ("queued", "downloading"):
            t.update(status="failed", error="程序重启导致中断")
    tasks.clear()
    tasks.update(persisted)
    apply_config()
    _bg.append(asyncio.create_task(_monitor_loop()))
    acc = await account(force=True)
    print(f"[网易云] 模块已启动，登录状态: {acc['nickname'] if acc else '未登录'}")


async def shutdown():
    for t in _bg:
        t.cancel()
    await asyncio.gather(*_bg, return_exceptions=True)
    await pool.stop()
    await _save_tasks()
    await client.close()


# ================= API =================
@router.get("/status")
async def status():
    acc = await account(force=True)
    return {"logged_in": bool(acc), "account": acc}


@router.get("/avatar")
async def avatar():
    acc = await account()
    url = (acc or {}).get("avatar")
    if not url:
        raise HTTPException(404, "无头像")
    if url not in _avatar_cache:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as http:
                r = await http.get(url.replace("http://", "https://", 1) + "?param=96y96")
                r.raise_for_status()
                _avatar_cache[url] = (r.content, r.headers.get("content-type", "image/jpeg"))
        except Exception as e:
            raise HTTPException(502, f"获取头像失败: {e}")
    content, mt = _avatar_cache[url]
    return Response(content, media_type=mt, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/qr")
async def qr_create():
    try:
        key = await client.qr_create()
    except NeteaseError as e:
        raise HTTPException(503, str(e))
    return {"key": key, "url": f"https://music.163.com/login?codekey={key}"}


@router.get("/qr/check")
async def qr_check(key: str):
    try:
        res = await client.qr_check(key)
    except Exception as e:
        return {"code": -1, "message": f"检查失败：{e}"}
    code = res.get("code")
    if code == 803:
        if res.get("cookie"):
            client.absorb_cookie_text(res["cookie"])
        acc = await account(force=True)
        return {"code": 803, "message": "登录成功" if acc else "已授权，但未能获取账号信息", "account": acc}
    msg = {800: "二维码已过期", 801: "等待扫码", 802: "已扫码，请在手机上确认"}.get(code) \
        or LOGIN_MESSAGES.get(code) or res.get("message", "")
    return {"code": code, "message": msg, "nickname": res.get("nickname")}


class SmsBody(BaseModel):
    phone: str
    ctcode: str = "86"


def _login_error(res: dict, fallback: str) -> str:
    code = res.get("code")
    return LOGIN_MESSAGES.get(code) or res.get("message") or res.get("msg") or f"{fallback}（code={code}）"


@router.post("/login/sms")
async def login_sms(body: SmsBody):
    phone = re.sub(r"\D", "", body.phone or "")
    if len(phone) < 6:
        raise HTTPException(400, "请输入正确的手机号")
    res = await client.send_sms(phone, body.ctcode)
    if res.get("code") == 200:
        return {"status": "success", "message": "验证码已发送，请注意查收短信"}
    return {"status": "risk" if res.get("code") in RISK_CODES else "failed",
            "code": res.get("code"), "message": _login_error(res, "发送验证码失败")}


class PhoneLoginBody(BaseModel):
    phone: str
    ctcode: str = "86"
    captcha: str = ""
    password: str = ""


@router.post("/login/phone")
async def login_phone(body: PhoneLoginBody):
    phone = re.sub(r"\D", "", body.phone or "")
    if len(phone) < 6:
        raise HTTPException(400, "请输入正确的手机号")
    try:
        res = await client.login_cellphone(phone, body.ctcode, body.captcha, body.password)
    except NeteaseError as e:
        raise HTTPException(400, str(e))
    if res.get("code") == 200:
        acc = await account(force=True)
        if acc:
            return {"status": "success", "account": acc, "message": f"登录成功，欢迎 {acc['nickname']}"}
        return {"status": "failed", "message": "登录接口返回成功，但未拿到有效登录态，请改用扫码或 Cookie 登录"}
    return {"status": "risk" if res.get("code") in RISK_CODES else "failed",
            "code": res.get("code"), "message": _login_error(res, "登录失败")}


class CookieBody(BaseModel):
    cookie: str


@router.post("/login/cookie")
async def login_cookie(body: CookieBody):
    try:
        client.set_cookie_string(body.cookie)
    except NeteaseError as e:
        raise HTTPException(400, str(e))
    acc = await account(force=True)
    if not acc:
        raise HTTPException(400, "Cookie 无效或已过期")
    return {"status": "success", "account": acc, "message": f"登录成功，欢迎 {acc['nickname']}"}


@router.post("/logout")
async def logout():
    client.logout()
    await account(force=True)
    return {"status": "success"}


@router.get("/playlists")
async def playlists():
    acc = await require_login()
    pls = await client.user_playlists(acc["uid"])
    mons = _load_monitor()
    for p in pls:
        p["monitored"] = p["id"] in mons
    return pls


@router.get("/playlist/{pid}")
async def playlist(pid: str):
    await require_login()
    try:
        detail, songs = await client.playlist_songs(pid)
    except NeteaseError as e:
        raise HTTPException(400, str(e))
    mon = _load_monitor().get(pid, {})
    target = playlist_dir(detail["name"], mon.get("download_dir", ""))
    return {"name": detail["name"], "cover": detail.get("cover", ""), "dir": target,
            "songs": annotate(songs, target)}


@router.get("/search")
async def search(keyword: str, page: int = 1, num: int = 30):
    if not keyword.strip():
        raise HTTPException(400, "关键词不能为空")
    songs = await client.search(keyword.strip(), page, num)
    return {"songs": annotate(songs, single_dir()), "dir": single_dir()}


# ---------- 播放：服务端代理（https 页面也能播 http 音源，支持拖动） ----------
_play_cache: dict = {}


@router.get("/play/{sid}")
async def play(sid: str):
    info = await client.song_url(sid, "exhigh")
    if not info:
        raise HTTPException(404, "无法获取播放链接（无版权或需要 VIP）")
    _play_cache[sid] = (info["url"], time.time() + 600)
    return {"url": f"/api/ncm/stream/{sid}", "trial": info["trial"]}


@router.get("/stream/{sid}")
async def stream(sid: str, request: Request):
    cached = _play_cache.get(sid)
    url = cached[0] if cached and cached[1] > time.time() else None
    if not url:
        info = await client.song_url(sid, "exhigh")
        if not info:
            raise HTTPException(404, "无法获取播放链接")
        url = info["url"]
        _play_cache[sid] = (url, time.time() + 600)
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        upstream = await http.send(http.build_request("GET", url, headers=headers), stream=True)
    except Exception as e:
        await http.aclose()
        raise HTTPException(502, f"拉取音频失败: {e}")
    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await http.aclose()
        raise HTTPException(502, f"音频源返回 {upstream.status_code}")
    out = {k: upstream.headers[k] for k in ("content-length", "content-range", "accept-ranges", "content-type")
           if upstream.headers.get(k)}
    out.setdefault("content-type", "audio/mpeg")
    out["cache-control"] = "no-store"

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await http.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out)


# ---------- 下载 ----------
class DownloadBody(BaseModel):
    song: dict
    force: bool = False
    playlist_id: str = ""
    playlist_name: str = ""


@router.post("/download")
async def download(body: DownloadBody):
    await require_download_ready()
    song = body.song
    if not song.get("id"):
        raise HTTPException(400, "缺少歌曲 id")
    if body.playlist_id:
        mon = _load_monitor().get(body.playlist_id, {})
        target = playlist_dir(body.playlist_name or mon.get("title", ""), mon.get("download_dir", ""))
        source = body.playlist_name
        date_folder = bool(mon.get("date_folder"))
    else:
        target, source, date_folder = single_dir(), "搜索", False
    t = tasks.get(song["id"])
    if t and t.get("status") in ("queued", "downloading"):
        return {"status": "skipped", "message": "已在下载队列中"}
    if not body.force:
        local = LocalIndex(target).find(song)
        if local:
            return {"status": "local_exists", "local": _file_info(local)}
    await enqueue(song, target, force=body.force or bool(t), source=source, date_folder=date_folder)
    return {"status": "queued"}


@router.post("/playlist/{pid}/download")
async def download_playlist(pid: str):
    await require_login()
    detail, songs = await client.playlist_songs(pid)
    mon = _load_monitor().get(pid, {})
    target = playlist_dir(detail["name"], mon.get("download_dir", ""))
    idx = LocalIndex(target)
    added = skipped = 0
    for s in songs:
        if idx.find(s):
            skipped += 1
            continue
        if await enqueue(s, target, source=detail["name"], date_folder=bool(mon.get("date_folder"))):
            added += 1
        else:
            skipped += 1
    return {"message": f"「{detail['name']}」已加入 {added} 首，跳过 {skipped} 首（本地已有或已在队列）",
            "added": added, "skipped": skipped, "dir": target}


@router.get("/tasks")
async def get_tasks():
    out = []
    for sid, t in tasks.items():
        item = {k: v for k, v in t.items() if k != "song"}
        item["id"] = sid
        item["cover"] = (t.get("song") or {}).get("cover", "")
        out.append(item)
    out.sort(key=lambda x: x.get("created", 0), reverse=True)
    return out


@router.post("/task/{sid}/{action}")
async def task_action(sid: str, action: str):
    t = tasks.get(sid)
    if not t:
        raise HTTPException(404, "任务不存在")
    if action == "cancel":
        if t.get("status") in ("queued", "downloading"):
            t.update(status="cancelled", error="用户手动取消")
    elif action == "retry":
        if t.get("status") in ("failed", "cancelled"):
            await enqueue(t["song"], t.get("download_dir", ""), force=True)
    elif action == "remove":
        if t.get("status") == "downloading":
            raise HTTPException(400, "下载中的任务请先取消")
        del tasks[sid]
    else:
        raise HTTPException(400, "未知操作")
    await _save_tasks()
    return {"status": "success"}


@router.post("/tasks/clear")
async def clear_tasks(kind: str = "finished"):
    for sid in [s for s, t in tasks.items() if t.get("status") not in ("queued", "downloading")
                and (kind == "finished" or t.get("status") == kind)]:
        del tasks[sid]
    await _save_tasks()
    return {"status": "success"}


@router.post("/tasks/retry_failed")
async def retry_failed():
    n = 0
    for sid in [s for s, t in tasks.items() if t.get("status") == "failed"]:
        if await enqueue(tasks[sid]["song"], tasks[sid].get("download_dir", ""), force=True):
            n += 1
    return {"message": f"已重新加入 {n} 个任务"}


# ---------- 监控 ----------
@router.get("/monitor")
async def monitor_list():
    return {pid: {"title": v.get("title"), "cover": v.get("cover", ""),
                  "download_dir": v.get("download_dir", ""),
                  "date_folder": bool(v.get("date_folder", False)),
                  "resolved_dir": playlist_dir(v.get("title", ""), v.get("download_dir", "")),
                  "count": len(v.get("known_ids", [])), "last_check": v.get("last_check")}
            for pid, v in _load_monitor().items()}


@router.post("/monitor/{pid}")
async def monitor_toggle(pid: str):
    await require_login()
    mons = _load_monitor()
    if pid in mons:
        title = mons.pop(pid).get("title", "")
        _save_monitor(mons)
        return {"monitored": False, "message": f"已取消监控「{title}」"}
    detail, songs = await client.playlist_songs(pid)
    mons[pid] = {"title": detail["name"], "cover": detail.get("cover", ""),
                 "known_ids": sorted(s["id"] for s in songs),
                 "download_dir": "", "date_folder": False, "last_check": int(time.time())}
    _save_monitor(mons)
    return {"monitored": True,
            "message": f"已开始监控「{detail['name']}」（当前 {len(songs)} 首），之后新加入的歌曲会自动下载"}


@router.put("/monitor")
async def monitor_update(data: dict):
    mons = _load_monitor()
    for pid, v in data.items():
        if pid in mons and isinstance(v, dict):
            if isinstance(v.get("download_dir", ""), str):
                mons[pid]["download_dir"] = v.get("download_dir", "").strip()
            if "date_folder" in v:
                mons[pid]["date_folder"] = str(v["date_folder"]).lower() in ("true", "1", "yes", "on")
    _save_monitor(mons)
    return {"status": "success"}
