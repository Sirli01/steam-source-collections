# Changelog

## 0.7.3 — 本机 appinfo 补名、促销后缀清理与界面优化

- **前置 `appinfo.vdf` 补名与头部时间戳修复**：`appinfo.py` 新增 `read_app_names()` 只读提取缺失游戏主名称，修复 `_header_end()` 按 64 位读取 `off + 12` 导致含非零 PICS 访问令牌的游戏被跳过的问题；`load_bundle()` 在构建匹配索引之前先用本机缓存填补空缺（绝不覆盖已有名称），大幅消除 `App <appid>` 占位符。
- **促销包后缀剥离与防错配**：`matching.py` 新增 `strip_promo_suffix()` 完整剥离 `Limited Free Promotional Package - Mon YYYY`、`Free Weekend - Mon YYYY` 等促销尾缀；`bundle` 分支跳过月份年份及版本碎片，杜绝跨游戏的年份误匹配；`pkgexpand.py` 同步剥离促销后缀并补充展开单名命中的未认领多应用礼包。
- **显示名与匹配索引隔离**：`bundle.py` 对单应用礼包兜底借用名清理促销后缀后仅用于展示，不再进入 `build_index()` 索引；`_guess_pid()` 优先精确匹配并跳过跨 appid 的归一化歧义键；顺序对齐推定（`via="order"`）置信分下调至 `0.65`，CLI 新增 `--strict-order`（`--no-order-infer` 别名）及默认关闭的可选 `--online-names` 联网补名开关。
- **前端 UI、报告体验与自主同步控制升级**：桌面界面增加吸顶导航与 GitHub 入口、分组占比条、游玩状态分组卡片（支持点击任意分组弹出搜索与复制名单）及「写入时按分组自由勾选」面板；写入 Steam 改用统一「✥ 」符号前缀（自动就地迁移旧版 `[来源]` 与 `①～⑨` 分组，未勾选旧组自动清理）；`detect_idled_appids` 结合掉卡时长窗口、集换式卡牌标记（`category_29`）、成就与存档信号估算挂卡刷时长（试验）；离线报告 `report.html` 新增实时搜索过滤栏与 `无商店记录` 徽标；Windows 主启动器简化为 `双击启动.bat`。

## 发布准备（0.7.2 同期，无功能变更）

- `.gitignore` 新增 `prototypes/preview/` 与 `*.log`：设计稿截图、渲染预览里含本机真实统计数字、真实导出包文件名和调试探针，属于验证产物，不进公开仓库。
- 清掉公开文案里的私人痕迹：设计稿的账号昵称改为占位名，README 去掉本机礼包包的具体数量，离线页注释去掉具体许可行数。
- README 澄清「命令行文件写入仍需退出 Steam / 图形界面优先客户端通道」这一差异；发布相关章节改为「二次发布 / 部署到你自己的账号」，面向 Fork 与独立仓库两种用法，并保留提交邮箱会永久公开、截图需打码的提醒。
- 新增 `tests/test_release.py`：静态核对忽略规则、LICENSE、版本号一致性，以及所有将被提交的文件里不含 SteamID、真实路径、账号昵称、真实导出包文件。

## 0.7.2 — 补齐首次使用的导出引导

- 界面把「怎么拿到导出包」提到首屏 NO.00 独立卡片，排在「一键统计」之前；没有导出包时自动展开并标记「未拿到导出包」，拿到后自动收起成一行，不干扰老用户。用户手动收起后不再被自动改动。
- 三步引导按真实点击顺序重写：拖书签到书签栏（明确「在这里点没有反应」）→ 打开许可页并在**那个页面**点书签（说明进度条、耗时、自动下载的文件名）→ 回来直接点一键统计（说明工具会自己找文件，不用翻目录）；辅助入口收进「找不到文件？」折叠区，避免六个按钮挤在一起。
- 修复 `scan()` 已返回、但界面从未展示的 `bundle_error`：文件损坏或格式不对时，状态卡显示真实原因并在引导卡给出「重新导出 / 换一份文件」的指引，不再误导成「还没找到导出包」。旧导出包缺家庭共享的提示也移到同一处。
- 按钮「数据不对？更新导出包」改为「查看导出步骤 / 重新导出」；README 把「第 1 次：先导出一份数据」前置为明确必经步骤，「两个大按钮」改标为「以后每次」，并同步 `service.py` / `cli.py` 里的提示文案；`try-it.html` 里两处「启动图形界面直接一键统计」也补上「第一次先导出一次」，避免试用页暗示不用导出。
- 收起态隐藏第一次使用的长说明，只留标题、状态徽标与入口；补窄屏规则（徽标换行、步骤里的长文件名可断行），窄视口实测无横向溢出。
- 新增 `tests/test_onboarding.py`：断言引导卡位置、默认展开、步骤顺序、状态驱动与错误展示、脚本引用的元素 ID、README 顺序、旧文案清零（纯静态检查，不碰真实 Steam 数据）。

