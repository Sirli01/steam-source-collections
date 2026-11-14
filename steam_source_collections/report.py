"""Write the dry-run outputs: JSON plan, CSV mapping and a readable report."""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .plan import Plan

CSV_FIELDS = (
    "appid",
    "name",
    "group",
    "method_raw",
    "license_package",
    "date",
    "match_via",
    "match_score",
    "playtime_minutes",
    "note",
)


def _html_report(plan: Plan, generated_at: str, notes: list[str]) -> str:
    """A self-contained, offline HTML report that a double-click can open."""
    esc = html.escape
    stats = plan.stats
    cards: list[str] = []

    def card(group: dict, idx: int) -> str:
        games = group.get("games") or []
        names = [str(g.get("name") or f"App {g.get('appid', '?')}") for g in games]
        if not names:
            names = [f"App {a}" for a in group["appids"]]
        listing = "\n".join(names)
        box_id = f"box-{idx}"
        kind = "cross" if group.get("kind") == "cross" else "source"
        label = "交叉组" if kind == "cross" else "来源组"
        return f"""<section class="card {kind}">
  <div class="card-head">
    <h3>{esc(str(group['name']))}</h3>
    <span class="count">{len(group['appids'])} 款</span>
    <span class="tag">{label}</span>
  </div>
  <div class="tools">
    <button onclick="copyBox('{box_id}',this)">复制游戏名清单</button>
    <span class="hint">在 Steam 库里建收藏集时可直接粘贴核对</span>
  </div>
  <div class="list">{esc(chr(10).join(names))}</div>
  <textarea id="{box_id}" class="hidden">{esc(listing)}</textarea>
</section>"""

    source_groups = [g for g in plan.groups if g.get("kind") != "cross"]
    cross_groups = [g for g in plan.groups if g.get("kind") == "cross"]
    for i, group in enumerate(source_groups):
        cards.append(card(group, i))
    for j, group in enumerate(cross_groups):
        cards.append(card(group, 100 + j))

    notes_html = ""
    if notes:
        notes_html = "<h2>本次数据说明</h2><ul class='notes'>" + "".join(
            f"<li>{esc(n)}</li>" for n in notes
        ) + "</ul>"

    unmatched_lic = ""
    if plan.unmatched_licenses:
        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(str(x.get("date", ""))), esc(str(x.get("package_name", ""))),
                esc(str(x.get("method_raw", ""))))
            for x in plan.unmatched_licenses[:100]
        )
        extra_lic = ""
        if len(plan.unmatched_licenses) > 100:
            extra_lic = (
                f'<p class="sub" style="margin-top:8px">…另有 {len(plan.unmatched_licenses) - 100} 行，'
                "详见 <code>collections_plan.json</code> 或 <code>mapping.csv</code>。</p>"
            )
        unmatched_lic = (
            "<h2>需要人工复核的许可行</h2>"
            "<table><thead><tr><th>日期</th><th>包名</th><th>获取方式</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>{extra_lic}"
        )

    unmatched_games_html = ""
    if plan.unmatched_games:
        g_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(str(x.get("appid", ""))),
                esc(str(x.get("name", ""))),
                esc(str(x.get("note", ""))),
            )
            for x in plan.unmatched_games[:100]
        )
        extra_g = ""
        if len(plan.unmatched_games) > 100:
            extra_g = (
                f'<p class="sub" style="margin-top:8px">…另有 {len(plan.unmatched_games) - 100} 款，'
                "详见 <code>collections_plan.json</code> 或 <code>mapping.csv</code>。</p>"
            )
        unmatched_games_html = (
            "<h2>没有许可记录的游戏</h2>"
            "<table><thead><tr><th>appid</th><th>名称</th><th>说明</th></tr></thead>"
            f"<tbody>{g_rows}</tbody></table>{extra_g}"
        )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Steam 收藏集分组结果</title>
