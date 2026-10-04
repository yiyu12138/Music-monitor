#!/usr/bin/env python3
"""把安装向导里的设置写入应用配置 data/config.json（保留已有配置）

用法: seed_config.py <数据目录> <音乐目录> [检查间隔秒]
"""
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        print("usage: seed_config.py <data_dir> <music_dir> [interval]")
        return 1
    data_root, music_dir = sys.argv[1], sys.argv[2]
    interval = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else None
    data_dir = os.path.join(data_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "config.json")
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception as e:
            print(f"读取已有配置失败（将新建）: {e}")
            cfg = {}
    download = cfg.setdefault("download", {})
    download["default_dir"] = music_dir
    download["downloads_root"] = music_dir
    # 只在配置里没设过时默认「平铺保存」，与旧版行为一致
    download.setdefault("playlist_subfolder", False)
    if interval:
        cfg.setdefault("monitor", {})["check_interval_seconds"] = interval
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"已写入配置: {path}（音乐目录 {music_dir}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
