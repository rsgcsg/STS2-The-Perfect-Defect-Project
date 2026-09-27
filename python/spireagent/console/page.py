"""A small shared shell with packaged assets and no external browser dependencies."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlsplit

ASSETS = {
    "console.css": "text/css",
    "console.js": "text/javascript",
    "identity.js": "text/javascript",
    "project.js": "text/javascript",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def asset(name: str) -> tuple[str, bytes] | None:
    if name not in ASSETS:
        return None
    return ASSETS[name], Path(__file__).with_name(name).read_bytes()


def render_shell(mode: str, api_base: str, cloud_url: str = "") -> str:
    if (mode, api_base) not in {("local", "/api/console"), ("cloud", "/app/api")}:
        raise ValueError("unsupported_console_mode")
    if cloud_url:
        parsed = urlsplit(cloud_url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not parsed.hostname
            or not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
        ):
            raise ValueError("invalid_console_cloud_url")
    assets = "/assets" if mode == "local" else "/app/assets"
    cloud = html.escape(cloud_url.rstrip("/"), quote=True)
    label = "本机工作台" if mode == "local" else "云端数据中心"
    local_primary = ([
        ("local-home", "本机工作台", "⌂"),
        ("campaigns", "真人采集", "◉"),
        ("local-models", "模型实战", "▷"),
        ("local-workspace", "本机资料", "▤"),
    ] if mode == "local" else [])
    shared_primary = [("collections", "数据", "▤")]
    team_primary = [
        ("datasets", "数据集", "▦"),
        ("research", "训练与模型", "◷"),
    ]
    primary = (
        local_primary
        + shared_primary
        + (team_primary if mode == "cloud" or cloud_url else [])
        + [("evaluations", "评估结果", "◇")]
    )
    nav = "".join(
        f'<a class="nav-item" href="?view={key}" data-view="{key}">'
        f'<span class="nav-icon" aria-hidden="true">{icon}</span>{name}</a>'
        for key, name, icon in primary
    )
    secondary = (
        '<a class="nav-item" href="?view=members" data-view="members" '
        'data-admin-only="true" hidden>成员管理</a>'
        '<a class="nav-item" href="?view=system" data-view="system">设置与诊断</a>'
    )
    cloud_link = (
        f'<a class="button secondary" href="{cloud}/app/" target="_blank" '
        'rel="noreferrer">打开云端 ↗</a>'
        if mode == "local" and cloud
        else ""
    )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpireAgent · {label}</title><link rel="stylesheet" href="{assets}/console.css">
<script src="{assets}/identity.js" defer></script>
<script src="{assets}/project.js" defer></script>
<script src="{assets}/console.js" defer></script></head>
<body data-mode="{mode}" data-api="{api_base}" data-cloud-url="{cloud}">
<a class="skip-link" href="#main">跳到内容</a>
<aside class="sidebar">
<a class="brand" href="?view={"local-home" if mode == "local" else "collections"}">
<span class="brand-mark">S</span>
<span>SpireAgent<small>项目控制台</small></span></a>
<div class="workspace-label">{label}</div><nav aria-label="主导航">{nav}</nav>
<nav class="secondary-nav" aria-label="管理与设置">{secondary}</nav>
<div class="sidebar-note"><p>游戏内操作 · 本机准备 · 云端共享</p></div></aside>
<div class="workspace"><header class="topbar"><div><span class="mode-pill">{label}</span>
<span id="connection" class="connection">正在读取状态…</span></div>
<div class="topbar-actions"><span id="account-actions"></span>{cloud_link}
<button id="refresh" class="button secondary"
type="button">刷新</button></div></header>
<main id="main" tabindex="-1"><label class="scope-label">数据查看范围（不改变本机控制）
<select id="device-scope" aria-label="电脑范围"></select></label>
<div class="page-heading"><div><p class="eyebrow">SPIREAGENT</p>
<h1 id="title">概览</h1><p id="subtitle" class="subtitle">采集、上传与研究进展，一处查看。</p></div>
<span id="updated" class="updated"></span></div>
<nav id="task-tabs" class="task-tabs" aria-label="当前任务"></nav>
<div id="notice" role="status" aria-live="polite"></div>
<div id="content" aria-busy="true"><div class="empty-state">正在读取已确认的数据…</div></div>
</main><footer>录制质量 · 云端验收 · 研究准入，分别展示。
<span id="lifecycle-note"></span></footer></div>
<noscript>此控制台需要启用 JavaScript。CLI 的 project status 仍可独立使用。</noscript>
</body></html>'''


def render_landing(source_revision: str) -> str:
    import re

    if re.fullmatch(r"[a-f0-9]{40}", source_revision) is None:
        raise ValueError("invalid_console_source_revision")
    guide = (
        "https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/"
        + source_revision
        + "/python/docs/PROJECT_CONSOLE.md#download-sign-in-bind-once"
    )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpireAgent · 项目工作台</title><link rel="stylesheet" href="/assets/console.css"></head>
<body><main class="landing"><a class="brand" href="/"><span class="brand-mark">S</span>
<span>SpireAgent<small>同一个账号，连接你的电脑与项目数据</small></span></a>
<section class="panel onboarding"><p class="eyebrow">项目工作台</p>
<h1>采集在本机，进展随时查看。</h1>
<p>同一账号查看项目共享数据、上传进度、训练与评估；在自己的电脑采集和测试模型。</p>
<p><a class="button" href="/app/">登录项目账号 →</a>
<a class="button secondary" href="{guide}" rel="noreferrer">获取开发者工作台 ↗</a></p>
<ol><li>在采集电脑打开工作台。</li><li>用受邀请的项目邮箱登录，核对并绑定电脑。</li>
<li>打开“真人采集”，一次同意并准备本机；在游戏里结束录制后自动上传。</li></ol>
<p>本机队列在工作台查看。云端只显示已收到的数据；账号登录不代表同意上传。</p>
<p class="small muted">当前供项目开发者使用，按邀请接入。无需单独注册密码。</p>
</section></main></body></html>'''