<meta name="color-scheme" content="light dark">
<style>
:root{{color-scheme:light;
 --bg:#F1F4F4; --panel:#FFFFFF; --panel-2:#E9EEEE; --line:#D5DCDC; --line-2:#C9D0D0;
 --ink:#161616; --muted:#5C5C5C; --muted-2:#777C7C; --accent:#161616; --cross:#5C5C5C;
 --accent-soft:#E5ECEC; --accent-line:#161616; --accent-ink:#161616; --btn-hover:#D5DCDC;
 --notes-bg:#FFFFFF; --notes-line:#D5DCDC; --tag-bg:#E5ECEC;
 --table-head:#E5ECEC; --code-bg:#E5ECEC; --footer:#5C5C5C; --list-ink:#161616;
 --signal:#7DE6FF; --on-signal:#161616}}
@media (prefers-color-scheme:dark){{
 :root{{color-scheme:dark;
  --bg:#191919; --panel:#222222; --panel-2:#2A2A2A; --line:#353535; --line-2:#454545;
  --ink:#EDEDED; --muted:#A3A3A3; --muted-2:#8E8E8E; --accent:#EDEDED; --cross:#A3A3A3;
  --accent-soft:#2E2E2E; --accent-line:#EDEDED; --accent-ink:#EDEDED; --btn-hover:#353535;
  --notes-bg:#222222; --notes-line:#454545; --tag-bg:#2E2E2E;
  --table-head:#2E2E2E; --code-bg:#2E2E2E; --footer:#A3A3A3; --list-ink:#EDEDED;
  --signal:#7DE6FF; --on-signal:#161616}}
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 20px 60px;background:var(--bg);color:var(--ink);
 font:15px/1.6 "Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
 background-image:linear-gradient(rgba(22,22,22,.025) 1px,transparent 1px),
                  linear-gradient(90deg,rgba(22,22,22,.025) 1px,transparent 1px);
 background-size:28px 28px}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:26px;font-weight:800;margin:0 0 6px;letter-spacing:-.01em}}
h2{{font-size:19px;font-weight:800;margin:30px 0 12px;border-top:1px solid var(--ink);padding-top:14px}}
.sub{{color:var(--muted);margin:0 0 20px;font-size:14px;overflow-wrap:anywhere}}
.stats{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}}
.stat{{background:var(--panel);border:1px solid var(--ink);border-top:5px solid var(--ink);
 padding:12px 16px;min-width:130px}}
.stat b{{display:block;font-size:26px;line-height:1.2;font-variant-numeric:tabular-nums}}
.stat span{{color:var(--muted);font-size:11px;letter-spacing:.1em;text-transform:uppercase;font-weight:700}}
.notes{{background:var(--notes-bg);border:1px solid var(--line);border-left:4px solid var(--ink);
 padding:12px 12px 12px 30px;font-size:14px;overflow-wrap:anywhere}}
.notes li{{margin:4px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(310px,100%),1fr));gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-top:5px solid var(--ink);
 padding:14px 16px;min-width:0}}
.card.cross{{border-top-color:var(--cross)}}
.card-head{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
.card-head h3{{font-size:16px;font-weight:800;margin:0;flex:1;min-width:0;overflow-wrap:anywhere}}
.count{{font-weight:800;font-size:20px;color:var(--accent);font-variant-numeric:tabular-nums}}
.tag{{font-size:11px;color:var(--ink);background:var(--tag-bg);padding:2px 8px;letter-spacing:.06em}}
.tools{{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}}
.tools button{{border:1px solid var(--accent-line);background:var(--accent-soft);
 padding:5px 10px;cursor:pointer;font-size:12px;font-weight:700;color:var(--accent-ink);
 white-space:nowrap;flex:none;transition:background-color .2s,color .2s}}
.tools button:hover{{background:var(--signal);color:var(--on-signal)}}
.hint{{font-size:12px;color:var(--muted-2)}}
.list{{white-space:pre-line;max-height:210px;overflow:auto;background:var(--panel-2);
 border:1px solid var(--line-2);padding:9px 11px;font-size:13px;
 color:var(--list-ink);overflow-wrap:anywhere}}
.hidden{{position:absolute;left:-9999px;width:1px;height:1px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);font-size:13px}}
th,td{{border:1px solid var(--line-2);padding:6px 9px;text-align:left;overflow-wrap:anywhere}}
th{{background:var(--table-head);font-size:11px;letter-spacing:.08em;text-transform:uppercase}}
footer{{margin-top:34px;padding-top:16px;border-top:1px solid var(--ink);
 color:var(--footer);font-size:13px;overflow-wrap:anywhere}}
