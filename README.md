# steam-source-collections

按「这款游戏是怎么来的」把 Steam 库分成收藏集：商店购买、CDK 激活、免费领取（喜加一）、赠礼／订阅、家庭共享。先生成**可审阅的 dry-run 计划**，确认无误后才可选写回 Steam。

**图形界面是主路径**：双击一个中文启动器，第一次先按界面 NO.00 的引导导出一份许可记录，之后点「一键统计」看结果、再点「一键写入 Steam」就完成整理，全程不需要碰命令行、不需要找文件。另有纯前端离线页 `try-it.html`（不装 Python 也能看结果）和零依赖 CLI（给维护者 / CI 用）。所有数据只留在本机。

> English: a small local desktop GUI (plus a zero-install offline page and a zero-dependency Python CLI) that reads your Steam license page, aligns package names to appids, and generates a reviewable plan for five acquisition-based collections. It never writes to Steam unless you explicitly opt in.

## 为什么需要它

| 数据源 | 有什么 | 缺什么 |
| --- | --- | --- |
| `IPlayerService/GetOwnedGames`（Steam Web API） | appid、名称、游玩时长 | **没有获取方式** |
| 账户明细 →「查看许可和产品序列号激活记录」 | 包名、日期、**获取方式** | **没有 appid** |

Steam 自带的动态收藏集能按类型、标签、游玩状态、已安装、时长等条件自动分组，但没有「获取方式」这一项。所以把这两份数据对齐，才是按来源分组的唯一可行路径——本工具做的就是中间这一层，并把对不上的行全部列出来交给你复核，而不是猜。

## 与已有项目的区别

| 项目 | 做到哪一步 |
| --- | --- |
| Greasy Fork「Steam 许可分类器」（MIT） | 在浏览器里把许可分成商店／零售／礼物／免费四类，显示 AppID、筛选、复制。**不生成收藏集，不含家庭共享** |
| `xPaw/SteamLicenseParser` | 粘贴许可文本生成统计，不写收藏集 |
| `IroN404/Steam-License-Remover` | 批量移除免费许可 |
| `Smirk1921/steam-collection-skill` 及其 fork | 抓许可页只用于「审计有没有漏游戏」，分类维度是类型／系列／厂商 |
| **本工具** | 许可 → appid 对齐 → **5 组来源收藏集 + 交叉组**，输出可审阅计划，可选写回 |

## 快速开始：图形界面（推荐）

需要 Python 3.9+，**没有第三方依赖**。

- **Windows**：双击项目里的 `启动 Steam 分组工具.bat`
- **macOS / Linux**：运行 `start-gui.sh`
- 任意平台：`python -m steam_source_collections.gui`

启动后会自动在浏览器打开本地界面。界面采用终末地风味的工业控制台风格：直角细线分区、编号与真实计数、切角按钮，浅色与深色两套主题跟随系统；完整报告页也是同一套配色。

### 第 1 次：先导出一份自己的数据（只做一次，约 30 秒）

**工具不会自动拿到你的许可记录**，第一次必须做这一步。界面最上面就是展开的 **NO.00「先导出一份自己的 Steam 数据」**，照着里面的顺序做三步：

1. 把「导出 Steam 数据」按钮**拖到浏览器书签栏**（看不到书签栏按 `Ctrl+Shift+B`）。**在工具页上点它没有反应**，必须拖走。
2. 点「打开 Steam 许可页」，确认已登录，然后**在许可页上点一下刚加的书签**。进度条会自动翻完所有分页、补全游戏名称、读取家庭共享（大库约 30～90 秒，走完前别关页面），完成后浏览器会**自动下载** `steam-licenses-*.json`。
3. 回到工具页，**直接点「一键统计 →」**。工具会自己在下载文件夹里找到刚下的文件，不用去翻目录；万一没找到，把文件拖进窗口，或点「找不到文件？」→「选择导出文件」。

> 为什么非要点这一下：「这款游戏是怎么来的」只显示在你已登录的许可页上，浏览器不允许本地工具去读登录状态，所以只能让书签运行在那一个页面上。导出包会被记住，**以后每次打开工具直接一键统计**；想补 ⑤ 家庭共享或换一份数据时，点「查看导出步骤 / 重新导出」再做一次。
>
> 书签只在许可页有效；在工具页上点它没有反应。之前拖过书签的请删掉重拖，点书签时右上角面板显示 `v4` 即为当前版本。

### 以后每次：两个大按钮