## 0.7.1 — 界面改为终末地风格

- 图形界面与完整报告页统一换成终末地风味工业控制台（coldstore 变体：ice / cool / tight / grid / standard / calm）：深色命令栏、三色条、大数字看板、1px 细线记录行、切角按钮、真实编号与计数；零圆角、零阴影、零渐变，强调色只做填充，红色只给「一键还原」。
- 浅色与深色是两套完整主题，继续跟随系统 `prefers-color-scheme`；`prefers-reduced-motion` 下关闭动效；离线单文件，不加载外部字体。
- 保留全部功能与元素 ID：一次性 token 校验、一键统计 / 写入、Steam 客户端云同步通道、一键还原、拖入文件、报告 iframe、书签导出；按钮文案去掉 emoji，与界面和 README 一致。
- 报告页同步更新写入说明：Steam 开着时由客户端自己保存并上传，不用退出；移除过时的「云同步可能覆盖本地新增」表述。
- 设计稿五版留在 `prototypes/ui-variants.html`（一次性原型，不进主界面）；89 个单测全过，Chrome 无头验证浅色 / 深色 / 窄屏渲染。

## 0.7.0

**写入改为驱动运行中的 Steam 客户端：不用退出 Steam，Steam Deck 自动同步。**

用户实测发现「重启 Deck 没用，但在客户端里手动改一下收藏集就立刻同步」，因此确认了根因：Steam Cloud 只上传**客户端自己发起的修改**。0.6.6 抬高 `cloud-storage-namespaces.json` 版本计数后，Steam 启动时仍把计数拉回云端值（6306 → 6308），新分组从未上传；用户在 Deck 上手动删掉「人格解体」后，Deck 才把 ①／⑥ 推到云端，PC 再合并下来（① 441、⑥ 29，version 6307/6308，其余四组仍是本地 6326~6329）。

- 新增 `cefbridge.py`：纯标准库的最小 WebSocket + Chrome DevTools Protocol 客户端，只连本机 `127.0.0.1:8080`，从 `/json/list` 找 `SharedJSContext`，用 `Runtime.evaluate` 调用 Steam 内部的 `collectionStore`。`StoreObject(id, data)` → `GetCollection(id)` → `SaveCollection(col)` 让 Steam 自己写版本号并上传，删除走 `DeleteCollection`。严格校验 `Valve Steam Client` UA，避免误连占用 8080 的其它软件。
- `service.py` 新增 `merge_plan_with_client()` 与 `client_write_plan()`：按名称复用现有组 id，用 `out/client_baseline.json` 区分「统计结果的变化」与「用户手动改动」——用户新增的游戏保留、用户删掉的游戏不会被加回来。`preflight()` 返回 `via=client|file`；`apply_plan()` 优先走客户端通道，失败才回退文件写入（此时仍要求退出 Steam）。
- `gui.py` / `gui_page.html`：客户端通道不再要求退出 Steam，确认层与成功提示改为「Steam 自动同步到账号和 Steam Deck」；文件通道保留原来的退出提示与一键还原。
- 实测：本机 Steam 的 CEF 调试接口已开启，探测到 `collectionStore` 与 6 个 `[来源]` 组；原样保存 6 组后云版本计数 6308 → 6310，再用真实统计结果跑完整通道 6314 → 6315，6 组内容保持 441/247/421/80/124/29（用户的手动移动完整保留）。
- 顺带修正第一次跑测试时误连真实 Steam 的问题：`tests/test_gui.py` 的两个测试类统一 mock 掉 `debugger_available`，单测绝不触碰真实客户端。

