"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bundle import load_bundle
from .licenses import load_licenses
from .local import discover, find_latest_bundle
from .matching import build_index, match_package
from .owned import fetch_owned, load_family, load_owned
from .plan import build_plan
from .report import write_outputs
from .service import build_notes
from .writeback import (
    WritebackError,
    apply as writeback_apply,
    preview as writeback_preview,
    steam_is_running,
)

LICENSE_PAGE = "https://store.steampowered.com/account/licenses/"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steam-source-collections",
        description="按「获取来源」把 Steam 游戏库分组，生成可审阅的收藏集计划（默认不写入 Steam）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "最省事（推荐，图形界面）:\n"
            "  python -m steam_source_collections.gui\n"
            "  Windows 也可双击「启动 Steam 分组工具.bat」；界面里点「一键统计」和「一键写入」\n\n"
            "不装 Python 的纯前端路径:\n"
            "  打开 try-it.html，把导出书签拖到书签栏 → 许可页点一下 → 把 JSON 拖回页面\n\n"
            "想把报告存成文件:\n"
            "  python -m steam_source_collections --auto --out out\n\n"
            "手动模式:\n"
            "  python -m steam_source_collections --licenses private/licenses.html \\\n"
            "      --owned private/owned.json --family-file private/family.txt --out out\n\n"
            "  python -m steam_source_collections --licenses private/licenses.csv \\\n"
            "      --api-key XXX --steamid 7656119XXXXXXXXXX --out out\n"
        ),
    )
    parser.add_argument("--auto", action="store_true",
                        help="自动发现本机 Steam 并读取下载目录里最新的一键导出包")
    parser.add_argument("--bundle", help="显式指定一键导出包 JSON")
    parser.add_argument("--steam-root", help="手动指定 Steam 安装目录（--auto 找不到时用）")
    parser.add_argument("--licenses", help="许可页 .html / .csv / .txt（手动模式）")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--owned", help="游戏库 JSON（GetOwnedGames 响应或简化导出）")
    source.add_argument("--api-key", help="Steam Web API Key")
    parser.add_argument("--steamid", help="SteamID64；--auto 时会自动从本机读取")
    parser.add_argument("--family-file", help="家庭共享清单：每行一个 appid 或游戏名，可选")
    parser.add_argument("--out", default="out", help="输出目录，默认 out/")
    parser.add_argument("--conflict", choices=("priority", "latest"), default="priority",
                        help="同一游戏命中多条许可时的取舍，默认付费优先")
    parser.add_argument("--threshold", type=float, default=0.86, help="模糊匹配阈值，默认 0.86")
    parser.add_argument("--no-extra-groups", action="store_true", help="不生成交叉分组")
    parser.add_argument("--no-order-infer", "--strict-order", dest="no_order_infer",
                        action="store_true",
                        help="只用直接证据对齐，不用本机缓存的许可行顺序推定礼包包")
    parser.add_argument("--online-names", action="store_true",
                        help="可选：联网查询缺失的游戏名称（默认关闭，仅在本地补全之后使用）")
    parser.add_argument("--writeback", help="实验性：Steam 本地收藏集 JSON 路径；--auto 时自动定位")
    parser.add_argument("--apply", action="store_true", help="实验性：真正写入 Steam 收藏集（需 --yes；--auto 会自动定位文件）")
    parser.add_argument("--yes", action="store_true", help="确认写入")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)

    local = None
    if args.auto or args.bundle:
        local = discover(args.steam_root)
        print("[自动发现] " + json.dumps(local.summary(), ensure_ascii=False))
        for note in local.notes:
            print(f"  · {note}")

    bundle_path = args.bundle or (find_latest_bundle() if args.auto else None)
    if args.auto and not bundle_path:
        print(
            "\n还没有找到一键导出包。推荐改用图形界面：python -m steam_source_collections.gui\n"
            "  （Windows 也可双击「启动 Steam 分组工具.bat」），界面最上面 NO.00 就是导出数据的三步。\n"
            "也可以：\n"
            f"  1. 打开 try-it.html，把「导出 Steam 许可」书签拖到书签栏\n"
            f"  2. 打开 {LICENSE_PAGE} （确认已登录）并点一下那个书签\n"
            "  3. 把下载的 JSON 拖进图形界面或 try-it.html\n"
            "  4. 想用本命令落盘报告时再回来重跑；也可 --bundle 指定文件"
        )
        return 1

    try:
        bundle_meta: dict = {}
        if bundle_path:
            bundle = load_bundle(bundle_path, local, args.threshold,
                                 infer_order=not args.no_order_infer,
                                 online_names=args.online_names)
            records, games, matches = bundle.records, bundle.games, bundle.matches
            bundle_meta = bundle.meta
            owned_source = f"一键导出包 {Path(bundle_path).name}"
            print("[导出包] " + json.dumps(bundle.meta, ensure_ascii=False))
            for warning in bundle.meta.get("warnings") or []:
                print("  ⚠️ " + warning)
            if bundle.meta["unnamed_apps"] and not bundle.meta.get("warnings"):
                print(f"  · 仍有 {bundle.meta['unnamed_apps']} 款游戏在 Steam 数据库中无名称记录"
                      "（多为已下架 / 内部测试 / 散装 DLC），本机缓存已尽量按礼包包归组。")
        else:
            if not args.licenses:
                parser.error("手动模式需要 --licenses；想全自动请用 --auto")
            records = load_licenses(args.licenses)
            if args.owned:
                games = load_owned(args.owned)
                owned_source = Path(args.owned).name
            elif args.api_key:
                steamid = args.steamid or (local.steamid64 if local else None)
                if not steamid:
                    parser.error("使用 --api-key 需要 --steamid（或用 --auto 自动读取）")
                games = fetch_owned(args.api_key, steamid)
                owned_source = "Steam Web API GetOwnedGames"
            else:
                parser.error("需要 --owned 或 --api-key 提供游戏库列表")
            index = build_index(games)
            matches = [match_package(r.package_name, index, args.threshold) for r in records]

        family_appids, family_names = load_family(args.family_file)
        plan = build_plan(
            records, games, matches, family_appids, family_names,
            conflict=args.conflict, extra_groups=not args.no_extra_groups,
        )
        outdir = Path(args.out)
        notes = build_notes(bundle_meta) if bundle_path else []
        outputs = write_outputs(
            plan, outdir,
            Path(bundle_path).name if bundle_path else Path(args.licenses).name,
            owned_source,
            notes=notes,
        )
    except (FileNotFoundError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(f"\n已解析 {plan.stats['license_rows']} 行许可、{plan.stats['games_total']} 款游戏\n")
    for group in plan.groups:
        print(f"  {group['name']}: {len(group['appids'])} 款")
    print(f"\n未对齐许可 {plan.stats['license_unmatched']} 行 / "
          f"无许可记录的游戏 {plan.stats['games_without_license']} 款")
    print("输出: " + ", ".join(str(path) for path in outputs))
    html_out = next((str(p) for p in outputs if p.suffix == ".html"), "out/report.html")
    print(f"完成。这是 dry-run 计划，未改动 Steam；报告：{html_out}"
          "（双击即可用浏览器打开），mapping.csv 用来逐条核对。")

    target = Path(args.writeback) if args.writeback else (
        local.cloud_path if (local and local.cloud_path and local.cloud_path.is_file()) else None
    )
    if args.writeback or (target and args.apply):
        try:
            info = writeback_preview(target, plan)
            print("\n[写回预检] " + json.dumps(info, ensure_ascii=False, indent=2))
            if args.apply and args.yes and steam_is_running():
                print("未写入: 你还没有关闭 Steam，请先完全退出（含托盘图标）。", file=sys.stderr)
                return 1
            if args.apply and args.yes and info.get("writable", True):
                backups = writeback_apply(target, plan, outdir)
                print("已写入。备份: " + ", ".join(str(path) for path in backups))
            else:
                print("未写入（需要同时给 --apply --yes，且文件结构必须可安全写入）。")
        except (WritebackError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            print(f"写回跳过: {exc}", file=sys.stderr)
            print("未改动 Steam 文件。可用 out/report.html 里的「复制游戏名清单」手动建组。", file=sys.stderr)
            return 1
    return 0