| 按钮 | 做了什么 |
| --- | --- |
| **一键统计 →** | 自动找到下载目录里最新（或上次拖进来）的导出包，只读解析本机 Steam 缓存、游玩时长与家庭共享数据，生成 `out/report.html` 并在页面上展示 ①～⑥ 分组。不改 Steam 任何文件 |
| **一键写入 Steam →** | 确认后只**新增** `[来源] ` 前缀的收藏集，写前双备份、原子替换；旁边就是「一键还原」 |

界面里还有「在桌面创建快捷方式」——点一次，以后直接从桌面图标启动，不必再打开项目文件夹，也不会出现任何命令行窗口。已经拿到导出包时，NO.00 会自动收起，只留标题、状态徽标和「查看导出步骤 / 重新导出」入口，不占地方。

**不装 Python 的备选**：用浏览器打开 `try-it.html`，把下载的文件拖进大框即可看结果。它是纯前端等价实现，不联网、不写入 Steam；需要「一键写入」或落盘完整报告时用图形界面。

### 图形界面的实现与边界

界面是一个本地网页，由 Python 标准库起的微型服务托管：只监听 `127.0.0.1` 的随机端口，用启动时生成的一次性 token 校验每个请求，并校验 Host 头防止 DNS rebinding；30 分钟无操作自动退出，界面里也有「退出程序」。它不联网、不上传、不读取 Cookie 或密码，只是把本机文件读写包装成按钮。页面加载后没有定时轮询、没有后台常驻检测；「Steam 是否关闭」只在点写入按钮时查一次，落盘前再复核一次。

> **⑥ 组太多？点「一键统计」就会自动补，不用重新导出、也不用找文件。** Steam 商店接口本来就不给 DLC／已下架／试玩项目返回名称，反复重导也补不回来。真正能精确对齐的是 Steam 客户端本机缓存的 `appcache/packageinfo.vdf`（「礼包包 → appid」映射），程序会自动定位并只读解析它。
>
> 这个缓存**不是你的游戏库清单，也不记录来源**：它是客户端缓存的公共包元数据，里面可能包含你并未拥有的包，字段是 `packageid / appids / depotids / billingtype / licensetype / status` 等，没有日期、账号、支付方式。「Steam 商店购买 / CDK / 喜加一 / 赠礼」只来自许可页的 `Acquisition Method`。缓存仅回答「这行包名对应哪几个 appid」。
>
> **导出是否成功**：点完书签后浏览器会下载 `steam-licenses-*.json`，拖进界面或点「重新检测」即可。只有「只翻了 1 页、许可行数远少于库」才是分页失败；翻了十几页、几百行许可属于正常情况，界面会直接给结果。
>
> **时长与交叉组**：书签调用 `GetOwnedGames` 取回游玩时长，供「吃灰区 / 真的玩过 / 付费没启动」使用；若接口不可用，界面会明确标注「未生成」而不是给出错数字，图形界面与 CLI `--auto` 会读本机 `localconfig.vdf` 补时长。

### ⑤ 家庭共享的数据从哪来

许可页是你自己的许可列表，`GetOwnedGames` 也只返回**这个账号拥有的**游戏，两者都不含家人借给你的游戏。Steam 官方只在游戏运行时提供 `BIsSubscribedFromFamilySharing` 这类逐款判定，没有「列出全部共享游戏」的公开接口。

导出书签用商店页自带的 `webapi_token` 调用未公开的 `IFamilyGroupsService`：`GetFamilyGroupForUser` 拿家庭组 ID → `GetSharedLibraryApps` 列出可借的 appid（过滤 `exclude_reason` 和已拥有的）。**不需要 API Key、不需要家人的账号或密码**。本机 `localconfig.vdf` 里能读到家庭组，但不含共享游戏清单，那份清单只在 Steam 云端。接口失败时导出包写入 `diagnostics.family.status`，⑤ 组会留空并提示可用 `--family-file` 手动填，不会假装成功。

### 能不能直接写进 Steam 收藏集

默认**不改动 Steam 任何文件**：`out/report.html` 里每组的「复制游戏名清单」可在客户端手动建组，最安全。想让工具代写，点图形界面里的「一键写入 Steam」：

1. **Steam 正在运行、且已开启本机调试接口时**：工具通过 Steam 客户端自己的收藏集接口写入，由 Steam 负责上传云同步。**不用退出 Steam、不用重启**，Steam Deck 和其他设备会自动看到新分组；你在客户端里手动做过的调整会保留。**多数 Steam 客户端默认没开这个接口**，所以通常走下面第 2 条。
2. **没有调试接口时**：回退到写入本机收藏集文件，会先提醒你**完全退出 Steam**（托盘图标 → 退出），写前双备份 + 原子替换，并同步抬高云存储版本计数；写入后重新启动 Steam 才会上传，Steam Deck 也要重启客户端。
3. 确认层列出将写入的收藏集，勾选确认后才执行；只新增 `[来源] ` 前缀的分组，不碰任何现有分组，重复执行不会重复建组。点写入时才会检查一次 Steam 状态，平时没有任何后台轮询。
4. 想撤销：退出 Steam 后点「一键还原」，从最近的备份恢复。