限制：客户端内部接口未公开，Steam 大版本更新后可能变化；届时自动回退到文件方式。

## 0.6.6

**修掉「写入成功、Steam Deck 上却还是旧收藏」的账号同步问题。**

收藏集是账号级云数据，客户端把本地状态记在 `cloud-storage-namespace-1.json`，把「已同步到云端的版本计数」记在同目录 `cloud-storage-namespaces.json`。旧版写入只改了前者：新键的 version 已经到 6319～6324，而 namespace 1 的计数还停在 6306，客户端不认为本地有新改动，因此从没把这 6 个 `[来源]` 分组上传到账号；本机 Steam 里能看到，Steam Deck 拉到的仍是云端旧数据。

- `writeback.py` 新增 `_bump_namespace_index()`：写入收藏集后，把 `cloud-storage-namespaces.json` 里 namespace 1 的计数抬到本次写入的最大 version；两个文件都双备份、原子替换，还原时一起回滚。
- 新增条目补上客户端自己会写的 `conflictResolutionMethod: "custom"` 与 `strMethodId: "union-collections"`，让 Steam 按收藏集合并策略处理云冲突。
- `service.restore_latest()` 会按备份时间戳找到配对的版本计数备份并一并还原；`restore-collections.bat` 同步还原两个文件。
- 界面与 README 说明写清楚：写入 ≠ 同步到账号，必须重启 Steam 等待上传，Steam Deck 也要重启客户端；若弹云同步冲突，选择保留本机版本。

写入仍要求 Steam 完全退出，且不会替用户执行写入。

## 0.6.5

**修掉「明明买过、Steam 里却只显示在 ⑥」的合集包问题。**

用户重新写入后发现 ⑥ 在客户端只显示 17 项，且看上去都是正经游戏。查真实数据：许可页 913 行里只有 288 行带礼包 ID，顺序对齐又跳过了不规则区间，剩下的 `Batman: Arkham Collection`、`《巫师 3：狂猎 - 完全版》`、`PAYDAY 2 - Pre-2024-08`、`Sid Meier's Civilization VI : Platinum Edition` 等**合集包名**既没有单个游戏与之同名，也没有礼包 ID 可用，于是整包几十个 appid 全部落进 ⑥。Steam 客户端只把作为独立游戏显示的 appid 计入收藏集数字，DLC／原声带／试玩／无名项目被折叠，所以报告里 131 项、界面里只有 17 项。

- 新增 `pkgexpand.py`：把「未对齐许可行」与本机 `packageinfo.vdf` 里「未被认领的礼包包」配对，用包内游戏名打分（归一化包含 / 公共前缀），高置信且无歧义时**整包展开**，本体与 DLC 一起归到同一来源。只认领尚未被任何许可行认领的 appid，绝不改写已有分组；一个包只给一行、一行只拿一个包。
- 中文合集包名用 `appinfo.py` 新增的本地化片段反查：多个本地化名指向同一个未认领包才算证据（「巫师」命中 3 个 appid、全在 Witcher 3 完全版包里），单个命中不猜。
- 修 `matching.normalize()`：`™` / `®` 必须先删除再做 NFKC，否则兼容性折叠会把 `™` 变成字母 `tm`，导致 `Middle-earth™: Shadow of War™` 永远对不上 `Middle-earth: Shadow of War Definitive Edition`。
- 报告说明、README 与界面数据说明补上 `match_via=bundle` 的含义与「客户端只显示独立游戏」这一点。

用 21:15 的真实导出包复算：19 行合集包展开、101 个 appid 从 ⑥ 归入正确来源，⑥ 131 → 30，未对齐许可 75 → 56；**所有分组变化都是 `unknown → 来源组`，原有分组零改动**。⑥ 剩下 30 项里 27 项无名，能显示出来的只有 2 个测试工具（`Star Swarm Stress Test`、R6 测试服）和 1 个中文译名游戏（`Depersonalization`／人格解体，其 appinfo 记录头格式不同，缓存定位失败）。