code{{background:var(--code-bg);padding:1px 5px}}
@media(prefers-reduced-motion:reduce){{
 *{{animation:none!important;transition:none!important}}
}}
</style></head><body><div class="wrap">
<h1>Steam 收藏集分组结果</h1>
<p class="sub">生成于 {esc(generated_at)} · 工具版本 {esc(__version__)} · 全程只读，没有改动 Steam 里的任何收藏集</p>
<div class="stats">
 <div class="stat"><b>{stats['games_total']}</b><span>游戏库总数</span></div>
 <div class="stat"><b>{stats['license_rows']}</b><span>许可记录行</span></div>
 <div class="stat"><b>{stats['license_unmatched']}</b><span>未对齐许可行</span></div>
 <div class="stat"><b>{stats['games_without_license']}</b><span>无许可记录的游戏</span></div>
</div>
{notes_html}
<h2>按获取来源分组</h2>
<div class="grid">{''.join(cards)}</div>
{unmatched_lic}
{unmatched_games_html}
<h2>这份报告是怎么来的</h2>
<p class="sub" style="margin:0">来源（Steam 商店 / CDK·Key / 免费领取 / 赠礼）来自你在许可页导出的一键包；
本机 Steam 客户端的 <code>packageinfo.vdf</code> 只提供「礼包包 → 游戏 appid」的身份对照，
由工具自动定位并只读解析，<b>不需要你去找这个文件</b>，它本身也不记录你每份许可的获取方式。</p>
<h2>想让这些分组直接出现在 Steam 库里？</h2>
<p class="sub" style="margin:0 0 10px">默认<b>不改动 Steam 任何文件</b>。上面每组的「复制游戏名清单」可以直接在客户端建收藏集，最安全。
若希望工具代为写入：打开图形界面（项目里的「启动 Steam 分组工具.bat」或桌面图标），点「一键写入 Steam」并勾选确认。
Steam 正在运行时，工具会让 <b>Steam 客户端自己保存并上传到账号</b>，Steam Deck 自动同步，不用退出、不用重启；
只有没开启本机接口时才需要先退出 Steam 改本机文件。
它只新增 <code>[来源] </code> 前缀的收藏集，不修改你现有的收藏集，写入前做两份备份；界面里的「一键还原」可以随时撤销。</p>
<p class="sub" style="margin:0">若 Steam 弹出云同步冲突，选择保留本机版本；写入与还原都会自动备份，数据不会丢。</p>
<footer>
本文件含你的游戏库清单，属于个人数据，不要提交到 GitHub（仓库已默认忽略 <code>out/</code>）。<br>
一个游戏可以同时属于多个收藏集；来源组和交叉组不冲突。家庭共享需要另给清单，许可页不含别人的游戏。
</footer>
</div>
<script>
function copyBox(id,btn){{
  var t=document.getElementById(id); if(!t)return;
  var ok=function(){{
    if(btn){{var old=btn.textContent;btn.textContent='已复制 ✓';setTimeout(function(){{btn.textContent=old;}},1400);}}
  }};
  var fallback=function(){{
    t.style.display='block'; t.select();
    try{{document.execCommand('copy');ok();}}catch(e){{}}
    t.style.display='none';
  }};
  if(navigator.clipboard&&navigator.clipboard.writeText){{
    navigator.clipboard.writeText(t.value).then(ok).catch(fallback);
  }}else fallback();
}}
</script></body></html>
"""


def write_outputs(plan: Plan, outdir: Path, license_source: str, owned_source: str,
                  notes: list[str] | None = None) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    payload = plan.as_dict()
    payload["meta"] = {
        "tool": "steam-source-collections",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license_source": license_source,
        "owned_source": owned_source,
    }

    json_path = outdir / "collections_plan.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    csv_path = outdir / "mapping.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for appid in sorted(plan.assignments):
            writer.writerow(plan.assignments[appid])

    stats = plan.stats
    lines = [
        "# Steam 收藏集分组计划（dry-run）",
        "",
        f"> 生成于 {payload['meta']['generated_at']} · 工具版本 {__version__}",
        "",
        "## 概览",
        "",
        "| 项目 | 数量 |",
        "| --- | --- |",
        f"| 游戏库总数 | {stats['games_total']} |",
        f"| 许可记录行数 | {stats['license_rows']} |",
        f"| 未能对齐的许可行 | {stats['license_unmatched']} |",
        f"| 没有许可记录的游戏 | {stats['games_without_license']} |",
        "",
        "## 分组结果",
        "",
        "| 收藏集 | 游戏数 | 前几款 |",
        "| --- | --- | --- |",
    ]
    for group in plan.groups:
        preview = "、".join(row["name"] for row in group["games"][:5]) or "—"
        lines.append(f"| {group['name']} | {len(group['appids'])} | {preview} |")

    if notes:
        lines += ["", "## 本次数据说明", ""] + [f"- {note}" for note in notes]

    lines += ["", "## 未能对齐的许可行", ""]
    if plan.unmatched_licenses:
        lines += ["| 日期 | 包名 | 获取方式 | 原因 |", "| --- | --- | --- | --- |"]
        for item in plan.unmatched_licenses[:100]:
            lines.append(
                f"| {item['date']} | {item['package_name']} | {item['method_raw']} | {item.get('note', '')} |"
            )
        if len(plan.unmatched_licenses) > 100:
            lines.append("")
            lines.append(f"…另有 {len(plan.unmatched_licenses) - 100} 行，见 collections_plan.json。")
    else:
        lines.append("无。")

    lines += ["", "## 没有许可记录的游戏", ""]
    if plan.unmatched_games:
        lines += ["| appid | 名称 | 说明 |", "| --- | --- | --- |"]
        for item in plan.unmatched_games[:100]:
            lines.append(f"| {item['appid']} | {item['name']} | {item['note']} |")
    else:
        lines.append("无。")

    lines += [
        "",
        "## 下一步",
        "",
        "1. 先看 `mapping.csv`，把误判的行改掉；⑥ 组和上面两张未匹配表是重点。",
        "2. 在 Steam 客户端里按上面的清单手动建收藏集（最安全），或用实验性的 `--writeback`。",
        "3. 一个游戏可以同时属于多个收藏集，来源组和交叉组不冲突。",
        "",
        "> ⚠️ 本目录含你的游戏库清单，属于个人数据。**不要提交到 GitHub**，",
        "> 仓库的 `.gitignore` 已默认忽略 `out/`。",
        "",
        "## 已知限制",
        "",
        "- 许可页没有 appid，只有包名；工具会剥离版本／地区后缀、拆分双语包名，并用本机 Steam 缓存的中文名对齐，仍对不上的（改名、Demo、无名 DLC）不会硬猜。",
        "- 「合集包／促销包」这类一行许可对应多个 appid 的情况，会按本机礼包缓存里的「包 → appid」精确展开，把本体和 DLC 一起归到同一来源（mapping.csv 中 match_via=bundle），不会把它们留在 ⑥。",
        "- 「免费周末」和「限时免费永久领取」在 Steam 上都是免费类许可，无法自动区分。",
        "- ⑥ 组与上面两张「未能对齐」表是同一次配对失败的两面：前者是没被许可行认领的 appid，后者是没找到 appid 的许可行。因此 ⑥ 里既有本来就没有许可行的 F2P / Demo / 免费周末残留，也有买过但包名没对上的完整游戏。",
        "- 家庭共享需要 `--family-file` 提供清单（许可页不含别人的游戏）。",
        "",
    ]

    md_path = outdir / "report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    html_path = outdir / "report.html"
    html_path.write_text(
        _html_report(plan, payload["meta"]["generated_at"], notes or []), encoding="utf-8"
    )
    return [json_path, csv_path, md_path, html_path]
