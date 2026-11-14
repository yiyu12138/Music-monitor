import asyncio
import httpx
import json
import time
import urllib.parse
from typing import Dict, Any, Optional
from config import config

# 歌单结果汇报里各状态的显示文案（v1.2.3 重写 Bark 推送时被误删，v1.3.4 补回）
STATUS_LABELS = {
    "completed": "✅已下载",
    "local_exists": "⏭本地已存在，跳过",
    "failed": "❌下载失败",
    "waiting": "⏳等待重试",
    "pending": "⬇️下载中",
    "cancelled": "🚫已取消",
    "unknown": "⬜未下载",
}

# 汇报里各状态的排列顺序
STATUS_SUMMARY_ORDER = ["completed", "local_exists", "failed", "waiting", "pending", "cancelled", "unknown"]


# 汇报里「没下成」的短前缀，比完整文案更省地方
_STATUS_SHORT = {
    "failed": "❌",
    "waiting": "⏳",
    "pending": "⬇️",
    "cancelled": "🚫",
    "unknown": "⬜",
}


def _split_by_bytes(text: str, max_bytes: int) -> list:
    """按 UTF-8 字节上限分段，优先在换行处断开（渠道通常限制消息字节数）"""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate.encode("utf-8")) > max_bytes:
            chunks.append(current)
            current = line
        else:
            current = candidate
        # 单行自身就超限时硬切
        while len(current.encode("utf-8")) > max_bytes:
            head = current.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
            if not head:
                head = current[:1]
            chunks.append(head)
            current = current[len(head):]
    if current:
        chunks.append(current)
    return chunks


def _split_by_chars(text: str, max_chars: int) -> list:
    """按字符数分段（Telegram 单条限制 4096 个 UTF-16 码元，这里按字符近似）"""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = line
        else:
            current = candidate
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        chunks.append(current)
    return chunks




