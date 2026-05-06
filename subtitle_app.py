from flask import Flask, request, jsonify, send_from_directory, Response, send_file
from flask_cors import CORS
import requests
import json as json_lib
import re, os, time, threading, subprocess, sys, hashlib, shutil, tempfile, datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# ========== 配置区域 ==========
EMBY_URL     = os.environ.get("EMBY_URL", "")
EMBY_API_KEY = os.environ.get("EMBY_API_KEY", "")

_proxy = os.environ.get("PROXY_HOST", "")
PROXIES = {"http": _proxy, "https": _proxy} if _proxy else {}

# ★ AI 服务商 API Keys
AI_KEYS = {
    "siliconflow": {
        "key":      os.environ.get("SILICONFLOW_KEY", ""),
        "base_url": "https://api.siliconflow.cn/v1",
        "type":     "openai",
    },
    "aliyun": {
        "key":      os.environ.get("ALIYUN_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "type":     "openai",
    },
    "moonshot": {
        "key":      os.environ.get("MOONSHOT_KEY", ""),
        "base_url": "https://api.moonshot.cn/v1",
        "type":     "openai",
    },
    "zhipu": {
        "key":      os.environ.get("ZHIPU_KEY", ""),
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "type":     "openai",
    },
    "gemini": {
        "key":      os.environ.get("GEMINI_KEY", ""),
        "base_url": "",
        "type":     "gemini",
    },
    "openrouter": {
        "key":      os.environ.get("OPENROUTER_KEY", ""),
        "base_url": "https://openrouter.ai/api/v1",
        "type":     "openai",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/subtitle-tool",
            "X-Title":      "Subtitle Translator",
        },
    },
}

# 排除关键词（模型 id 包含这些词则过滤掉）
MODEL_EXCLUDE_KEYWORDS = [
    "embed", "embedding", "whisper", "tts", "speech", "dall-e", "dall_e",
    "image", "vision", "rerank", "moderation", "audio", "video",
    "instruct-v3", "text-search", "text-similarity",
]

# ─────────────────────────────────────────────
#  路径
# ─────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
IMAGE_DIR = BASE_DIR / "static" / "img_cache"

CACHE_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

EMBY_LIBRARY_FILE  = CACHE_DIR / "emby_library.json"
EMBY_EPISODES_FILE = CACHE_DIR / "emby_episodes.json"
MODELS_CACHE_FILE  = CACHE_DIR / "models.json"

# ─────────────────────────────────────────────
#  内存缓存
# ─────────────────────────────────────────────
_mem: dict = {}
MEM_TTL = 60

def _mem_get(key):
    e = _mem.get(key)
    return e[0] if e and time.time() < e[1] else None

def _mem_set(key, data, ttl=MEM_TTL):
    _mem[key] = (data, time.time() + ttl)

def _read_json(path: Path, default=None):
    try:
        return json_lib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _write_json(path: Path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json_lib.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))

# ─────────────────────────────────────────────
#  模型列表抓取与缓存
# ─────────────────────────────────────────────

def _is_text_model(model_id: str) -> bool:
    mid = model_id.lower()
    return not any(kw in mid for kw in MODEL_EXCLUDE_KEYWORDS)

