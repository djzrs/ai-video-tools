#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agnes Video 2.5 Flash 视频生成工具（Python 版）
================================================

基于官方文档实现的零依赖 Python 工具（仅标准库，Python 3.8+），
支持文生视频、首尾帧控制、图片/音频参考生成三种工作流，
内置异步任务轮询与视频自动下载。

作为库调用（方便 AI / 脚本集成）::

    from agnes_video import generate, AgnesVideoError

    try:
        result = generate(
            prompt="雨后的未来城市街道，霓虹灯倒映在地面，一辆银色跑车缓慢驶过，电影级运镜",
            mode="text",                      # text | keyframe | reference
            seconds="5",                      # "4"~"12"
            aspect_ratio="16:9",
            api_key="sk-...",                 # 或设置环境变量 AGNES_API_KEY
            output_dir="./out",               # 结果自动保存为 mp4
        )
        print(result["file"], result["dims"])
    except AgnesVideoError as e:
        print("生成失败:", e)

命令行调用::

    # 文生视频（创建任务 → 自动轮询 → 下载 mp4）
    python agnes_video.py "雨后的未来城市街道，霓虹灯倒映在地面，电影级运镜" --seconds 5

    # 首尾帧控制（首帧/尾帧至少提供一个，须为公开可访问的 URL）
    python agnes_video.py "人物从首帧姿态自然转身走向窗边" --mode keyframe \
        --first-frame https://example.com/first.png --last-frame https://example.com/last.png

    # 图片参考（最多 5 张，prompt 中用 <Picture N> 指代）
    python agnes_video.py "以 <Picture 1> 中的角色为参考，角色在花田中自然奔跑" \
        --mode reference --images https://example.com/character.png

    # 查询并下载已有任务
    python agnes_video.py --query VIDEO_ID --out ./out

    # 批量生成（JSON 文件驱动，顺序执行）
    python agnes_video.py --batch jobs.json --out ./out

    # 测试 API Key 连通性（会创建一个 4 秒真实任务，当前限时免费）
    python agnes_video.py --test

API Key 获取顺序：--api-key 参数 > 环境变量 AGNES_API_KEY。
Base URL 获取顺序：--base-url 参数 > 环境变量 AGNES_BASE_URL > 默认值。

文档依据：https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash
端点说明：官方文档示例为 api.agnes-ai.cn，但该域 Key 与生图平台不通用；
本工具默认走 apihub.agnes-ai.com（与生图工具同平台，同一把 Key 即可访问，
2026-10-14 实测验证）。查询端点自动推导为 {域名}/agnesapi。
关键规则：
  * size 固定为 "720P"（Flash 专属限制），画幅由 aspect_ratio 决定；
  * seconds 为字符串 "4"~"12"，默认 "5"；
  * mode=keyframe 时 first_frame/last_frame 至少一个；
  * mode=reference 时 images≤5、audios≤3，不支持 videos；
  * 查询任务必须带 model_name=agnes-video-2.5-flash（keyframe/reference 模式强制）；
  * 任务完成取响应顶层 url 字段下载视频；
  * 服务端限流（video queue is full）自动退避重试；
  * 输出视频秒数当前限时免费（¥0/秒），以平台最新公告为准。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------- 常量

MODEL = "agnes-video-2.5-flash"
ENV_API_KEY = "AGNES_API_KEY"
ENV_BASE_URL = "AGNES_BASE_URL"
# 默认端点取 apihub（与生图工具同平台、Key 通用）。
# 官方文档示例为 https://api.agnes-ai.cn/v1，但该域需要视频平台单独签发的 Key；
# apihub 域可用同一把 Key 访问视频接口（2026-10-14 实测）。
# 查询端点由本工具自动推导为 {域名}/agnesapi。
DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DOC_BASE_URL = "https://api.agnes-ai.cn/v1"
MODEL_NAME_QUERY = MODEL          # 查询任务时强制携带
SIZE = "720P"                     # Flash 专属：固定 720P
DEFAULT_SECONDS = "5"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_POLL_INTERVAL = 2.0       # 文档建议 1-2 秒轮询一次
DEFAULT_TIMEOUT = 900             # 单任务创建+轮询总超时（秒）
DOWNLOAD_TIMEOUT = 300            # 视频下载超时（秒）
MAX_IMAGES = 5                    # Flash 限制：参考图最多 5 张
MAX_AUDIOS = 3                    # Flash 限制：参考音频最多 3 段
MODES = ("text", "keyframe", "reference")
ASPECT_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# aspect_ratio -> 720P 输出像素（官方输出尺寸参考表）
ASPECT_DIMS: Dict[str, str] = {
    "21:9": "1680x720",
    "16:9": "1280x704",
    "4:3":  "960x720",
    "1:1":  "720x720",
    "3:4":  "720x960",
    "9:16": "720x1280",
}

