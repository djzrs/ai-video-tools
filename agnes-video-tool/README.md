# Agnes Video 2.5 Flash 视频生成工具

基于 [Agnes Video 2.5 Flash](https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash) 官方文档实现的视频生成工具（Python 版）：

| 形态 | 文件 | 适用场景 |
|---|---|---|
| Python 版 | `agnes_video.py` | **AI / 脚本调用**：零依赖（仅标准库）、可导入、可 CLI、可批量 |

支持 **文生视频 / 首尾帧控制 / 图片·音频参考生成** 三种工作流；时长 4-12 秒；720P 六种画幅；异步任务创建 → 轮询 → 自动下载 mp4。输出视频当前**限时免费**（¥0/秒，以平台公告为准）。

> **端点说明**：官方文档示例为 `api.agnes-ai.cn`（该域需视频平台单独签发的 Key）。本工具默认走 `apihub.agnes-ai.com/v1`——与生图工具同平台、**同一把 Key 通用**（2026-10-14 实测验证），查询端点自动推导为 `{域名}/agnesapi`。可用 `--base-url` 或环境变量 `AGNES_BASE_URL` 覆盖。

---

## 一、快速开始

零第三方依赖，Python 3.8+。API Key 通过 `--api-key` 参数或环境变量 `AGNES_API_KEY` 提供。

```bash
export AGNES_API_KEY="YOUR_API_KEY"
export AGNES_BASE_URL="https://api.agnes-ai.cn/v1"   # 可选，默认即此值
```

### 命令行

```bash
# 文生视频（创建任务 → 自动轮询 → 下载 mp4 到 ./out）
python agnes_video.py "雨后的未来城市街道，霓虹灯倒映在地面，一辆银色跑车缓慢驶过，电影级运镜" --seconds 5 --out ./out

# 首尾帧控制（first_frame/last_frame 至少一个，须为公网 URL）
python agnes_video.py "人物从首帧姿态自然转身走向窗边，镜头缓慢推进" --mode keyframe \
    --first-frame https://example.com/first.png --last-frame https://example.com/last.png

# 图片参考（最多 5 张，prompt 中用 <Picture N> 指代）
python agnes_video.py "以 <Picture 1> 中的角色和美术风格为参考，角色在花田中自然奔跑，保持外观一致" \
    --mode reference --images https://example.com/character.png

# 音频参考（最多 3 段，prompt 中用 <Audio N> 指代）
python agnes_video.py "以 <Audio 1> 的节奏和环境氛围作为参考，生成电影感夜间驾驶画面" \
    --mode reference --audios https://example.com/ref.mp3

# 竖屏画幅（720P 输出 720x1280）
python agnes_video.py "短剧式特写：女主角含泪抬眼，镜头缓慢推近" --aspect-ratio 9:16 --seconds 8

# 查询并下载已有任务（断点续取）
python agnes_video.py --query VIDEO_ID --out ./out

# 批量生成（JSON 驱动，顺序执行）
python agnes_video.py --batch jobs.json --out ./out

# 测试 API Key 连通性（创建一个 4 秒真实任务，当前限时免费）
python agnes_video.py --test
```

### 作为库调用

```python
from agnes_video import generate, AgnesVideoError

try:
    result = generate(
        prompt="雨后的未来城市街道，霓虹灯倒映在地面，电影级运镜",
        mode="text",                      # text | keyframe | reference
        seconds="5",                      # "4"~"12"
        aspect_ratio="16:9",
        api_key="sk-...",                 # 或设环境变量 AGNES_API_KEY
        output_dir="./out",               # 结果自动保存为 mp4
        name="ep01_sc01",                 # 文件名（默认取提示词前 24 字）
    )
    print(result["file"], result["dims"], result["video_id"])
except AgnesVideoError as e:
    print("生成失败:", e)
```

### 批量生成（JSON 驱动）

```bash
python agnes_video.py --batch jobs.json --out ./out
```

`jobs.json` 格式：

