# Subtitle Translator

AI 驱动的字幕翻译工具。支持本地字幕文件上传翻译，也可直接对接 Emby 媒体库，从视频中提取字幕、翻译后自动写回。

## 功能

**文件翻译**
- 拖拽上传 SRT / ASS / VTT 字幕文件，支持多文件批量处理
- 翻译完成后直接下载，支持双语对照或纯译文输出

**Emby 媒体库**
- 搜索媒体库，点击影片/剧集直接翻译
- FFmpeg 自动提取内嵌字幕流，可手动选择多语言轨道
- 字幕翻译完成后写回视频同目录，自动触发 Emby 刷新

**AI 服务商**
- 硅基流动 · 阿里云百炼 · 月之暗面(Kimi) · 智谱 BigModel · Google Gemini · OpenRouter
- 可配置 QPS 和批次大小，失败条目自动逐条重试

## 快速开始

```bash
git clone https://github.com/shawnxustudio/subtitle.git
cd subtitle
pip install -r requirements.txt
cp .env.example .env   # 填写 API Key
python subtitle_app.py
```

访问 `http://localhost:9897`

## 环境变量

| 变量 | 说明 |
|------|------|
| `EMBY_URL` | Emby 服务器地址，如 `http://192.168.1.100:8096` |
| `EMBY_API_KEY` | Emby API 密钥（管理员后台生成） |
| `DOCKER_PATH_MAP` | Docker 路径映射，如 `/video:/volume1/video/link`（多条用 `;` 分隔） |
| `PROXY_HOST` | HTTP 代理，用于访问 Gemini / OpenRouter 等国际 API |
| `SILICONFLOW_KEY` | 硅基流动 API Key |
| `ALIYUN_KEY` | 阿里云百炼 API Key |
| `MOONSHOT_KEY` | 月之暗面 (Kimi) API Key |
| `ZHIPU_KEY` | 智谱 BigModel API Key |
| `GEMINI_KEY` | Google Gemini API Key |
| `OPENROUTER_KEY` | OpenRouter API Key |

AI Key 至少填一个，其余留空。不使用 Emby 功能时 `EMBY_URL` / `EMBY_API_KEY` 也可填占位值。

## 群晖 NAS 部署

```bash
# 安装依赖（群晖套件中心先装 Python 3）
cd /volume1/script/subtitle
pip3 install -r requirements.txt
cp .env.example .env && vi .env

# 后台运行
nohup python3 subtitle_app.py > app.log 2>&1 &
```

开机自启：控制面板 → 任务计划 → 触发的任务 → 开机，脚本填：

```bash
cd /volume1/script/subtitle && nohup python3 subtitle_app.py > app.log 2>&1 &
```

Emby 在 Docker 中时，通过 `DOCKER_PATH_MAP` 配置容器路径到宿主机路径的映射。

## 技术栈

- **后端**：Python · Flask · FFmpeg
- **前端**：原生 JS · 玻璃态 UI（无框架）
- **AI 接口**：OpenAI 兼容 API + Google Gemini API
