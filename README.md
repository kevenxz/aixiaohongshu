# 跨平台内容整理与小红书工作流

> 自托管的小红书内容工作流：多来源采集 → AI 翻译改写 → 人工审核 → MCP 发布。
> An AI-powered content pipeline for Xiaohongshu: collect from Reddit / X / YouTube / RSS / 微博 / Instagram, draft in Chinese with an LLM, review, then publish via MCP.

这是一个可自托管的 Python 服务，用于：

1. 定时从 Reddit、X、YouTube 获取候选内容；
2. 按 URL 与规范化内容哈希去重；
3. 按热度排序，并按 `TARGET_POSTS_PER_DAY` 控制每天入库数量；
4. 调用 OpenAI-compatible LLM 翻译、改写成中文草稿（调用失败时保留可编辑的原文占位稿）；
5. 在满足版权状态时抓取图片，并限制单文件大小；
6. 保存到 SQLite，提供草稿编辑、批准、忽略、Markdown 导出和发布；
7. 预留小红书发布适配器；
8. 提供一个作品级的 `Signal Atelier` 前端控制台：真实指标、今日配额、最近 12 小时脉冲、来源状态、草稿编辑抽屉、来源/日历/洞察/设置面板；桌面与手机 H5 自适应，手机端使用底部导航和全屏编辑抽屉；
9. 支持多账号运营：`config/accounts.yaml` 定义多个小红书账号，控制台顶部切换，数据、来源、配额、排期和发布通道按账号隔离；
10. 新增明星图集链路：微博 UID + Instagram RSSHub/第三方 feed，下载 3-9 张原图并走图集专用提示词。

默认开启 AI-only 模式：`AI_ONLY=true`。只有 AI 相关度达到 `MIN_AI_SCORE` 的内容才会入库和出现在 `/hot` 热榜，避免泛科技、设计或娱乐内容混入 AI 账号。明星账号在配置中关闭该过滤（`relevance.only: false`），以关注的微博/Instagram 账号本身作为筛选条件。

## 目录结构

```text
src/xhs_workflow/        采集、翻译、审核、发布与 Web 服务
web/                     单页控制台（原生 HTML/CSS/JS）
config/                  多账号配置，accounts.example.yaml 为模板
tests/                   pytest 测试
tools/xiaohongshu-mcp/   本地 MCP 启动脚本（二进制与 cookies 不入库）
data/                    运行时数据：数据库、图片、导出、日志（不入库）
docker-compose.yml       app + Caddy + 双 MCP 容器编排
```

## 重要限制

小红书官方开放平台当前并不保证普通应用拥有 `write_notes` 发布权限。因此默认发布器是 `manual`：它会自动生成可复制的 Markdown 草稿，不会绕过小红书登录或风控。你如果有自己的 MCP HTTP 网关，可将 `PUBLISHER=mcp`，并配置 `XHS_MCP_SERVER_URL` 与 `XHS_MCP_TOOL_NAME`。

图片默认只抓取 `rights_status=authorized` 或 `cc` 的来源。可以在环境变量中显式打开未核验图片抓取，但上线前应确认你拥有转载或改编权。`MEDIA_MAX_BYTES` 默认限制单张图片为 10 MB。

明星图片的版权与肖像权风险更高：明星账号默认 `media.rights_mode: editorial`，会下载图片但强制写入风险标记，默认关闭自动发布并建议可见范围先设为「仅自己可见」。本项目不提供去水印、换脸或规避平台查重能力，发布前必须人工确认授权范围。

发布接口只接受已批准的草稿：`draft → approved → published`。忽略（`rejected`）的内容不会出现在 `/hot` 队列，但保留在数据库中。

## 快速开始

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env
# 编辑 .env：LLM_API_KEY 可选；Reddit 可走 RSS，YouTube/X 凭证按需填写
python -m xhs_workflow run-once
uvicorn xhs_workflow.main:app --reload
```

启动后打开 `http://127.0.0.1:8000/` 使用控制台。服务默认只监听本机；如需局域网访问，将 `APP_HOST` 改为 `0.0.0.0`，并自行评估访问控制。

## 多账号运营

账号配置在 `config/accounts.yaml`，完整示例见 `config/accounts.example.yaml`。密钥不写入 YAML，只写 `*_env` 环境变量名，真实值仍放在 `.env`。