## 0.6.4

**修掉统计结果卡片溢出，并让界面跟随系统的浅色／深色模式。**

- `.groups` / `.status-grid` 从 `repeat(3,1fr)` 改为 `repeat(3,minmax(0,1fr))`。`1fr` 的最小宽度是 `auto`，第二列那条超长的英文游戏名把整列撑到 580px，后两列被挤出卡片；现在列宽可收缩，长样本名在卡片内显示省略号。网格项补 `min-width:0;overflow:hidden`，说明文字补 `overflow-wrap:anywhere`。
- 主界面与 `report.html` 全部改用 CSS 变量，新增 `@media (prefers-color-scheme:dark)` 深色配色，并加 `<meta name="color-scheme" content="light dark">` 让滚动条、复选框等浏览器原生控件一并跟随。离线试用页原本已支持深色，三处配色保持一致。
- 完整报告嵌在界面的 iframe 里，因此也同步深色化，避免在深色系统中出现一整块白底；报告网格同时改为 `minmax(min(310px,100%),1fr)`，窄窗口也不会横向溢出。

## 0.6.3

**修复「明明买过、却有来源信息却落进 ⑥」的对齐失败。**

许可页只给包名，不给 appid。913 行许可里有 807 行靠本机 `packageinfo.vdf` 的礼包 ID 精确对齐；剩下那些既没有礼包 ID、又不在可推断区间的行，只能退化到字符串比名。而 Steam 的包名常带版本／地区后缀（`Atomic Heart - Standard Edition (CN)`）、双语名（`Palworld / 幻兽帕鲁`）或纯中文译名（`米塔` 对应 `MiSide`），旧的字符串相似度阈值一概判不出来，于是许可行进了「未能对齐」、对应游戏进了 ⑥。

- `matching.py` 新增保守的名称改写：只有当被删掉的部分**全是**版本／地区词时才剥离后缀；`/` 双语名取非中文的一半。像 `Counter-Strike: Global Offensive` 这种真实副标题不会被削成另一款游戏，地区词也只在判断尾部片段时使用。
- 新增 `appinfo.py`：只读解析 Steam 客户端 `appcache/appinfo.vdf` 里的本地化名称，把中文包名定位到你已拥有的 appid。命中要求该名称在记录内、appid 属于你的库、且所有出现位置指向同一个 appid，含糊的一律不猜。
- README 与 `report.md` 说明 ⑥ 组和「未能对齐」是同一次配对失败的两面，不再把 ⑥ 一概解释成 DLC／免费残留。

用 21:15 的真实导出包复算：未匹配许可行 92 → 75，⑥ 组 147 → 131，16 款完整游戏从 ⑥ 移到 ①／②（8 + 8），原有分组零改动。玩家举出的米塔、幻兽帕鲁、泰坦陨落 2 归入 ①，原子之心、P5S 归入 ②。

## 0.6.2

**去掉后台轮询：只在玩家真正要写入时检查一次 Steam。**

- 原先界面每 12 秒请求一次 `/api/state`，后端顺带查一次 Steam 进程；玩家只是看报告时这个检测毫无意义。现在 `service.scan()` 与 `writeback.preview()` 都不再探测进程，页面顶部也不再显示实时运行态，界面加载后零后台任务。
- 新增 `/api/preflight`：只有玩家点「✏️ 一键写入 Steam」时才检测一次。若客户端还在运行，弹窗直接说明「写入前需要关闭 Steam」；玩家点「我已退出，继续」会再查一次，仍没关就明确提示「你还没有关闭它」，不会偷偷写入。
- `/api/apply` 与 `service.apply_plan()` 在真正落盘前各再校验一次，防止检查后又启动 Steam；失败信息直接告诉玩家没关客户端。CLI `--apply --yes` 同样改成写入前一次性检查并拒绝执行。
- 真实写入已由玩家在 21:17 完成：6 个 `[来源]` 收藏集全部出现在 Steam 客户端里。

## 0.6.1

**去掉黑色控制台窗口，并清理面向用户的开发史说明。**