TERMINAL_STATUS = ("completed", "failed", "canceled", "cancelled")


class AgnesVideoError(Exception):
    """工具统一异常；message 为可直接展示给用户/AI 的错误描述。"""


# ---------------------------------------------------------------- 基础工具

def resolve_api_key(api_key: Optional[str] = None) -> str:
    """按优先级解析 API Key：参数 > 环境变量 AGNES_API_KEY。"""
    key = (api_key or os.environ.get(ENV_API_KEY) or "").strip()
    if not key:
        raise AgnesVideoError(
            f"未提供 API Key：请通过 api_key 参数、--api-key 参数或环境变量 {ENV_API_KEY} 提供"
        )
    return key


def resolve_base_url(base_url: Optional[str] = None) -> str:
    """按优先级解析 Base URL：参数 > 环境变量 AGNES_BASE_URL > 默认值。"""
    url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise AgnesVideoError(f"Base URL 必须以 http(s):// 开头: {url!r}")
    return url


def query_endpoint(base_url: str) -> str:
    """
    查询端点固定为 {协议+域名}/agnesapi（不在 /v1 路径下）。
    例如 base_url=https://api.agnes-ai.cn/v1 -> https://api.agnes-ai.cn/agnesapi
    """
    m = re.match(r"^(https?://[^/]+)", base_url)
    if not m:
        raise AgnesVideoError(f"无法从 Base URL 解析域名: {base_url!r}")
    return m.group(1) + "/agnesapi"


def check_seconds(seconds: Any) -> str:
    """seconds 必须为字符串 "4"~"12"；容忍 int 输入并转换。"""
    s = str(seconds).strip()
    if not re.fullmatch(r"\d+", s):
        raise AgnesVideoError(f"seconds 必须为数字字符串，当前: {seconds!r}")
    if not (4 <= int(s) <= 12):
        raise AgnesVideoError(f"seconds 必须在 4~12 之间，当前: {s!r}")
    return s


def check_media_url(src: str, field: str) -> str:
    """
    校验媒体输入。视频 API 要求所有媒体为 Agnes AI 服务可公开访问的 URL，
    本地文件需先自行托管为公网 URL 后再传入。
    """
    s = (src or "").strip()
    if not s:
        raise AgnesVideoError(f"{field} 不能为空")
    if not s.startswith(("http://", "https://")):
        raise AgnesVideoError(
            f"{field} 仅支持公网可访问的 http(s) URL（当前: {src!r}）。"
            "本地文件请先上传至公网可访问地址（对象存储/图床等）后再传入。"
        )
    return s