> ⚠️ 客户端通道用的是 Steam 未公开的内部接口，客户端大版本更新后可能变化，那时会自动回退到文件方式。调试接口只监听本机 `127.0.0.1:8080`，工具不上传任何数据、不读密码或 Cookie。**不开启也完全能用**，只是写入时要退出一次 Steam；想省这一步，可在 Steam 安装目录放一个空的 `.cef-enable-remote-debugging` 文件后重启 Steam 开启该接口。

### 为什么还必须点这一下

- **浏览器不允许本地网页读取 Steam 的登录状态。** 同源策略禁止 `try-it.html`（一个本机文件）去读 `store.steampowered.com` 的 Cookie；如果可以，随便一个网页都能偷你的账号。本工具也不会去翻浏览器的 Cookie 数据库、Steam 客户端的登录凭据或 WebHelper 缓存。
- **Steam 客户端本地没有明文「获取方式」。** 库、游玩时长、收藏集都在本地文件里，但许可数据是加密的 `licensecache`；`GetOwnedGames` 也不返回支付方式。这份数据只存在于需要登录的许可页上。
- 所以唯一绕不过去的，是让代码**运行在那个已经登录的页面上**。书签脚本只访问 Steam 自己的页面与接口：顺着 `a.license_paginator_next` 抓完所有许可分页，用页面 `#application_config` 里的 Web API token 向 `api.steampowered.com` 发一次 `GetOwnedGames` 取时长（商店 CSP 明确允许该来源），再用商店自己的 `appdetails` 补齐名称，结果只下载到你本机，不发给任何第三方。

> 想完全不碰浏览器，可以改用 `--api-key` 拉库列表，但那样依然拿不到获取方式，分组就无从谈起。

## 可选：用 Python 落盘报告

```bash
cd steam-source-collections
python -m steam_source_collections --auto --out out
```

`--auto` 会**只读**完成这些事：定位本机 Steam 安装目录（Windows 注册表 / 常见路径 / Linux / macOS）、读取 SteamID64 和账号目录、识别现有收藏集格式、读取本地游玩时长、解析 `appcache/packageinfo.vdf` 得到「礼包包 → appid」精确映射、在下载目录里找到刚导出的 JSON，最后输出 `out/report.html`（可直接双击打开的完整分组网页）/ `report.md` / `mapping.csv` / `collections_plan.json`。这一步既补上浏览器路径缺失的时长，也能把无名称的 DLC／下架游戏按许可记录精确归组。

本机缓存还用于一种**保守的顺序对齐**：许可页是「新→旧」，`rgOwnedPackages` 是「旧→新」，带真实 packageid 的行形成锚点，只在行数与包数严格 1:1 的空档里推定缺失的 packageid。推定结果在 `mapping.csv` 里以 `match_via=order` 标注，且不会覆盖已有明确证据。想只用直接证据可加 `--no-order-infer`。

试用页是同一套规则的 JS 等价复刻：导出包路径与手动路径的九个分组都和 Python CLI 逐条对照过；两者结果一致。

## 快速开始（手动模式 · 备选）

需要 Python 3.9+。

```bash
git clone <你的仓库地址>
cd steam-source-collections
python -m steam_source_collections --help
```

### 1. 保存许可页

登录 Steam 后打开 <https://store.steampowered.com/account/licenses/>，把**全部记录加载完**（页面是 AJAX 翻页，往下滚到底或翻页完成），再用浏览器 `Ctrl+S` 保存为 `private/licenses.html`。

许可很多时保存的 HTML 可能只有第一页，更稳的方式是整理成 CSV：

```csv
date,name,method
2023-01-14,Aurora Drift,Steam 商店
2022-11-20,Tidebound Tactics,零售
2020-12-25,Paper Kingdom,免费赠送
```

也支持 `.txt`（每行 `日期 | 包名 | 获取方式`）。工具识别中英文标签：`Steam 商店 / Steam Store`、`零售 / Retail`、`免费赠送 / Complimentary / Free`、`礼物 / Gift / Guest Pass`。

### 2. 拿到游戏库列表

二选一：

