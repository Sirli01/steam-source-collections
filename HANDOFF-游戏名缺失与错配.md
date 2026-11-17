# 待修复：Steam 收藏集来源分组工具 —— 游戏名缺失与许可行错配

> 本文档是给接手开发者的任务说明。所有数字均为在本机（Windows，Steam 装在 `D:\steam`，账号 `1126824019`，游戏 1342 款）上的**实测结果**，不是估算。

## 0. 项目位置与运行方式

- 仓库：`C:\Users\玄陌离\WorkBuddy\2026-11-08-16-15-50\steam-source-collections`
- 语言：Python 3.12，仅标准库，无第三方依赖
- 入口：`python -m steam_source_collections`（CLI）／`python -m steam_source_collections.gui`（图形界面）
- 测试：`python -m unittest discover tests`（`tests/` 下已有 7 个测试文件）
- 已有产物：`out/collections_plan.json`、`out/report.md`、`out/mapping.csv`
- 本机导出包：`C:\Users\玄陌离\Downloads\steam-licenses-20261108-2115.json`（可直接复现）

**复现命令**（这就是出问题的那条路径）：

```python
from steam_source_collections.local import discover
from steam_source_collections.bundle import load_bundle
local = discover()                      # 自动找到 D:\steam
b = load_bundle(path_to_bundle, local)  # 关键：必须传 local
print(b.meta["named_apps"], b.meta["unnamed_apps"])   # 979 / 239
```

不传 `local` 时是 `735 / 483`，会看起来更糟——说明本机缓存确实起作用了，但**用错了地方**（见问题 2）。

---

## 1. 现状与目标

当前结果：

| 指标 | 数值 |
|---|---:|
| 游戏总数 | 1342 |
| 名字已知 | 979 |
| **名字缺失（显示为 `App <appid>`）** | **239** |
| 许可行总数 | 913 |
| **未能对齐到游戏的许可行** | **56** |
| 归入「⑥ 未能判定」的游戏 | 30 |

目标：尽可能消除 239 个 `App <appid>`，并把 56 行未对齐许可行降下来。**修完后要能跑同一份导出包，给出前后对比数字。**

---

## 2. 问题一（主因）：名字补全存在循环依赖，顺序错了

### 现象

`bundle.py` 第 315 行：

```python
index = build_index(games[: len(owned)])
```

匹配索引是**建在 `Game.name` 上的**。而 `Game.name` 在没名字时是占位符 `f"App {appid}"`（第 299 行）。

于是形成闭环：

> 许可行要靠「游戏名」去认领游戏 → 但游戏名要靠许可行的礼包信息反推 → 名字缺 → 匹配失败 → 名字继续缺。

### 已有但没被使用的素材

`appinfo.py` 已经能读本机 `D:\steam\appcache\appinfo.vdf`，但当前**只用在中文别名救援**（`bundle.py` 第 331 行 `resolve_localized_names`，只处理「含中日韩字符」的包名）。

这个文件里其实**有每个 app 的英文主名字**，且是零网络、零登录、只读的。

### 实测：能救回多少

用 `appinfo.py` **已存在的** `_nearest_appid()` 辅助函数即可，无需新写解析器：

```python
import struct
from steam_source_collections import appinfo

data = open(r"D:\steam\appcache\appinfo.vdf", "rb").read()
owned = {g.appid for g in games}

# Steam 二进制 KeyValues 里 name 字段的编码是：
#   0x01 (类型=字符串) + uint32 键序号(4 = "name") + UTF-8 文本 + NUL
MARK = b"\x01" + struct.pack("<I", 4)

found, pos = {}, 0
while True:
    i = data.find(MARK, pos)
    if i < 0:
        break
    pos = i + len(MARK)
    start = i + len(MARK)
    end = data.find(b"\x00", start)
    if end < 0 or end - start > 250:
        continue
    try:
        text = data[start:end].decode("utf-8").strip()
    except UnicodeDecodeError:
        continue
    if not text or "\n" in text:
        continue
    appid = appinfo._nearest_appid(data, i, owned)   # 复用现成函数
    if appid is not None and appid not in found:
        found[appid] = text
```

**实测结果**：本机缓存共 880 条记录，这套写法捞出 **1231** 个 app 的名字（含合并包成员），其中 **186 / 239** 个正好是当前的 `App <appid>` 空缺。

### 准确性验证（重要，别盲目信任）

拿「已经信任的名字」（来自 Steam Web API / appdetails）做交叉验证：

