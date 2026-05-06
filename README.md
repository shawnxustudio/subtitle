# Subtitle Translator

AI 驱动的字幕翻译工具，支持本地文件上传和 Emby 媒体库集成。

## 功能特性

- **多格式支持**：SRT、ASS、VTT 字幕格式
- **多 AI 服务商**：硅基流动、阿里云百炼、月之暗面(Kimi)、智谱 BigModel、Google Gemini、OpenRouter
- **Emby 集成**：直接从媒体库提取字幕、翻译后写回并刷新库
- **灵活输出**：双语字幕或纯译文，支持 UTF-8/UTF-8 BOM 编码
- **速率控制**：可配置 QPS 和批次大小，适配各服务商限制
- **批量处理**：多文件并行翻译，实时进度查看

## 快速开始

### 环境要求

- Python 3.9+
- FFmpeg（Emby 字幕提取功能需要）
- Emby 媒体服务器（可选，仅媒体库功能需要）

### 安装

```bash
git clone https://github.com/shawnxustudio/subtitle.git
cd subtitle
pip install -r requirements.txt
```

### 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写一个 AI 服务商的 API Key（其余留空即可）。

### 启动

```bash
python subtitle_app.py
```

访问 `http://localhost:9897`

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `EMBY_URL` | 是 | Emby 服务器地址，如 `http://192.168.1.100:8096` |
| `EMBY_API_KEY` | 是 | Emby API 密钥（管理员后台生成） |
| `PROXY_HOST` | 否 | HTTP 代理，用于访问 Gemini/OpenRouter 等国际 API |
| `SILICONFLOW_KEY` | 否 | 硅基流动 API Key |
| `ALIYUN_KEY` | 否 | 阿里云百炼 API Key |
| `MOONSHOT_KEY` | 否 | 月之暗面(Kimi) API Key |
| `ZHIPU_KEY` | 否 | 智谱 BigModel API Key |
| `GEMINI_KEY` | 否 | Google Gemini API Key |
| `OPENROUTER_KEY` | 否 | OpenRouter API Key |

> **注意**：不使用 Emby 功能时，`EMBY_URL` 和 `EMBY_API_KEY` 仍需在 `.env` 中填写（可填占位值）。

## Docker 部署

如果 Emby 运行在 Docker 中，需要配置路径映射，使本工具能访问 Emby 容器内的媒体文件：

```
DOCKER_PATH_MAP=/media:/mnt/media
```

格式为 `容器内路径:宿主机路径`，多个映射用分号分隔。

## 使用说明

### 文件翻译

1. 在「上传翻译」页面拖入字幕文件（支持多文件）
2. 点击右上角设置，选择 AI 服务商和模型
3. 选择输出格式（双语/仅译文）
4. 点击翻译，等待完成后下载

### Emby 媒体库翻译

1. 在「媒体库」页面浏览影片或剧集
2. 点击影片/单集上的字幕按钮
3. 选择字幕流（多音轨时可选择）
4. 翻译完成后字幕自动保存至视频同目录，Emby 自动刷新

## 项目结构

```
subtitle/
├── subtitle_app.py        # Flask 后端
├── static/
│   └── subtitle_index.html  # 前端单页应用
├── cache/                 # 模型列表等缓存（自动创建）
├── requirements.txt
├── .env.example
└── .gitignore
```

## 技术栈

- **后端**：Python Flask + APScheduler
- **前端**：原生 JS（无框架）+ CSS 玻璃态设计
- **字幕处理**：FFmpeg / FFprobe
- **AI 接口**：OpenAI 兼容接口 + Gemini API
