#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agnes Image 2.5 Flash 生图工具（Python 版）
=============================================

基于官方文档实现的零依赖 Python 工具（仅标准库，Python 3.8+），
支持文生图、图生图、多图合成三种工作流。

作为库调用（方便 AI / 脚本集成）::

    from agnes_image import generate, AgnesImageError

    try:
        result = generate(
            prompt="林小颖（二十四岁，鹅蛋脸白皙通透……），电影级写实，横屏构图",
            size="2K", ratio="16:9", output_format="url",
            api_key="sk-...",                      # 或设置环境变量 AGNES_API_KEY
            output_dir="./out",                    # 结果自动保存到文件
        )
        print(result["file"], result["dims"])
    except AgnesImageError as e:
        print("生成失败:", e)

命令行调用::

    # 文生图（URL 输出并自动下载）
    python agnes_image.py "一个发光浮空城市，电影级写实" --size 2K --ratio 16:9

    # 图生图（本地图片自动转 Base64）
    python agnes_image.py "改为赛博朋克夜景，保留原始构图" --image input.png

    # 多图合成
    python agnes_image.py "将两张图合成一张海报" --image a.png --image b.png --mode multi

    # 批量生成（JSON 文件驱动）
    python agnes_image.py --batch prompts.json --out ./out

    # 测试 API Key 连通性
    python agnes_image.py --test

API Key 获取顺序：--api-key 参数 > 环境变量 AGNES_API_KEY。

文档依据：https://agnes-ai.com/zh-Hans/docs/agnes-image-25-flash
关键规则：response_format 必须放在 extra_body 内（不要放顶层）；
图生图无需传 tags；当前所有档位与参考图免费。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# ---------------------------------------------------------------- 常量

ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"
MODEL = "agnes-image-2.5-flash"
ENV_API_KEY = "AGNES_API_KEY"
DEFAULT_TIMEOUT = 360          # 文档建议客户端超时 60-360s
MAX_LOCAL_IMAGE_BYTES = 8 * 1024 * 1024

# ratio -> [1K, 2K, 3K, 4K] 输出像素（官方输出尺寸参考表）
DIMS: Dict[str, List[str]] = {
    "1:1":  ["1024x1024", "2048x2048", "3072x3072", "4096x4096"],
    "3:4":  ["864x1152", "1728x2304", "2592x3456", "3456x4608"],
    "4:3":  ["1152x864", "2304x1728", "3456x2592", "4608x3456"],
    "16:9": ["1312x736", "2624x1472", "3936x2208", "5248x2944"],
    "9:16": ["736x1312", "1472x2624", "2208x3936", "2944x5248"],
    "2:3":  ["832x1248", "1664x2496", "2496x3744", "3328x4992"],
    "3:2":  ["1248x832", "2496x1664", "3744x2496", "4992x3328"],
    "21:9": ["1568x672", "3136x1344", "4704x2016", "6272x2688"],
}
SIZE_INDEX = {"1K": 0, "2K": 1, "3K": 2, "4K": 3}
RATIOS = list(DIMS.keys())
SIZES = list(SIZE_INDEX.keys())
MODES = ("t2i", "i2i", "multi")


class AgnesImageError(Exception):
    """工具统一异常；message 为可直接展示给用户/AI 的错误描述。"""


# ---------------------------------------------------------------- 基础工具

def get_dimensions(size: str, ratio: str) -> str:
    """返回 size 档位 + ratio 宽高比对应的输出像素，如 ('2K','16:9') -> '2624x1472'。"""
    if size not in SIZE_INDEX:
        raise AgnesImageError(f"不支持的 size: {size!r}，可选 {SIZES}")
    if ratio not in DIMS:
        raise AgnesImageError(f"不支持的 ratio: {ratio!r}，可选 {RATIOS}")
    return DIMS[ratio][SIZE_INDEX[size]]


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """按优先级解析 API Key：参数 > 环境变量 AGNES_API_KEY。"""
    key = (api_key or os.environ.get(ENV_API_KEY) or "").strip()
    if not key:
        raise AgnesImageError(
            f"未提供 API Key：请通过 api_key 参数、--api-key 参数或环境变量 {ENV_API_KEY} 提供"
        )
    return key