- 用 `normalize()` 归一化后比对：**933 / 1045 一致（89.3%）**
- 不一致的 112 条里，绝大多数是**缓存的名字更干净**，不是错误。典型：
  - `'KEO Limited Free Promotional Package - Nov 2021'` ↔ 缓存 `'KEO'`（缓存对）
  - `'Banana Hell Limited Free Promotional Pac...'` ↔ 缓存 `'Banana Hell'`（缓存对）
  - `'Press Any Button Limited Free Promotiona...'` ↔ 缓存 `'Press Any Button'`（缓存对）
  - `'RogueGlitch'` ↔ `'Rogue Glitch Ultra'`、`'NEEDY STREAMER OVERLOAD'` ↔ `'NEEDY GIRL OVERDOSE'`（游戏改过名，两边都"对"）
- 少数是真差异：`'Project: Zeitgeist'` ↔ `'Zeitgeist'`、`'Animal Jam - Play Wild!'` ↔ `'Animal Jam'`。属可接受。

**结论：这套补名是可靠的，且比现在的名字质量更高。**

### 要求的实现方式（关键约束）

**只填 `App <appid>` 的空缺，绝不覆盖已经信任的名字。**

这样做风险为零（那 239 行本来就没有有效信息），且能立刻把 186 个补上。**不要**改成无条件覆盖——虽然覆盖率更高，但会引入回归风险，没必要。

### 落地位置

在 `bundle.py` 的 `load_bundle()` 里，**在 `games = [...]` 构造之前**（第 296 行附近）、`index = build_index(...)` 之前，插一步：

```python
# 新：用本机 appinfo 缓存给缺名的 appid 补名（只填空缺）
if local:
    gaps = {a for a in owned if str(names.get(str(a), "")).startswith("App ") or not names.get(str(a))}
    if gaps:
        recovered = read_app_names(local.root, gaps)   # 建议在 appinfo.py 新增这个函数
        for a, n in recovered.items():
            names.setdefault(str(a), n)
        meta["cache_recovered_names"] = len(recovered)   # 加进 meta，报告里体现
```

`read_app_names(root, wanted)` 建议新增到 `appinfo.py`，复用 `_nearest_appid()`，并且**只返回 `wanted` 里的 appid**，避免无谓开销。同时要像 `appinfo.py` 里其它函数一样支持传入 `bytes`（便于测试）。

### 预期收益

| 指标 | 现在 | 补名后 |
|---|---:|---:|
| `App <appid>` 数量 | 239 | **53** |
| 未对齐许可行 | 56 | **51**（用真实 matcher 实跑的结果，非估算） |

> 注意：补名对「未对齐许可行」的改善**有限**（56 → 51）。因为那 56 行里大部分是 Demo / 基准测试工具 / 已下架项目，本来就没有对应游戏。**别指望这一步解决 56 行的问题。**

---

## 3. 问题二：促销包后缀没剥干净，把礼包名当成了游戏名

### 现象

`matching.py` 的 `alias_candidates()`（第 49–80 行）本意是剥掉版本/地区后缀。但下面这行的正则（第 39–44 行）剥得不完整：

输入：`'KEO Limited Free Promotional Package - Nov 2021'`
实际输出：`'KEO Package - Nov 2021'` ← **只剥掉了 "Limited Free Promotional"，把 " Package - Nov 2021" 留在后面**

### 后果一：32 个 appid 的"游戏名"变成一整串包名

实测，`out/collections_plan.json` 里有 32 条这样的脏名字：

```
1448030 -> 'Press Any Button Limited Free Promotional Package - Dec 2021'
844890  -> 'World of Warships — Exclusive Starter Pack Limited Free Promotional Package - ...'
934680  -> 'World of Warships — Yubari Steam Pack Limited Free Promotional Package - ...'
...
```

这些脏名字会直接进 Steam 收藏集，用户看到的就是这串东西。

> 附带说明：这 32 个名字**不是** `alias_candidates` 直接写进去的，而是走 `via="order"`（按礼包顺序推定）时，把 `record.package_name` 原样当成了 `license_package`，然后 UI 层拿它当显示名。修的时候要顺手把「显示名」和「许可包名」分开，不要让包名泄漏成游戏名。

### 后果二：造成一次静默错配（这个更严重）

许可行 `'Box: The Game Limited Free Promotional Package - Dec 2021'`：

- **没有**出现在 `unmatched_licenses` 里（用户以为它匹配好了）
- 但也没有落到正确的游戏 `879850 Box: The Game` 上
- 实测它被错配给了 `1448030`，`via="bundle"`, `score=0.5`

**原因已定位**：`match_package()` 的 bundle 分支（第 156–173 行）按 `[+&,/·:：\-]` 切分，切出了 `' Dec 2021'` 这个碎片，`normalize()` 后变成 `'dec2021'`（7 个字符，长度 ≥5 所以通过门槛），然后子串命中了另一条脏名字 `pressanybuttonlimitedfreepromotionalpackagedec2021`。