```bash
# A. 直接用 Steam Web API（需免费 API Key + 游戏详情设为公开）
python -m steam_source_collections \
  --licenses private/licenses.csv \
  --api-key YOUR_KEY --steamid 7656119XXXXXXXXXX \
  --out out

# B. 用自己导出的 GetOwnedGames JSON（不需要把 Key 交给任何脚本）
python -m steam_source_collections \
  --licenses private/licenses.csv \
  --owned private/owned.json \
  --family-file private/family.txt \
  --out out
```

`--owned` 接受完整的 `GetOwnedGames` 响应、`{"response": {"games": [...]}}`、`[{appid, name, playtime_forever}, ...]`，或简单的 `{"730": "Counter-Strike"}` 映射。

### 3. 家庭共享（可选）

许可页里**没有别人的游戏**。若想把 ⑤ 组填满，给 `--family-file` 一个清单，每行一个 appid 或游戏名；库 JSON 里带 `family_shared: true` 的条目也会被自动归组。

### 4. 先看计划，再决定

```text
out/collections_plan.json   机器可读的分组计划
out/mapping.csv             每款游戏的来源、匹配方式、时长，方便 Excel 里改错
out/report.md               中文摘要 + 未匹配清单 + 已知限制
```

**默认不会改动 Steam 的任何文件。**

想先跑通看效果，可以直接用仓库里的合成示例（虚构游戏名，不含任何真实账号数据）：

```bash
python -m steam_source_collections \
  --licenses examples/licenses_sample.html \
  --owned examples/owned_games_sample.json \
  --family-file examples/family_sample.txt \
  --out out
```

## 分组规则

| 收藏集 | 匹配到的获取方式 |
| --- | --- |
| ① Steam 商店购买 | `Steam 商店` / `Steam Store` |
| ② CDK·Key 激活 | `零售` / `Retail` / `Activation Code` |
| ③ 免费领取·喜加一 | `免费赠送` / `Complimentary` / `Free` / `Promotional` / `NoCost` |
| ④ 赠礼·订阅 | `礼物` / `Gift` / `Guest Pass` / `订阅` |
| ⑤ 家庭共享（别人的） | `--family-file` 或库数据的 `family_shared` 标记 |
| ⑥ 未能判定 | 没有许可行匹配到，需人工／AI 兜底 |

交叉组（可用 `--no-extra-groups` 关闭）：`③b 免费领取·从未启动`、`③c 免费领取·真的玩过`、`⑦ 付费购入·从未启动`。一个游戏可以同时属于多个收藏集。导出包不含时长时会跳过这三个组并明确提示；图形界面与 CLI `--auto` 会读本机 `localconfig.vdf` 补上。

同一 appid 命中多条许可时，默认 `--conflict priority` 让付费记录优先（买过的不会被误标成喜加一）；`--conflict latest` 则按日期取最新。

## 已知限制

- 许可页只有包名、没有 appid，且包名常带版本／地区后缀或本地化译名。工具会剥离后缀、拆分双语包名，并用本机 Steam 缓存里的中文名精确对齐；仍对不上的（改名、Demo、无名 DLC）会列进「未能对齐」而不是硬猜。
- 合集包／促销包是一行许可对应多个 appid（`Batman: Arkham Collection` 之流）。这类行会按本机 `packageinfo.vdf` 的「包 → appid」映射整包展开，本体和 DLC 一起归到同一来源，`mapping.csv` 里标为 `match_via=bundle`；只有高置信且未被其它许可行认领的包才会展开。
- 「免费周末」和「限时免费永久领取」在 Steam 都显示为免费类许可，无法自动区分。
- ⑥ 组与「未能对齐」是同一次配对失败的两面：前者是没被许可行认领的 appid，后者是没找到 appid 的许可行。Steam 客户端只把库里作为独立游戏显示的 appid 计入收藏集数字，DLC／原声带／试玩／无名 appid 会被折叠，所以 ⑥ 在报告里可能有一百多项、客户端里只显示十几个。两张表可以互相对照人工补。
- Steam 客户端更新可能改变许可页 DOM；此时用 CSV 输入更可靠。
- 不含剧情／爽玩这类玩法判断。那是另一个维度，需要联网逐款核实，适合再叠一层 AI 分类。
- 新版 Steam 客户端把收藏集存成 `[key, meta]` 键值数组（旧版是字典），格式未公开且随客户端变化。收藏集是**账号级云数据**：只有 Steam 客户端自己发起的修改才会上传，外部程序改本地文件不会被同步到其他设备（抬高云同步版本计数也不等于客户端上传）。因此工具优先驱动运行中的客户端写入；没有该接口时才回退到文件写入，并明确提示重启 Steam。
- 导出书签靠 Steam 商店的公开 `appdetails` 接口补游戏名称；DLC／已下架／地区限制项目本来就不返回名称，重导也补不回来，由「一键统计」用本机 Steam 缓存精确对齐。
- 离线页 `try-it.html` 能读导出包里的时长与家庭共享清单，但**不能读本机 Steam 文件、不能写入收藏集**；这两件事只有图形界面 / CLI 能做。