```yaml
accounts:
  - slug: ai-signals          # AI 账号；省略 sources/schedule/publish 时继续继承 .env
    name: AI 情报站
  - slug: star-gallery        # 明星图集账号
    name: 明星图集
    content_style: gallery
    target_posts_per_day: 9
    media:
      rights_mode: editorial  # 允许下载，但强制版权/肖像风险标记
      max_images: 9
      min_images: 3
    sources:
      weibo:
        uids:
          - uid: "1234567890"
            name: 明星A工作室
        cookie_env: WEIBO_COOKIE
      instagram:
        feeds:
          - name: 明星A
            url: https://rsshub.example.com/instagram/user/star_a
    publish:
      mode: mcp
      mcp_url: ${XHS_MCP_STAR_URL:-http://127.0.0.1:18061/mcp}
      token_env: XHS_MCP_TOKEN_STAR
      visibility: 仅自己可见
```

- 控制台右上角切换账号；来源筛选、指标、队列、发布和设置全部跟随账号。
- 每个账号有独立的来源、关键词、每日配额、采集/自动发布排期和发布通道。
- 数据库首次启动会从 v1 迁移到 v2，并自动生成 `workflow.db.bak-v1-<时间>` 备份；旧数据归属默认账号。
- 旧部署不创建 `config/accounts.yaml` 时仍按 `.env` 启动单账号，行为不变。
- 常用命令：

```powershell
python -m xhs_workflow check-sources --account all
python -m xhs_workflow run-once --account star-gallery
python -m xhs_workflow retranslate --account star-gallery --platform weibo
```

### 微博与 Instagram 来源

- **微博**：在 `sources.weibo.uids` 填明星本人/工作室/图站的 UID，`name` 仅用于展示。微博 H5 接口可能要求登录态；遇到 432/403 时，从浏览器 `m.weibo.cn` 复制 Cookie 到 `.env` 的 `WEIBO_COOKIE`，或改用 `sources.weibo.feeds` 的 RSSHub 兜底。图片下载会自动带微博 Referer。
- **Instagram**：使用显式 RSSHub/第三方 RSS 地址（`sources.instagram.feeds`），不硬编码易变的 RSSHub 路由。公共实例限速时建议自建 RSSHub。
- 明星图片会保留来源署名、写入「图片来自微博/Instagram，发布前确认授权与肖像使用范围」风险标记；不抹水印、不做规避处理。
- 图集草稿支持 1-9 张图，低于 `min_images` 会加风险标记，0 张图片的帖子不会入库。

## 发布队列与小红书 MCP

草稿批准后进入发布队列。控制台右侧的「发布全部」会调用批量接口，按当前账号的 `PUBLISHER` 逐篇发布，发布成功后才会标记为 `published`。

### 每个账号一个 MCP 实例

`xpzouying/xiaohongshu-mcp` 的登录态是单账号的，因此多账号需要多个实例、各自独立 cookies：

| 账号 | 本地端口 | cookies | token 环境变量 |
| --- | --- | --- | --- |
| AI 账号 | `127.0.0.1:18060` | `tools/xiaohongshu-mcp/cookies.json` | `XHS_MCP_AUTH_TOKEN` |
| 明星图集 | `127.0.0.1:18061` | `data/xhs-mcp-star/cookies.json` | `XHS_MCP_TOKEN_STAR` |

- 本地运行 `start-all.ps1` 会同时启动两个 MCP 实例和 Web 服务。
- Docker 使用 `xiaohongshu-mcp` 与 `xiaohongshu-mcp-star` 两个服务，分别挂载 `data/xhs-mcp` 和 `data/xhs-mcp-star`。
- 每个实例首次都需要单独通过 MCP Inspector（对应端口）扫码登录；扫码和凭证由你本人完成，项目不读取、不记录、不代管。

### 每日审核后自动发布

开启后，系统按 `AUTO_PUBLISH_CRON` 自动发布**当天已批准**的图文，最多 `AUTO_PUBLISH_LIMIT` 条；不会自动批准草稿，仍需人工审核。

```dotenv
AUTO_PUBLISH=true
AUTO_PUBLISH_CRON=30 9 * * *
AUTO_PUBLISH_LIMIT=3
XHS_MCP_VISIBILITY=公开可见
```

