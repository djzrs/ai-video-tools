# Agnes Image 2.5 Flash 生图工具

基于 [Agnes Image 2.5 Flash](https://agnes-ai.com/zh-Hans/docs/agnes-image-25-flash) 官方文档实现的生图工具，提供**网页版**与 **Python 版**两种形态：

| 形态 | 文件 | 适用场景 |
|---|---|---|
| 网页版 | `index.html` | 人工操作：可视化调参、资产快插、结果 gallery |
| Python 版 | `agnes_image.py` | **AI / 脚本调用**：零依赖（仅标准库）、可导入、可 CLI、可批量 |

支持 **文生图 / 图生图 / 多图合成** 三种工作流；`size` 档位 1K/2K/3K/4K；宽高比 8 种；URL / Base64 双输出。当前所有档位与参考图**免费**。

---

## 一、Python 版（方便 AI 调用）

零第三方依赖，Python 3.8+。API Key 通过 `--api-key` 参数或环境变量 `AGNES_API_KEY` 提供。

### 作为库调用

```python
from agnes_image import generate, AgnesImageError

try:
    result = generate(
        prompt="林小颖（二十四岁，鹅蛋脸白皙通透，月牙眼；浅色碎花裙），电影级写实，横屏构图",
        size="2K", ratio="16:9",          # 输出 2624x1472
        output_format="url",               # url / b64_json
        api_key="sk-...",                  # 或设环境变量 AGNES_API_KEY
        output_dir="./out",                # 结果自动落盘
        name="char_linqianying",           # 文件名前缀
    )
    print(result["file"], result["dims"], result["elapsed"])
except AgnesImageError as e:
    print("生成失败:", e)
```

### 命令行

```bash
# 文生图（URL 输出并自动下载到 ./out）
python agnes_image.py "日出时分薄雾峡谷上方的发光浮空城市，电影级写实" --size 2K --ratio 16:9 --out ./out

# 图生图（本地图片自动转 Base64）
python agnes_image.py "改为赛博朋克夜景，保留原始构图" --image input.png --mode i2i

# 多图合成（多张参考图按顺序传入）
python agnes_image.py "将两张图合成一张电影级海报" --image a.png --image b.png --mode multi

# Base64 输出（文生图走 return_base64，符合文档规则）
python agnes_image.py "产品图，白底" --format b64 --out ./out

# 测试 API Key 连通性
python agnes_image.py --test
```

### 批量生成（JSON 驱动）

```bash
python agnes_image.py --batch prompts.json --out ./out
```

`prompts.json` 格式：

```json
{
  "defaults": { "size": "2K", "ratio": "16:9", "format": "url" },
  "items": [
    { "prompt": "林小颖定妆照……", "name": "char_01" },
    { "prompt": "改为夜景，保留构图", "images": ["ref.png"], "mode": "i2i" },
    { "prompt": "双人海报", "images": ["a.png", "b.png"], "mode": "multi", "size": "1K" }
  ]
}
```

### AI 集成约定

- 每条结果以**单行 JSON** 输出到 stdout（字段：`ok / file / url / prompt / mode / size / ratio / dims / elapsed`），错误信息以 JSON 输出到 stderr，退出码 0/1/2（2=批量部分失败）。
- 异常统一为 `AgnesImageError`，`message` 可直接展示。
- 网络/超时错误自动退避重试（默认 2 次）；API 业务错误（如 Key 无效）立即失败不重试。
- 结果保存：URL 输出自动下载；Base64 输出自动解码；均存为 PNG 到 `--out` 目录。

---

## 二、网页版

单文件 `index.html`，浏览器直接打开即可（也可部署为静态站点）。

- **三模式切换**：文生图 / 图生图 / 多图合成，参考图支持 URL 粘贴或本地上传（自动转 Base64）
- **参数面板**：size 档位 + ratio 宽高比，实时预览输出像素（如 2K+16:9 = 2624×1472），附全档位对照表
- **输出格式**：URL / Base64（严格遵循文档：`response_format` 放 `extra_body` 内）
- **请求预览**：实时显示实际发送的 JSON，便于核对
- **提示词结构模板**：文生图 / 图生图 / 多图合成 / 高密度四种官方推荐结构，一键插入
- **本剧资产快插**：内置《前男友和闺蜜结婚后，我送了他们一份大礼》角色锚点句与场景描述（点击追加）
- **结果管理**：gallery 展示、下载、复制提示词/URL/Base64、复用参数、URL 结果本地历史（localStorage）
- API Key 仅存本机浏览器 localStorage

---

## 三、参数速查

| 参数 | 取值 | 说明 |
|---|---|---|
| `model` | `agnes-image-2.5-flash` | 固定 |
| `prompt` | 文本 | 必填 |
| `size` | `1K`/`2K`/`3K`/`4K` | 必填，档位式 |
| `ratio` | `1:1`/`3:4`/`4:3`/`16:9`/`9:16`/`2:3`/`3:2`/`21:9` | 默认 `1:1`（本工具默认 16:9） |
| `extra_body.image` | URL / Data URI 数组 | 图生图与多图合成必填 |
| `extra_body.response_format` | `url`/`b64_json` | **必须放 extra_body 内，勿放顶层** |
| `return_base64` | `true` | 仅文生图 Base64 输出使用 |

输出像素示例：`2K+16:9=2624x1472`｜`1K+1:1=1024x1024`｜`4K+21:9=6272x2688`｜`3K+9:16=2208x3936`

## 四、注意事项

1. 客户端超时建议 60-360s（Python 版默认 360s）。
2. 参考图需公共可访问 URL；不可公开访问时用本地上传（自动转 Base64）。
3. 图生图**不要**传 `tags: ["img2img"]`。
4. 本地图片超过 8MB 会报错，请先压缩。