def load_image_input(src: str) -> str:
    """
    将图片输入统一为 API 可接受的形式：
    - http(s) URL 或 data: URI：原样返回
    - 本地文件路径：读取并转为 Data URI Base64（data:image/<ext>;base64,...）
    """
    if src.startswith(("http://", "https://", "data:")):
        return src
    path = Path(src)
    if not path.is_file():
        raise AgnesImageError(f"图片文件不存在: {src}")
    if path.stat().st_size > MAX_LOCAL_IMAGE_BYTES:
        raise AgnesImageError(
            f"图片过大（>{MAX_LOCAL_IMAGE_BYTES // 1024 // 1024}MB）: {src}，请先压缩"
        )
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_request(
    prompt: str,
    size: str = "2K",
    ratio: str = "16:9",
    images: Optional[Sequence[str]] = None,
    mode: str = "t2i",
    output_format: str = "url",
) -> Dict[str, Any]:
    """
    按官方规则构建请求体。

    :param prompt: 文本指令（必填）
    :param size: 输出档位 1K/2K/3K/4K（必填）
    :param ratio: 宽高比，默认 16:9
    :param images: 参考图列表（本地路径/URL/Data URI）；i2i/multi 模式必填
    :param mode: t2i 文生图 | i2i 图生图 | multi 多图合成
    :param output_format: url 或 b64_json
    :return: 请求体 dict（extra_body 承载 image/response_format）
    """
    if not prompt or not prompt.strip():
        raise AgnesImageError("prompt 不能为空")
    if mode not in MODES:
        raise AgnesImageError(f"不支持的 mode: {mode!r}，可选 {MODES}")
    if output_format not in ("url", "b64_json"):
        raise AgnesImageError("output_format 仅支持 'url' 或 'b64_json'")
    get_dimensions(size, ratio)  # 校验

    body: Dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt.strip(),
        "size": size,
        "ratio": ratio,
    }
    extra: Dict[str, Any] = {}

    if mode in ("i2i", "multi"):
        if not images:
            raise AgnesImageError(f"mode={mode} 需要至少 1 张参考图（images 参数）")
        extra["image"] = [load_image_input(x) for x in images]

    # 关键规则：response_format 放 extra_body 内，不要放顶层
    extra["response_format"] = "url" if output_format == "url" else "b64_json"
    # 文生图 Base64 用 return_base64；图生图 Base64 用 extra_body.response_format
    if mode == "t2i" and output_format == "b64_json":
        body["return_base64"] = True

    body["extra_body"] = extra
    return body


# ---------------------------------------------------------------- 网络层