实测确认：

```
fragment ' Dec 2021'
  normalize -> 'dec2021'  (len 7)
  MATCHES 1 candidate(s): [('pressanybuttonlimitedfreepromotionalpackagedec2021', [1448030])]
```

### 修法建议

1. **剥干净后缀**：`Limited Free Promotional Package - Mon YYYY`、`Free Weekend - Mon YYYY` 整段剥掉（含月份年份）。注意别把游戏名里的真单词剥掉，保守起见只匹配这几种固定形态。
2. **bundle 分支加防护**：
   - 跳过纯月份/年份/数字碎片（`normalize` 后形如 `^(jan|feb|...|dec)?\d{4}$` 的直接丢弃）
   - 或者干脆提高 bundle 分支的门槛：`score=0.5` 的匹配**不应该写入收藏集**，只应进 `unmatched` 等人工核对
3. **修完后必须验证 `'Box: The Game...'` 这行落到 `879850`**，这是回归用例。

---

## 4. 问题三：拿本地缓存"间接推定"给的 `score=0.9` 太高

`bundle.py` 的 `align_license_packages()`（第 110–155 行）会按「许可页行序 vs `rgOwnedPackages` 顺序」做顺序对齐，这是个**间接证据**。当前：

- 807 行走了这条路（`order_aligned_rows`）
- 每行都给 `score=0.9`
- 实测有 **520 行**是纯顺序推定

问题：`score=0.9` 和真正的「包→appid 精确对应」(`score=1.0`) 太接近，用户/后续逻辑无法区分可信度。而且顺序推定一旦遇到「用户删过免费许可」「退款」「地区差异」就会整段错位。

**建议**：把顺序推定的 `score` 降到 `0.6~0.7` 区间，并在 `mapping.csv` 里保留 `match_via=order` 便于筛选复核（这个字段已经有了）。如果要做严格模式，加个 `--strict-order` 开关只信直接证据。

---

## 5. 问题四：`GetAppList` 等公开接口已失效（别再走这条路）

我试过的所有「查 appid → 名字」的公开路径，实测结论：

| 路径 | 状态 |
|---|---|
| `api.steampowered.com/ISteamApps/GetAppList/v2`（及 v1/v0002） | **404，已下线** |
| `store.steampowered.com/api/appdetails?appids=<多个>` | **400**（批量已废，只能一个一个查） |
| `store.steampowered.com/api/packagedetails?packageids=<多个>` | **400**（同上） |
| `store.steampowered.com/api/appdetails?appids=<单个>` | 可用，但**已下架/无记录的 app 返回 `{"success":false}`**，补不了 |
| `store.steampowered.com/app/<id>/` | 返回「Welcome to Steam」壳页，无标题 |
| `steamdb.info/app/<id>/` | **403**（Cloudflare），不可用 |
| `api.steampowered.com/IStoreBrowseService/GetItems/v1/` | **可用，且支持批量** ✅ |
| `api.steamcmd.net/v1/info/<id>` | **可用**，逐个查，覆盖已下架项 ✅ |
| 运行中的 Steam 客户端 `window.appStore.GetAppOverviewByAppID(id)` | **可用**，仅 Steam 运行时 ✅ |

### 推荐的联网补名方案（如果要做，优先级排在问题一之后）

`IStoreBrowseService/GetItems` 是最划算的，支持一次传多个：

```python
import json, urllib.parse, urllib.request

body = {
    "ids": [{"appid": a} for a in appids],
    "context": {"language": "english", "country_code": "US", "steam_realm": 1},
    "data_request": {"include_basic_info": True},
}
url = ("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json="
       + urllib.parse.quote(json.dumps(body)))
# 每个 item 里 item["name"] 就是名字；item["success"] == 15 + name=="" 表示
# 该 app 在 Steam 数据库里已无记录（下架/清除），补不了，属正常。
```

**实测覆盖**（针对当前 239 个空缺）：

| 来源 | 单独解决 |
|---|---:|
| 本机 `appinfo.vdf`（问题一的方案） | **186** |
| `IStoreBrowseService/GetItems` | 63 |
| `api.steamcmd.net` | 43 |
| 运行中 Steam 客户端 | 13 |

三者合并理论上是 206/239（86%），但**既然问题一已经能拿到 186，联网方案的边际收益只有约 20 个**。建议：

- 把联网补名做成**可选开关**（`--online-names` / GUI 勾选），默认关闭
- 必须做超时、重试、失败静默降级——这台机器上 Steam API 偶发丢包/超时很明显（我实测中多次遇到）
- **不要**默认开启，否则工具会从「全本地」变成「要联网」，违背项目现有定位

