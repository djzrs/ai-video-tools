# ai-video-tools

AI 短剧生产管线工具集：七步工作流交互式页面 + Agnes Image 2.5 Flash 生图工具（网页版 / Python 版）+ Agnes Video 2.5 Flash 视频生成工具（Python 版）。

## 仓库结构

```
ai-video-tools/
├── index.html                  # 七步工作流交互式页面（单文件，无依赖）
├── README.md                   # 本文件
├── agnes-image-tool/
│   ├── index.html              # Agnes 生图工具 · 网页版（单文件，无依赖）
│   ├── agnes_image.py          # Agnes 生图工具 · Python 版（零依赖，方便 AI 调用）
│   └── README.md               # 生图工具详细文档
└── agnes-video-tool/
    ├── agnes_video.py          # Agnes 视频生成工具 · Python 版（零依赖，方便 AI 调用）
    └── README.md               # 视频生成工具详细文档
```

## 一、七步工作流页面

单文件 `index.html`，无外部依赖，浏览器直接打开即用。覆盖从小说文本到成片交付的完整生产链路，每个环节配有可直接复制使用的提示词模板。

| 步骤 | 名称 | 核心 |
|---|---|---|
| 1 | 小说转标准剧本 | Show don't Tell，心理描写 100% 外化为动作、表情、道具 |
| 2 | 剧本转分镜表 | 七要素分镜表，每集 10-13 镜、每镜 ≤5 秒，10 维度提示词标准 |
| 3 | 提取资产 | 角色（定妆照/三视图/表情变体）、场景、道具三类视觉 DNA |
| 4 | 生成资产库并锁定一致性 | 多角度角色表 + 连续性锁，Kling Element Library 跨镜复用 |
| 5 | 生成首帧（锚点帧） | 构图/光线/服装全部锁定后再继续，Kling 3.0 可绑 3 个元素 |
| 6 | 图生视频 | 首帧/首尾帧，运动提示词绑定参照物，万相 3.0 提示词公式 |
| 7 | 剪映合成 | jianying-automation 流水线，每分镜独立片段进时间线可逐镜编辑 |

页面功能：顶部 7 个标签点击直达对应步骤；上一步/下一步按钮 + 渐变进度条；键盘 ← → 切换；每步附可复制提示词模板。

```bash
# 本地打开
python3 -m http.server 8000   # 访问 http://localhost:8000
```

## 二、Agnes Image 2.5 Flash 生图工具

基于 [Agnes Image 2.5 Flash](https://agnes-ai.com/zh-Hans/docs/agnes-image-25-flash) 官方文档实现，支持**文生图 / 图生图 / 多图合成**，size 档位 1K/2K/3K/4K，8 种宽高比，URL / Base64 双输出，当前免费。

**网页版**（`agnes-image-tool/index.html`）：可视化调参、实时输出像素预览、请求 JSON 预览、提示词结构模板、本剧资产快插、结果 gallery 与本地历史。

**Python 版**（`agnes-image-tool/agnes_image.py`）：零第三方依赖，方便 AI / 脚本调用——

```python
from agnes_image import generate
r = generate(prompt="...", size="2K", ratio="16:9", output_dir="./out")
```

```bash
# 文生图
python agnes_image.py "发光浮空城市，电影级写实" --size 2K --ratio 16:9 --out ./out
# 图生图 / 多图合成
python agnes_image.py "改为夜景，保留构图" --image input.png --mode i2i
python agnes_image.py "合成海报" --image a.png --image b.png --mode multi
# 批量（JSON 驱动）
python agnes_image.py --batch prompts.json --out ./out
```

详细参数与批量格式见 [`agnes-image-tool/README.md`](agnes-image-tool/README.md)。

## 三、Agnes Video 2.5 Flash 视频生成工具

基于 [Agnes Video 2.5 Flash](https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash) 官方文档实现，支持**文生视频 / 首尾帧控制 / 图片·音频参考生成**，时长 4-12 秒，720P 六种画幅，异步任务轮询 + 自动下载 mp4，输出视频当前限时免费。

> 视频平台（`api.agnes-ai.cn`）与生图平台 Key **不通用**，需单独获取。

```python
from agnes_video import generate
r = generate(prompt="雨后的城市街道，霓虹倒映，电影级运镜", mode="text", seconds="5", output_dir="./out")
```

```bash
# 文生视频
python agnes_video.py "雨后的城市街道，霓虹倒映，电影级运镜" --seconds 5 --out ./out
# 首尾帧控制
python agnes_video.py "人物转身走向窗边" --mode keyframe --first-frame https://example.com/f.png
# 图片参考（≤5 张，prompt 用 <Picture N> 指代）
python agnes_video.py "以 <Picture 1> 为参考，角色奔跑" --mode reference --images https://example.com/c.png
# 查询续取 / 批量 / 连通性测试
python agnes_video.py --query VIDEO_ID --out ./out
python agnes_video.py --batch jobs.json --out ./out
python agnes_video.py --test
```

详细参数与批量格式见 [`agnes-video-tool/README.md`](agnes-video-tool/README.md)。

## 在线预览

https://a075149bde7adfaf1.app.workbuddy.host

> 该链接为沙箱固定发布地址，内容随最新发布更新（当前为 Agnes 生图工具网页版）；七步工作流页面见根目录 `index.html`。