默认建议流程：08:00 自动采集 → 上午审核/编辑 → 09:30 自动发布当天已批准的内容。没有配图的草稿会自动生成标题卡封面。

时间可以在控制台「设置」中直接修改并立即生效，覆盖值保存在 `data/schedule.json`，重启后仍有效；也可以编辑 `.env` 的 `COLLECT_CRON` / `AUTO_PUBLISH_CRON` 作为初始默认值。

自动任务运行在 Web 服务进程内，因此 Web 服务、MCP 服务都需要常驻。可用 [start-all.ps1](start-all.ps1) 启动两者，或在 Windows Task Scheduler 中设置为登录时运行。

## Docker 部署

仓库包含 `Dockerfile` 和 [docker-compose.yml](docker-compose.yml)，包含 Web 服务、Caddy 和两个 `xpzouying/xiaohongshu-mcp` 容器（AI 账号 / 明星账号）：

```bash
# 1. 准备配置（XHS_MCP_AUTH_TOKEN 会在两个容器间共用）
cp .env.example .env
# 编辑 .env：填写平台 key、LLM，确认 PUBLISHER=mcp、AUTO_PUBLISH=true

# 2. 构建并启动
docker compose up -d --build

# 3. 查看日志
docker compose logs -f app xiaohongshu-mcp
```

- Web UI 默认只映射到 `127.0.0.1:8000`，远程访问建议放在带认证的 Caddy/Nginx 后面；不要直接暴露发布接口。
- MCP 端口默认只映射到 `127.0.0.1:18060` 与 `127.0.0.1:18061`，用于首次扫码/Inspector 调试。
- 首次登录：用 MCP Inspector 分别连接 `http://127.0.0.1:18060/mcp`（AI 账号）和 `http://127.0.0.1:18061/mcp`（明星账号），带上各自 token，调用 `get_login_qrcode` 扫码；也可以把各自的 `cookies.json` 上传到服务器对应的 `data/xhs-mcp` / `data/xhs-mcp-star` 后重启 MCP 容器。
- 数据目录 `./data` 持久化数据库、图片、导出、定时设置和 MCP cookies；容器使用 `restart: unless-stopped`。
- Docker 内发布图片时，app 会通过 `http://app:8000/media/...` 传给 MCP，避免跨容器路径不可见；对应 `XHS_MEDIA_BASE_URL` 已在 compose 中设置。
- 服务器访问 Reddit/YouTube 需要代理时，在 `.env` 设置 `HTTPS_PROXY`；MCP 访问小红书需要代理则在 compose 的 MCP `environment` 中设置 `XHS_PROXY`。

- `PUBLISHER=manual`：导出 Markdown，不连接小红书。
- `PUBLISHER=mcp`：通过 `XHS_MCP_SERVER_URL` 的 JSON-RPC `tools/call` 调用 `XHS_MCP_TOOL_NAME`，载荷包含 `title`、`content`、`tags` 和 `images`（绝对路径列表）。
- 已验证 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 的接口：默认服务地址 `http://localhost:18060/mcp`，发布工具为 `publish_content`，图片支持本地绝对路径；如果服务端设置了 `AUTH_TOKEN`，把同一个值填到 `XHS_MCP_AUTH_TOKEN`。
- 本项目**不内置小红书 MCP 服务**，也没有自动安装第三方二进制。Qoder 当前没有已连接的小红书 MCP；必须由你部署并完成扫码登录后才能实际发布。
- 如果 MCP 服务运行在另一台机器，本地 `media_path` 不可直接访问，需要在网关上先上传图片或把 `images` 改为可访问 URL。
- 小红书图文至少需要一张图。发布时如果草稿没有源图片，且发布器是 MCP，系统会自动生成一张 1080×1440 的标题卡封面（`src/xhs_workflow/cover.py`），不会因缺图失败；有源图片时优先使用源图。
- 自动封面只使用标题、来源和正文摘要，不复制来源图片；仍建议发布前人工检查排版和内容。

## 控制台