- 状态栏每 12 秒检查「Steam 是否在运行」时，原先会启动 `tasklist.exe`；在 `pythonw.exe`（无控制台）下，Windows 会为每个命令行子进程开一个黑色窗口，于是反复闪屏。现在改为进程内调用 Win32 Toolhelp32 快照，不再产生任何子进程；万一被安全软件拦截，回退路径也用 `CREATE_NO_WINDOW` + `SW_HIDE` 隐藏窗口。创建桌面快捷方式的 `powershell` 同样加了无窗口参数。
- Windows 启动器优先使用 `pythonw` / `pyw`；只有找不到它们时才以最小化窗口启动 `python`，不会再留一个常驻黑窗口。界面里的「桌面创建快捷方式」直接指向 `pythonw`，完全没有命令行窗口。
- 导出面板不再出现「全量，不再只补前 600 款」这类只有开发者才关心的历史说明；书签源码、README、离线页、CLI 与 GUI 提示里的 v1～v4 演进叙述一并删除，只保留当前版本号供核对。开发过程记录统一收在 CHANGELOG 里。

## 0.6.0

**把产品形态从「一堆脚本」改成「一个图形界面」**。这是面向发布的结构性调整：用户只需要双击一个中文启动器（或桌面图标），之后统计、查看报告、写入 Steam、撤销都在界面里点按钮。

- 新增 `steam_source_collections/gui.py`：Python 标准库起的本地服务，只监听 `127.0.0.1` 随机端口，一次性 token + Host 头校验（防 DNS rebinding），30 分钟空闲自动退出，界面里有退出按钮。
- 新增 `steam_source_collections/gui_page.html`：两个大按钮「🔍 一键统计」/「✏️ 一键写入 Steam」，状态卡、分组结果、内嵌完整报告、写入确认层、一键还原、桌面快捷方式、拖入导出包自动统计。
- 新增 `steam_source_collections/service.py`：把「扫描 → 统计 → 预检 → 写入 → 还原」抽成共用编排层，GUI / CLI 不再各写一套。`locate_bundle()` 会记住用户拖入过的导出包（`out/uploaded/`），下载目录被清理也不会丢。
- 新增 `启动 Steam 分组工具.bat`（Windows）与 `start-gui.sh`（macOS/Linux）；`pyproject.toml` 增加 `steam-collections-gui` 命令与 `gui_page.html` / `bookmark_src.js` package-data，pip 安装后也能启动界面。
- **移除** `make-report.bat`、`prepare-cache.bat`、`apply-collections.bat`、`open-downloads.bat`；文档、离线页、报告、书签提示里所有「双击 xx.bat」的引导统一改为图形界面按钮。仓库里只剩一个启动器。
- 修正文档里过时的限制说明（新版键值格式现在可追加写入、v4 导出包含时长与家庭共享）。
- 47 个单元测试全过；真实启动 GUI 进程做了端到端验证：无 token 403、页面注入、扫描、上传、统计、报告、书签、未确认写入被拒绝、退出。全程未改动用户真实 Steam 文件。

## 0.5.0

补上最后两块：⑤ 家庭共享自动扫描，以及真正写进 Steam 收藏集（默认关闭）。