class NotificationManager:
    """通知管理模块，支持多种通知方式"""
    def __init__(self):
        self._config = config
        self._clients = {}
    
    async def _get_client(self, client_id: str = "default", proxy: str = "") -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端（代理不同时各建一个，避免互相串线）"""
        key = f"{client_id}|{proxy}" if proxy else client_id
        if key not in self._clients:
            kwargs = {"timeout": 15.0}
            if proxy:
                kwargs["proxy"] = proxy  # httpx >= 0.28；旧版参数名是 proxies
            try:
                self._clients[key] = httpx.AsyncClient(**kwargs)
            except TypeError:
                if proxy:
                    kwargs.pop("proxy", None)
                    kwargs["proxies"] = proxy
                self._clients[key] = httpx.AsyncClient(**kwargs)
        return self._clients[key]
    
    async def _send_webhook(self, message: str, title: str = "QQ音乐下载器通知") -> bool:
        """发送Webhook通知"""
        webhook_config = self._config.get("notification.webhook")
        if not webhook_config or not webhook_config.get("enabled", False):
            return False
        
        url = webhook_config.get("url")
        if not url:
            return False
        
        try:
            client = await self._get_client("webhook")
            payload = {
                "title": title,
                "message": message,
                "timestamp": int(time.time())
            }
            
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"发送Webhook通知失败: {e}")
            return False
    
    async def _send_bark(self, message: str, title: str = "QQ音乐下载器通知") -> bool:
        """发送Bark通知

        用 POST /push 提交 JSON，而不是 GET /{key}/{title}/{body}：
        GET 路径里正文中的 "/" 会被 Bark 当成路径分隔符，整条推送会 404。
        下载位置、URL 这类含斜杠或长链接的字段最常踩这个坑，整条通知直接丢失。
        """
        bark_config = self._config.get("notification.bark")
        if not bark_config or not bark_config.get("enabled", False):
            return False

        server_url = (bark_config.get("server_url") or "https://api.day.app").strip().rstrip("/")
        # POST 接口要求 device_key 与服务端完全一致，不能像 GET 路径那样靠 strip("/") 容错，
        # 否则尾部多带的 "/" 会变成另一个设备，直接返回 400
        device_key = (bark_config.get("device_key") or "").strip()
        if not device_key.strip("/"):
            return False
        device_key = device_key.strip("/")

        try:
            client = await self._get_client("bark")
            # POST 接口不受 URL 长度限制，只受消息体积限制，超长时分段发送
            chunks = _split_by_bytes(message, 1800)
            total = len(chunks)
            ok = True
            for index, chunk in enumerate(chunks, 1):
                payload = {
                    "device_key": device_key,
                    "title": title,
                    "body": (f"({index}/{total})\n" if total > 1 else "") + chunk,
                    "group": "音乐下载器",
                    "sound": "bell",
                    "isArchive": 1,
                }
                response = await client.post(f"{server_url}/push", json=payload)
                response.raise_for_status()
                data = response.json()
                if data.get("code") not in (200, None):
                    raise RuntimeError(data.get("message") or f"Bark 返回 {data.get('code')}")
                if index < total:
                    await asyncio.sleep(0.3)  # 稍微错开，避免触发频率限制
            return ok
        except Exception as e:
            print(f"发送Bark通知失败: {e}")
            return False

    async def _send_wecom(self, message: str, title: str = "QQ音乐下载器通知") -> bool:
        """发送企业微信群机器人通知 (Webhook + 纯文本)

        纯文本无独立标题字段，标题并入 content；企业微信限制 2048 字节，
        超长时按行自动拆成多条依次发送。

        Args:
            message: 通知内容
            title: 通知标题

        Returns:
            bool: 是否发送成功
        """
        wecom_config = self._config.get("notification.wecom")
        if not wecom_config or not wecom_config.get("enabled", False):
            return False

        webhook_url = wecom_config.get("webhook_url")
        if not webhook_url:
            return False

        client = await self._get_client("wecom")
        content = f"{title}\n{message}"
        # 预留 "(i/N)\n" 与 JSON 包装的余量
        chunks = _split_by_bytes(content, 1750)
        total = len(chunks)
        ok = True
        for index, chunk in enumerate(chunks, 1):
            head = f"({index}/{total})\n" if total > 1 else ""
            payload = {
                "msgtype": "text",
                "text": {"content": head + chunk},
            }
            try:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
            except Exception as e:
                print(f"发送企业微信通知失败({index}/{total}): {e}")
                ok = False
            if index < total:
                await asyncio.sleep(0.3)  # 稍微错开，避免触发频率限制
        return ok

    async def _send_telegram(self, message: str, title: str = "QQ音乐下载器通知") -> bool:
        """发送 Telegram 机器人通知（Bot API sendMessage）

        - 纯文本，不启用 parse_mode：歌名里带 * _ [ ] 这类字符很容易触发解析错误而整条发送失败
        - 国内直连 api.telegram.org 基本不通，可以在配置里填 HTTP 代理或自建反代地址
        - 单条上限 4096 个 UTF-16 码元，超长时按行拆成多条并标注序号

        Args:
            message: 通知内容
            title: 通知标题（合并进正文首行，Telegram 没有独立标题字段）

        Returns:
            bool: 是否全部发送成功
        """
        tg_config = self._config.get("notification.telegram")
        if not tg_config or not tg_config.get("enabled", False):
            return False

        bot_token = (tg_config.get("bot_token") or "").strip()
        chat_id = str(tg_config.get("chat_id") or "").strip()
        if not bot_token or not chat_id:
            return False

        api_base = (tg_config.get("api_base") or "").strip().rstrip("/") or "https://api.telegram.org"
        proxy = (tg_config.get("proxy") or "").strip()
        url = f"{api_base}/bot{bot_token}/sendMessage"

        client = await self._get_client("telegram", proxy)
        content = f"【{title}】\n{message}"
        chunks = _split_by_chars(content, 3800)
        total = len(chunks)
        ok = True
        for index, chunk in enumerate(chunks, 1):
            head = f"({index}/{total})\n" if total > 1 else ""
            payload = {
                "chat_id": chat_id,
                "text": head + chunk,
                "disable_web_page_preview": True,
            }
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if not data.get("ok", False):
                    raise RuntimeError(data.get("description") or "Telegram 返回 ok=false")
            except Exception as e:
                # URL 里带着 bot_token，日志和报错里必须抹掉
                detail = str(e).replace(bot_token, "***")
                print(f"发送 Telegram 通知失败({index}/{total}): {detail}")
                ok = False
            if index < total:
                await asyncio.sleep(0.4)  # 稍微错开，避免触发频率限制
        return ok

    async def send_notification(self, message: str, title: str = "QQ音乐下载器通知") -> Dict[str, bool]:
        """发送通知，支持多种渠道并行发送

        Args:
            message: 通知内容
            title: 通知标题

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        results = {}

        # 并行发送所有启用的通知
        tasks = []

        # Webhook通知
        webhook_task = asyncio.create_task(self._send_webhook(message, title))
        tasks.append(("webhook", webhook_task))

        # Bark通知
        bark_task = asyncio.create_task(self._send_bark(message, title))
        tasks.append(("bark", bark_task))

        # 企业微信通知
        wecom_task = asyncio.create_task(self._send_wecom(message, title))
        tasks.append(("wecom", wecom_task))

        # Telegram 机器人通知
        telegram_task = asyncio.create_task(self._send_telegram(message, title))
        tasks.append(("telegram", telegram_task))

        # 等待所有通知发送完成
        for name, task in tasks:
            try:
                results[name] = await task
            except Exception as e:
                print(f"{name}通知任务执行失败: {e}")
                results[name] = False

        return results
    
    async def send_download_complete_notification(self, song_name: str, quality: str, file_size: str = "", file_path: str = "") -> Dict[str, bool]:
        """发送下载完成通知

        Args:
            song_name: 歌曲名称
            quality: 下载音质
            file_size: 文件大小（可选）
            file_path: 歌曲保存目录（可选，不含文件名）

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        detail = " · ".join(p for p in (quality, file_size) if p)
        message = f"{song_name}" + (f"\n{detail}" if detail else "")
        return await self.send_notification(message, "下载完成")

    async def send_download_failed_notification(self, song_name: str, error: str) -> Dict[str, bool]:
        """发送下载失败通知

        Args:
            song_name: 歌曲名称
            error: 失败原因

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        message = f"{song_name}\n{error}"
        return await self.send_notification(message, "下载失败")

    async def send_playlist_update_notification(self, playlist_name: str, new_songs: list, total_count: int = 0) -> Dict[str, bool]:
        """发送歌单更新通知

        Args:
            playlist_name: 歌单名称
            new_songs: 新歌曲列表
            total_count: 歌单当前总曲目数（可选）

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        head = f"{playlist_name} +{len(new_songs)} 首"
        if total_count:
            head += f"（共 {total_count} 首）"
        lines = [head]
        # 新歌少时列名字，多了只报数量，避免一次收藏几十首把通知刷屏
        if len(new_songs) <= 3:
            for song in new_songs:
                singers = ", ".join(s["name"] for s in song["singer"])
                lines.append(f"· {song['name']} - {singers}")
        return await self.send_notification("\n".join(lines), "歌单更新")

    async def send_playlist_report_notification(
        self,
        playlist_name: str,
        entries: list,
        stats: dict,
        total_count: int = 0,
        batch_count: int = 0,
        truncated: bool = False,
        still_running: int = 0,
    ) -> Dict[str, bool]:
        """发送歌单完整下载结果（≤50 首，逐条列出状态与未下载原因）

        Args:
            playlist_name: 歌单名称
            entries: [{"index": int, "label": str, "status": str, "detail": str, "in_batch": bool}]
                     status 取 completed/local_exists/failed/waiting/pending/cancelled/unknown
            stats: 各状态计数
            total_count: 歌单总曲目数
            batch_count: 本次新增（入队）曲目数
            truncated: 歌单曲目超过展示上限时为 True
            still_running: 汇报时仍在下载的数量

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        # 只统计本批新增的歌：歌单里的历史曲目不算进「本次结果」
        batch_entries = [e for e in entries if e.get("in_batch")] or entries
        batch_stats = {}
        for entry in batch_entries:
            key = entry.get("status") or "unknown"
            batch_stats[key] = batch_stats.get(key, 0) + 1
        total = batch_count or len(batch_entries)

        problems = [e for e in batch_entries if e.get("status") not in ("completed", "local_exists")]
        if not problems:
            # 少数几首直接把歌名带上，多了只报数量
            if total <= 3:
                names = "\n".join(e.get("label", "") for e in batch_entries)
                message = f"+{total} 首，全部完成\n{names}"
            else:
                message = f"+{total} 首，全部完成"
            return await self.send_notification(message, playlist_name)

        # 只报有发生的状态，一行说完
        parts = []
        for key, label in (
            ("completed", "完成"), ("local_exists", "已有"), ("failed", "失败"),
            ("waiting", "待重试"), ("pending", "下载中"),
            ("cancelled", "取消"), ("unknown", "未下载"),
        ):
            if batch_stats.get(key):
                parts.append(f"{label} {batch_stats[key]}")

        lines = [f"+{total} 首：" + " · ".join(parts)]
        # 只列没下成的，成功的不占篇幅；超过 8 条就截断
        for entry in problems[:8]:
            short = _STATUS_SHORT.get(entry.get("status"), "⬜")
            detail = entry.get("detail") or ""
            detail_text = f"（{detail}）" if detail else ""
            lines.append(f"{short} {entry.get('label', '')}{detail_text}")
        if len(problems) > 8:
            lines.append(f"…另有 {len(problems) - 8} 首未完成，详见网页")
        if still_running:
            lines.append(f"（仍有 {still_running} 首在下载中）")
        return await self.send_notification("\n".join(lines), playlist_name)

    async def send_playlist_completion_notification(self, playlist_name: str, completed_songs: list) -> Dict[str, bool]:
        """发送监控歌单新增歌曲部分下载完成通知

        Args:
            playlist_name: 歌单名称
            completed_songs: 已下载完成的歌曲列表（含 name 和 singer）

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        message = f"{playlist_name}：{len(completed_songs)} 首已下载完成"
        return await self.send_notification(message, "下载完成")

    async def send_login_expired_notification(self, message: str) -> Dict[str, bool]:
        """发送登录状态失效通知

        Args:
            message: 失效原因

        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        content = f"QQ 音乐登录已失效，下载暂停。\n{message}"
        return await self.send_notification(content, "登录失效")

    async def close(self):
        """关闭所有HTTP客户端"""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

# 创建全局通知管理器实例
notification_manager = NotificationManager()

# 示例使用
async def main():
    """示例函数，演示如何使用通知管理器"""
    # 发送普通通知
    result = await notification_manager.send_notification("这是一条测试通知", "测试标题")
    print(f"通知发送结果: {result}")
    
    # 发送下载完成通知
    result = await notification_manager.send_download_complete_notification("测试歌曲", "无损音质")
    print(f"下载完成通知发送结果: {result}")
    
    # 发送歌单更新通知
    new_songs = [
        {"name": "歌曲1", "singer": [{"name": "歌手1"}]},
        {"name": "歌曲2", "singer": [{"name": "歌手2"}]}
    ]
    result = await notification_manager.send_playlist_update_notification("测试歌单", new_songs)
    print(f"歌单更新通知发送结果: {result}")
    
    await notification_manager.close()

if __name__ == "__main__":
    asyncio.run(main())