def _post(url: str, payload: Dict[str, Any], api_key: str, timeout: int) -> Dict[str, Any]:
    """发送 POST 请求并解析 JSON；HTTP/业务错误统一抛 AgnesImageError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
            msg = (j.get("error") or {}).get("message") or raw
        except Exception:
            msg = raw or f"HTTP {e.code}"
        if re.search(r"invalid token|unauthorized|401", msg, re.I):
            msg = f"API Key 无效或已过期（{msg}）"
        raise AgnesImageError(msg) from None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if "timed out" in str(reason).lower():
            raise AgnesImageError(
                f"请求超时（{timeout}s）。可降低 size 档位或稍后重试"
            ) from None
        raise AgnesImageError(f"网络错误: {reason}") from None
    except TimeoutError:
        raise AgnesImageError(f"请求超时（{timeout}s）。可降低 size 档位或稍后重试") from None


def _download(url: str, dest: Path, timeout: int = 120) -> Path:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())
    return dest


# ---------------------------------------------------------------- 核心 API

def generate(
    prompt: str,
    size: str = "2K",
    ratio: str = "16:9",
    images: Optional[Sequence[str]] = None,
    mode: str = "t2i",
    output_format: str = "url",
    api_key: Optional[str] = None,
    output_dir: Optional[Union[str, Path]] = None,
    name: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """
    生成一张图像（库调用主入口）。

    :return: dict，字段：
        ok(bool) / file(保存路径, output_dir 有效时) / url / b64 /
        prompt / mode / size / ratio / dims / elapsed(秒) / raw(原始响应)
    :raises AgnesImageError: 参数错误、网络错误、API 业务错误
    """
    key = resolve_api_key(api_key)
    body = build_request(prompt, size=size, ratio=ratio, images=images,
                         mode=mode, output_format=output_format)

    last_err: Optional[Exception] = None
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            t0 = time.time()
            resp = _post(ENDPOINT, body, key, timeout)
            break
        except AgnesImageError as e:
            # 仅网络/超时类错误退避重试；API 业务错误（如 Key 无效）立即抛出
            if re.search(r"超时|网络错误", str(e)):
                last_err = e
                if attempt < max(1, max_retries):
                    time.sleep(3 * attempt)
                    continue
            raise
    else:  # pragma: no cover
        raise last_err or AgnesImageError("未知错误")

    elapsed = round(time.time() - t0, 2)
    data = (resp.get("data") or [{}])[0]
    url = data.get("url")
    b64 = data.get("b64_json")
    if not url and not b64:
        raise AgnesImageError(f"响应中未包含图像数据: {json.dumps(resp, ensure_ascii=False)[:300]}")

    result: Dict[str, Any] = {
        "ok": True,
        "url": url,
        "b64": b64,
        "prompt": body["prompt"],
        "mode": mode,
        "size": size,
        "ratio": ratio,
        "dims": get_dimensions(size, ratio),
        "elapsed": elapsed,
        "file": None,
        "raw": resp,
    }

    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w一-龥-]+", "_", (name or body["prompt"])[:24]).strip("_") or "image"
        fname = f"{time.strftime('%H%M%S')}_{slug}.png"
        dest = out_dir / fname
        try:
            if url:
                _download(url, dest)
            else:
                dest.write_bytes(base64.b64decode(b64))
            result["file"] = str(dest)
        except Exception as e:  # 下载/落盘失败不视为生成失败
            result["save_error"] = f"结果保存失败: {e}"
    return result


def generate_batch(
    batch: Dict[str, Any],
    api_key: Optional[str] = None,
    output_dir: Optional[Union[str, Path]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    批量生成。batch 格式::

        {
          "defaults": {"size": "2K", "ratio": "16:9", "format": "url"},
          "items": [
            {"prompt": "...", "name": "char_01"},
            {"prompt": "...", "images": ["a.png"], "mode": "i2i"}
          ]
        }

    单条失败不中断整批，结果中该条 ok=false 且带 error 字段。
    """
    defaults = batch.get("defaults") or {}
    items = batch.get("items") or []
    if not items:
        raise AgnesImageError("批量文件中 items 不能为空")
    results: List[Dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        prompt = item.get("prompt")
        fmt = item.get("format", defaults.get("format", "url"))
        try:
            r = generate(
                prompt=prompt,
                size=item.get("size", defaults.get("size", "2K")),
                ratio=item.get("ratio", defaults.get("ratio", "16:9")),
                images=item.get("images"),
                mode=item.get("mode", "i2i" if item.get("images") else "t2i"),
                output_format="b64_json" if fmt in ("b64", "b64_json") else "url",
                api_key=api_key,
                output_dir=item.get("output_dir", output_dir),
                name=item.get("name"),
                timeout=timeout,
            )
        except AgnesImageError as e:
            r = {"ok": False, "index": i, "name": item.get("name"),
                 "prompt": prompt, "error": str(e)}
        results.append(r)
    return results


# ---------------------------------------------------------------- CLI

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agnes_image.py",
        description="Agnes Image 2.5 Flash 生图工具（文生图/图生图/多图合成，零依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("prompt", nargs="?", help="文本提示词（--batch/--test 模式下可省略）")
    p.add_argument("--mode", choices=MODES, default="t2i",
                   help="t2i 文生图（默认）| i2i 图生图 | multi 多图合成")
    p.add_argument("--image", action="append", default=[], metavar="PATH_OR_URL",
                   help="参考图（本地路径/URL/Data URI），可多次传入；i2i/multi 必填")
    p.add_argument("--size", choices=SIZES, default="2K", help="输出档位，默认 2K")
    p.add_argument("--ratio", choices=RATIOS, default="16:9", help="宽高比，默认 16:9")
    p.add_argument("--format", dest="fmt", choices=("url", "b64", "b64_json"), default="url",
                   help="输出格式：url（默认，自动下载）或 b64（Base64）")
    p.add_argument("--api-key", default=None, help=f"API Key（默认读环境变量 {ENV_API_KEY}）")
    p.add_argument("--out", "--output-dir", dest="out", default=None, metavar="DIR",
                   help="结果保存目录（默认只打印不落盘）")
    p.add_argument("--name", default=None, help="输出文件名前缀（默认取提示词前 24 字）")
    p.add_argument("--batch", default=None, metavar="JSON",
                   help="批量生成：JSON 文件路径，格式见文件头 docstring")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"单次请求超时秒数，默认 {DEFAULT_TIMEOUT}")
    p.add_argument("--test", action="store_true", help="测试 API Key 连通性后退出")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    fmt = "b64_json" if args.fmt in ("b64", "b64_json") else "url"

    def emit(obj: Dict[str, Any]) -> None:
        # 结果以单行 JSON 打印到 stdout，方便 AI/脚本解析；提示信息走 stderr
        slim = {k: v for k, v in obj.items() if k not in ("raw", "b64")}
        print(json.dumps(slim, ensure_ascii=False))

    try:
        if args.test:
            r = generate(prompt="a tiny test dot, plain background", size="1K",
                         ratio="1:1", output_format="url", api_key=args.api_key,
                         timeout=min(args.timeout, 120))
            emit({**r, "note": "API Key 连通正常"})
            return 0

        if args.batch:
            batch_path = Path(args.batch)
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            results = generate_batch(batch, api_key=args.api_key,
                                     output_dir=args.out, timeout=args.timeout)
            ok = sum(1 for r in results if r.get("ok"))
            for r in results:
                emit(r)
            print(json.dumps({"summary": {"total": len(results), "ok": ok,
                                          "failed": len(results) - ok}},
                             ensure_ascii=False), file=sys.stderr)
            return 0 if ok == len(results) else 2

        if not args.prompt:
            _build_parser().error("缺少 prompt（或使用 --batch / --test）")

        if args.mode in ("i2i", "multi") and not args.image:
            raise AgnesImageError(f"mode={args.mode} 需要通过 --image 提供参考图")

        r = generate(
            prompt=args.prompt,
            size=args.size,
            ratio=args.ratio,
            images=args.image or None,
            mode=args.mode,
            output_format=fmt,
            api_key=args.api_key,
            output_dir=args.out,
            name=args.name,
            timeout=args.timeout,
        )
        emit(r)
        return 0

    except AgnesImageError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"批量 JSON 解析失败: {e}"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "用户中断"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