- **⑤ 以前是 0 的原因**：许可页与 `GetOwnedGames` 都只列出自己账号拥有的游戏；Steam 官方只在游戏运行时提供逐款 `BIsSubscribedFromFamilySharing` 判定，没有公开「列出共享库」的接口。本机 `localconfig.vdf` 的 `FamilyGroup` 只存成员 accountid（本机 5 位），不含游戏清单；社区 games XML 未登录返回 Sign In 页；拉家人 `GetOwnedGames` 需要 API Key 且对方库公开。
- **v4 导出脚本**：用商店页已有的 `webapi_token` 调用 `IFamilyGroupsService/GetFamilyGroupForUser` + `GetSharedLibraryApps`（端点实测存在，无凭证返回 401 而非 404），把借来的 appid 写入 `family_shared_appids` / `family_shared_names` / `family_groupid` 与 `diagnostics.family`，`schema` 升 `/3`。不需要 API Key 或家人凭据；失败只影响 ⑤ 组。
- **Python / 网页接入**：`bundle.py` 与 `try-it.html` 把共享游戏作为 `family_shared` 的 Game 加入 ⑤ 组，并**排除出许可行名称匹配索引**，避免同名借来的游戏抢走许可；`plan.py` 原本就不把它们算作「缺许可」。旧包会明确提示「用 v4 重导一次」（这次重导有实际收益，与之前「重导也补不回名称」不同）。
- **自动写回（默认关闭）**：新版 `[key, meta]` 云存储结构已解析清楚（`user-collections.uc-XXX` → `{"id","name","added","removed"}`）。`writeback.py` 改为**只追加** `[来源] ` 前缀键、不修改现有条目、幂等；要求 Steam 完全退出，双备份 + 原子替换 + 生成 `out/restore-collections.bat`。新增 `apply-collections.bat`（双击 + 输入 YES），`report.html` 写明风险与回滚。
- 38 个单元测试全过；模拟 v4 包验证 ⑤=6 且网页与 Python 六个来源组逐条一致；三处 JS 语法通过；真实收藏集文件**只做只读预检**（`steam_running=true`，未写入任何内容）。

## 0.4.1

把「让用户自己找 `packageinfo.vdf`」这道多余工序删掉：

- 事实上 CLI `--auto` 本来就通过注册表／常见路径自动定位 Steam 并解析缓存，用户根本不需要知道这个文件在哪；上一版却在网页里让用户去 `Steam 安装目录\appcache\` 翻文件。
- `make-report.bat` 改为真正的**一键全流程**：自动定位 Steam + 读缓存 + 找「下载」里最新的导出包 + 生成报告并直接用浏览器打开（原来只是打开记事本里的 `report.md`）。
- `report.py` 新增自包含离线 `out/report.html`：概览数字、本次数据说明、每个分组的完整游戏清单与「复制游戏名清单」按钮、待复核表。
- 新增 `prepare-cache.bat`，把本机缓存复制成下载目录里的 `steam-packageinfo.vdf`；`try-it.html` 的「从下载文件夹里自动找」会把它与导出包一起读到。移除会暴露安装路径的 `open-appcache.bat`。
- 文档写清 `packageinfo.vdf` 的语义：Steam 客户端缓存的**公共包元数据**（不是个人游戏库清单，不含来源／日期／账号），来源只来自许可页的 `Acquisition Method`。

## 0.4.0

修掉「重导多少次都不完整」的根因，并让已经导出的包也能直接出可用结果：

- **v2 的真实缺陷**：商店页的 `webapi_token` 并不能换回完整名称表，回退的 `appdetails` 被硬限制成 `ids.slice(0, 600)`，1218 款的库必然剩几百款无名；翻页遇到单页无新增就提前收工。所以页面显示「请重新导出一次」是误导——重导也不会变好。
- **v3 导出脚本**：批量探测 `appdetails` / `packagedetails`，支持批量时 20 个一批、不支持时逐个全量，**不再有 600 上限**；翻页连续两页空转才停；放宽 `packageid` 抓取；导出包新增 `diagnostics`（Web API 状态、名称请求数、翻页停止原因）与 `schema: steam-license-export/2`。
- **本机缓存精确对齐**：新增只读 `vdfcache.py`，解析 Steam 客户端 `appcache/packageinfo.vdf` 的「礼包包 → appid」。CLI `--auto` 与网页（选择 / 拖入 `packageinfo.vdf`）都能用，无需重新导出、无需联网、无需 API Key。
- **保守的顺序对齐**：许可页「新→旧」与 `rgOwnedPackages`「旧→新」严格反向对应；只在带真实 packageid 的锚点之间、行数与包数 1:1 的空档推定，结果标 `match_via=order` 且不覆盖直接证据，`--no-order-infer` 可关闭。
- **告警重写**：只有「单页 + 行数远少于库」才判为分页失败；几百款无名被正确解释为 DLC／下架／试玩项目，页面直接给 ✅ 结果并提示可选补强，不再要求无意义重导。网页新增可选的本机缓存补强入口。
- 真实 1218 款库验证：⑥ 未判定从 **837 → 147**，精确对齐许可行 0 → 807；与独立名称证据交叉验证 487 个新归组里仅 6 个冲突（约 1%）。

## 0.3.0

修好了真实大库上暴露的两个致命问题（导出包只有 97 行许可 / 1028 款无名称，几乎全部落进⑥组）：

- **许可页分页**：Steam 在 2026 年把 `account/licenses` 改成按时间戳分页（每页约 100 条，`a.license_paginator_next` + `continuationToken`）。v2 书签会自动顺着下一页链接抓完全部分页，带指纹去重、回绕检测、页间限速和进度显示。
- **名称与时长一次取回**：旧版逐个请求 `appdetails` 补 1200+ 个名称，被限流后只剩不到 200 个，且下架／DLC 应用本身返回 `success:false`。v2 读取商店页面 `#application_config` 的 Web API token，一次 `IPlayerService/GetOwnedGames(include_appinfo=1)` 拿回全部名称与 `playtime_forever`；失败时才回退到低并发限速的 `appdetails`。商店 CSP 的 `connect-src` 明确允许 `api.steampowered.com`，且该接口对商店来源返回 `Access-Control-Allow-Origin`。
- **数据体检**：`try-it.html` 与 CLI 都会检测「许可行数远少于游戏数」「无名称游戏占比过高」，直接提示重新导出，而不是把错数字当成结果。示例包补齐 `license_pages` / `license_total` / `name_source` / `steamid64` 字段。
- 导出包新增 `playtimes`，网页路径因此也能生成「吃灰区 / 真的玩过 / 付费没启动」交叉组；不含时长时仍明确标注「未生成」。