def _fetch_models_openai(provider: str, cfg: dict) -> list:
    """通用 OpenAI 兼容接口获取模型列表"""
    base_url = cfg["base_url"].rstrip("/")
    headers  = {"Authorization": f"Bearer {cfg['key']}"}
    headers.update(cfg.get("extra_headers", {}))
    # openrouter 不需要走内网代理，其他国内服务商也不需要
    r = requests.get(f"{base_url}/models", headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    models = data.get("data", data) if isinstance(data, dict) else data
    result = []
    for m in models:
        mid = m.get("id", "")
        if not mid or not _is_text_model(mid):
            continue
        # openrouter 额外按 modality 过滤
        if provider == "openrouter":
            arch = m.get("architecture", {})
            modality = arch.get("modality", "") or arch.get("input_modalities", "")
            if isinstance(modality, list):
                if "text" not in modality:
                    continue
            elif isinstance(modality, str) and modality and "text" not in modality:
                continue
        result.append(mid)
    return sorted(result)

def _fetch_models_gemini(cfg: dict) -> list:
    """Gemini 模型列表"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={cfg['key']}&pageSize=200"
    r = requests.get(url, timeout=20, proxies=PROXIES)
    r.raise_for_status()
    result = []
    for m in r.json().get("models", []):
        name = m.get("name", "")          # e.g. "models/gemini-2.5-flash"
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        mid = name.replace("models/", "")
        if _is_text_model(mid):
            result.append(mid)
    return sorted(result)

def fetch_models_for_provider(provider: str) -> list:
    """拉取单个服务商的模型列表，返回 id 列表"""
    cfg = AI_KEYS.get(provider)
    if not cfg:
        return []
    try:
        if cfg["type"] == "gemini":
            return _fetch_models_gemini(cfg)
        else:
            return _fetch_models_openai(provider, cfg)
    except Exception as e:
        print(f"[models] fetch {provider} failed: {e}")
        return []

def refresh_all_models():
    """拉取所有服务商模型列表，合并写入缓存文件"""
    print("[models] 开始刷新所有服务商模型列表…")
    cache = _read_json(MODELS_CACHE_FILE, {})
    updated = False
    for provider in AI_KEYS:
        models = fetch_models_for_provider(provider)
        if models:
            cache[provider] = {"models": models, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            print(f"[models] {provider}: {len(models)} 个模型")
            updated = True
        else:
            print(f"[models] {provider}: 拉取失败，保留旧缓存")
    if updated:
        _write_json(MODELS_CACHE_FILE, cache)
    print("[models] 刷新完成")
    return cache

def _models_scheduler():
    """后台线程：每天凌晨 1 点刷新一次"""
    # 启动时若缓存不存在，先立即拉一次
    if not MODELS_CACHE_FILE.exists():
        print("[models] 首次启动，立即拉取模型列表…")
        threading.Thread(target=refresh_all_models, daemon=True).start()

    while True:
        now  = datetime.datetime.now()
        # 计算距下一个凌晨 1:00 的秒数
        next_run = now.replace(hour=1, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        sleep_sec = (next_run - now).total_seconds()
        print(f"[models] 下次刷新：{next_run.strftime('%Y-%m-%d %H:%M:%S')}（{int(sleep_sec//3600)}h后）")
        time.sleep(sleep_sec)
        refresh_all_models()

# 启动调度线程
threading.Thread(target=_models_scheduler, daemon=True).start()

# ─────────────────────────────────────────────
#  ffmpeg / 临时目录
# ─────────────────────────────────────────────
FFMPEG_PATH = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
TMP_DIR = Path(tempfile.gettempdir()) / "subtitle_tool"
TMP_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
#  任务状态
# ─────────────────────────────────────────────
_subtitle_tasks: dict = {}

def _st_new_task(task_id: str):
    _subtitle_tasks[task_id] = {"status": "pending", "log": [], "result": None, "error": None}

def _st_log(task_id: str, msg: str):
    print(f"[subtitle:{task_id}] {msg}")
    if task_id in _subtitle_tasks:
        _subtitle_tasks[task_id]["log"].append({"ts": time.strftime("%H:%M:%S"), "msg": msg})

def _st_done(task_id: str, result=None):
    if task_id in _subtitle_tasks:
        _subtitle_tasks[task_id]["status"] = "done"
        _subtitle_tasks[task_id]["result"] = result

def _st_fail(task_id: str, error: str):
    if task_id in _subtitle_tasks:
        _subtitle_tasks[task_id]["status"] = "error"
        _subtitle_tasks[task_id]["error"] = error

# ─────────────────────────────────────────────
#  Docker 路径转换
# ─────────────────────────────────────────────
_PATH_MAPS = []
for _entry in os.environ.get("DOCKER_PATH_MAP", "").split(";"):
    _entry = _entry.strip()
    if ":" in _entry:
        _src, _dst = _entry.split(":", 1)
        if _src and _dst:
            _PATH_MAPS.append((_src.strip(), _dst.strip()))

def _docker_to_local(path):
    if not path:
        return path
    for src, dst in _PATH_MAPS:
        if path.startswith(src):
            return dst + path[len(src):]
    return path

# ─────────────────────────────────────────────
#  字幕提取（ffmpeg）
# ─────────────────────────────────────────────
def _extract_subtitle(video_path: str, task_id: str, stream_index=None):
    vp = Path(video_path)
    out_srt = TMP_DIR / f"{task_id}_{vp.stem}.srt"

    if stream_index is not None:
        _st_log(task_id, f"使用指定字幕流 #0:{stream_index}")
        map_arg = f"0:{stream_index}"
    else:
        probe_cmd = [FFMPEG_PATH, "-i", str(vp), "-hide_banner", "-loglevel", "info"]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True)
        stderr = probe.stderr
        stream_idx = None
        for line in stderr.splitlines():
            m = re.search(r"Stream #0:(\d+).*Subtitle", line, re.IGNORECASE)
            if m:
                stream_idx = m.group(1)
                _st_log(task_id, f"找到字幕流 #0:{stream_idx}：{line.strip()}")
                break
        if stream_idx is None:
            _st_log(task_id, "未明确找到字幕流，尝试 0:s:0")
            map_arg = "0:s:0"
        else:
            map_arg = f"0:{stream_idx}"

    cmd = [FFMPEG_PATH, "-y", "-i", str(vp), "-map", map_arg, "-c:s", "srt", str(out_srt)]
    _st_log(task_id, f"提取命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _st_log(task_id, f"ffmpeg stderr: {result.stderr[-500:]}")
        return None
    if not out_srt.exists() or out_srt.stat().st_size == 0:
        return None
    _st_log(task_id, f"字幕提取成功: {out_srt.name} ({out_srt.stat().st_size} bytes)")
    return str(out_srt)

def _process_srt(srt_path: str, task_id: str) -> str:
    out_path = srt_path.replace(".srt", "_processed.srt")
    with open(srt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    output_lines = []
    i = 0
    current_index = 1
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit() and i + 1 < len(lines):
            time_line = lines[i + 1].strip()
            text_lines = []
            j = i + 2
            while j < len(lines) and lines[j].strip():
                text_lines.append(lines[j].strip())
                j += 1
            merged = " ".join(text_lines)
            merged = re.sub(r"\s+", " ", merged).strip()
            output_lines += [str(current_index), time_line, merged, ""]
            current_index += 1
            i = j + 1
        else:
            i += 1
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(output_lines) + "\n")
    _st_log(task_id, f"字幕预处理完成: {current_index - 1} 条")
    return out_path

def _parse_srt(text):
    blocks = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = chunk.strip().splitlines()
        if len(lines) < 3: continue
        if not lines[0].strip().isdigit(): continue
        if "-->" not in lines[1]: continue
        blocks.append({
            "idx":     lines[0].strip(),
            "time":    lines[1].strip(),
            "content": "\n".join(lines[2:]).strip()
        })
    return blocks

def _run_subtitle_pipeline(task_id: str, video_path: str, stream_index=None):
    try:
        _subtitle_tasks[task_id]["status"] = "running"
        video_path = _docker_to_local(video_path)
        vp = Path(video_path)
        _st_log(task_id, f"▶ 开始处理: {vp.name}")
        _st_log(task_id, "① 提取字幕…")
        srt_raw = _extract_subtitle(video_path, task_id, stream_index)
        if not srt_raw:
            raise RuntimeError("未能提取到字幕，请确认视频文件包含内嵌字幕流")
        _st_log(task_id, "② 预处理字幕…")
        srt_processed = _process_srt(srt_raw, task_id)
        with open(srt_processed, "r", encoding="utf-8") as f:
            srt_text = f.read()
        blocks = _parse_srt(srt_text)
        _st_log(task_id, f"③ 共解析 {len(blocks)} 条字幕，等待前端翻译…")
        try:
            Path(srt_raw).unlink(missing_ok=True)
            Path(srt_processed).unlink(missing_ok=True)
        except Exception:
            pass
        _st_done(task_id, {"srt_text": srt_text, "blocks": len(blocks), "video_path": str(vp)})
        _st_log(task_id, f"✅ 提取完成，共 {len(blocks)} 条，等待前端翻译并写回")
    except Exception as e:
        _st_log(task_id, f"❌ 错误: {e}")
        _st_fail(task_id, str(e))

def _refresh_emby_dir(directory: Path):
    try:
        folder_name = directory.name
        r = requests.get(f"{EMBY_URL}/Items", params={
            "api_key": EMBY_API_KEY, "SearchTerm": folder_name,
            "IncludeItemTypes": "Season,Series,Movie",
            "Recursive": "true", "Fields": "Path", "Limit": 10,
        }, timeout=10)
        if r.status_code != 200:
            return
        for item in r.json().get("Items", []):
            item_path      = item.get("Path", "")
            item_path_norm = item_path.replace("/volume1/video/link", "/video")
            dir_norm       = str(directory).replace("/volume1/video/link", "/video")
            if item_path_norm == dir_norm or item_path_norm.endswith(folder_name):
                item_id   = item.get("Id")
                requests.post(f"{EMBY_URL}/Items/{item_id}/Refresh", params={
                    "api_key": EMBY_API_KEY, "Recursive": "true",
                    "MetadataRefreshMode": "None", "ImageRefreshMode": "None",
                    "ReplaceAllMetadata": "false", "ReplaceAllImages": "false",
                }, timeout=10)
                return
        requests.post(f"{EMBY_URL}/Library/Refresh", params={"api_key": EMBY_API_KEY}, timeout=10)
    except Exception as e:
        print(f"[subtitle] Emby refresh error: {e}")

def _build_library_list():
    result   = []
    seen_ids = set()
    try:
        r = requests.get(f"{EMBY_URL}/Items", params={
            "api_key": EMBY_API_KEY, "SortBy": "SortName", "SortOrder": "Ascending",
            "IncludeItemTypes": "Movie,Series", "Recursive": "true",
            "Fields": "ProviderIds,Path,ProductionYear,ImageTags,PrimaryImageTag",
            "Limit": 2000,
        }, timeout=25)
        if r.status_code != 200:
            return result
        lib = _read_json(EMBY_LIBRARY_FILE) or {}
        for item in r.json().get("Items", []):
            itype        = item.get("Type", "")
            provider_ids = item.get("ProviderIds", {})
            tmdbid       = str(provider_ids.get("Tmdb", "") or provider_ids.get("tmdb", ""))
            title        = item.get("Name", "")
            year         = str(item.get("ProductionYear", "") or "")
            emby_id      = item.get("Id", "")
            path         = _docker_to_local(item.get("Path", "") or "") or item.get("Path", "")
            if not title:
                continue
            dedup_key = f"{tmdbid}:{itype}" if tmdbid else f"title:{title}:{itype}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)
            img_tags    = item.get("ImageTags") or {}
            primary_tag = img_tags.get("Primary") or item.get("PrimaryImageTag") or ""
            poster = f"/api/emby_img?item_id={emby_id}&tag={primary_tag}" if emby_id else ""
            if itype == "Movie":
                result.append({"title": title, "tmdbid": tmdbid, "type": "电影",
                                "year": year, "path": path, "poster": poster, "seasons": []})
            elif itype == "Series":
                sinfo   = lib.get("series", {}).get(tmdbid, {}) if tmdbid else {}
                seasons = sinfo.get("seasons", []) if isinstance(sinfo, dict) else []
                result.append({"title": title, "tmdbid": tmdbid, "type": "剧集",
                                "year": year, "path": path, "poster": poster, "seasons": seasons})
    except Exception as e:
        print(f"_build_library_list error: {e}")
    return result

# ═══════════════════════════════════════════════
#  路由
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory("static", "subtitle_index.html")

# ── Emby 图片代理 ──
@app.route("/api/emby_img")
def proxy_emby_image():
    item_id = request.args.get("item_id", "")
    tag     = request.args.get("tag", "")
    if not item_id:
        return "", 400
    if not EMBY_URL or not EMBY_API_KEY:
        return jsonify({"error": "Emby 未配置，请在 .env 中填写 EMBY_URL 和 EMBY_API_KEY"}), 503
    emby_url = f"{EMBY_URL}/Items/{item_id}/Images/Primary"
    params   = {"api_key": EMBY_API_KEY, "maxHeight": "300"}
    if tag:
        params["tag"] = tag
    cache_key = hashlib.md5(f"emby_{item_id}_{tag}".encode()).hexdigest()
    local     = IMAGE_DIR / f"{cache_key}.jpg"
    if local.exists() and local.stat().st_size > 0:
        return send_file(str(local), mimetype="image/jpeg", max_age=86400, conditional=True)
    try:
        resp = requests.get(emby_url, params=params, timeout=10)
        if resp.status_code != 200:
            return Response("", status=404)
        content      = resp.content
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        def _save():
            try:
                tmp = local.with_suffix(".tmp")
                tmp.write_bytes(content)
                os.replace(str(tmp), str(local))
            except Exception:
                pass
        threading.Thread(target=_save, daemon=True).start()
        return Response(content, content_type=content_type,
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        print(f"proxy_emby_image error: {e}")
        return Response("", status=502)

# ── AI 代理 ──
@app.route("/api/ai_proxy", methods=["POST"])
def ai_proxy():
    body     = request.json or {}
    provider = body.get("provider", "")
    model    = body.get("model", "")
    messages = body.get("messages", [])

    if provider not in AI_KEYS:
        return jsonify({"error": f"未知服务商: {provider}"}), 400
    if not model:
        return jsonify({"error": "model 不能为空"}), 400
    if not messages:
        return jsonify({"error": "messages 不能为空"}), 400

    cfg     = AI_KEYS[provider]
    api_key = cfg["key"]

    if not api_key:
        return jsonify({"error": f"{provider} 的 API Key 未配置，请在 .env 中填写"}), 400

    try:
        if cfg["type"] == "gemini":
            system_parts = [{"text": m["content"]} for m in messages if m["role"] == "system"]
            user_parts   = [{"text": m["content"]} for m in messages if m["role"] == "user"]
            payload = {"contents": [{"parts": user_parts}]}
            if system_parts:
                payload["system_instruction"] = {"parts": system_parts}
            url  = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = requests.post(url, json=payload, timeout=120, proxies=PROXIES)
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            base_url = cfg["base_url"].rstrip("/")
            is_mt = any(k in model for k in ("qwen-mt", "mt-flash", "mt-plus", "mt-turbo", "mt-lite"))
            if is_mt:
                messages = [m for m in messages if m["role"] != "system"]
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            headers.update(cfg.get("extra_headers", {}))
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={"model": model, "max_tokens": 4096, "messages": messages},
                timeout=120,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"text": text})
    except requests.HTTPError as e:
        return jsonify({"error": f"API 请求失败: {e.response.status_code} {e.response.text[:200]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── 模型列表 ──
@app.route("/api/models/<provider>")
def get_models(provider):
    if provider not in AI_KEYS:
        return jsonify({"error": "未知服务商"}), 400
    cache = _read_json(MODELS_CACHE_FILE, {})
    entry = cache.get(provider, {})
    models     = entry.get("models", [])
    updated_at = entry.get("updated_at", "")
    return jsonify({"provider": provider, "models": models, "updated_at": updated_at})

@app.route("/api/models/<provider>/refresh", methods=["POST"])
def refresh_models(provider):
    """手动触发单个服务商的模型列表刷新"""
    if provider not in AI_KEYS:
        return jsonify({"error": "未知服务商"}), 400
    def _do():
        models = fetch_models_for_provider(provider)
        if models:
            cache = _read_json(MODELS_CACHE_FILE, {})
            cache[provider] = {"models": models, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            _write_json(MODELS_CACHE_FILE, cache)
            print(f"[models] 手动刷新 {provider}: {len(models)} 个模型")
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "message": f"正在后台刷新 {provider} 的模型列表"})

# ── 字幕路由 ──
@app.route("/api/subtitle/health")
def subtitle_health():
    ffmpeg_ok = shutil.which("ffmpeg") is not None or Path(FFMPEG_PATH).exists()
    return jsonify({"status": "ok", "ffmpeg": FFMPEG_PATH, "ffmpeg_found": ffmpeg_ok})

@app.route("/api/subtitle/library")
def subtitle_library():
    if not EMBY_URL or not EMBY_API_KEY:
        return jsonify({"error": "Emby 未配置，请在 .env 中填写 EMBY_URL 和 EMBY_API_KEY"}), 503
    cached = _mem_get("subtitle_library")
    if cached is not None:
        return jsonify({"items": cached, "count": len(cached)}), 200
    try:
        items = _build_library_list()
        _mem_set("subtitle_library", items, ttl=300)
        return jsonify({"items": items, "count": len(items)}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/subtitle/episodes", methods=["POST"])
def subtitle_episodes():
    if not EMBY_URL or not EMBY_API_KEY:
        return jsonify({"error": "Emby 未配置，请在 .env 中填写 EMBY_URL 和 EMBY_API_KEY"}), 503
    body        = request.json or {}
    tmdbid      = body.get("tmdbid", "").strip()
    series_path = body.get("series_path", "").strip()
    if not tmdbid and not series_path:
        return jsonify({"error": "需要 tmdbid 或 series_path"}), 400
    try:
        emby_id  = None
        lib      = _read_json(EMBY_LIBRARY_FILE) or {}
        ep_cache = _read_json(EMBY_EPISODES_FILE) or {}
        if tmdbid:
            sinfo = lib.get("series", {}).get(tmdbid)
            if isinstance(sinfo, dict):
                emby_id = sinfo.get("emby_id")
        if not emby_id and series_path:
            folder = series_path.rstrip("/").split("/")[-1]
            for tid, sinfo in lib.get("series", {}).items():
                if not isinstance(sinfo, dict): continue
                cached_path = sinfo.get("path", "")
                if cached_path.endswith(folder) or sinfo.get("name", "") == folder.split(" (")[0]:
                    emby_id = sinfo.get("emby_id")
                    tmdbid  = tmdbid or tid
                    break
        if emby_id and emby_id in ep_cache:
            episodes = ep_cache[emby_id]
            return jsonify({"episodes": episodes, "count": len(episodes), "source": "cache"})
        series_emby_id = emby_id
        if not series_emby_id:
            search_name = series_path.rstrip("/").split("/")[-1] if series_path else ""
            if search_name:
                r = requests.get(f"{EMBY_URL}/Items", params={
                    "api_key": EMBY_API_KEY, "SearchTerm": search_name,
                    "IncludeItemTypes": "Series", "Recursive": "true",
                    "Fields": "ProviderIds,Path", "Limit": 10,
                }, timeout=15)
                if r.status_code == 200:
                    for item in r.json().get("Items", []):
                        if item.get("Path", "").endswith(search_name):
                            series_emby_id = item.get("Id")
                            break
        if not series_emby_id:
            return jsonify({"error": "在 Emby 中找不到该剧集，请先更新缓存"}), 404
        r = requests.get(f"{EMBY_URL}/Items", params={
            "api_key": EMBY_API_KEY, "ParentId": series_emby_id,
            "IncludeItemTypes": "Episode", "Recursive": "true",
            "Fields": "Path,IndexNumber,ParentIndexNumber,Name",
            "SortBy": "ParentIndexNumber,IndexNumber", "SortOrder": "Ascending",
            "Limit": 2000,
        }, timeout=20)
        if r.status_code != 200:
            return jsonify({"error": f"Emby 查集失败: {r.status_code}"}), 502
        episodes = []
        for item in r.json().get("Items", []):
            raw_path = item.get("Path", "")
            if not raw_path: continue
            sn    = item.get("ParentIndexNumber")
            ep_no = item.get("IndexNumber")
            label = f"S{sn:02d}E{ep_no:02d}" if sn and ep_no else item.get("Name", "")
            episodes.append({"season": sn, "episode": ep_no, "title": item.get("Name", ""),
                              "path": raw_path, "name": Path(raw_path).name, "label": label})
        return jsonify({"episodes": episodes, "count": len(episodes), "source": "realtime"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/subtitle/probe", methods=["POST"])
def subtitle_probe():
    body = request.json or {}
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "path 不能为空"}), 400
    path = _docker_to_local(path)
    FFPROBE_PATH = shutil.which("ffprobe") or "/usr/bin/ffprobe"
    if FFPROBE_PATH and Path(FFPROBE_PATH).exists():
        try:
            r = subprocess.run(
                [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_streams", path],
                capture_output=True, text=True, timeout=20)
            data = json_lib.loads(r.stdout)
            streams = []
            for s in data.get("streams", []):
                tags = s.get("tags", {})
                streams.append({"index": str(s["index"]), "lang": tags.get("language", ""),
                                 "title": tags.get("title", ""),
                                 "type": s.get("codec_type", "").capitalize(),
                                 "codec": s.get("codec_name", "")})
            return jsonify({"streams": streams})
        except Exception as e:
            print(f"ffprobe error: {e}")
    try:
        probe = subprocess.run(
            [FFMPEG_PATH, "-i", path, "-hide_banner", "-loglevel", "info"],
            capture_output=True, text=True, timeout=15)
        streams = []
        current = None
        for line in probe.stderr.splitlines():
            m = re.search(r"Stream #0:(\d+)(?:\((\w+)\))?: (Subtitle|Audio|Video)(.*)", line)
            if m:
                current = {"index": m.group(1), "lang": m.group(2) or "", "title": "",
                           "type": m.group(3), "codec": m.group(4).strip().split(",")[0].strip(": ")}
                streams.append(current)
            elif current and re.search(r"title\s*:\s*(.+)", line):
                mt = re.search(r"title\s*:\s*(.+)", line)
                current["title"] = mt.group(1).strip()
                current = None
        return jsonify({"streams": streams})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/subtitle/start", methods=["POST"])
def subtitle_start():
    body         = request.json or {}
    video_path   = body.get("path", "")
    stream_index = body.get("stream_index", None)
    if not video_path:
        return jsonify({"error": "path 不能为空"}), 400
    task_id = f"sub_{int(time.time() * 1000)}"
    _st_new_task(task_id)
    threading.Thread(target=_run_subtitle_pipeline, args=(task_id, video_path, stream_index), daemon=True).start()
    return jsonify({"task_id": task_id})

@app.route("/api/subtitle/save", methods=["POST"])
def subtitle_save():
    body        = request.json or {}
    video_path  = body.get("video_path", "").strip()
    srt_content = body.get("srt_content", "")
    output_mode = body.get("output_mode", "bilingual")
    if not video_path:
        return jsonify({"error": "video_path 不能为空"}), 400
    if not srt_content:
        return jsonify({"error": "srt_content 不能为空"}), 400
    video_path = _docker_to_local(video_path)
    vp       = Path(video_path)
    lang_tag = "zh-en" if output_mode == "bilingual" else "zh"
    out_name = vp.stem + f".{lang_tag}.srt"
    out_dir  = vp.parent
    if not os.access(str(out_dir), os.W_OK):
        out_dir = TMP_DIR
    out_path = out_dir / out_name
    try:
        out_path.write_text(srt_content, encoding="utf-8")
    except Exception as e:
        return jsonify({"error": f"写入失败: {e}"}), 500
    threading.Thread(target=_refresh_emby_dir, args=(out_dir,), daemon=True).start()
    return jsonify({"ok": True, "out_path": str(out_path), "out_name": out_name})

@app.route("/api/subtitle/status/<task_id>")
def subtitle_status(task_id):
    t = _subtitle_tasks.get(task_id)
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(t)

@app.route("/api/subtitle/cancel/<task_id>", methods=["POST"])
def subtitle_cancel(task_id):
    if task_id in _subtitle_tasks:
        _subtitle_tasks[task_id]["status"] = "cancelled"
        return jsonify({"ok": True})
    return jsonify({"error": "任务不存在"}), 404

# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9897, debug=False)
