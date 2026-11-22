const PLATFORMS = {
  reddit: { label: "Reddit", mark: "R", env: ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"] },
  x: { label: "X", mark: "𝕏", env: ["X_BEARER_TOKEN"] },
  youtube: { label: "YouTube", mark: "▶", env: ["YOUTUBE_API_KEY"] },
  web: { label: "官网 RSS", mark: "✦", env: [] },
  weibo: { label: "微博", mark: "微", env: ["WEIBO_COOKIE"] },
  instagram: { label: "Instagram", mark: "IG", env: ["RSSHub / 第三方 feed"] },
};
const STYLE_LABELS = { article: "AI 情报", gallery: "明星图集" };
const STATUS_LABELS = { draft: "待审核", approved: "已批准", published: "已发布", rejected: "已忽略" };
const PUBLISHER_LABELS = { manual: "本地 Markdown", webhook: "Webhook", mcp: "MCP 网关" };
const FEED_PAGE_SIZE = 10;
const PUBLISH_PAGE_SIZE = 6;

const state = {
  posts: [],
  stats: null,
  status: null,
  accounts: [],
  accountSlug: localStorage.getItem("atelier-account") || "",
  filter: "all",
  query: "",
  sort: "heat",
  feedPage: 1,
  publishPage: 1,
  busy: false,
  acting: false,
  loaded: false,
  apiOk: true,
  activeId: null,
  sheet: null,
  emptyMode: "clear",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const pad2 = (value) => String(Number(value) || 0).padStart(2, "0");

function escapeHtml(value = "") {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}
function platformMeta(platform) {
  return PLATFORMS[platform] || { label: platform || "Signal", mark: "✦", env: [] };
}
function statusLabel(status) { return STATUS_LABELS[status] || status || "未知"; }
function publisherMode() { return state.status?.publisher?.mode || "manual"; }
function publisherLabel() { return PUBLISHER_LABELS[publisherMode()] || publisherMode(); }
function findPost(id) { return state.posts.find((post) => String(post.id) === String(id)); }
function currentAccount() {
  return state.accounts.find((account) => account.slug === state.accountSlug) || state.accounts[0] || null;
}
function accountQuery() {
  return state.accountSlug ? `account=${encodeURIComponent(state.accountSlug)}` : "";
}
function withAccount(url) {
  const query = accountQuery();
  if (!query) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${query}`;
}
function relevanceLabel() {
  return currentAccount()?.content_style === "gallery" ? "相关" : "AI";
}

function renderAccountSwitcher() {
  const account = currentAccount();
  if (!account) return;
  $("#accountAvatar").textContent = account.avatar || "S";
  $("#accountName").textContent = account.name || account.slug;
  $("#accountStyle").textContent = STYLE_LABELS[account.content_style] || account.content_style || "账号";
  $("#accountMenu").innerHTML = state.accounts.map((item) => `
    <button class="account-option ${item.slug === state.accountSlug ? "is-active" : ""}" data-account="${escapeHtml(item.slug)}" role="menuitem">
      <span class="account-option-avatar" style="--option-accent:${escapeHtml(item.accent || "#c9f967")}">${escapeHtml(item.avatar || (item.name || "S").slice(0, 1))}</span>
      <span class="account-option-copy"><strong>${escapeHtml(item.name || item.slug)}</strong><small>${escapeHtml(STYLE_LABELS[item.content_style] || item.content_style)} · 今日 ${Number(item.captured_today) || 0} 条</small></span>
    </button>`).join("");
}

function renderAccountChrome() {
  const account = currentAccount();
  if (!account) return;
  document.documentElement.style.setProperty("--account-accent", account.accent || "#c9f967");
  $("#heroEyebrow").innerHTML = `<span class="eyebrow-line"></span>${account.content_style === "gallery" ? "STAR GALLERY" : "CONTENT ORCHESTRATION"} / 2026`;
  $("#heroTitle").innerHTML = escapeHtml(account.tagline || account.name || "").replace(/\n/g, "<br />");
  $("#heroDescription").textContent = account.description || "";
  document.title = `${account.name || "Signal Atelier"} — 内容编排台`;
}

async function loadAccounts() {
  try {
    const accounts = await apiJson("/accounts");
    state.accounts = Array.isArray(accounts) ? accounts : [];
    const stored = localStorage.getItem("atelier-account");
    const active = state.accounts.find((account) => account.slug === stored) || state.accounts[0];
    state.accountSlug = active ? active.slug : "";
    if (state.accountSlug) localStorage.setItem("atelier-account", state.accountSlug);
  } catch (_) {
    state.accounts = [];
    state.accountSlug = "";
  }
  renderAccountSwitcher();
  renderAccountChrome();
}

function switchAccount(slug) {
  if (!slug) return;
  if (slug === state.accountSlug) {
    $("#accountMenu").hidden = true;
    $("#accountButton").setAttribute("aria-expanded", "false");
    return;
  }
  state.accountSlug = slug;
  localStorage.setItem("atelier-account", slug);
  state.filter = "all";
  state.feedPage = 1;
  state.publishPage = 1;
  $("#accountMenu").hidden = true;
  $("#accountButton").setAttribute("aria-expanded", "false");
  loadData();
}

function tzOptions() {
  const timezone = state.status?.app?.timezone;
  if (!timezone) return {};
  try {
    new Intl.DateTimeFormat("zh-CN", { timeZone: timezone });
    return { timeZone: timezone };
  } catch (_) {
    return {};
  }
}
function formatClock(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false, ...tzOptions() });
}
function formatRelative(iso) {
  if (!iso) return "刚刚";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "刚刚";
  const minutes = Math.max(1, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", ...tzOptions() });
}
function sameDay(a, b) {
  return a.toLocaleDateString("sv-SE", tzOptions()) === b.toLocaleDateString("sv-SE", tzOptions());
}
function formatNextRun(iso) {
  if (!iso) return "自动调度未启用";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "自动调度未启用";
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 86400000);
  if (sameDay(date, now)) return `今天 ${formatClock(iso)}`;
  if (sameDay(date, tomorrow)) return `明天 ${formatClock(iso)}`;
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, ...tzOptions() });
}
function formatUpdated(iso) {
  if (!iso) return "";
  return `${formatRelative(iso)} 更新`;
}
function cronToTime(expression) {
  const parts = String(expression || "").trim().split(/\s+/);
  if (parts.length !== 5 || parts[2] !== "*" || parts[3] !== "*" || parts[4] !== "*") return "";
  const minute = Number(parts[0]);
  const hour = Number(parts[1]);
  if (!Number.isInteger(minute) || !Number.isInteger(hour) || minute < 0 || minute > 59 || hour < 0 || hour > 23) return "";
  return `${pad2(hour)}:${pad2(minute)}`;
}

function deriveStats(posts) {
  const today = new Date().toDateString();
  const todayPosts = posts.filter((post) => post.created_at && new Date(post.created_at).toDateString() === today);
  const countStatus = (status) => posts.filter((post) => post.status === status).length;
  const byPlatform = {};
  const todayByPlatform = {};
  for (const post of posts) {
    byPlatform[post.platform] = (byPlatform[post.platform] || 0) + 1;
  }
  for (const post of todayPosts) {
    todayByPlatform[post.platform] = (todayByPlatform[post.platform] || 0) + 1;
  }
  const scores = todayPosts.map((post) => Number(post.ai_score) || 0);
  const hours = [];
  const now = new Date();
  for (let index = 11; index >= 0; index -= 1) {
    const bucket = new Date(now.getTime() - index * 3600000);
    const count = posts.filter((post) => {
      if (!post.created_at) return false;
      const created = new Date(post.created_at);
      return !Number.isNaN(created.getTime())
        && created.getFullYear() === bucket.getFullYear()
        && created.getMonth() === bucket.getMonth()
        && created.getDate() === bucket.getDate()
        && created.getHours() === bucket.getHours();
    }).length;
    hours.push({ hour: `${String(bucket.getHours()).padStart(2, "0")}:00`, count });
  }
  const peak = Object.entries(todayByPlatform).sort((a, b) => b[1] - a[1])[0];
  return {
    target: 30,
    captured_today: todayPosts.length,
    published_today: todayPosts.filter((post) => post.published_at && new Date(post.published_at).toDateString() === today).length,
    drafts: countStatus("draft"),
    approved: countStatus("approved"),
    published: countStatus("published"),
    rejected: countStatus("rejected"),
    total: posts.length,
    avg_ai_score: scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : 0,
    by_platform: byPlatform,
    today_by_platform: todayByPlatform,
    peak_platform: peak ? peak[0] : null,
    hourly: hours,
    last_run: null,
  };
}
function currentStats() { return state.stats || deriveStats(state.posts); }

function sortedPosts(posts) {
  const copy = [...posts];
  if (state.sort === "new") {
    copy.sort((a, b) => (new Date(b.created_at || 0) - new Date(a.created_at || 0)) || Number(b.id) - Number(a.id));
  } else {
    copy.sort((a, b) => (Number(b.heat_score) - Number(a.heat_score)) || Number(b.id) - Number(a.id));
  }
  return copy;
}
function visiblePosts() {
  const query = state.query.trim().toLowerCase();
  return sortedPosts(state.posts.filter((post) => {
    if (state.filter !== "all" && post.platform !== state.filter) return false;
    const haystack = `${post.title || ""} ${post.body || ""} ${post.author || ""} ${(post.tags || []).join(" ")}`.toLowerCase();
    return haystack.includes(query);
  }));
}

function updateMetrics() {
  const stats = currentStats();
  const target = Number(stats.target) || 0;
  const visible = visiblePosts();
  $("#targetMetric").innerHTML = `${target} <small>条内容</small>`;
  $("#capturedMetric").textContent = pad2(stats.captured_today);
  $("#reviewMetric").textContent = pad2(stats.drafts);
  $("#readyMetric").textContent = pad2(stats.approved);
  $("#signalCount").textContent = pad2(stats.captured_today);
  $("#quotaCount").textContent = `${stats.captured_today}/${target}`;
  $("#queueCount").textContent = pad2(visible.length);
  $("#stationNumber").textContent = pad2(stats.approved);
  $("#stationProgress").style.width = `${Math.min(100, (stats.approved / Math.max(1, target)) * 100)}%`;
  const publisherReady = state.status ? state.status.publisher.configured : false;
  $("#publisherState").textContent = !state.status ? "发布通道" : publisherReady ? (publisherMode() === "manual" ? "本地导出" : "已连接发布通道") : "通道未配置";
  $("#stationHint").textContent = publisherReady ? publisherLabel() : "发布通道待配置";
  renderFilterChips();
}

function renderPulse(stats) {
  let bars = Array.isArray(stats.hourly) && stats.hourly.length ? stats.hourly.slice(-12) : [];
  while (bars.length < 12) bars.unshift({ hour: "—", count: 0 });
  const max = Math.max(1, ...bars.map((bar) => Number(bar.count) || 0));
  $("#pulseChart").innerHTML = bars.map((bar) => {
    const count = Number(bar.count) || 0;
    const height = count ? Math.max(14, 16 + (count / max) * 82) : 8;
    return `<span class="${count === max && count > 0 ? "hot" : ""}" style="height:${height}%" title="${escapeHtml(bar.hour)} · ${count} 条"></span>`;
  }).join("");
  $("#pulseLabels").innerHTML = [bars[0]?.hour, bars[3]?.hour, bars[7]?.hour, "NOW"]
    .map((label) => `<span>${escapeHtml(label || "—")}</span>`).join("");
  $("#pulseScore").innerHTML = `${((Number(stats.avg_ai_score) || 0) * 10).toFixed(1)}<small>/10</small>`;
  const peak = stats.peak_platform ? platformMeta(stats.peak_platform).label : "暂无信号";
  $("#peakSource").textContent = peak;
  $("#pulseMeta").textContent = stats.captured_today ? `↗ ${stats.captured_today} 条` : "等待采集";
}

function availablePlatforms() {
  const declared = state.status?.account?.platforms || Object.keys(state.status?.providers || {});
  const fromPosts = state.posts.map((post) => post.platform);
  return [...new Set([...declared, ...fromPosts])].filter((key) => PLATFORMS[key]);
}

function renderFilterChips() {
  const platforms = availablePlatforms();
  if (state.filter !== "all" && !platforms.includes(state.filter)) state.filter = "all";
  const chips = [
    `<button class="filter-chip ${state.filter === "all" ? "is-active" : ""}" data-filter="all" role="tab">全部 <span>${pad2(state.posts.length)}</span></button>`,
  ];
  for (const key of platforms) {
    const meta = platformMeta(key);
    const count = state.posts.filter((post) => post.platform === key).length;
    const active = state.filter === key ? "is-active" : "";
    chips.push(`<button class="filter-chip ${active}" data-filter="${escapeHtml(key)}" role="tab"><span class="source-glyph ${escapeHtml(key)}-glyph">${escapeHtml(meta.mark)}</span> ${escapeHtml(meta.label)} <span>${pad2(count)}</span></button>`);
  }
  $("#filterRow").innerHTML = chips.join("");
}

function renderHealthRows(stats) {
  const providers = state.status?.providers || {};
  const keys = Object.keys(providers);
  $("#healthRows").innerHTML = keys.map((key) => {
    const meta = platformMeta(key);
    const configured = providers[key];
    const count = Number(stats.today_by_platform?.[key]) || 0;
    return `<div class="health-row ${configured === false ? "is-off" : ""}" data-health="${escapeHtml(key)}">
      <span class="health-source"><i class="health-dot dot-${escapeHtml(key)}"></i>${escapeHtml(meta.label)}</span>
      <span>${configured === true ? "已连接" : "未配置"}</span>
      <strong>${count} 条</strong>
    </div>`;
  }).join("") || `<p class="sheet-note">当前账号还没有配置采集来源。</p>`;
  $("#healthTime").textContent = stats.last_run ? formatClock(stats.last_run.finished_at) : "—";
}

function configuredSourceNames(status) {
  const names = [];
  for (const [key, ready] of Object.entries(status?.providers || {})) {
    if (!ready) continue;
    if (key === "reddit") names.push(status.reddit_stream?.mode === "rss" ? "Reddit RSS" : "Reddit");
    else if (key === "web") names.push("官网 RSS");
    else names.push(platformMeta(key).label);
  }
  return names;
}

function renderSetup() {
  const banner = $("#setupBanner");
  const status = state.status;
  if (!status) {
    banner.hidden = true;
    return;
  }
  const sources = configuredSourceNames(status);
  if (!sources.length) {
    banner.hidden = false;
    $("#setupText").textContent = "当前账号还没有连接任何内容来源。请在 config/accounts.yaml 中配置来源。";
    return;
  }
  if (!status.llm.configured) {
    banner.hidden = false;
    $("#setupText").textContent = `已启用：${sources.join("、")}。LLM 未配置，采集会跳过草稿生成。`;
    return;
  }
  if (!status.publisher.configured) {
    banner.hidden = false;
    $("#setupText").textContent = "发布通道未配置。编辑 .env 中的 PUBLISHER 配置后重启服务。";
    return;
  }
  banner.hidden = true;
}

function renderStats() {
  const stats = currentStats();
  const schedulerOn = Boolean(stats.scheduler_running ?? state.status?.app?.scheduler_running);
  $("#syncLabel").textContent = state.status ? (schedulerOn ? "自动采集中" : "手动模式") : (state.apiOk ? "连接中" : "服务离线");
  $("#syncState").classList.toggle("is-idle", !schedulerOn);
  $("#nextRunLabel").textContent = schedulerOn ? formatNextRun(stats.next_run_at) : "自动调度未启用";
  renderPulse(stats);
  renderHealthRows(stats);
  renderSetup();
  updateMetrics();
}

function feedCard(post, index) {
  const meta = platformMeta(post.platform);
  const status = post.status || "draft";
  const title = post.title || "未命名信号";
  const actions = [];
  if (status === "draft") {
    actions.push(`<button class="mini-action" data-action="approve" data-id="${escapeHtml(post.id)}" title="批准" aria-label="批准">✓</button>`);
    actions.push(`<button class="mini-action" data-action="reject" data-id="${escapeHtml(post.id)}" title="忽略" aria-label="忽略">×</button>`);
  } else if (status === "approved") {
    actions.push(`<button class="mini-action" data-action="publish" data-id="${escapeHtml(post.id)}" title="发布" aria-label="发布">↗</button>`);
    actions.push(`<button class="mini-action" data-action="reject" data-id="${escapeHtml(post.id)}" title="忽略" aria-label="忽略">×</button>`);
  }
  const media = post.media_file
    ? `<img src="/media/${encodeURIComponent(post.media_file)}" alt="" loading="lazy" />`
    : "";
  const mediaCount = Number(post.media_count) || 0;
  const mediaBadge = mediaCount > 1 ? `<span class="media-count">${mediaCount} 图</span>` : "";
  const aiScore = Math.round((Number(post.ai_score) || 0) * 100);
  return `
    <article class="feed-card" data-id="${escapeHtml(post.id)}" tabindex="0" aria-label="编辑：${escapeHtml(title)}" style="animation-delay:${Math.min(index * .055, .4)}s">
      <div class="feed-visual visual-${escapeHtml(post.platform)} ${post.media_file ? "has-media" : ""}">
        <span class="feed-visual-mark">${meta.mark}</span>${media}${mediaBadge}
      </div>
      <div class="feed-content">
        <div class="feed-meta">
          <span class="feed-source">${escapeHtml(meta.label)}</span><span class="feed-meta-dot"></span>
          <span>${escapeHtml(post.author || "open signal")}</span><span class="feed-meta-dot"></span>
          <span>${formatRelative(post.created_at)}</span>
          <span class="heat-badge"><i>↗</i> ${escapeHtml(post.heat_label || "signal")}</span>
          <span class="ai-badge">${relevanceLabel()} ${aiScore}%</span>
        </div>
        <h3 title="${escapeHtml(title)}">${escapeHtml(title)}</h3>
        <p class="feed-snippet">${escapeHtml(post.body || "等待模型整理…")}</p>
      </div>
      <div class="feed-actions">
        <span class="feed-status ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
        ${actions.join("")}
      </div>
    </article>`;
}

function updateEmptyState() {
  const hasFilters = state.filter !== "all" || state.query.trim();
  const stats = currentStats();
  let mode = "clear";
  let title = "还没有匹配的信号";
  let copy = "换一个关键词，或者清除筛选。";
  let action = "清除筛选";
  if (!state.apiOk) {
    mode = "retry";
    title = "无法连接工作流服务";
    copy = "请确认 FastAPI 服务正在运行，然后再试一次。";
    action = "重试连接";
  } else if (!state.posts.length) {
    mode = "setup";
    title = "信号池还是空的";
    copy = "配置来源与 LLM 后点击「现在采集」，第一批草稿会出现在这里。";
    action = "打开设置";
  } else if (!hasFilters && stats.total > 0) {
    mode = "refresh";
    title = "队列已清空";
    copy = "已忽略的内容不会出现在队列中，可以刷新或等待下一轮采集。";
    action = "刷新队列";
  }
  state.emptyMode = mode;
  $("#emptyTitle").textContent = title;
  $("#emptyCopy").textContent = copy;
  $("#emptyAction").textContent = action;
}

function renderFeedPagination(total, pages) {
  const bar = $("#feedPagination");
  $("#feedPageStatus").textContent = `第 ${state.feedPage} / ${pages} 页 · 共 ${total} 条`;
  $("#feedPrev").disabled = state.feedPage <= 1;
  $("#feedNext").disabled = state.feedPage >= pages;
  bar.hidden = !state.loaded || total <= FEED_PAGE_SIZE;
}

function goToFeedPage(page) {
  const pages = Math.max(1, Math.ceil(visiblePosts().length / FEED_PAGE_SIZE));
  state.feedPage = Math.min(Math.max(1, page), pages);
  renderFeed();
  document.querySelector(".queue-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderFeed() {
  const posts = visiblePosts();
  const pages = Math.max(1, Math.ceil(posts.length / FEED_PAGE_SIZE));
  state.feedPage = Math.min(Math.max(1, state.feedPage), pages);
  const start = (state.feedPage - 1) * FEED_PAGE_SIZE;
  $("#feedList").innerHTML = posts.slice(start, start + FEED_PAGE_SIZE).map(feedCard).join("");
  renderFeedPagination(posts.length, pages);
  $("#emptyState").hidden = posts.length > 0 || !state.loaded;
  if (!posts.length && state.loaded) updateEmptyState();
  $$(".feed-visual img").forEach((image) => {
    image.addEventListener("error", () => {
      const visual = image.closest(".feed-visual");
      image.remove();
      if (visual) visual.classList.remove("has-media");
    }, { once: true });
  });
  updateMetrics();
}

function renderAll() {
  renderStats();
  renderFeed();
  if (!$("#infoSheet").hidden) renderSheet();
}

function toast(message) {
  $("#toastText").textContent = message;
  $("#toast").classList.add("is-visible");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => $("#toast").classList.remove("is-visible"), 2800);
}

async function apiJson(url, options = {}) {
  const headers = { Accept: "application/json" };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  let payload = null;
  const text = await response.text();
  if (text) {
    try { payload = JSON.parse(text); } catch (_) { payload = { detail: text }; }
  }
  if (!response.ok) {
    const detail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg || String(item)).join("; ")
      : payload?.detail || `请求失败 (${response.status})`;
    throw new Error(detail);
  }
  return payload;
}

async function loadData({ notify = false } = {}) {
  await loadAccounts();
  const [postsResult, statsResult, statusResult] = await Promise.allSettled([
    apiJson(withAccount("/hot?limit=100")),
    apiJson(withAccount("/stats")),
    apiJson(withAccount("/status")),
  ]);
  state.apiOk = postsResult.status === "fulfilled" && Array.isArray(postsResult.value);
  state.posts = state.apiOk ? postsResult.value : [];
  state.stats = statsResult.status === "fulfilled" ? statsResult.value : null;
  state.status = statusResult.status === "fulfilled" ? statusResult.value : null;
  state.loaded = true;
  renderAll();
  if (notify) toast(state.apiOk ? "队列已刷新" : "无法连接工作流服务");
}

async function runCollection() {
  if (state.busy) return;
  state.busy = true;
  const button = $("#runButton");
  const label = button.querySelector("span:nth-child(2)");
  button.classList.add("is-loading");
  label.textContent = "采集中…";
  try {
    const result = await apiJson(withAccount("/run"), { method: "POST" });
    if (result?.skipped) {
      toast("已有一轮采集正在进行");
    } else if (result?.created > 0) {
      toast(`采集完成 · 新增 ${result.created} 条`);
    } else if (result?.quota_reached) {
      toast("今日配额已完成，明天再采");
    } else if (result?.errors?.length) {
      toast(`采集未完成：${String(result.errors[0]).slice(0, 70)}`);
    } else {
      toast("采集完成 · 暂无新的内容");
    }
    await loadData();
  } catch (error) {
    toast(error.message || "采集通道暂不可用");
  } finally {
    state.busy = false;
    button.classList.remove("is-loading");
    label.textContent = "现在采集";
  }
}

async function publishQueue() {
  if (state.acting) return;
  const count = Number(state.stats?.approved) || 0;
  if (!count) {
    toast("没有已批准的草稿，先批准再加入发布队列");
    return;
  }
  if (state.status && !state.status.publisher.configured) {
    toast("发布通道未配置");
    openSheet("settings");
    return;
  }
  const mode = publisherMode();
  const label = publisherLabel();
  const accountName = currentAccount()?.name || "当前账号";
  const message = mode === "manual"
    ? `导出「${accountName}」的 ${count} 条已批准草稿？`
    : `通过「${label}」为「${accountName}」发布 ${count} 条图文内容？`;
  if (!window.confirm(message)) return;
  state.acting = true;
  try {
    const result = await apiJson(withAccount("/queue/publish"), { method: "POST" });
    if (result?.skipped) {
      toast("已有发布任务正在进行，请稍后再试");
      return;
    }
    const published = Number(result?.published) || 0;
    const failed = Number(result?.failed) || 0;
    if (failed) {
      const firstError = result?.results?.find((item) => !item.ok)?.error || "";
      toast(`成功 ${published} 条，失败 ${failed} 条${firstError ? `：${String(firstError).slice(0, 60)}` : ""}`);
    } else {
      toast(mode === "manual" ? `已导出 ${published} 篇 Markdown` : `已发布 ${published} 条图文内容`);
    }
    await loadData();
  } catch (error) {
    toast(error.message || "发布失败，请检查 MCP 通道");
  } finally {
    state.acting = false;
  }
}

async function performAction(id, action) {
  if (state.acting) return;
  const post = findPost(id);
  if (!post) return;
  const mode = publisherMode();
  if (action === "publish" && !window.confirm(mode === "manual"
    ? "导出这条草稿的 Markdown，并标记为已发布？"
    : `通过「${publisherLabel()}」通道发布这条草稿？`)) return;
  if (action === "reject" && !window.confirm("忽略后该内容会从今日队列移除，确认？")) return;

  state.acting = true;
  try {
    const result = await apiJson(`/posts/${id}/${action}`, { method: "POST" });
    if (action === "approve") toast("已加入发布队列");
    if (action === "reject") toast("已忽略这条内容");
    if (action === "publish") {
      toast(result?.result?.mode === "manual" ? "已导出 Markdown 草稿" : "已交给发布通道");
    }
    if (action === "publish" || action === "reject") closeEditor();
    await loadData();
    if (action === "approve" && state.activeId === String(id)) {
      const current = findPost(id);
      if (current) fillEditor(current);
    }
  } catch (error) {
    toast(error.message || "操作失败，请检查服务状态");
  } finally {
    state.acting = false;
  }
}

function parseTags(value) {
  return [...new Set(String(value).split(/[,，、]+/).map((tag) => tag.trim().replace(/^#+/, "")).filter(Boolean))].slice(0, 10);
}

async function saveEditor() {
  if (state.acting || !state.activeId) return;
  const title = $("#editorTitle").value.trim();
  const body = $("#editorBody").value.trim();
  if (!title || !body) {
    toast("标题和正文不能为空");
    return;
  }
  const tags = parseTags($("#editorTags").value);
  state.acting = true;
  try {
    const result = await apiJson(`/posts/${state.activeId}`, {
      method: "PATCH",
      body: JSON.stringify({ title, body, tags }),
    });
    const updated = result.post;
    const index = state.posts.findIndex((post) => String(post.id) === String(updated.id));
    if (index >= 0) state.posts[index] = updated;
    fillEditor(updated);
    renderFeed();
    toast("修改已保存");
  } catch (error) {
    toast(error.message || "保存失败");
  } finally {
    state.acting = false;
  }
}

function fillEditor(post) {
  const meta = platformMeta(post.platform);
  const status = post.status || "draft";
  const locked = status === "published";
  $("#editorPlatform").textContent = `${meta.label.toUpperCase()} / DRAFT`;
  $("#editorHeading").textContent = locked ? "已发布草稿" : "编辑草稿";
  $("#editorMeta").innerHTML = `
    <span>${escapeHtml(post.author || "open signal")}</span>
    <span class="feed-meta-dot"></span><span>${formatRelative(post.created_at)}</span>
    <span class="feed-meta-dot"></span><span>${relevanceLabel()} ${Math.round((Number(post.ai_score) || 0) * 100)}%</span>
    <span class="feed-meta-dot"></span><span>${escapeHtml(post.heat_label || "signal")}</span>`;
  $("#editorStatus").textContent = statusLabel(status);
  $("#editorStatus").className = `feed-status ${status}`;
  $("#editorUpdated").textContent = formatUpdated(post.updated_at);
  $("#editorTitle").value = post.title || "";
  $("#editorBody").value = post.body || "";
  $("#editorTags").value = (post.tags || []).map((tag) => `#${tag}`).join(", ");
  $("#editorTitle").disabled = locked;
  $("#editorBody").disabled = locked;
  $("#editorTags").disabled = locked;

  const risks = post.risk_flags || [];
  $("#editorRisks").innerHTML = risks.length
    ? risks.map((risk) => `<span class="risk-chip">${escapeHtml(risk)}</span>`).join("")
    : `<span class="risk-none">未标记风险</span>`;

  const media = $("#editorMedia");
  const gallery = $("#editorGallery");
  const mediaFiles = post.media_files || (post.media_file ? [post.media_file] : []);
  if (mediaFiles.length) {
    gallery.innerHTML = mediaFiles.map((file, index) => `
      <a class="media-thumb" href="/media/${encodeURIComponent(file)}" target="_blank" rel="noreferrer" title="第 ${index + 1} 张">
        <img src="/media/${encodeURIComponent(file)}" alt="" loading="lazy" />
        <span>${index + 1}</span>
      </a>`).join("");
    media.hidden = false;
  } else {
    gallery.innerHTML = "";
    media.hidden = true;
  }

  const source = $("#editorSource");
  if (post.source_url && /^https?:\/\//i.test(post.source_url)) {
    source.hidden = false;
    source.href = post.source_url;
  } else {
    source.hidden = true;
    source.removeAttribute("href");
  }

  $("#saveButton").hidden = locked;
  $("#approveButton").hidden = !["draft", "rejected"].includes(status);
  $("#publishButton").hidden = status !== "approved";
  $("#rejectButton").hidden = ["published", "rejected"].includes(status);
  $("#editorPrimaryActions").hidden = locked;
}

function openEditor(post) {
  if (!post) return;
  closeSheet({ resetRail: false });
  state.activeId = String(post.id);
  fillEditor(post);
  $("#editor").hidden = false;
  syncOverlays();
  updateRailActive("inbox");
  $("#editor").focus();
}

function closeEditor() {
  $("#editor").hidden = true;
  state.activeId = null;
  syncOverlays();
}

function xStreamsSummary(status) {
  const streams = status?.x_streams;
  if (!streams) return "";
  const bits = [];
  if (streams.list_configured) bits.push("List 时间线");
  if (streams.priority_accounts) bits.push(`${streams.priority_accounts} 个重点账号`);
  if (bits.length) bits.push(streams.priority_all ? "重点内容优先" : "重点内容走 AI 过滤");
  return bits.length ? bits.join(" · ") : "仅 AI 主题搜索";
}

function configItem(key, status) {
  const meta = platformMeta(key);
  const stats = currentStats();
  const ready = status?.providers?.[key];
  const today = Number(stats.today_by_platform?.[key]) || 0;
  const total = Number(stats.by_platform?.[key]) || 0;
  let extra = "";
  if (ready === true && key === "x") extra = xStreamsSummary(status);
  if (ready === true && key === "reddit") {
    extra = status?.reddit_stream?.mode === "rss" ? "公开 RSS · 无需 API Key" : "OAuth API";
  }
  if (ready === true && key === "web") extra = `${Number(status?.rss?.feeds) || 0} 个订阅源`;
  if (key === "weibo" && status?.social?.weibo) {
    const weibo = status.social.weibo;
    extra = `UID ${weibo.uids} 个 · Feed ${weibo.feeds} 个 · Cookie ${weibo.cookie_configured ? "已配置" : "未配置"}`;
  }
  if (key === "instagram" && status?.social?.instagram) {
    extra = `${status.social.instagram.feeds} 个 RSSHub / 第三方 feed`;
  }
  return `
    <div class="config-item ${ready === true ? "is-ok" : "is-off"}">
      <div class="config-item-head">
        <span class="health-source"><i class="health-dot dot-${escapeHtml(key)}"></i>${escapeHtml(meta.label)}</span>
        <span class="state-pill ${ready === true ? "ok" : "off"}">${ready === true ? "已连接" : ready === false ? "未配置" : "未知"}</span>
      </div>
      <p>${ready === true ? `今日捕捉 ${today} 条 · 累计 ${total} 条` : `需要 ${meta.env.join(" / ") || "账号配置"}`}</p>
      ${extra ? `<span class="config-extra">${escapeHtml(extra)}</span>` : ""}
    </div>`;
}

function socialNote(status) {
  const social = status?.social;
  if (!social) return "";
  const bits = [];
  if (social.weibo) {
    bits.push(`微博：UID ${social.weibo.uids} 个 · Feed ${social.weibo.feeds} 个 · Cookie ${social.weibo.cookie_configured ? "已配置" : "未配置（遇到 432 时需要补充 WEIBO_COOKIE）"}`);
  }
  if (social.instagram) {
    bits.push(`Instagram：${social.instagram.feeds} 个 RSSHub / 第三方 feed`);
  }
  return bits.length ? `<p class="sheet-note">${bits.map((item) => escapeHtml(item)).join("<br />")}</p>` : "";
}

function publishQueueRow(post) {
  const meta = platformMeta(post.platform);
  const title = post.title || "未命名信号";
  const mediaCount = Number(post.media_count) || 0;
  const thumb = post.media_file
    ? `<img src="/media/${encodeURIComponent(post.media_file)}" alt="" loading="lazy" />`
    : `<span>${meta.mark}</span>`;
  return `
    <div class="publish-item">
      <button class="publish-main" data-open-post="${escapeHtml(post.id)}" aria-label="编辑：${escapeHtml(title)}">
        <span class="publish-thumb visual-${escapeHtml(post.platform)}">${thumb}${mediaCount > 1 ? `<i class="thumb-count">${mediaCount}</i>` : ""}</span>
        <span class="publish-copy">
          <span class="publish-meta">${escapeHtml(meta.label)}<span class="feed-meta-dot"></span>${escapeHtml(post.author || "open signal")}<span class="feed-meta-dot"></span>${formatRelative(post.updated_at || post.created_at)}</span>
          <span class="publish-title">${escapeHtml(title)}</span>
          <span class="publish-snippet">${escapeHtml(post.body || "等待模型整理…")}</span>
        </span>
      </button>
      <span class="publish-actions">
        <span class="feed-status approved">已批准</span>
        <button class="mini-action" data-publish-post="${escapeHtml(post.id)}" title="发布" aria-label="发布 ${escapeHtml(title)}">↗</button>
      </span>
    </div>`;
}

function buildSheet(section) {
  const stats = currentStats();
  const status = state.status;
  const account = currentAccount();
  if (section === "sources") {
    const providerKeys = Object.keys(status?.providers || {});
    return `
      <p class="sheet-lede">当前账号「${escapeHtml(account?.name || "—")}」：每个来源独立采集，再统一按热度与账号相关度进入队列。</p>
      ${status ? `<div class="config-list">${providerKeys.map((key) => configItem(key, status)).join("")}</div>`
        : `<p class="sheet-note">服务离线，暂时无法读取来源配置。</p>`}
      ${socialNote(status)}
      <p class="sheet-note">账号与来源在 config/accounts.yaml 配置，凭证只保存在本机 .env；修改后重启服务生效。</p>`;
  }
  if (section === "settings") {
    const llm = status?.llm;
    const publisher = status?.publisher;
    const app = status?.app;
    const filters = status?.filters;
    const media = status?.media;
    const automation = status?.automation;
    const rows = [
      ["账号", account ? `${account.name} · ${STYLE_LABELS[account.content_style] || account.content_style}` : "未知"],
      ["LLM", llm?.configured ? llm.model || "已配置" : "未配置，将跳过草稿生成"],
      ["发布通道", publisher ? `${publisherLabel()} · ${publisher.configured ? "就绪" : "未配置"}` : "未知"],
      ["可见范围", publisher?.visibility || "未知"],
      ["每日目标", `${stats.target} 条`],
      ["自动采集", app ? `${app.collect_cron} · ${app.timezone}` : "未知"],
      ["下次采集", formatNextRun(stats.next_run_at)],
      ["自动发布", automation?.auto_publish ? `${automation.auto_publish_cron} · 最多 ${automation.auto_publish_limit} 条（仅当天已批准）` : "关闭"],
      ["下次自动发布", automation?.auto_publish ? formatNextRun(automation.auto_publish_next_run_at) : "未启用"],
      ["相关度过滤", filters ? (filters.ai_only ? `开启 · 阈值 ${filters.min_ai_score}` : "关闭（关注即入库）") : "未知"],
      ["图片策略", media ? `${media.rights_mode} · 最多 ${media.max_images} 张` : "未知"],
      ["媒体上限", media ? `${Math.max(1, Math.round(media.max_bytes / 1024 / 1024))} MB` : "未知"],
    ];
    const collectTime = cronToTime(app?.collect_cron);
    const autoTime = cronToTime(automation?.auto_publish_cron);
    return `
      <div class="sheet-grid">${rows.map(([label, value]) => `
        <div class="sheet-row"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
      <form class="schedule-form" id="scheduleForm">
        <p class="eyebrow compact sheet-subtitle"><span class="eyebrow-line"></span>DAILY SCHEDULE</p>
        <div class="schedule-grid">
          <label><span>每日采集</span><input type="time" name="collect_time" value="${collectTime}" required /></label>
          <label><span>自动发布</span><input type="time" name="auto_publish_time" value="${autoTime}" required /></label>
          <label><span>最多发布</span><input type="number" name="auto_publish_limit" min="1" max="10" value="${automation?.auto_publish_limit || 3}" required /></label>
        </div>
        <div class="schedule-foot"><span>按 ${escapeHtml(app?.timezone || "Asia/Shanghai")} 执行，修改后立即生效</span><button type="submit" class="ghost-button">保存时间</button></div>
      </form>
      <p class="sheet-note">排期覆盖值保存在 data/schedule.json，按账号隔离；其他配置在 config/accounts.yaml 与 .env 中调整。</p>`;
  }
  if (section === "publish") {
    const approved = sortedPosts(state.posts.filter((post) => post.status === "approved"));
    const publisher = status?.publisher;
    const automation = status?.automation;
    const pages = Math.max(1, Math.ceil(approved.length / PUBLISH_PAGE_SIZE));
    state.publishPage = Math.min(Math.max(1, state.publishPage), pages);
    const start = (state.publishPage - 1) * PUBLISH_PAGE_SIZE;
    const pageApproved = approved.slice(start, start + PUBLISH_PAGE_SIZE);
    const summary = [
      ["待发布", `${approved.length} 条`],
      ["发布通道", publisher ? `${publisherLabel()} · ${publisher.configured ? "就绪" : "未配置"}` : "未知"],
      ["可见范围", publisher?.visibility || "未知"],
      ["自动发布", automation?.auto_publish
        ? `${formatNextRun(automation.auto_publish_next_run_at)} · 最多 ${automation.auto_publish_limit} 条`
        : "已关闭"],
    ];
    return `
      <p class="sheet-lede">已批准的草稿会停在这里；发布成功后自动移出。点击任意一条可打开编辑抽屉，核对标题、正文和配图。</p>
      <div class="sheet-grid sheet-grid-4">${summary.map(([label, value]) => `
        <div class="sheet-row"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
      <div class="publish-toolbar">
        <span>READY TO PUBLISH · ${approved.length} 条</span>
        ${approved.length ? `<button class="ghost-button" data-sheet-action="publish-all">发布全部 ↗</button>` : ""}
      </div>
      ${approved.length
        ? `<div class="publish-list">${pageApproved.map(publishQueueRow).join("")}</div>
           ${pages > 1 ? `<div class="publish-pagination">
             <button class="page-button" data-publish-page="prev" type="button" ${state.publishPage <= 1 ? "disabled" : ""}>← 上一页</button>
             <span class="page-status">第 ${state.publishPage} / ${pages} 页 · 共 ${approved.length} 条</span>
             <button class="page-button" data-publish-page="next" type="button" ${state.publishPage >= pages ? "disabled" : ""}>下一页 →</button>
           </div>` : ""}
           <p class="sheet-note">「发布全部」会再次确认；单条发布成功后会从列表移除，失败的内容会保留。</p>`
        : `<div class="publish-empty"><span>◎</span><p>还没有待发布内容</p><small>先在今日信号里批准草稿，它会出现在这里。</small></div>`}`;
  }
  if (section === "calendar") {
    const scheduleStatus = status?.app;
    const posts = sortedPosts(state.posts).slice(0, 10);
    const list = posts.length ? posts.map((post) => `
      <li>
        <span class="calendar-time">${formatClock(post.created_at)}</span>
        <span class="feed-status ${escapeHtml(post.status)}">${escapeHtml(statusLabel(post.status))}</span>
        <button class="sheet-post-link" data-open-post="${escapeHtml(post.id)}">${escapeHtml(post.title || "未命名信号")}</button>
      </li>`).join("") : `<li class="calendar-empty">今天还没有内容，先采集一轮。</li>`;
    return `
      <div class="calendar-hero">
        <span>NEXT RUN</span>
        <strong>${escapeHtml(formatNextRun(stats.next_run_at))}</strong>
        <p>${scheduleStatus ? `${escapeHtml(scheduleStatus.collect_cron)} · ${escapeHtml(scheduleStatus.timezone)}` : "自动调度未启用"}</p>
      </div>
      <div class="sheet-grid sheet-grid-4">
        <div class="sheet-row"><span>今日捕捉</span><strong>${stats.captured_today}</strong></div>
        <div class="sheet-row"><span>待审核</span><strong>${stats.drafts}</strong></div>
        <div class="sheet-row"><span>已批准</span><strong>${stats.approved}</strong></div>
        <div class="sheet-row"><span>今日发布</span><strong>${stats.published_today}</strong></div>
      </div>
      <p class="eyebrow compact sheet-subtitle"><span class="eyebrow-line"></span>RECENT SIGNALS</p>
      <ul class="calendar-list">${list}</ul>`;
  }
  if (section === "insights") {
    const platforms = availablePlatforms();
    const maxPlatform = Math.max(1, ...platforms.map((key) => Number(stats.by_platform?.[key]) || 0));
    const tagCounts = {};
    for (const post of state.posts) {
      for (const tag of post.tags || []) tagCounts[tag] = (tagCounts[tag] || 0) + 1;
    }
    const topTags = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
    const publishRate = stats.total ? Math.round((stats.published / stats.total) * 100) : 0;
    return `
      <div class="sheet-grid sheet-grid-4">
        <div class="sheet-row"><span>平均相关度</span><strong>${((Number(stats.avg_ai_score) || 0) * 100).toFixed(0)}%</strong></div>
        <div class="sheet-row"><span>已发布占比</span><strong>${publishRate}%</strong></div>
        <div class="sheet-row"><span>累计捕捉</span><strong>${stats.total}</strong></div>
        <div class="sheet-row"><span>已忽略</span><strong>${stats.rejected}</strong></div>
      </div>
      <p class="eyebrow compact sheet-subtitle"><span class="eyebrow-line"></span>PLATFORM MIX</p>
      <div class="distribution">${platforms.map((key) => {
        const count = Number(stats.by_platform?.[key]) || 0;
        return `<div class="dist-row"><span>${platformMeta(key).label}</span><div class="dist-track"><i style="width:${(count / maxPlatform) * 100}%"></i></div><strong>${count}</strong></div>`;
      }).join("")}</div>
      <p class="eyebrow compact sheet-subtitle"><span class="eyebrow-line"></span>TAG CLOUD</p>
      <div class="tag-cloud">${topTags.length ? topTags.map(([tag, count]) => `<span>#${escapeHtml(tag)} <i>${count}</i></span>`).join("") : `<span class="risk-none">还没有标签</span>`}</div>`;
  }
  return "";
}

function renderSheet() {
  const titles = {
    sources: ["SOURCE STATUS", "来源与配置"],
    settings: ["WORKFLOW SETTINGS", "工作流设置"],
    publish: ["PUBLISH QUEUE", "待发布队列"],
    calendar: ["PUBLISH SCHEDULE", "发布日历"],
    insights: ["SIGNAL INSIGHTS", "内容洞察"],
  };
  const [kicker, title] = titles[state.sheet] || ["STUDIO", "面板"];
  $("#sheetKicker").innerHTML = `<span class="eyebrow-line"></span>${kicker}`;
  $("#sheetTitle").textContent = title;
  $("#sheetBody").innerHTML = buildSheet(state.sheet);
}

function updateRailActive(section) {
  $$(".rail-link").forEach((link) => link.classList.toggle("is-active", link.dataset.section === section));
}

function openSheet(section) {
  closeEditor();
  state.sheet = section;
  if (section === "publish") state.publishPage = 1;
  renderSheet();
  $("#sheetBody").scrollTop = 0;
  $("#infoSheet").hidden = false;
  syncOverlays();
  updateRailActive(section);
  $("#infoSheet").focus();
}

function closeSheet({ resetRail = true } = {}) {
  $("#infoSheet").hidden = true;
  state.sheet = null;
  syncOverlays();
  if (resetRail) updateRailActive("inbox");
}

function syncOverlays() {
  const editorHidden = $("#editor").hidden;
  const sheetHidden = $("#infoSheet").hidden;
  $("#scrim").hidden = editorHidden && sheetHidden;
  document.body.classList.toggle("is-locked", !editorHidden || !sheetHidden);
}

function clearFilters() {
  state.filter = "all";
  state.query = "";
  state.feedPage = 1;
  $("#searchInput").value = "";
  $$(".filter-chip").forEach((chip) => chip.classList.toggle("is-active", chip.dataset.filter === "all"));
  renderFeed();
}

$("#filterRow").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-filter]");
  if (!chip) return;
  state.filter = chip.dataset.filter;
  state.feedPage = 1;
  renderFeed();
});
$("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value;
  state.feedPage = 1;
  renderFeed();
});
$("#sortButton").addEventListener("click", () => {
  state.sort = state.sort === "heat" ? "new" : "heat";
  state.feedPage = 1;
  $("#sortLabel").textContent = state.sort === "heat" ? "热度" : "最新";
  renderFeed();
});
$("#runButton").addEventListener("click", runCollection);
$("#feedPrev").addEventListener("click", () => goToFeedPage(state.feedPage - 1));
$("#feedNext").addEventListener("click", () => goToFeedPage(state.feedPage + 1));
$("#refreshButton").addEventListener("click", () => loadData({ notify: true }));
$("#accountButton").addEventListener("click", (event) => {
  event.stopPropagation();
  const menu = $("#accountMenu");
  menu.hidden = !menu.hidden;
  $("#accountButton").setAttribute("aria-expanded", String(!menu.hidden));
  if (!menu.hidden) renderAccountSwitcher();
});
$("#accountMenu").addEventListener("click", (event) => {
  const option = event.target.closest("[data-account]");
  if (!option) return;
  switchAccount(option.dataset.account);
});
document.addEventListener("click", (event) => {
  if (!$("#accountSwitcher").contains(event.target)) {
    $("#accountMenu").hidden = true;
    $("#accountButton").setAttribute("aria-expanded", "false");
  }
});
$("#setupButton").addEventListener("click", () => openSheet("settings"));
$("#publishAllButton").addEventListener("click", publishQueue);
$("#publishQueueButton").addEventListener("click", () => openSheet("publish"));
$("#emptyAction").addEventListener("click", () => {
  if (state.emptyMode === "retry" || state.emptyMode === "refresh") loadData({ notify: true });
  else if (state.emptyMode === "setup") openSheet("settings");
  else clearFilters();
});
$("#closeEditor").addEventListener("click", closeEditor);
$("#closeSheet").addEventListener("click", () => closeSheet());
$("#scrim").addEventListener("click", () => { closeEditor(); closeSheet(); });
$("#saveButton").addEventListener("click", saveEditor);
$("#approveButton").addEventListener("click", () => { if (state.activeId) performAction(state.activeId, "approve"); });
$("#rejectButton").addEventListener("click", () => { if (state.activeId) performAction(state.activeId, "reject"); });
$("#publishButton").addEventListener("click", () => { if (state.activeId) performAction(state.activeId, "publish"); });
$("#downloadButton").addEventListener("click", () => {
  if (!state.activeId) return;
  window.location.href = `/posts/${state.activeId}/markdown`;
  toast("Markdown 已开始下载");
});