## 0.2.1

- `try-it.html` is now the primary zero-install path: drop the downloaded
  `steam-licenses-*.json` anywhere on the page and the report appears
  immediately — no Python, no terminal, no "Start" button.
- Reordered the wizard (bookmark → license page → drop file), added an explicit
  warning that the bookmark only works on the license page, and a big direct
  link to that page.
- Added "pick file", "read from Downloads folder" (File System Access API),
  "copy collection summary", plus `open-downloads.bat` / `make-report.bat`
  double-click helpers for Windows.
- The browser path deliberately omits playtime-based cross groups and marks
  them unavailable, since the license page exposes no playtime; CLI `--auto`
  still supplies them from the local Steam install.
- Bookmark completion message now points at the drop zone instead of a command.
- Verified the browser algorithm against the Python CLI on both the export
  bundle and the manual sample (all group appids identical); 24 unit tests.

## 0.2.0

- One-click export: `steam-export.user.js` runs inside the logged-in license
  page (as a userscript or a drag-to-bookmark link) and downloads a single
  JSON bundle with license rows, owned appids, package-to-appid links and game
  names. No cookies or credentials are read, nothing is sent to third parties.
- `--auto` read-only discovery of a local Steam install: registry / common
  paths, SteamID64, account id3, existing collections, local playtimes, and
  the newest export bundle in the Downloads folder.
- Newer `[key, meta]` cloud-storage collections are now parsed for statistics;
  writeback explicitly refuses this format (`writable: false`) instead of
  risking corruption of existing collections.
- Fixed the class-less saved-license-page fallback in both the Python parser
  and the browser demo.
- `try-it.html` is now a two-step wizard plus an offline demo; 24 unit tests.

## 0.1.0

- Parse Steam's license / product-key-activation page (HTML, CSV or TXT) and
  classify each row as store / retail / gift / free.
- Align license package names to appids from GetOwnedGames (exact, substring,
  fuzzy and bundle matching); report everything that cannot be matched.
- Produce five source collections plus optional cross groups (free-unplayed,
  free-played, paid-unplayed) as a dry-run JSON / CSV / Markdown plan.
- Optional family-sharing group via `--family-file`.
- Experimental, opt-in writeback with structure probing, double backup and
  atomic replace.
- Zero third-party dependencies; synthetic example data and unit tests.
- Offline browser demo (`try-it.html`) with the same rules, file upload and
  JSON/CSV export; no network calls, no Steam file access.
- Robust fallback when a saved license page has its CSS class names stripped.