- 指标卡展示今日目标、今日已捕捉、待审核、可发布，数据来自 `/stats`；
- 队列卡片显示来源、热度、AI 相关度、本地图片预览；主信号列表每页 10 条，支持上一页/下一页，筛选、搜索、排序后自动回到第一页；
- 点击任意卡片打开编辑抽屉，可修改标题、正文、标签，或直接批准、忽略、发布、下载 Markdown；
- 发布站「查看队列」会打开待发布列表，每页 6 条，可逐条核对、快速发布或批量发布；发布成功后自动移出列表；
- 左栏「来源」「日历」「洞察」「设置」会展示真实的配置状态、下次运行时间、来源分布与标签云；
- 未配置来源或 LLM 时会出现配置提示；
- 发布按钮只在草稿已批准后出现，发布前会二次确认。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查、各账号摘要 |
| GET | `/accounts` | 账号列表、今日指标、来源/发布/排期状态 |
| GET | `/status?account=` | 不暴露密钥的账号配置摘要 |
| PATCH | `/settings/schedule?account=` | 调整当前账号的采集/自动发布时间和发布上限 |
| GET | `/stats?account=` | 当前账号今日配额、状态计数、来源分布、12 小时脉冲、最近一次运行 |
| GET | `/posts?account=&status=draft` | 当前账号文章列表，可用 `limit` 限制 |
| GET | `/hot?account=&limit=30` | 当前账号活跃热榜（不含已忽略） |
| GET | `/posts/{id}` | 文章详情（自动按文章所属账号） |
| PATCH | `/posts/{id}` | 修改 `title` / `body` / `tags` |
| POST | `/posts/{id}/approve` | 批准草稿 |
| POST | `/posts/{id}/reject` | 忽略草稿 |
| POST | `/posts/{id}/publish` | 通过文章所属账号的通道发布已批准的草稿 |
| POST | `/queue/publish?account=` | 批量发布当前账号已批准草稿 |
| GET | `/posts/{id}/markdown` | 下载 Markdown 草稿 |
| GET | `/media/{filename}` | 查看已下载的本地图片 |
| POST | `/run?account=` | 手动触发当前账号采集；进行中会返回 `skipped` |

所有账号相关接口省略 `account` 时使用第一个启用账号。单篇 `/posts/{id}` 会按文章所属账号自动路由。

## 采集流程

`fetch → URL/内容哈希去重 → AI 过滤 → 热度排序 → 每日配额 → LLM 翻译改写 → 图片处理 → draft`。

- LLM 提示词要求**完整翻译改写**，禁止“去看原视频/原帖/阅读原文”这类导流句；信息不足时明确标注，不用导流代替正文。
- YouTube 通过官方 `videos` 接口取完整标题和简介并翻译。视频字幕接口在未登录/服务器 IP 下会被 YouTube 拦截，当前草稿会标记“未获取完整字幕”；如果需要逐字全文，需要额外提供字幕/转写来源。
- Reddit 使用公开 RSS 中的帖子正文翻译，不再引导读者回到原帖。
- 源页面有图片时会下载作为配图，并在风险标记中提示确认转载版权；`ALLOW_UNVERIFIED_REPOST=true` 只建议在确认授权后使用。

- 所有来源先抓取候选，再统一排序选稿，避免第一个平台吃满全部配额；
- 若昨日/今日已有入库内容，会先扣除当日已用配额；
- 单条 LLM 失败不会中断整轮采集，会写入带风险标记的占位稿；
- 每轮运行写入 `runs` 表，控制台可看到最近一次运行的抓取数、新增数、错误和配额状态；
- 采集使用进程内锁，手动触发与定时任务不会重叠。

## 接入三个来源