```json
{
  "defaults": { "mode": "text", "seconds": "5", "aspect_ratio": "16:9" },
  "items": [
    { "prompt": "出租屋日态，阳光洒进客厅，固定机位", "name": "sc01_day" },
    { "prompt": "人物转身走向窗边", "mode": "keyframe",
      "first_frame": "https://example.com/f.png", "seconds": "8", "name": "kb01" },
    { "prompt": "以 <Picture 1> 为参考，角色奔跑", "mode": "reference",
      "images": ["https://example.com/char.png"], "name": "ref01" }
  ]
}
```

---

## 二、参数速查

| 参数 | 取值 | 说明 |
|---|---|---|
| `model` | `agnes-video-2.5-flash` | 固定 |
| `prompt` | 文本 | 必填；reference 模式可用 `<Picture N>` / `<Audio N>` 指代素材 |
| `mode` | `text`/`keyframe`/`reference` | 必填 |
| `seconds` | 字符串 `"4"`~`"12"` | 默认 `"5"` |
| `size` | `"720P"` | **Flash 固定**，其他值返回 HTTP 400 |
| `aspect_ratio` | `21:9`/`16:9`/`4:3`/`1:1`/`3:4`/`9:16` | 默认 `16:9` |
| `first_frame` / `last_frame` | 公网 URL | keyframe 模式至少一个 |
| `images` | URL 数组（≤5） | reference 模式 |
| `audios` | URL 数组（≤3） | reference 模式 |
| `seed` | 整数 | 可选 |
| `n` | `1` | 当前仅支持 1 |

### 画幅与输出像素（720P）

| aspect_ratio | 输出像素 |
|---|---|
| `21:9` | 1680x720 |
| `16:9` | 1280x704 |
| `4:3` | 960x720 |
| `1:1` | 720x720 |
| `3:4` | 720x960 |
| `9:16` | 720x1280 |

---

## 三、工作方式

1. **创建任务**：`POST {base_url}/videos`，响应中保存 `video_id`。
2. **轮询任务**：`GET {域名}/agnesapi?video_id=<ID>&model_name=agnes-video-2.5-flash`，建议 1-2 秒一次（本工具默认 2 秒），直至 `status` 为 `completed`/`failed`。`internal_status`/`internal_progress` 为内部字段，以 `status`/`progress` 为准。
3. **下载视频**：`status=completed` 后取响应顶层 `url` 字段下载，保存为 `{HHMMSS}_{name}.mp4`。
4. **断点续取**：任务创建后无论是否下载，都可用 `--query VIDEO_ID` 随时续查下载。

---

## 四、注意事项

1. **端点与 Key**：工具默认 `apihub.agnes-ai.com/v1`（与生图工具同平台，Key 通用）；官方文档示例域 `api.agnes-ai.cn` 需视频平台单独签发的 Key，两种域可用 `--base-url` 切换。
2. **媒体必须公网可访问**：`first_frame`/`last_frame`/`images`/`audios` 只接受 http(s) URL，本地文件需先托管到公网（对象存储/图床等）；本地路径会报错并提示。
3. **模式互斥规则**：text 模式禁止任何媒体字段；keyframe 模式禁止 images/audios；reference 模式禁止 first_frame/last_frame 与 videos。
4. **查询必须带 model_name**：`keyframe`/`reference` 模式强制；纯 `video_id` 查询仅适用于 text 模式。
5. **轮询超时**：默认单任务总超时 900 秒（`--timeout` 可调）；超时后任务仍在云端，可用 `--query` 续取。
6. **计费**：当前限时免费（输出秒数、参考图片均按 ¥0 计费），政策以 Agnes AI 平台最新公告为准。
7. 不要在前端代码、日志或公开仓库中暴露 API Key。

---

## 五、AI 集成约定

- `generate()` 返回 dict：`video_id / status / file / url / seconds / dims / raw`。
- 异常统一为 `AgnesVideoError`，`message` 可直接展示。
- 批量模式单项失败不中断整体，最后汇总 `成功数/总数`，退出码 0（全成）/ 1（有失败）。
- CLI 结果以单行 JSON 输出到 stdout，错误输出到 stderr。