def sanitize_name(name: str) -> str:
    """清理文件名字符（去掉路径分隔符与 Windows 保留字符）。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())
    return s[:80] or "video"


def safe_output_name(prompt: str, name: Optional[str]) -> str:
    """生成输出文件名（不含扩展名）：优先 name，否则取提示词前 24 字。"""
    base = sanitize_name(name) if name else sanitize_name(prompt[:24])
    return base


# ---------------------------------------------------------------- 请求构建

def build_request(
    prompt: str,
    mode: str = "text",
    seconds: Any = DEFAULT_SECONDS,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    images: Optional[Sequence[str]] = None,
    audios: Optional[Sequence[str]] = None,
    seed: Optional[int] = None,
    n: int = 1,
) -> Dict[str, Any]:
    """
    按官方规则构建创建任务请求体。

    :param prompt: 视频内容描述（必填；reference 模式可用 <Picture N>/<Audio N> 指代素材）
    :param mode: text 文生视频 | keyframe 首尾帧控制 | reference 图片/音频参考
    :param seconds: 视频时长，字符串 "4"~"12"，默认 "5"
    :param aspect_ratio: 画幅，默认 16:9（720P 下输出像素见 ASPECT_DIMS）
    :param first_frame: keyframe 模式首帧图片 URL
    :param last_frame: keyframe 模式尾帧图片 URL
    :param images: reference 模式参考图片 URL 列表（≤5）
    :param audios: reference 模式参考音频 URL 列表（≤3）
    :param seed: 随机种子（可选）
    :param n: 生成数量，当前仅支持 1
    :return: 请求体 dict
    """
    if not prompt or not prompt.strip():
        raise AgnesVideoError("prompt 不能为空")
    if mode not in MODES:
        raise AgnesVideoError(f"不支持的 mode: {mode!r}，可选 {MODES}")
    if aspect_ratio not in ASPECT_DIMS:
        raise AgnesVideoError(f"不支持的 aspect_ratio: {aspect_ratio!r}，可选 {ASPECT_RATIOS}")
    if n != 1:
        raise AgnesVideoError("n 当前仅支持 1")

    body: Dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt.strip(),
        "mode": mode,
        "seconds": check_seconds(seconds),
        "size": SIZE,
        "aspect_ratio": aspect_ratio,
    }

    if mode == "keyframe":
        ff = check_media_url(first_frame, "first_frame") if first_frame else None
        lf = check_media_url(last_frame, "last_frame") if last_frame else None
        if not ff and not lf:
            raise AgnesVideoError("mode=keyframe 需要 first_frame 与 last_frame 至少提供一个")
        if ff:
            body["first_frame"] = ff
        if lf:
            body["last_frame"] = lf
    elif mode == "reference":
        imgs = [check_media_url(x, "images") for x in (images or []) if str(x).strip()]
        auds = [check_media_url(x, "audios") for x in (audios or []) if str(x).strip()]
        if not imgs and not auds:
            raise AgnesVideoError("mode=reference 需要 images 或 audios 至少一类非空")
        if len(imgs) > MAX_IMAGES:
            raise AgnesVideoError(f"Flash 限制参考图最多 {MAX_IMAGES} 张，当前 {len(imgs)} 张")
        if len(auds) > MAX_AUDIOS:
            raise AgnesVideoError(f"Flash 限制参考音频最多 {MAX_AUDIOS} 段，当前 {len(auds)} 段")
        if imgs:
            body["images"] = imgs
        if auds:
            body["audios"] = auds
    else:  # text 模式不允许任何媒体字段
        for field, val in (("first_frame", first_frame), ("last_frame", last_frame),
                           ("images", images), ("audios", audios)):
            if val:
                raise AgnesVideoError(f"mode=text 不允许传入 {field}")

    if seed is not None:
        body["seed"] = int(seed)
    return body


# ---------------------------------------------------------------- 网络层

def _request(url: str, api_key: str, timeout: int, payload: Optional[Dict[str, Any]] = None,
             method: Optional[str] = None) -> Dict[str, Any]:
    """发送 HTTP 请求并解析 JSON；HTTP/业务错误统一抛 AgnesVideoError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            j = json.loads(raw)
            # 错误格式兼容：{"detail": ...} / {"error": {"message": ...}} / {"message": ...}
            msg = (j.get("detail")
                   or (j.get("error") or {}).get("message")
                   or j.get("message")
                   or raw[:300])
        except Exception:
            msg = raw[:300]
        raise AgnesVideoError(f"HTTP {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise AgnesVideoError(f"网络错误: {reason}") from None
    except TimeoutError:
        raise AgnesVideoError(f"请求超时（{timeout}s）") from None
    except json.JSONDecodeError:
        raise AgnesVideoError("响应不是合法 JSON") from None


def create_task(body: Dict[str, Any], api_key: str, base_url: str, timeout: int = 60,
                max_retries: int = 6) -> str:
    """
    POST {base}/videos 创建视频任务，返回 video_id。
    服务端限流（video queue is full / rate limit）按 5s×attempt 退避重试。
    """
    last_err: Optional[AgnesVideoError] = None
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            resp = _request(f"{base_url}/videos", api_key, timeout, payload=body, method="POST")
            break
        except AgnesVideoError as e:
            # 队列满/限流类错误退避重试；参数错误、Key 无效等立即抛出
            if re.search(r"queue is full|rate limit|retry later|too many", str(e), re.I):
                last_err = e
                if attempt < max(1, max_retries):
                    time.sleep(5 * attempt)
                    continue
            raise
    else:
        raise last_err or AgnesVideoError("创建任务失败")
    video_id = resp.get("video_id") or resp.get("id") or resp.get("task_id")
    if not video_id:
        raise AgnesVideoError(f"创建响应中未找到 video_id: {json.dumps(resp, ensure_ascii=False)[:300]}")
    return str(video_id)


def query_task(video_id: str, api_key: str, base_url: str, timeout: int = 30) -> Dict[str, Any]:
    """GET {域名}/agnesapi 查询任务状态（必须携带 model_name）。"""
    qs = urllib.parse.urlencode({"video_id": video_id, "model_name": MODEL_NAME_QUERY})
    return _request(f"{query_endpoint(base_url)}?{qs}", api_key, timeout)


def wait_for_completion(
    video_id: str,
    api_key: str,
    base_url: str,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: int = DEFAULT_TIMEOUT,
    on_progress=None,
) -> Dict[str, Any]:
    """
    轮询任务直至终态（completed/failed/canceled），返回最终响应 dict。
    on_progress(dict) 可选回调，用于进度展示。
    """
    deadline = time.time() + max(1, timeout)
    last_status = ""
    while True:
        resp = query_task(video_id, api_key, base_url)
        status = str(resp.get("status") or "").lower()
        if status != last_status:
            last_status = status
            if on_progress:
                on_progress(resp)
        if status in TERMINAL_STATUS:
            return resp
        if time.time() > deadline:
            raise AgnesVideoError(
                f"轮询超时（{timeout}s），最后状态: {status or '未知'}，video_id={video_id}（可稍后重试查询）"
            )
        time.sleep(max(0.5, poll_interval))


def download_video(url: str, out_dir: str, name: str) -> str:
    """下载视频到 out_dir/{HHMMSS}_{name}.mp4，返回本地路径。"""
    if not url:
        raise AgnesVideoError("任务完成但响应中无视频 URL")
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    fname = f"{time.strftime('%H%M%S')}_{sanitize_name(name)}.mp4"
    dest = path / fname
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise AgnesVideoError(f"视频下载失败: {e}") from None
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise AgnesVideoError("下载到的视频文件为空")
    return str(dest)


# ---------------------------------------------------------------- 高层封装

def generate(
    prompt: str,
    mode: str = "text",
    seconds: Any = DEFAULT_SECONDS,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    images: Optional[Sequence[str]] = None,
    audios: Optional[Sequence[str]] = None,
    seed: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    output_dir: Optional[str] = None,
    name: Optional[str] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: int = DEFAULT_TIMEOUT,
    on_progress=None,
) -> Dict[str, Any]:
    """
    完整流程：构建请求 → 创建任务 → 轮询 → 下载。
    返回 dict：video_id / status / file / url / dims / seconds / raw（最终响应）。
    """
    key = resolve_api_key(api_key)
    base = resolve_base_url(base_url)
    body = build_request(prompt, mode=mode, seconds=seconds, aspect_ratio=aspect_ratio,
                         first_frame=first_frame, last_frame=last_frame,
                         images=images, audios=audios, seed=seed)
    video_id = create_task(body, key, base)
    resp = wait_for_completion(video_id, key, base, poll_interval=poll_interval,
                               timeout=timeout, on_progress=on_progress)
    status = str(resp.get("status") or "").lower()
    result: Dict[str, Any] = {
        "video_id": video_id,
        "status": status,
        "url": resp.get("url"),
        "seconds": resp.get("seconds", str(seconds)),
        "dims": ASPECT_DIMS.get(aspect_ratio, ""),
        "raw": resp,
    }
    if status != "completed":
        err = resp.get("error") or "未知原因"
        raise AgnesVideoError(f"视频任务未完成（status={status}）: {err}")
    out_name = safe_output_name(prompt, name)
    if output_dir:
        result["file"] = download_video(resp["url"], output_dir, out_name)
    else:
        result["file"] = None
    return result


def fetch_existing(video_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None,
                   output_dir: Optional[str] = None, name: Optional[str] = None,
                   poll_interval: float = DEFAULT_POLL_INTERVAL,
                   timeout: int = DEFAULT_TIMEOUT, on_progress=None) -> Dict[str, Any]:
    """查询已有任务，完成后下载（用于断点续取/跨会话恢复）。"""
    key = resolve_api_key(api_key)
    base = resolve_base_url(base_url)
    resp = wait_for_completion(video_id, key, base, poll_interval=poll_interval,
                               timeout=timeout, on_progress=on_progress)
    status = str(resp.get("status") or "").lower()
    if status != "completed":
        err = resp.get("error") or "未知原因"
        raise AgnesVideoError(f"视频任务未完成（status={status}）: {err}")
    result = {"video_id": video_id, "status": status, "url": resp.get("url"),
              "seconds": resp.get("seconds"), "dims": resp.get("size"), "raw": resp}
    if output_dir:
        result["file"] = download_video(resp["url"], output_dir, name or video_id)
    else:
        result["file"] = None
    return result


# ---------------------------------------------------------------- 批量模式

def run_batch(batch_path: str, out_dir: str, api_key: Optional[str] = None,
              base_url: Optional[str] = None, poll_interval: float = DEFAULT_POLL_INTERVAL,
              timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """
    批量生成：JSON 格式
    {
      "defaults": {"mode": "text", "seconds": "5", "aspect_ratio": "16:9"},
      "items": [
        {"prompt": "...", "name": "clip_01"},
        {"prompt": "...", "mode": "keyframe", "first_frame": "https://...", "name": "clip_02"}
      ]
    }
    顺序执行，单项失败不中断整体，结果汇总返回。
    """
    key = resolve_api_key(api_key)
    base = resolve_base_url(base_url)
    cfg = json.loads(Path(batch_path).read_text(encoding="utf-8"))
    defaults = cfg.get("defaults") or {}
    items = cfg.get("items") or []
    if not items:
        raise AgnesVideoError("批量 JSON 中 items 为空")

    results: List[Dict[str, Any]] = []
    for i, it in enumerate(items, 1):
        merged = dict(defaults)
        merged.update(it)
        name = merged.get("name") or f"clip_{i:02d}"
        try:
            r = generate(
                prompt=merged["prompt"],
                mode=merged.get("mode", "text"),
                seconds=merged.get("seconds", DEFAULT_SECONDS),
                aspect_ratio=merged.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
                first_frame=merged.get("first_frame"),
                last_frame=merged.get("last_frame"),
                images=merged.get("images"),
                audios=merged.get("audios"),
                seed=merged.get("seed"),
                api_key=key, base_url=base,
                output_dir=out_dir, name=name,
                poll_interval=poll_interval, timeout=timeout,
                on_progress=lambda resp, n=name: print(
                    f"  [{n}] status={resp.get('status')} progress={resp.get('progress')}", flush=True),
            )
            print(f"[{i}/{len(items)}] OK {name} -> {r.get('file') or r.get('url')}", flush=True)
            results.append(r)
        except AgnesVideoError as e:
            print(f"[{i}/{len(items)}] FAIL {name}: {e}", flush=True)
            results.append({"name": name, "error": str(e)})
    return results


# ---------------------------------------------------------------- CLI

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Agnes Video 2.5 Flash 视频生成工具（文生视频/首尾帧/参考生成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="文档: https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash",
    )
    ap.add_argument("prompt", nargs="?", help="视频内容描述（--batch/--query/--test 模式下可省略）")
    ap.add_argument("--mode", choices=MODES, default="text",
                    help="生成模式：text 文生视频 | keyframe 首尾帧控制 | reference 图片/音频参考（默认 text）")
    ap.add_argument("--seconds", default=DEFAULT_SECONDS,
                    help='视频时长，字符串 "4"~"12"，默认 "5"')
    ap.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, default=DEFAULT_ASPECT_RATIO,
                    help="画幅（720P 输出）：21:9/16:9/4:3/1:1/3:4/9:16，默认 16:9")
    ap.add_argument("--first-frame", help="keyframe 模式首帧图片 URL")
    ap.add_argument("--last-frame", help="keyframe 模式尾帧图片 URL")
    ap.add_argument("--images", nargs="*", default=[], help="reference 模式参考图片 URL（≤5）")
    ap.add_argument("--audios", nargs="*", default=[], help="reference 模式参考音频 URL（≤3）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可选）")
    ap.add_argument("--query", metavar="VIDEO_ID", help="查询已有任务并下载（不创建新任务）")
    ap.add_argument("--batch", metavar="JSON", help="批量生成：JSON 文件路径")
    ap.add_argument("--out", "--output-dir", dest="out", default=None,
                    help="视频保存目录（默认只打印不落盘）")
    ap.add_argument("--name", default=None, help="输出文件名（不含扩展名，默认取提示词前 24 字）")
    ap.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL,
                    help="轮询间隔秒数，默认 2（文档建议 1-2 秒）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"单任务总超时秒数，默认 {DEFAULT_TIMEOUT}")
    ap.add_argument("--api-key", default=None, help=f"API Key（默认读环境变量 {ENV_API_KEY}）")
    ap.add_argument("--base-url", default=None,
                    help=f"API Base URL（默认读环境变量 {ENV_BASE_URL}，否则 {DEFAULT_BASE_URL}）")
    ap.add_argument("--test", action="store_true",
                    help="测试 API Key 连通性（创建一个 4 秒真实任务，当前限时免费）")
    args = ap.parse_args(argv)

    try:
        if args.test:
            args.prompt = args.prompt or "连通性测试：一片绿叶在微风中轻轻摇动，阳光柔和，固定机位"
            args.seconds = "4"
            args.mode = "text"
        elif args.query:
            r = fetch_existing(args.query, api_key=args.api_key, base_url=args.base_url,
                               output_dir=args.out, name=args.name,
                               poll_interval=args.poll_interval, timeout=args.timeout,
                               on_progress=lambda resp: print(
                                   f"  status={resp.get('status')} progress={resp.get('progress')}", flush=True))
            print(json.dumps({k: v for k, v in r.items() if k != "raw"},
                             ensure_ascii=False, indent=1))
            return 0
        elif args.batch:
            results = run_batch(args.batch, args.out or ".", api_key=args.api_key,
                                base_url=args.base_url, poll_interval=args.poll_interval,
                                timeout=args.timeout)
            ok = sum(1 for r in results if r.get("file") or r.get("url"))
            print(f"批量完成：{ok}/{len(results)} 成功")
            return 0 if ok == len(results) else 1
        elif not args.prompt:
            ap.error("缺少 prompt（或使用 --query/--batch/--test 模式）")

        r = generate(
            prompt=args.prompt, mode=args.mode, seconds=args.seconds,
            aspect_ratio=args.aspect_ratio, first_frame=args.first_frame,
            last_frame=args.last_frame, images=args.images, audios=args.audios,
            seed=args.seed, api_key=args.api_key, base_url=args.base_url,
            output_dir=args.out, name=args.name,
            poll_interval=args.poll_interval, timeout=args.timeout,
            on_progress=lambda resp: print(
                f"  status={resp.get('status')} progress={resp.get('progress')}", flush=True),
        )
        print(json.dumps({k: v for k, v in r.items() if k != "raw"},
                         ensure_ascii=False, indent=1))
        return 0
    except AgnesVideoError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