- **Reddit**：优先用 OAuth API：在 <https://www.reddit.com/prefs/apps> 创建 `script` 应用，把 Client ID / Secret 写入 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`。`REDDIT_USER_AGENT` 建议写成 `xhs-workflow/0.2 by u/你的机器人账号`。Reddit 新版注册流程可能要求一个独立的 **bot 账号**：登录状态保持为你的真人类主账号，但表单里的用户名/密码要填另一个专用 bot 账号，不能填主账号。bot 账号密码只保存在密码管理器，不写入 `.env`；本项目只使用应用的 Client ID / Secret。没有 API 凭证时，`REDDIT_MODE=auto` 会自动改用公开 RSS（也可强制 `rss`），详见下文。Reddit 目前要求先阅读并同意 [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)；禁止将 Reddit 数据用于模型训练、转售或未经批准的商业用途。若你的小红书账号用于商业变现，请先获得 Reddit 的书面许可。
- **X**：在 <https://developer.x.com> 创建应用并生成 Bearer Token，写入 `X_BEARER_TOKEN`。recent search 需要 Basic 及以上付费套餐；具体流配置见下面「X 的重点关注流」。
- **YouTube**：在 Google Cloud Console 创建项目，启用 **YouTube Data API v3**，创建 API Key 并写入 `YOUTUBE_API_KEY`。默认按最近 7 天、4 个 AI 查询词抓取，免费配额足够日常使用。
- **LLM**：任意 OpenAI-compatible 服务均可，写入 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。未配置或调用失败时会跳过该条来源，不会再把英文原文写成草稿；下一次采集会重试。
- **网络**：如果服务器无法直接访问 Reddit / X / YouTube，在 `.env` 中启用 `HTTPS_PROXY` / `HTTP_PROXY`，`requests` 会自动使用；本地服务地址可放进 `NO_PROXY`。

单个来源未配置不会阻塞其他来源：采集会继续运行，`/status` 会显示对应来源为未配置，`/run` 的 `errors` 会包含失败原因。

凭证填好后，先跑一次只读检查（不写数据库、不调用 LLM，只打印各来源抓到的数量和错误）：

```powershell
python -m xhs_workflow check-sources
```

如果历史草稿仍是英文，可先预览再重译。命令默认处理所有平台，也可以用 `--platform` 只处理某一个来源；它会跳过已发布/已忽略的帖子，逐篇调用 LLM 并更新标题、正文、标签和风险标记，LLM 仍失败时保持原样并报告错误：

```powershell
python -m xhs_workflow retranslate --dry-run
python -m xhs_workflow retranslate --platform reddit
```

### X 的重点关注流

X 采集器现在会合并三条流，并按推文 ID 去重：

1. **重点账号**：`X_PRIORITY_ACCOUNTS=OpenAI,AnthropicAI,GoogleDeepMind`。代码会用 `from:` 搜索这些账号最近 7 天的推文，无需额外授权，复用 `X_BEARER_TOKEN`。
2. **List 时间线**：`X_LIST_ID=你的ListID`。在 X 上把重点账号放进一个**公开 List**，从 `https://x.com/i/lists/1234567890` 这样的 URL 里取 ID。应用级 Bearer 可以读取公开 List，适合账号较多时使用。
3. **AI 主题搜索**：`X_QUERIES` 保持现有逻辑，搜索全站 AI 相关推文。

重点流的推文会获得 `X_PRIORITY_BOOST=4` 的热度加权，并在控制台显示「关注」标签。`X_PRIORITY_ALL=true`（默认）表示重点账号的内容即使没有命中 AI 关键词也会进入草稿；设为 `false` 则仍要通过 `MIN_AI_SCORE` 过滤。重点流默认最多占单次 X 候选的 60%，其余位置留给 AI 主题搜索；搜索不足时会用重点内容补满。

> 完整“所有关注人的 Home Timeline”需要 OAuth 1.0a 或 OAuth 2.0 用户授权，应用级 Bearer 无法读取。当前用「重点账号 + List」实现同样目标；如需全量 Home Timeline，需要再增加用户授权流程。

### 没有 Reddit API 权限时

Reddit 现在收紧了 API 申请，但公开 RSS 仍可作为低频、个人使用的兜底：

```dotenv
REDDIT_MODE=auto
```

- `auto`（默认）：有 Client ID/Secret 走 OAuth API，没有则自动走公开 RSS；
- `rss`：强制使用 RSS，即使有 API 凭证；
- `api`：强制使用 OAuth API，缺少凭证会报错。

Reddit RSS 仍然属于 Reddit 数据：请只做低频、个人、非商业读取，保留来源链接，不转售、不用于模型训练，并遵守 Reddit 政策。RSS 可能被限速或屏蔽；如果 Python 请求超时而浏览器能打开，请在 `.env` 配置可用的 `HTTPS_PROXY`。

还可以直接订阅官方 AI 博客，不需要任何 key：

```dotenv
RSS_FEEDS=OpenAI|https://openai.com/news/rss.xml,Google DeepMind|https://deepmind.google/blog/rss.xml
```

