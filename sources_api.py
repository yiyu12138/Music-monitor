"""自定义下载源（洛雪格式）的管理接口：/api/sources/*"""
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from lx_source import describe_error, manager

router = APIRouter(prefix="/api/sources", tags=["sources"])


class AddBody(BaseModel):
    url: Optional[str] = None
    script: Optional[str] = None


class ToggleBody(BaseModel):
    enabled: bool


class MoveBody(BaseModel):
    delta: int


class TestBody(BaseModel):
    platform: str = "qq"
    songmid: str
    name: str = ""
    singer: str = ""


@router.get("")
async def list_sources():
    return {"available": manager.available, "sources": manager.list()}


@router.post("")
async def add_source(body: AddBody):
    try:
        if body.url:
            item = await manager.add_from_url(body.url)
        elif body.script:
            item = await manager.add(body.script)
        else:
            raise ValueError("请提供脚本链接或脚本内容")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"添加失败: {describe_error(e)}")
    return {"message": f"已添加「{item['name']}」", "source": item}


@router.post("/upload")
async def upload_source(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    try:
        item = await manager.add(text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": f"已添加「{item['name']}」", "source": item}


@router.delete("/{sid}")
async def delete_source(sid: str):
    try:
        await manager.remove(sid)
    except KeyError:
        raise HTTPException(404, "源不存在")
    return {"message": "已删除"}


@router.post("/{sid}/toggle")
async def toggle_source(sid: str, body: ToggleBody):
    try:
        item = await manager.set_enabled(sid, body.enabled)
    except KeyError:
        raise HTTPException(404, "源不存在")
    return {"message": "已启用" if body.enabled else "已停用", "source": item}


@router.post("/{sid}/reload")
async def reload_source(sid: str):
    try:
        item = await manager.reload(sid)
    except KeyError:
        raise HTTPException(404, "源不存在")
    msg = "已重新加载" if item.get("loaded") else f"重新加载失败：{item.get('error') or '未知错误'}"
    return {"message": msg, "source": item}


@router.post("/{sid}/move")
async def move_source(sid: str, body: MoveBody):
    try:
        await manager.move(sid, body.delta)
    except KeyError:
        raise HTTPException(404, "源不存在")
    return {"sources": manager.list()}


@router.post("/{sid}/test")
async def test_source(sid: str, body: TestBody):
    info = {"songmid": body.songmid, "name": body.name, "singer": body.singer}
    try:
        r = await manager.test(sid, body.platform, info)
    except Exception as e:
        raise HTTPException(400, f"测试失败: {describe_error(e)}")
    return {"message": f"成功获取 {r['quality']} 链接", **r}