$("#feedList").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]");
  if (action) {
    event.stopPropagation();
    performAction(action.dataset.id, action.dataset.action);
    return;
  }
  const card = event.target.closest(".feed-card");
  if (card) openEditor(findPost(card.dataset.id));
});
$("#feedList").addEventListener("keydown", (event) => {
  if (!["Enter", " "].includes(event.key)) return;
  const card = event.target.closest(".feed-card");
  if (card) {
    event.preventDefault();
    openEditor(findPost(card.dataset.id));
  }
});
$("#sheetBody").addEventListener("click", (event) => {
  const pageButton = event.target.closest("[data-publish-page]");
  if (pageButton) {
    state.publishPage = Math.max(1, state.publishPage + (pageButton.dataset.publishPage === "next" ? 1 : -1));
    renderSheet();
    $("#sheetBody").scrollTop = 0;
    return;
  }
  const publish = event.target.closest("[data-publish-post]");
  if (publish) {
    performAction(publish.dataset.publishPost, "publish");
    return;
  }
  const action = event.target.closest("[data-sheet-action]");
  if (action?.dataset.sheetAction === "publish-all") {
    publishQueue();
    return;
  }
  const trigger = event.target.closest("[data-open-post]");
  if (!trigger) return;
  const post = findPost(trigger.dataset.openPost);
  if (post) openEditor(post);
});
$("#sheetBody").addEventListener("submit", async (event) => {
  if (event.target.id !== "scheduleForm") return;
  event.preventDefault();
  const form = new FormData(event.target);
  const collectTime = String(form.get("collect_time") || "");
  const autoTime = String(form.get("auto_publish_time") || "");
  if (!collectTime || !autoTime) {
    toast("请选择采集和自动发布时间");
    return;
  }
  try {
    await apiJson(withAccount("/settings/schedule"), {
      method: "PATCH",
      body: JSON.stringify({
        collect_time: collectTime,
        auto_publish_time: autoTime,
        auto_publish_limit: Number(form.get("auto_publish_limit")) || 3,
      }),
    });
    toast("每日时间已更新");
    await loadData();
  } catch (error) {
    toast(error.message || "时间更新失败");
  }
});
$$("[data-section]").forEach((trigger) => trigger.addEventListener("click", () => {
  const section = trigger.dataset.section;
  if (section === "inbox") {
    closeSheet();
    closeEditor();
    updateRailActive("inbox");
    document.querySelector(".workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  openSheet(section);
}));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("#accountMenu").hidden) {
    $("#accountMenu").hidden = true;
    $("#accountButton").setAttribute("aria-expanded", "false");
  } else if (!$("#editor").hidden) closeEditor();
  else if (!$("#infoSheet").hidden) closeSheet();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.activeId && $("#infoSheet").hidden) loadData();
});

$("#editor").setAttribute("tabindex", "-1");
$("#infoSheet").setAttribute("tabindex", "-1");
loadData();