这些文章会以「官网」来源进入队列，和 Reddit/X/YouTube 一起参与 AI 过滤、热度排序和每日配额。OpenAI 和 DeepMind 的 RSS 已验证可直接读取；Anthropic 目前没有公开 RSS，可继续用官网文章手动导入。

## 配置

完整配置见 `.env.example`。常用项：

- `TARGET_POSTS_PER_DAY=30`：默认账号每日入库上限；
- `ACCOUNTS_FILE=config/accounts.yaml`：多账号配置文件；不存在时回退单账号；
- `COLLECT_CRON=0 8 * * *` 与 `APP_TIMEZONE=Asia/Shanghai`：定时采集与统计日界；
- `WEIBO_COOKIE`：微博 H5 接口 Cookie（仅明星账号使用，遇到 432 时更新）；
- `XHS_MCP_STAR_URL` / `XHS_MCP_TOKEN_STAR`：明星账号独立 MCP 地址与 token；
- `FETCH_PER_PROVIDER=20`：每个来源抓取候选数；
- `AI_ONLY` / `MIN_AI_SCORE`：AI 相关度过滤；
- `MEDIA_MAX_BYTES`：图片大小上限；
- `REDDIT_MODE=auto|api|rss`：Reddit API / 公开 RSS 切换；
- `RSS_FEEDS=名称|URL,...`：官方博客或其他 RSS 来源；
- `PUBLISHER=manual|webhook|mcp`：发布适配器；
- `AUTO_PUBLISH` / `AUTO_PUBLISH_CRON` / `AUTO_PUBLISH_LIMIT`：审核后自动发布；
- `XHS_MCP_VISIBILITY`：发布可见范围，测试阶段可用「仅自己可见」；
- `APP_USERNAME` / `APP_PASSWORD`：控制台和 API 的 Basic Auth，公网部署必须设置；
- `APP_MEDIA_TOKEN`：MCP 通过 URL 取图的专用 token，公网部署必须设置；
- `DOMAIN`：使用自带 Caddy 时的 HTTPS 域名。

## 公网访问与认证

应用启用了 HTTP Basic Auth：设置 `APP_PASSWORD` 后，除 `/health` 外的 UI、API、`/media` 都需要账号密码。`/media` 也可以凭 `APP_MEDIA_TOKEN` 访问，这是给 MCP 服务取图用的专用通道。

- Basic Auth 只能走 HTTPS。生产环境请使用自带的 Caddy compose 服务，或放在已有的 Nginx/Caddy 后面并启用 TLS。
- Compose 里 Web UI 只绑定 `127.0.0.1:8000`，公网入口是 Caddy 的 80/443；MCP 的 18060 也只绑定本机，不要直接暴露。
- Caddy 使用 `DOMAIN` 自动申请证书；先在 `.env` 设置域名，并确保 DNS 指向服务器、80/443 开放。
- 修改密码或 token 后重启 app 容器；账号密码和 token 只放在服务器 `.env`，不要提交仓库或发到聊天里。
- 如果用已有反向代理，保留应用自身的 Basic Auth，并确保代理传递 `Authorization` 头。

## 仓库安全与敏感数据

本仓库只包含源码与示例配置，以下内容不会进入 Git：

- `.env`：真实 LLM/YouTube/Reddit/X 凭证、Basic Auth 密码、MCP token、微博 Cookie；仓库只保留占位的 `.env.example`；
- `data/`：SQLite 数据库、下载的图片、导出、日志、MCP cookies、部署密钥；
- `tools/xiaohongshu-mcp/` 下的 `.exe` 二进制、`cookies.json*` 登录态与日志；
- `dist/` 打包产物与 `.idea/` 本地 IDE 配置。

首次部署请复制 `.env.example` 为 `.env` 后填写自己的凭证；扫码登录与 token 只保存在本机或服务器上，不要提交或发送到聊天中。

## 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖数据库去重与统计、每日配额、LLM 失败回退、草稿编辑/批准/忽略/发布、Markdown 导出、媒体文件服务与配置接口。

## 定时运行

服务启动时会按 `COLLECT_CRON` 启动后台任务，使用 `APP_TIMEZONE` 解释 cron。生产环境建议用 Docker 或 Windows Task Scheduler，并把 `DATABASE_PATH`、`MEDIA_DIR`、`EXPORT_DIR` 挂载到持久化磁盘。
