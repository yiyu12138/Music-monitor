"""自定义下载源：兼容洛雪音乐 (LX Music) 自定义源脚本

在设置里导入 LX 格式的自定义源 JS 脚本（链接 / 粘贴 / 上传），下载时优先用这些源
获取歌曲直链，全部失败再回退到账号官方渠道（由调用方负责回退）。

脚本运行在内嵌的 QuickJS 里，不依赖 Node.js。宿主提供 LX 脚本需要的 API：
  lx.on / lx.send / lx.request / lx.utils(crypto/buffer/zlib) / lx.EVENT_NAMES / lx.version / lx.env
  以及 setTimeout / clearTimeout / console。

每个源一个独立 JS 上下文；QuickJS 上下文不是线程安全的，所以所有 JS 调用都在
同一个专用线程里执行，网络请求由 Python 发起（异步），结果再回调给脚本。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import httpx

try:
    import quickjs  # type: ignore
except Exception:  # pragma: no cover - 运行环境缺少引擎时整个功能降级为不可用
    quickjs = None

DATA_DIR = "data"

SOURCES_DIR = os.path.join(DATA_DIR, "sources")
INDEX_FILE = os.path.join(SOURCES_DIR, "sources.json")

# 平台在 LX 里的代号
PLATFORM_KEYS = {"qq": "tx", "netease": "wy"}
PLATFORM_NAMES = {"tx": "QQ 音乐", "wy": "网易云", "kg": "酷狗", "kw": "酷我", "mg": "咪咕", "local": "本地"}
# 从高到低请求的音质；每个源只尝试它声明支持的音质
QUALITY_ORDER = ["flac24bit", "flac", "320k", "128k"]
QUALITY_INFO = {
    "flac24bit": ("Hi-Res 无损", ".flac"),
    "flac": ("无损", ".flac"),
    "320k": ("320k", ".mp3"),
    "192k": ("192k", ".mp3"),
    "128k": ("128k", ".mp3"),
}

INIT_TIMEOUT = 15.0
REQUEST_TIMEOUT = 20.0
MAX_SCRIPT_SIZE = 2 * 1024 * 1024

# 解析脚本头部注释里的元信息：@name @description @version @author @homepage
_META_RE = re.compile(r"^\s*\*?\s*@(name|description|version|author|homepage)\s+(.+?)\s*$", re.M)


def normalize_script(script: str) -> str:
    """与洛雪客户端一致：去 BOM、统一换行为 LF、去首尾空白（部分源会对自身内容做 md5 校验）"""
    s = (script or "").lstrip("\ufeff")
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def parse_meta(script: str) -> Dict[str, str]:
    head = script[:4096]
    m = re.search(r"/\*[\s\S]*?\*/", head)
    meta: Dict[str, str] = {}
    if m:
        for k, v in _META_RE.findall(m.group(0)):
            meta.setdefault(k, v.strip()[:200])
    return meta


# --------------------------------------------------------------------------- 网络

def describe_error(e: BaseException) -> str:
    """取异常链最底层的原因（httpx 的 ConnectError 等 str() 常常是空的）"""
    root = e
    seen = set()
    while (root.__cause__ or root.__context__) and id(root) not in seen:
        seen.add(id(root))
        root = root.__cause__ or root.__context__
    for x in (e, root):
        msg = str(x).strip()
        if msg:
            return f"{type(x).__name__}: {msg}"[:300]
    return type(root).__name__


async def http_request(method: str, url: str, *, timeout: float = 20.0, **kw) -> httpx.Response:
    """发请求；连接/握手失败时改用 IPv4 再试一次。

    有些网络能解析出 IPv6 地址但实际连不通（典型表现是 SSL: UNEXPECTED_EOF），
    异步请求不会像 curl 那样自动回退 IPv4，这里手动兜底。
    """
    retryable = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError)
    last: Optional[BaseException] = None
    # 依次：默认 → 强制 IPv4 → 稍等后再强制 IPv4（应对 GitHub 等站点偶发的握手中断）
    for attempt, ipv4 in enumerate((False, True, True)):
        if attempt == 2:
            await asyncio.sleep(1.0)
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0") if ipv4 else None
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport) as http:
                return await http.request(method, url, **kw)
        except retryable as e:
            last = e
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------------- JS 预置环境

_PRELUDE = r"""
(function(){
  var g = globalThis;
  g.__lx = { handlers: {}, cbs: {}, timers: {}, seq: 0, inited: null };
  function nid(){ g.__lx.seq += 1; return 'c' + g.__lx.seq; }
  function S(x){ try { return typeof x === 'string' ? x : JSON.stringify(x); } catch(e){ return String(x); } }
  g.console = {
    log: function(){ __host_log('log', Array.prototype.map.call(arguments, S).join(' ')); },
    info: function(){ __host_log('info', Array.prototype.map.call(arguments, S).join(' ')); },
    warn: function(){ __host_log('warn', Array.prototype.map.call(arguments, S).join(' ')); },
    error: function(){ __host_log('error', Array.prototype.map.call(arguments, S).join(' ')); },
    debug: function(){ __host_log('debug', Array.prototype.map.call(arguments, S).join(' ')); }
  };
  // 洛雪源常用但我们不需要真正实现的 console 方法：一律当作普通日志或空操作，避免 "not a function"
  ['group', 'groupCollapsed', 'trace', 'dir', 'dirxml', 'table'].forEach(function(k){ g.console[k] = g.console.log; });
  ['groupEnd', 'time', 'timeEnd', 'timeLog', 'count', 'countReset', 'clear', 'profile', 'profileEnd'].forEach(function(k){ g.console[k] = function(){}; });
  g.console.assert = function(c){ if (!c) g.console.error.apply(null, Array.prototype.slice.call(arguments, 1)); };
  g.setTimeout = function(fn, ms){ var id = nid(); g.__lx.timers[id] = fn; __host_timer(id, Number(ms) || 0); return id; };
  g.clearTimeout = function(id){ delete g.__lx.timers[id]; };
  g.setInterval = function(){ return 0; };
  g.clearInterval = function(){};
  g.__lx_fire_timer = function(id){ var fn = g.__lx.timers[id]; delete g.__lx.timers[id]; if (typeof fn === 'function') fn(); };

  // 二进制统一用 hex 字符串在宿主与脚本之间传递
  function toHex(s){ var h=''; for (var i=0;i<s.length;i++){ var c=s.charCodeAt(i)&255; h+=(c<16?'0':'')+c.toString(16);} return h; }
  function Buf(hex){ this.__hex = hex || ''; this.length = this.__hex.length/2; }
  Buf.prototype.toString = function(enc){ return __host_buf('toString', this.__hex, enc || 'utf8'); };
  Buf.isBuffer = function(b){ return b instanceof Buf; };
  function bufFrom(data, enc){
    if (data instanceof Buf) return new Buf(data.__hex);
    if (Array.isArray(data)) { var h=''; for (var i=0;i<data.length;i++){ var c=data[i]&255; h+=(c<16?'0':'')+c.toString(16);} return new Buf(h); }
    return new Buf(__host_buf('from', String(data), enc || 'utf8'));
  }
  var lx = {
    EVENT_NAMES: { request: 'request', inited: 'inited', updateAlert: 'updateAlert' },
    version: '2.0.0',
    env: 'desktop',
    currentScriptInfo: g.__lx_script_info || {},
    on: function(ev, h){ g.__lx.handlers[ev] = h; return Promise.resolve(); },
    send: function(ev, data){
      if (ev === 'inited') g.__lx.inited = data || {};
      __host_send(String(ev), S(data || {}));
      return Promise.resolve();
    },
    request: function(url, opts, cb){
      var id = nid();
      g.__lx.cbs[id] = cb;
      __host_http(id, String(url), S(opts || {}));
      return function(){ delete g.__lx.cbs[id]; };
    },
    utils: {
      buffer: { from: bufFrom, bufToString: function(b, enc){ return (b instanceof Buf ? b : bufFrom(b)).toString(enc); } },
      crypto: {
        md5: function(s){ return __host_crypto('md5', String(s), ''); },
        sha1: function(s){ return __host_crypto('sha1', String(s), ''); },
        sha256: function(s){ return __host_crypto('sha256', String(s), ''); },
        randomBytes: function(n){ return new Buf(__host_crypto('random', String(n), '')); },
        aesEncrypt: function(buf, mode, key, iv){
          var r = __host_crypto('aes', S({b: (buf instanceof Buf ? buf : bufFrom(buf)).__hex, m: mode, k: (key instanceof Buf ? key : bufFrom(key)).__hex, i: iv ? (iv instanceof Buf ? iv : bufFrom(iv)).__hex : ''}), '');
          return new Buf(r);
        },
        rsaEncrypt: function(buf, key){ return new Buf(__host_crypto('rsa', S({b: (buf instanceof Buf ? buf : bufFrom(buf)).__hex, k: String(key)}), '')); }
      },
      zlib: {
        inflate: function(buf){ return Promise.resolve(new Buf(__host_buf('inflate', (buf instanceof Buf ? buf : bufFrom(buf)).__hex, ''))); },
        deflate: function(buf){ return Promise.resolve(new Buf(__host_buf('deflate', (buf instanceof Buf ? buf : bufFrom(buf)).__hex, ''))); }
      }
    }
  };
  g.lx = lx;
  g.__lx_dispatch = function(id, err, resp, body){
    var cb = g.__lx.cbs[id]; delete g.__lx.cbs[id];
    if (typeof cb !== 'function') return;
    if (err) { cb(new Error(err), null, null); return; }
    cb(null, resp, body);
  };
  g.__lx_call = function(reqId, payload){
    var h = g.__lx.handlers['request'];
    var done = function(ok, v){
      if (!ok) { var m = v && v.message ? v.message : S(v); if (!m || m === '{}' || m === 'undefined') m = '源返回错误（无具体原因）'; __host_result(reqId, 'err', m); return; }
      __host_result(reqId, 'ok', S(v));
    };
    if (typeof h !== 'function') { done(false, '该源未注册 request 处理函数'); return; }
    try {
      var r = h(payload);
      if (r && typeof r.then === 'function') r.then(function(v){ done(true, v); }, function(e){ done(false, e); });
      else done(true, r);
    } catch (e) { done(false, e); }
  };
})();
"""


class LxRuntime:
    """一个自定义源脚本的运行时（独立 QuickJS 上下文）"""

    def __init__(self, source_id: str, script: str, info: Dict[str, Any], executor: ThreadPoolExecutor):
        self.id = source_id
        self.script = script
        self.info = info
        self._ex = executor
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ctx = None
        self._results: Dict[str, asyncio.Future] = {}
        self._inited: Optional[asyncio.Future] = None
        self.sources: Dict[str, Any] = {}
        self.logs: List[str] = []
        self.error: Optional[str] = None

    # ---- 线程内执行 JS
    async def _js(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._ex, fn, *args)

    def _pump(self):
        # 跑完 Promise 微任务
        for _ in range(10000):
            try:
                if not self._ctx.execute_pending_job():
                    break
            except Exception as e:  # 微任务里抛错不影响宿主
                self._log("error", f"脚本异步错误: {e}")
                break

    def _eval(self, src: str):
        try:
            return self._ctx.eval(src)
        finally:
            self._pump()

    def _log(self, level: str, msg: str):
        line = f"[{level}] {msg}"[:500]
        self.logs.append(line)
        del self.logs[:-50]

    # ---- 宿主回调（在 JS 线程里被调用）
    def _on_send(self, ev, data):
        if ev == "inited":
            try:
                payload = json.loads(data) if data else {}
            except Exception:
                payload = {}
            self.sources = payload.get("sources") or {}
            fut = self._inited
            if fut and self._loop and not fut.done():
                self._loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(True))
        elif ev == "updateAlert":
            self._log("info", f"源提示更新: {str(data)[:200]}")
        return None

    def _on_http(self, cid, url, opts):
        try:
            o = json.loads(opts) if opts else {}
        except Exception:
            o = {}
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._do_http(cid, url, o), self._loop)
        return None

    def _on_timer(self, tid, ms):
        if self._loop:
            delay = max(0.0, min(float(ms) / 1000.0, REQUEST_TIMEOUT))
            self._loop.call_soon_threadsafe(
                lambda: self._loop.call_later(delay, lambda: asyncio.ensure_future(self._fire_timer(tid)))
            )
        return None

    def _on_result(self, rid, status, value):
        fut = self._results.get(rid)
        if fut and self._loop:
            def _set():
                if fut.done():
                    return
                if status == "ok":
                    try:
                        fut.set_result(json.loads(value) if value and value[:1] in '{["' else value)
                    except Exception:
                        fut.set_result(value)
                else:
                    fut.set_exception(RuntimeError(str(value)[:300] or "源返回错误"))
            self._loop.call_soon_threadsafe(_set)
        return None

    def _on_log(self, level, msg):
        self._log(level, str(msg))
        return None

    @staticmethod
    def _on_buf(op, data, enc):
        try:
            if op == "from":
                if enc in ("hex",):
                    return data.lower()
                if enc in ("base64",):
                    return base64.b64decode(data + "=" * (-len(data) % 4)).hex()
                if enc in ("binary", "latin1"):
                    return data.encode("latin-1", "replace").hex()
                return data.encode("utf-8").hex()
            raw = bytes.fromhex(data)
            if op == "toString":
                if enc == "hex":
                    return raw.hex()
                if enc == "base64":
                    return base64.b64encode(raw).decode()
                if enc in ("binary", "latin1"):
                    return raw.decode("latin-1")
                return raw.decode("utf-8", "replace")
            if op == "inflate":
                try:
                    return zlib.decompress(raw).hex()
                except zlib.error:
                    return zlib.decompress(raw, -15).hex()
            if op == "deflate":
                return zlib.compress(raw).hex()
        except Exception:
            return ""
        return ""

    @staticmethod
    def _on_crypto(op, data, extra):
        try:
            if op in ("md5", "sha1", "sha256"):
                return getattr(hashlib, op)(data.encode("utf-8")).hexdigest()
            if op == "random":
                return secrets.token_bytes(max(0, min(int(data), 4096))).hex()
            if op == "aes":
                from cryptography.hazmat.primitives import padding
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                p = json.loads(data)
                raw, key = bytes.fromhex(p["b"]), bytes.fromhex(p["k"])
                mode = str(p.get("m") or "").lower()
                if "cbc" in mode:
                    padder = padding.PKCS7(128).padder()
                    raw = padder.update(raw) + padder.finalize()
                    enc = Cipher(algorithms.AES(key), modes.CBC(bytes.fromhex(p["i"]))).encryptor()
                else:
                    if "nopadding" not in mode:
                        padder = padding.PKCS7(128).padder()
                        raw = padder.update(raw) + padder.finalize()
                    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
                return (enc.update(raw) + enc.finalize()).hex()
            if op == "rsa":
                from cryptography.hazmat.primitives import serialization
                p = json.loads(data)
                pub = serialization.load_pem_public_key(p["k"].encode())
                raw = bytes.fromhex(p["b"])
                n = pub.public_numbers().n
                k = (n.bit_length() + 7) // 8
                m = int.from_bytes(raw.rjust(k, b"\x00"), "big")
                return pow(m, pub.public_numbers().e, n).to_bytes(k, "big").hex()
        except Exception:
            return ""
        return ""

    # ---- 异步动作
    async def _do_http(self, cid: str, url: str, o: Dict[str, Any]):
        err, resp, body = None, None, None
        try:
            method = str(o.get("method") or "GET").upper()
            headers = {str(k): str(v) for k, v in (o.get("headers") or {}).items()}
            headers.setdefault("User-Agent", "lx-music-desktop/2.0.0")
            timeout = min(float(o.get("timeout") or REQUEST_TIMEOUT * 1000) / 1000.0, REQUEST_TIMEOUT)
            kw: Dict[str, Any] = {}
            if o.get("form") is not None:
                kw["data"] = o["form"]
            elif o.get("formData") is not None:
                kw["data"] = o["formData"]
            elif o.get("body") is not None:
                b = o["body"]
                if isinstance(b, (dict, list)):
                    kw["json"] = b
                else:
                    kw["content"] = str(b).encode()
            r = await http_request(method, url, timeout=timeout, headers=headers, **kw)
            text = r.text
            try:
                body = json.loads(text)
            except Exception:
                body = text
            resp = {"statusCode": r.status_code, "statusMessage": r.reason_phrase,
                    "headers": dict(r.headers), "body": body, "raw": None}
        except Exception as e:
            err = f"请求失败: {describe_error(e)}"[:300]
        await self._js(self._dispatch, cid, err, resp, body)

    def _dispatch(self, cid, err, resp, body):
        self._eval("__lx_dispatch(%s, %s, %s, %s)" % (
            json.dumps(cid), json.dumps(err), json.dumps(resp, ensure_ascii=False),
            json.dumps(body, ensure_ascii=False)))

    async def _fire_timer(self, tid):
        await self._js(lambda: self._eval("__lx_fire_timer(%s)" % json.dumps(tid)))

    # ---- 生命周期
    def _setup(self):
        ctx = quickjs.Context()
        ctx.set_memory_limit(64 * 1024 * 1024)
        # 注意：不能 set_time_limit —— 设了时间限制后 QuickJS 不允许回调 Python（宿主 API 全部失效）。
        # 超时由外层 asyncio.wait_for 兜底（初始化 15s、单次取链 25s）。
        for name, fn in (
            ("__host_send", self._on_send), ("__host_http", self._on_http), ("__host_timer", self._on_timer),
            ("__host_result", self._on_result), ("__host_log", self._on_log),
            ("__host_buf", self._on_buf), ("__host_crypto", self._on_crypto),
        ):
            ctx.add_callable(name, fn)
        self._ctx = ctx
        self._eval("globalThis.__lx_script_info = %s;" % json.dumps({
            "name": self.info.get("name", ""), "description": self.info.get("description", ""),
            "version": self.info.get("version", ""), "author": self.info.get("author", ""),
            "homepage": self.info.get("homepage", ""), "rawScript": self.script,
        }, ensure_ascii=False))
        self._eval(_PRELUDE)
        self._eval(self.script)

    async def start(self) -> bool:
        if quickjs is None:
            self.error = "缺少 JS 引擎（quickjs），请重新安装依赖"
            return False
        self._loop = asyncio.get_running_loop()
        self._inited = self._loop.create_future()
        try:
            await self._js(self._setup)
            await asyncio.wait_for(self._inited, INIT_TIMEOUT)
        except asyncio.TimeoutError:
            self.error = "脚本初始化超时（没有调用 lx.send('inited')）"
            return False
        except Exception as e:
            self.error = f"脚本加载失败: {describe_error(e)}"[:300]
            return False
        self.error = None
        return True

    def supports(self, platform: str) -> List[str]:
        src = self.sources.get(platform) or {}
        if not isinstance(src, dict):
            return []
        actions = src.get("actions") or ["musicUrl"]
        if "musicUrl" not in actions:
            return []
        return [q for q in (src.get("qualitys") or []) if isinstance(q, str)]

    async def music_url(self, platform: str, quality: str, music_info: Dict[str, Any]) -> str:
        rid = secrets.token_hex(6)
        fut = self._loop.create_future()
        self._results[rid] = fut
        mi = dict(music_info or {})
        mi["source"] = platform
        mi.setdefault("songmid", "")
        mi.setdefault("id", mi["songmid"])
        mi.setdefault("name", "")
        mi.setdefault("singer", "")
        mi.setdefault("albumName", "")
        mi.setdefault("albumId", "")
        mi.setdefault("interval", "")
        mi.setdefault("img", "")
        mi.setdefault("types", [])
        mi.setdefault("_types", {})
        mi.setdefault("typeUrl", {})
        if platform == "tx":
            mi.setdefault("strMediaMid", mi["songmid"])
            mi.setdefault("albumMid", "")
        payload = {"source": platform, "action": "musicUrl",
                   "info": {"type": quality, "musicInfo": mi}}
        try:
            await self._js(lambda: self._eval("__lx_call(%s, %s)" % (
                json.dumps(rid), json.dumps(payload, ensure_ascii=False))))
            value = await asyncio.wait_for(fut, REQUEST_TIMEOUT + 5)
        finally:
            self._results.pop(rid, None)
        if isinstance(value, dict):
            value = value.get("url") or value.get("data") or ""
        url = str(value or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise RuntimeError("源未返回有效链接")
        return url


# --------------------------------------------------------------------------- 源管理

class SourceManager:
    def __init__(self):
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lx-js")
        self._items: List[Dict[str, Any]] = []
        self._rt: Dict[str, LxRuntime] = {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return quickjs is not None

    # ---- 持久化
    def _load_index(self):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                self._items = json.load(f) or []
        except FileNotFoundError:
            self._items = []
        except Exception as e:
            print(f"[下载源] 读取源列表失败: {e}")
            self._items = []

    def _save_index(self):
        os.makedirs(SOURCES_DIR, exist_ok=True)
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, INDEX_FILE)

    def _script_path(self, sid: str) -> str:
        return os.path.join(SOURCES_DIR, f"{sid}.js")

    def _find(self, sid: str) -> Optional[Dict[str, Any]]:
        return next((x for x in self._items if x["id"] == sid), None)

    # ---- 启动
    async def startup(self):
        self._load_index()
        if not self.available:
            print("[下载源] 未安装 quickjs，自定义下载源不可用")
            return
        for item in self._items:
            if item.get("enabled", True):
                await self._load_runtime(item)
        n = sum(1 for x in self._items if x["id"] in self._rt)
        if self._items:
            print(f"[下载源] 已加载 {n}/{len(self._items)} 个自定义下载源")

    async def _load_runtime(self, item: Dict[str, Any]) -> Optional[LxRuntime]:
        self._rt.pop(item["id"], None)
        try:
            with open(self._script_path(item["id"]), "r", encoding="utf-8", newline="") as f:
                script = normalize_script(f.read())
        except Exception as e:
            item["error"] = f"脚本文件丢失: {e}"
            return None
        rt = LxRuntime(item["id"], script, item, self._ex)
        ok = await rt.start()
        item["error"] = None if ok else rt.error
        item["platforms"] = {k: v.get("qualitys", []) for k, v in rt.sources.items() if isinstance(v, dict)} if ok else {}
        if ok:
            self._rt[item["id"]] = rt
        return rt if ok else None

    # ---- 增删改
    async def add(self, script: str, url: str = "") -> Dict[str, Any]:
        script = normalize_script(script)
        if not script:
            raise ValueError("脚本内容为空")
        if len(script.encode("utf-8")) > MAX_SCRIPT_SIZE:
            raise ValueError("脚本太大（超过 2MB）")
        # 只拦明显不是 JS 的内容（例如把网页链接当成脚本链接）；
        # 混淆过的源连 "lx" 字样都可能没有，是否合格以「能否完成 inited 初始化」为准
        if re.match(r"^\s*(<!doctype|<html|<\?xml|<head|<body)", script, re.I):
            raise ValueError("这是网页内容，不是脚本。链接导入请使用脚本的原始链接（raw，以 .js 结尾）")
        if not self.available:
            raise ValueError("缺少 JS 引擎（quickjs），无法加载自定义源")
        meta = parse_meta(script)
        sid = hashlib.md5(script.encode("utf-8")).hexdigest()[:12]
        async with self._lock:
            if self._find(sid):
                raise ValueError("这个源已经添加过了")
            os.makedirs(SOURCES_DIR, exist_ok=True)
            with open(self._script_path(sid), "w", encoding="utf-8", newline="\n") as f:
                f.write(script)
            item = {
                "id": sid,
                "name": meta.get("name") or "未命名源",
                "description": meta.get("description", ""),
                "version": meta.get("version", ""),
                "author": meta.get("author", ""),
                "homepage": meta.get("homepage", ""),
                "url": url,
                "enabled": True,
                "added_at": int(time.time()),
                "error": None,
                "platforms": {},
            }
            rt = await self._load_runtime(item)
            if rt is None:
                try:
                    os.remove(self._script_path(sid))
                except OSError:
                    pass
                raise ValueError(item.get("error") or "脚本加载失败")
            self._items.append(item)
            self._save_index()
        return self.public(item)

    async def add_from_url(self, url: str) -> Dict[str, Any]:
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise ValueError("请填写 http(s) 开头的脚本链接")
        try:
            r = await http_request("GET", url, headers={"User-Agent": "lx-music-desktop/2.0.0"})
        except Exception as e:
            raise ValueError(f"下载脚本失败（{describe_error(e)}）。可以改用加速链接，或下载 .js 后用「本地文件」导入")
        if r.status_code != 200:
            raise ValueError(f"下载脚本失败：HTTP {r.status_code}")
        return await self.add(r.text, url=url)

    async def remove(self, sid: str):
        async with self._lock:
            item = self._find(sid)
            if not item:
                raise KeyError(sid)
            self._items.remove(item)
            self._rt.pop(sid, None)
            try:
                os.remove(self._script_path(sid))
            except OSError:
                pass
            self._save_index()

    async def set_enabled(self, sid: str, enabled: bool) -> Dict[str, Any]:
        async with self._lock:
            item = self._find(sid)
            if not item:
                raise KeyError(sid)
            item["enabled"] = bool(enabled)
            if enabled:
                await self._load_runtime(item)
            else:
                self._rt.pop(sid, None)
            self._save_index()
            return self.public(item)

    async def reload(self, sid: str) -> Dict[str, Any]:
        async with self._lock:
            item = self._find(sid)
            if not item:
                raise KeyError(sid)
            if item.get("url"):
                try:
                    r = await http_request("GET", item["url"], headers={"User-Agent": "lx-music-desktop/2.0.0"})
                    if r.status_code == 200 and r.text.strip():
                        text = normalize_script(r.text)
                        with open(self._script_path(sid), "w", encoding="utf-8", newline="\n") as f:
                            f.write(text)
                        meta = parse_meta(text)
                        for k in ("name", "description", "version", "author", "homepage"):
                            if meta.get(k):
                                item[k] = meta[k]
                except Exception as e:
                    print(f"[下载源] 更新脚本失败（继续用本地副本）: {describe_error(e)}")
            await self._load_runtime(item)
            self._save_index()
            return self.public(item)

    async def move(self, sid: str, delta: int):
        async with self._lock:
            item = self._find(sid)
            if not item:
                raise KeyError(sid)
            i = self._items.index(item)
            j = max(0, min(len(self._items) - 1, i + delta))
            self._items.insert(j, self._items.pop(i))
            self._save_index()

    def public(self, item: Dict[str, Any]) -> Dict[str, Any]:
        rt = self._rt.get(item["id"])
        return {
            **{k: item.get(k) for k in ("id", "name", "description", "version", "author", "homepage", "url", "enabled", "added_at")},
            "loaded": rt is not None,
            "error": item.get("error"),
            "platforms": [
                {"key": k, "name": PLATFORM_NAMES.get(k, k), "qualitys": v}
                for k, v in (item.get("platforms") or {}).items()
            ],
            "logs": (rt.logs[-10:] if rt else []),
        }

    def list(self) -> List[Dict[str, Any]]:
        return [self.public(x) for x in self._items]

    def has_usable(self, platform: str) -> bool:
        """是否有已启用、已加载、且支持该平台取链的源"""
        key = PLATFORM_KEYS.get(platform, platform)
        return any(
            item.get("enabled", True) and item["id"] in self._rt and self._rt[item["id"]].supports(key)
            for item in self._items
        )

    # ---- 解析
    async def resolve(self, platform: str, music_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """按源顺序、音质从高到低取直链；全部失败返回 None（由调用方回退官方渠道）"""
        key = PLATFORM_KEYS.get(platform, platform)
        for item in list(self._items):
            rt = self._rt.get(item["id"])
            if not item.get("enabled", True) or rt is None:
                continue
            qualitys = rt.supports(key)
            if not qualitys:
                continue
            for q in [x for x in QUALITY_ORDER if x in qualitys]:
                try:
                    url = await rt.music_url(key, q, music_info)
                except Exception as e:
                    print(f"[下载源] {item['name']} 获取 {PLATFORM_NAMES.get(key, key)} {q} 失败: {e}")
                    continue
                label, ext = QUALITY_INFO.get(q, (q, ".mp3"))
                ext = _guess_ext(url) or ext
                print(f"[下载源] 使用「{item['name']}」获取到 {label} 链接")
                return {"url": url, "quality": f"{label}（{item['name']}）", "extension": ext,
                        "source": item["name"], "level": q}
        return None

    async def test(self, sid: str, platform: str, music_info: Dict[str, Any]) -> Dict[str, Any]:
        rt = self._rt.get(sid)
        if rt is None:
            raise ValueError("该源未加载")
        key = PLATFORM_KEYS.get(platform, platform)
        qualitys = rt.supports(key)
        if not qualitys:
            raise ValueError(f"该源不支持 {PLATFORM_NAMES.get(key, key)}")
        q = next((x for x in ["320k", "128k", "flac"] if x in qualitys), qualitys[0])
        url = await rt.music_url(key, q, music_info)
        return {"quality": q, "url": url}


def _guess_ext(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext in (".flac", ".mp3", ".m4a", ".ogg", ".ape", ".wav"):
        if path.endswith(ext):
            return ext
    return ""


manager = SourceManager()