## 写回 Steam（实验性 · 默认关闭）

```bash
python -m steam_source_collections ... \
  --writeback "C:/Program Files (x86)/Steam/userdata/<id3>/config/cloudstorage/cloud-storage-namespace-1.json" \
  --apply --yes
```

写回逻辑会先探测文件结构，只创建／覆盖名称以 `[来源] ` 开头的收藏集，其它内容（含系统「收藏夹」「已隐藏」）原样保留；写入前在两处生成备份并原子替换。探测失败会直接拒绝写入并提示改用 `report.md` 手动建组。

> ⚠️ 这一节是**命令行直接写本地文件**的方式，属于**非官方**操作：**必须完全退出 Steam**、确认备份存在后再试。图形界面会优先让运行中的 Steam 客户端自己保存（见上文「能不能直接写进 Steam 收藏集」），那条路径不用退出 Steam。客户端更新可能改变存储格式（较新的客户端可能已改用 LevelDB）；最安全的方式始终是按报告在客户端里手动建 6 个收藏集。

## 隐私与安全

- 不需要、也不会保存你的 Steam 登录 Cookie；`--auto` 只读安装路径、SteamID64、游玩时长和收藏集结构，**不读** `loginusers.vdf` 里的账号名/记住密码字段、不读 Cookie、不读 WebHelper 缓存、不解密 `licensecache`。
- 一键导出书签只请求 `store.steampowered.com` 自己的页面和公开接口，结果直接下载到你本机；不向任何第三方发送数据。`try-it.html` 读取文件后只在浏览器内存里解析，不联网、不存储、刷新即清空。
- 使用 `--api-key` 时，请求只发往 `api.steampowered.com`；密钥只出现在命令行，不写入任何输出文件。
- 输出的 `report.md` / `mapping.csv`、以及 `steam-licenses-*.json` 导出包都含你的**完整游戏库与许可记录**，属于个人数据。`private/`、`out/`、`demo/`、`prototypes/preview/`、`steam-licenses-*.json` 已在 `.gitignore` 中忽略，**发布前请 `git status --ignored` 复查一遍**。
- 自动化访问请遵守 Steam 服务条款、控制请求频率，不要用于批量抓取他人数据。

## 二次发布 / 部署到你自己的账号

代码是 MIT 许可。想基于它改一版，直接 Fork 即可；想放到一个独立的新仓库里：

```bash
git clone https://github.com/Sirli01/steam-source-collections.git
cd steam-source-collections
git status --ignored          # 确认 out/、demo/、prototypes/preview/ 被忽略
git remote remove origin
# 在 GitHub 网页新建一个空仓库（不要勾选 README / LICENSE / .gitignore），然后：
git remote add origin https://github.com/<你的用户名>/steam-source-collections.git
git push -u origin main
```

改之前建议先做三件事：

1. 把 `LICENSE` 里的版权行改成你的名字（或保留 `contributors`）；
2. **检查提交身份**：`git config user.name` / `git config user.email` 会永久写进公开提交记录。不想公开私人邮箱，就只对当前仓库执行一次 `git config user.email "<用户名>@users.noreply.github.com"`（带数字前缀的专用地址可在 GitHub 的 Emails 设置页查到），不影响全局配置；
3. 想放效果图，用 `prototypes/ui-variants.html` 这类设计稿，或重新跑一次并打码 SteamID；**别把 `out/` 里的真实报告或 `prototypes/preview/` 的调试截图传上去**。`examples/` 里的样例游戏名全是虚构的，可放心提交。

## 测试

```bash
python -m unittest discover -s tests -v
```

## 致谢与许可

- 许可页 DOM 字段与分类关键字参考了 Greasy Fork 上的「Steam 许可分类器」（MIT）及 `xPaw/SteamLicenseParser`。
- 许可页审计思路参考 `Smirk1921/steam-collection-skill`；`ValvePython/steam` 文档中的 `EPaymentMethod` 用于确认底层字段语义。
- MIT License。与 Valve / Steam 无关联，亦未获其认可；按「现状」提供，使用风险自负。