**剩下的 33 个永远查不到**：它们在整个 Steam 数据库里已无记录（`success:15, visible:false`），包括 Batman 合集的 15 个散装 DLC、Battlefield 1 的散包、Kingston Playtest 之类。这是数据不存在，不是代码问题。**建议在报告里明确标注「该 appid 在 Steam 数据库中已无记录」，而不是留个 `App 123456` 让用户困惑。**

---

## 6. 验收标准

改完后请用 `C:\Users\玄陌离\Downloads\steam-licenses-20261108-2115.json` + 本机 Steam 跑一遍，并给出这张表的前后对比：

| 指标 | 现在 | 修复后要求 |
|---|---:|---|
| `App <appid>` 数量 | 239 | **≤ 55** |
| 名字是整串包名的脏数据 | 32 | **0** |
| `'Box: The Game ...'` 是否落到 879850 | 否（错配给 1448030） | **是** |
| 未对齐许可行 | 56 | ≤ 55（此项改善有限，不强求） |
| 现有测试是否全绿 | — | **必须全绿** |

**特别强调**：`tests/` 下已有测试必须全部通过。特别是 `tests/test_writeback.py` 里有一条断言，要求「写回时不动 `已隐藏` 收藏集」——**这个行为绝对不能破坏**（用户有 147 个隐藏游戏，见下节）。

---

## 7. 背景：用户特别关心的「已隐藏」游戏

用户有 **147 个隐藏游戏**（Steam 的 `user-collections.hidden`）。当前工具：

- **能把它们全部分类**（实测 147/147 都在 `collections_plan.json` 里，数据没丢）
- **不会修改** `已隐藏` 这个收藏集（`writeback.py` 第 12–13 行明确保证）

但**不要**在这次修改里擅自加「尊重隐藏」的逻辑（比如把隐藏游戏踢出分组）——那是个独立需求，需要用户确认。当前只需保证不破坏现状。

> 供参考：如果不改动，隐藏游戏会被正常写进各 `[来源]` 分组，只是在 Steam 界面里因为被隐藏而不显示。例如 `[来源] ③ 免费领取·喜加一` 共 421 个，其中 119 个是隐藏的，用户实际只能看到 302 个。这是 Steam 的行为，不是 bug。

---

## 8. 代码结构速查

| 文件 | 职责 |
|---|---|
| `bundle.py` | **主战场**。读导出包、补名、对齐许可行、产出 `Bundle` |
| `appinfo.py` | 读本机 `appcache/appinfo.vdf`（已有 `_nearest_appid` 可复用） |
| `matching.py` | 包名 ↔ 游戏名匹配。`alias_candidates`（后缀剥离）、`match_package`（bundle 分支）在这里 |
| `local.py` | 只读发现 Steam 安装、收藏集、游玩时长 |
| `plan.py` | 把匹配结果组织成分组计划 |
| `report.py` | 生成 `report.md` / `report.html` / `mapping.csv` |
| `writeback.py` | 写回 Steam（有 `PREFIX = "[来源] "` 保护 + 双备份） |
| `cefbridge.py` | 通过 Steam 客户端 CEF 调试口写入（已有 `evaluate()` 可复用来查名字） |
| `licenses.py` | 解析许可页 / CSV，判定来源类别 |

**重要**：`bundle.py` 里的 `meta` 字典是给 UI 和报告用的，加新字段（如 `cache_recovered_names`）时记得同步到 `service.py` 的 `build_notes()` 和 `report.py`，否则用户看不到。

---

## 9. 建议的实施顺序

1. **问题二（后缀剥离 + bundle 分支防护）** —— 纯逻辑、无 IO、风险最低，且有明确回归用例（Box 那行）。先做这个。
2. **问题一（appinfo 补名前置）** —— 收益最大（239 → 53），复用现成函数，风险可控（只填空缺）。
3. **问题三（score 调整）** —— 一行改动，但会影响 UI 展示，放到后面。
4. **问题四（联网补名）** —— 可选开关，最后做，默认关闭。

每步做完都跑一次 `python -m unittest discover tests`，并用同一份导出包记录数字变化。

---

## 10. 一句话总结

> 工具**已经能读到**本机所有游戏的真实名字，但补名的时机排在了「用名字去匹配」之后，形成循环依赖，导致 239 个游戏没名、56 行许可对不上、32 条脏名字；同时促销包后缀剥离不干净，还引发了一次静默错配。
>
> 核心修法：**把本机 `appinfo.vdf` 的补名提前到匹配之前，只填 `App <appid>` 的空缺**；**把促销包后缀剥干净，并禁止 bundle 分支匹配年份碎片**。