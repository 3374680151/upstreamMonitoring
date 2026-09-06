"""Billing-audit orchestration: pull, match, and judge request charges.

对应设计稿 ``docs/billing-audit/billing-audit-方案.md``。每轮：按主站增量
拉 ``/api/log/?type=2``（游标存 app_settings）→ 落 ``billing_request_checks``
→ 逐行经 ``channel_upstream_bindings`` 找上游站点 → 拉上游
``/api/log/self`` 按 模型+tokens+时间窗 匹配 → 官方价 / 上游公示倍率 /
上游实扣 / 主站实收 四方判定 → 红绿灰。全程只读远端，不写任何上游数据。

判定口径（与设计稿 3.3 一致）：
* 灰（unknown）优先于红——上游倍率证据缺失（ratio_field_missing）、无官方价
  （no_official_price）、绑不到上游（no_binding/no_token）、匹配不到上游日志
  （no_upstream_log）时整行归灰，绝不误报红；已判出的红因仍写进 reason_codes。
* 主站侧次要判定（main_billing_inconsistent / group_ratio_gap）缺字段时只
  跳过该判定，不影响整行颜色。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.core.normalize import normalize_base_url
from backend.core.state import BILLING_AUDIT_RUN_LOCK, STOP_EVENT
from backend.core.time import APP_TIMEZONE, app_now, parse_iso_dt, utc_now_iso
from backend.integrations.newapi import fetch_newapi_pricing_for_site
from backend.integrations.newapi_logs import (
    fetchNewapiAdminConsumeLogs,
    fetchNewapiSelfConsumeLogs,
)
from backend.repositories import billing_audit as billingRepo
from backend.repositories.admin_sites import list_admin_site_rows
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    get_site_or_404,
    list_channel_discovery_links,
    site_auth_ready,
    site_groups_from_row,
)
from backend.services.channel_match_service import list_channel_upstream_bindings
from backend.services.monitoring_service import (
    cache_newapi_pricing_payload,
    get_newapi_pricing_cache,
)

MAIN_LOG_PAGE_SIZE = 100
MAIN_LOG_MAX_PAGES = 10
UPSTREAM_LOG_PAGE_SIZE = 100
UPSTREAM_LOG_MAX_PAGES = 10
PENDING_LIMIT_PER_RUN = 300
MAX_LIST_PAGE_SIZE = 100
_EPS = 1e-12


# ---------------------------------------------------------------------------
# 判定纯函数
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    """other 字段里的数值可能是字符串，统一容错转 float。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _relDiff(actual: float, expected: float) -> float:
    if abs(expected) <= _EPS:
        return 0.0 if abs(actual) <= _EPS else float("inf")
    return abs(actual - expected) / abs(expected)


def _officialCost(priceRow: Dict[str, Any], tokens: Dict[str, Any]) -> Optional[float]:
    """官方成本；per_token 缺单价的分量按输入价兜底（缓存写入价缺省视为 0 档按输入价）。"""
    inputPrice = priceRow.get("input_usd_per_m")
    if str(priceRow.get("quota_type") or "per_token") == "per_call":
        callPrice = priceRow.get("price_per_call_usd")
        return float(callPrice) if isinstance(callPrice, (int, float)) else None
    if not isinstance(inputPrice, (int, float)):
        return None
    fallback = float(inputPrice)

    def component(field: str) -> float:
        value = priceRow.get(field)
        return float(value) if isinstance(value, (int, float)) else fallback

    cacheRead = float(tokens.get("cache_read_tokens") or 0)
    cacheWrite = float(tokens.get("cache_creation_tokens") or 0)
    cost = (
        float(tokens.get("prompt_tokens") or 0) * fallback
        + cacheRead * component("cached_input_usd_per_m")
        + cacheWrite * component("cache_write_usd_per_m")
        + float(tokens.get("completion_tokens") or 0) * component("output_usd_per_m")
    ) / 1_000_000.0
    return round(cost, 10)


def _parsePricing(payload: Any) -> Dict[str, Any]:
    """从 /api/pricing 返回体提取 模型倍率 / 补全倍率 / 分组倍率 / 按次价。"""
    body = payload
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body.get("data")
    models: Dict[str, Dict[str, float]] = {}
    groupRatios: Dict[str, float] = {}
    if not isinstance(body, dict):
        return {"models": models, "group_ratios": groupRatios}
    rawModels = body.get("data")
    if isinstance(rawModels, list):
        for item in rawModels:
            if not isinstance(item, dict):
                continue
            name = str(item.get("model_name") or item.get("name") or "").strip()
            if not name:
                continue
            entry: Dict[str, float] = {}
            for src in ("model_ratio", "completion_ratio", "model_price", "quota_type"):
                value = item.get(src)
                if isinstance(value, (int, float)):
                    entry[src] = float(value)
            models[name.casefold()] = entry
    rawGroups = body.get("group_ratio")
    if isinstance(rawGroups, dict):
        for name, value in rawGroups.items():
            if isinstance(value, (int, float)):
                groupRatios[str(name)] = float(value)
    elif isinstance(rawGroups, list):
        for item in rawGroups:
            if isinstance(item, dict) and item.get("name") and isinstance(item.get("ratio"), (int, float)):
                groupRatios[str(item["name"])] = float(item["ratio"])
    return {"models": models, "group_ratios": groupRatios}


def _expectedUpstreamUsd(
    row: Dict[str, Any],
    modelPub: Optional[Dict[str, float]],
    groupRatio: Optional[float],
    quotaPerUnit: float,
) -> Optional[float]:
    """按上游公示倍率快照反推这条请求「应该」扣多少美元；缺倍率返回 None。

    缓存命中按 cache_ratio（未知取 1）计入，缓存写入按普通输入计——两个
    口径只影响灰度兜底分支的金额对比，倍率证据优先时不用到它。
    """
    if not modelPub or "model_ratio" not in modelPub or groupRatio is None:
        return None
    if float(modelPub.get("quota_type") or 0) == 1:
        callPrice = modelPub.get("model_price")
        return round(float(callPrice) * groupRatio / quotaPerUnit, 10) if callPrice else None
    completionRatio = float(modelPub.get("completion_ratio") or 1.0)
    cacheRatio = float(row.get("_cache_ratio_hint") or 1.0)
    weighted = (
        float(row.get("prompt_tokens") or 0)
        + float(row.get("cache_creation_tokens") or 0)
        + float(row.get("cache_read_tokens") or 0) * cacheRatio
        + float(row.get("completion_tokens") or 0) * completionRatio
    )
    return round(weighted * float(modelPub["model_ratio"]) * groupRatio / quotaPerUnit, 10)


# ---------------------------------------------------------------------------
# 运行上下文（单个上游站点的站点行 / 公示价格 / 已拉日志窗口）
# ---------------------------------------------------------------------------

class _UpstreamContext:
    def __init__(self, base: str, site: Optional[Dict[str, Any]]) -> None:
        self.base = base
        self.site = site
        self.logs: Optional[List[Dict[str, Any]]] = None
        self.oldest: Optional[datetime] = None
        self.exhausted = False
        self.pagesFetched = 0
        self.pricing: Optional[Dict[str, Any]] = None
        self.pricingLoaded = False
        self.usedLogIds: set = set()


def _ensureUpstreamLogs(ctx: _UpstreamContext, neededFrom: datetime, windowSeconds: int) -> None:
    """保证已拉日志窗口覆盖 neededFrom - window（覆盖不到就尽力拉到页数上限）。

    上游 /api/log/self 按时间倒序返回，翻页直到覆盖目标时间或到页数上限；
    同一轮内多个请求共享已拉的日志，避免逐行重复请求上游。
    """
    target = neededFrom - timedelta(seconds=windowSeconds)
    while True:
        if ctx.logs is None:
            ctx.logs = []
        covered = ctx.oldest is not None and ctx.oldest <= target
        if covered or ctx.exhausted or ctx.pagesFetched >= UPSTREAM_LOG_MAX_PAGES:
            return
        if STOP_EVENT.is_set():
            return
        page = ctx.pagesFetched
        ctx.pagesFetched += 1
        ok, logs, _meta, error = fetchNewapiSelfConsumeLogs(
            ctx.site, page, UPSTREAM_LOG_PAGE_SIZE
        )
        if not ok:
            print(f"[计费核对] 上游日志拉取失败 base={ctx.base} page={page} err={error}", flush=True)
            ctx.exhausted = True
            return
        if not logs:
            ctx.exhausted = True
            return
        for log in logs:
            ctx.logs.append(log)
            created = log.get("created_iso")
            if created and (ctx.oldest is None or created < ctx.oldest):
                ctx.oldest = created


def _ensurePricing(ctx: _UpstreamContext) -> Optional[Dict[str, Any]]:
    """上游公示价格：优先复用监控模块的 pricing 缓存，未命中时直拉一次。"""
    if ctx.pricingLoaded:
        return ctx.pricing
    ctx.pricingLoaded = True
    payload: Any = None
    site = ctx.site or {}
    siteId = int(site.get("id") or 0)
    if siteId > 0:
        cached, _age = get_newapi_pricing_cache(siteId)
        if cached:
            payload = cached
    if payload is None and siteId > 0:
        ok, fetched, error = fetch_newapi_pricing_for_site(site)
        if ok:
            payload = fetched
            cache_newapi_pricing_payload(siteId, fetched)
        else:
            print(f"[计费核对] 上游公示价格拉取失败 base={ctx.base} err={error}", flush=True)
    if payload is not None:
        ctx.pricing = _parsePricing(payload)
    if (not ctx.pricing or not ctx.pricing["group_ratios"]) and siteId > 0:
        # /api/pricing 未带分组倍率时退回站点监控快照（/api/user/groups）。
        groups = site_groups_from_row(site)
        fallback = {
            name: float(info.get("ratio"))
            for name, info in (groups or {}).items()
            if isinstance(info, dict) and isinstance(info.get("ratio"), (int, float))
        }
        if fallback:
            ctx.pricing = ctx.pricing or {"models": {}, "group_ratios": {}}
            ctx.pricing["group_ratios"].update(fallback)
    return ctx.pricing


def _resolveGroupRatio(
    upLog: Dict[str, Any],
    binding: Optional[Dict[str, Any]],
    pricing: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[float]]:
    """定位这条请求走的上游分组：优先上游日志自记 user_group，其次渠道绑定分组。"""
    groupRatios = (pricing or {}).get("group_ratios") or {}
    candidates: List[str] = []
    userGroup = upLog.get("user_group")
    if isinstance(userGroup, str) and userGroup.strip():
        candidates.append(userGroup.strip())
    for entry in (binding or {}).get("matched_groups") or []:
        if isinstance(entry, dict):
            name = entry.get("group") or entry.get("name")
            if isinstance(name, str):
                candidates.append(name)
        elif isinstance(entry, str):
            candidates.append(entry)
    candidates.append("default")
    for name in candidates:
        if name in groupRatios:
            return name, float(groupRatios[name])
    if len(groupRatios) == 1:
        onlyName = next(iter(groupRatios))
        return onlyName, float(groupRatios[onlyName])
    return None, None


# ---------------------------------------------------------------------------
# 单行核对
# ---------------------------------------------------------------------------

def _compactJson(value: Any) -> Optional[str]:
    if not isinstance(value, dict) or not value:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _finalize(
    row: Dict[str, Any],
    patch: Dict[str, Any],
    matchStatus: str,
    status: str,
    reasons: List[str],
) -> None:
    patch["match_status"] = matchStatus
    patch["status"] = status
    patch["reason_codes_json"] = json.dumps(reasons, ensure_ascii=False) if reasons else None
    patch["checked_at"] = utc_now_iso()
    billingRepo.updateBillingCheckResult(int(row["id"]), patch)


def _buildChannelLinks(adminSiteId: int) -> Dict[str, Dict[str, Any]]:
    """每个主站渠道解析一次上游：site_discovery_links 优先，bindings 兜底。

    值结构：{"base": 上游 Base URL, "site_row": 上游监控站点行（可为 None）,
    "matched_groups": 渠道绑定分组（分组倍率兜底定位用）}。
    """
    links = list_channel_discovery_links(adminSiteId)
    bindings = list_channel_upstream_bindings(adminSiteId) or {}
    result: Dict[str, Dict[str, Any]] = {}
    for channelId in set(links.keys()) | set(bindings.keys()):
        link = links.get(channelId) or {}
        binding = bindings.get(channelId) or {}
        base = normalize_base_url(
            str(link.get("upstream_base_url") or binding.get("upstream_base_url") or "")
        )
        if not base:
            continue
        siteRow: Optional[Dict[str, Any]] = None
        siteId = link.get("site_id")
        if siteId:
            siteRow = get_site_or_404(int(siteId))[0]
        if siteRow is None:
            siteRow = find_monitor_site_for_channel(base)
        result[str(channelId)] = {
            "base": base,
            "site_row": siteRow,
            "matched_groups": binding.get("matched_groups") or [],
        }
    return result


def _auditRow(
    row: Dict[str, Any],
    channelLinks: Dict[str, Dict[str, Any]],
    ctxByBase: Dict[str, _UpstreamContext],
    settings: Dict[str, Any],
    priceCache: Dict[str, Optional[Dict[str, Any]]],
) -> None:
    tol = float(settings["tolerance_percent"]) / 100.0
    quotaPerUnit = float(settings["quota_per_unit"] or 500000)
    windowSeconds = int(settings["match_window_seconds"] or 300)
    patch: Dict[str, Any] = {}
    red: List[str] = []
    gray: Optional[str] = None

    mainQuota = float(row.get("main_quota") or 0)
    mainUsd = mainQuota / quotaPerUnit
    patch["main_usd"] = round(mainUsd, 10)

    link = channelLinks.get(str(row.get("channel_id"))) if row.get("channel_id") is not None else None
    if not link:
        _finalize(row, patch, "no_binding", "unknown", ["no_binding"])
        return
    base = str(link["base"])
    ctx = ctxByBase.get(base)
    if ctx is None:
        ctx = _UpstreamContext(base, link.get("site_row"))
        # 跨轮去重：历史行已占用的上游日志 id 一并排除，保证一条上游日志
        # 只配一条主站记录（设计稿第 7 节）。
        ctx.usedLogIds.update(billingRepo.listUsedUpstreamLogIds(int(row["admin_site_id"])))
        ctxByBase[base] = ctx
    if ctx.site is None or not site_auth_ready(ctx.site):
        _finalize(row, patch, "no_token", "unknown", ["no_token"])
        return
    if str(ctx.site.get("platform") or "newapi") != "newapi":
        # P1 只支持 NewAPI 上游的日志核对（设计稿第 12 节分期口径）。
        _finalize(row, patch, "no_log_api", "unknown", ["no_log_api"])
        return
    # 审计期回填站点归属：老行插入时可能还没有发现来源映射。
    patch["upstream_site_id"] = int(ctx.site.get("id") or 0) or None
    patch["upstream_site_name"] = str(ctx.site.get("name") or "") or None

    requestAt = parse_iso_dt(str(row.get("request_at") or ""))
    if requestAt is None:
        _finalize(row, patch, "unmatched", "unknown", ["no_upstream_log"])
        return
    _ensureUpstreamLogs(ctx, requestAt, windowSeconds)
    if STOP_EVENT.is_set():
        # 停机时不把「没拉完日志」定格成 unmatched，留待下一轮重判。
        return
    logs = ctx.logs or []
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for log in logs:
        logId = log.get("id")
        if logId is None or logId in ctx.usedLogIds:
            continue
        if str(log.get("model_name") or "") != str(row.get("model_name") or ""):
            continue
        if int(log.get("prompt_tokens") or 0) != int(row.get("prompt_tokens") or 0):
            continue
        if int(log.get("completion_tokens") or 0) != int(row.get("completion_tokens") or 0):
            continue
        created = log.get("created_iso")
        if created is None:
            continue
        delta = abs((created - requestAt).total_seconds())
        if delta > windowSeconds:
            continue
        mainCacheRead = row.get("cache_read_tokens")
        upCacheRead = log.get("cache_read_tokens")
        if mainCacheRead is not None and upCacheRead is not None:
            if int(mainCacheRead) != int(upCacheRead):
                continue
        if best is None or delta < best[0]:
            best = (delta, log)
    if best is None:
        _finalize(row, patch, "unmatched", "unknown", ["no_upstream_log"])
        return

    upLog = best[1]
    ctx.usedLogIds.add(upLog.get("id"))
    upQuota = float(upLog.get("quota") or 0)
    upstreamUsd = upQuota / quotaPerUnit
    patch["upstream_log_id"] = upLog.get("id")
    patch["upstream_actual_usd"] = round(upstreamUsd, 10)
    patch["upstream_model_ratio"] = upLog.get("model_ratio")
    patch["upstream_group_ratio"] = upLog.get("group_ratio")
    patch["upstream_completion_ratio"] = upLog.get("completion_ratio")
    row["_cache_ratio_hint"] = upLog.get("cache_ratio")

    pricing = _ensurePricing(ctx)
    modelName = str(row.get("model_name") or "")
    modelPub = ((pricing or {}).get("models") or {}).get(modelName.casefold())
    groupName, groupRatio = _resolveGroupRatio(upLog, link, pricing)
    patch["published_model_ratio"] = (modelPub or {}).get("model_ratio")
    patch["published_completion_ratio"] = (modelPub or {}).get("completion_ratio")
    patch["published_group_ratio"] = groupRatio

    # 官方价格 → 官方成本；价格缺失或单价不可用（如 per_call 缺按次价）按
    # 设计稿整行归灰，绝不拿残缺单价硬算。
    if modelName not in priceCache:
        priceCache[modelName] = billingRepo.getOfficialModelPrice(modelName)
    priceRow = priceCache[modelName]
    officialUsd: Optional[float] = None
    if priceRow:
        patch["official_input_usd_per_m"] = priceRow.get("input_usd_per_m")
        patch["official_cached_input_usd_per_m"] = priceRow.get("cached_input_usd_per_m")
        patch["official_cache_write_usd_per_m"] = priceRow.get("cache_write_usd_per_m")
        patch["official_output_usd_per_m"] = priceRow.get("output_usd_per_m")
        officialUsd = _officialCost(priceRow, row)
        patch["official_usd"] = officialUsd
        if officialUsd is None:
            gray = gray or "no_official_price"
    else:
        gray = gray or "no_official_price"

    # 判定 1：上游暗改倍率。上游日志自记倍率与公示快照逐项对比（优先证据），
    # 自记倍率缺失时退回金额对比（期望成本按公示倍率反推）。
    expectedUsd = _expectedUpstreamUsd(row, modelPub, groupRatio, quotaPerUnit)
    if expectedUsd is not None:
        patch["upstream_expected_usd"] = expectedUsd
    driftRunnable = False
    if (modelPub or {}).get("model_ratio") is not None and upLog.get("model_ratio") is not None:
        driftRunnable = True
        pairs = [
            (upLog.get("model_ratio"), (modelPub or {}).get("model_ratio")),
            (upLog.get("completion_ratio"), (modelPub or {}).get("completion_ratio")),
            (upLog.get("group_ratio"), groupRatio),
        ]
        for actual, expected in pairs:
            if actual is None or expected is None:
                continue
            if _relDiff(float(actual), float(expected)) > tol:
                red.append("upstream_ratio_drift")
                break
    elif expectedUsd is not None:
        driftRunnable = True
        if _relDiff(upstreamUsd, expectedUsd) > tol:
            red.append("upstream_ratio_drift")
    if not driftRunnable and gray is None:
        gray = "ratio_field_missing"

    # 判定 2：主站计费自洽（主站日志 other 自记倍率重算 vs 实扣 quota）。
    # 只覆盖经典计费：model_ratio>0 走按量公式，model_price>0 走按次公式；
    # tiered_expr 等分层计费无法复算，静默跳过（主站侧次要判定，不整行归灰）。
    mMain, gMain, cMain = (
        row.get("main_model_ratio"),
        row.get("main_group_ratio"),
        row.get("main_completion_ratio"),
    )
    mainOther: Dict[str, Any] = {}
    try:
        parsedOther = json.loads(row.get("main_other_json") or "{}")
        if isinstance(parsedOther, dict):
            mainOther = parsedOther
    except ValueError:
        mainOther = {}
    mPrice = _num(mainOther.get("model_price"))
    mainCacheRatio = _num(mainOther.get("cache_ratio"))
    if mainQuota > 0 and gMain is not None:
        recalc: Optional[float] = None
        if mPrice is not None and mPrice > 0:
            recalc = mPrice * float(gMain) * quotaPerUnit
        elif mMain is not None and mMain > 0 and cMain is not None:
            cacheRead = float(row.get("cache_read_tokens") or 0)
            cacheRatio = mainCacheRatio if isinstance(mainCacheRatio, (int, float)) else None
            if cacheRead <= 0 or cacheRatio is not None:
                recalc = (
                    (
                        float(row.get("prompt_tokens") or 0)
                        + float(row.get("cache_creation_tokens") or 0)
                        + cacheRead * float(cacheRatio if cacheRatio is not None else 1.0)
                        + float(row.get("completion_tokens") or 0) * float(cMain)
                    )
                    * float(mMain)
                    * float(gMain)
                )
        if recalc is not None and _relDiff(mainQuota, recalc) > tol:
            red.append("main_billing_inconsistent")

    # 判定 3：主站/上游实际费用比 vs 分组倍率之比。
    gUp = upLog.get("group_ratio")
    if gMain is not None and gUp is not None and mainUsd > 0 and upstreamUsd > 0:
        expectedMarkup = float(gMain) / float(gUp)
        actualMarkup = mainUsd / upstreamUsd
        if _relDiff(actualMarkup, expectedMarkup) > tol:
            red.append("group_ratio_gap")

    # 判定 4：亏本请求。
    if math.isfinite(upstreamUsd) and mainUsd < upstreamUsd - _EPS:
        red.append("negative_margin")

    # 灰优先于红（设计稿 3.3 规则 0）：核不了的行绝不标红，红因仍留在
    # reason_codes 里供详情查看。
    status = "unknown" if gray else ("mismatch" if red else "ok")
    _finalize(row, patch, "matched", status, red + ([gray] if gray else []))


# ---------------------------------------------------------------------------
# 主站增量拉取 + 整轮编排
# ---------------------------------------------------------------------------

def _pullNewMainLogs(
    adminSite: Dict[str, Any], cursor: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """按游标增量拉主站消费日志；首轮只取最新一页（有界回填最近流量）。"""
    collected: List[Dict[str, Any]] = []
    error: Optional[str] = None
    firstRun = cursor <= 0
    for page in range(0, MAIN_LOG_MAX_PAGES):
        if STOP_EVENT.is_set():
            break
        ok, logs, _meta, pageError = fetchNewapiAdminConsumeLogs(
            adminSite, page, MAIN_LOG_PAGE_SIZE
        )
        if not ok:
            error = pageError
            break
        if not logs:
            break
        pageMinId = min(int(log.get("id") or 0) for log in logs)
        for log in logs:
            logId = int(log.get("id") or 0)
            if logId > cursor and (not firstRun or page == 1):
                collected.append(log)
        if firstRun or pageMinId <= cursor:
            break
    return collected, error


def _runForAdminSite(
    adminSite: Dict[str, Any], settings: Dict[str, Any]
) -> Dict[str, Any]:
    adminSiteId = int(adminSite["id"])
    summary: Dict[str, Any] = {
        "admin_site_id": adminSiteId,
        "name": adminSite.get("name"),
        "inserted": 0,
        "checked": 0,
    }
    cursor = billingRepo.getBillingCursor(adminSiteId)
    newLogs, error = _pullNewMainLogs(adminSite, cursor)
    if error:
        summary["error"] = str(error)
        return summary
    channelLinks = _buildChannelLinks(adminSiteId)
    siteInfoByBase: Dict[str, Tuple[Optional[int], Optional[str]]] = {}
    for log in newLogs:
        logId = int(log.get("id") or 0)
        if logId <= 0:
            continue
        channelId = log.get("channel")
        link = channelLinks.get(str(channelId)) if channelId is not None else None
        upstreamSiteId: Optional[int] = None
        upstreamSiteName: Optional[str] = None
        channelName: Optional[str] = None
        if link:
            site = link.get("site_row")
            if site:
                upstreamSiteId = int(site.get("id") or 0) or None
                upstreamSiteName = str(site.get("name") or "") or None
            if link.get("base") in siteInfoByBase:
                cachedId, cachedName = siteInfoByBase[link["base"]]
                upstreamSiteId = upstreamSiteId or cachedId
                upstreamSiteName = upstreamSiteName or cachedName
            else:
                siteInfoByBase[link["base"]] = (upstreamSiteId, upstreamSiteName)
            channelName = str(link.get("channel_name") or "") or None
        createdIso = log.get("created_iso")
        if createdIso is None:
            # created_at 解析失败的日志没有可信时间，跳过以免污染时间窗匹配
            # 与保留清理；打印上下文便于排查该主站的异常数据。
            print(
                f"[计费核对] 跳过无时间戳日志 admin_site={adminSiteId} log_id={logId}",
                flush=True,
            )
            continue
        requestAt = createdIso.astimezone(APP_TIMEZONE).isoformat(timespec="seconds")
        inserted = billingRepo.insertBillingCheck({
            "admin_site_id": adminSiteId,
            "channel_id": int(channelId) if channelId is not None else None,
            "channel_name": channelName,
            "upstream_site_id": upstreamSiteId,
            "upstream_site_name": upstreamSiteName,
            "main_log_id": logId,
            "request_at": requestAt,
            "model_name": log.get("model_name") or "unknown",
            "prompt_tokens": log.get("prompt_tokens"),
            "completion_tokens": log.get("completion_tokens"),
            "cache_read_tokens": int(log["cache_read_tokens"]) if log.get("cache_read_tokens") is not None else None,
            "cache_creation_tokens": int(log["cache_creation_tokens"]) if log.get("cache_creation_tokens") is not None else None,
            "main_quota": log.get("quota"),
            "main_model_ratio": log.get("model_ratio"),
            "main_group_ratio": log.get("group_ratio"),
            "main_completion_ratio": log.get("completion_ratio"),
            "main_other_json": _compactJson(log.get("other")),
        })
        if inserted:
            summary["inserted"] += 1
    if newLogs:
        maxId = max(int(log.get("id") or 0) for log in newLogs)
        billingRepo.setBillingCursor(adminSiteId, max(maxId, cursor))

    pending = billingRepo.listPendingBillingChecks(adminSiteId, PENDING_LIMIT_PER_RUN)
    ctxByBase: Dict[str, _UpstreamContext] = {}
    priceCache: Dict[str, Optional[Dict[str, Any]]] = {}
    for row in pending:
        if STOP_EVENT.is_set():
            break
        _auditRow(row, channelLinks, ctxByBase, settings, priceCache)
        summary["checked"] += 1
    # 汇总用当前全量口径（而非仅本轮），手动触发的回显里直观展示现状。
    summary["total_mismatch"] = billingRepo.countBillingChecksByStatus(adminSiteId, "mismatch")
    summary["total_unknown"] = billingRepo.countBillingChecksByStatus(adminSiteId, "unknown")
    return summary


def runBillingAuditOnce(adminSiteId: Optional[int] = None) -> Dict[str, Any]:
    """跑一轮计费核对；拿不到运行锁时幂等返回 executed=False。"""
    if not BILLING_AUDIT_RUN_LOCK.acquire(blocking=False):
        return {"triggered": True, "executed": False, "busy": True, "sites": []}
    try:
        settings = billingRepo.getBillingAuditSettings()
        allRows = list_admin_site_rows()
        if adminSiteId:
            row = next((r for r in allRows if int(r["id"]) == int(adminSiteId)), None)
            if row is None or str(row.get("platform") or "") != "newapi":
                return {
                    "triggered": True,
                    "executed": False,
                    "busy": False,
                    "error": "主站不存在或不是 NewAPI 平台（P1 仅支持 NewAPI 主站）",
                    "sites": [],
                }
            targets = [row]
        else:
            targets = [
                r for r in allRows
                if str(r.get("platform") or "newapi") == "newapi"
            ]
        summaries: List[Dict[str, Any]] = []
        for adminSite in targets:
            if STOP_EVENT.is_set():
                break
            try:
                summaries.append(_runForAdminSite(adminSite, settings))
            except Exception as exc:  # noqa: BLE001 - 单主站失败不影响其他主站
                print(
                    f"[计费核对] 主站核对异常 id={adminSite.get('id')} name={adminSite.get('name')} "
                    f"err={exc}",
                    flush=True,
                )
                summaries.append({
                    "admin_site_id": int(adminSite["id"]),
                    "name": adminSite.get("name"),
                    "error": str(exc),
                })
        return {"triggered": True, "executed": True, "busy": False, "sites": summaries}
    finally:
        BILLING_AUDIT_RUN_LOCK.release()


# ---------------------------------------------------------------------------
# 查询编排（router 用）
# ---------------------------------------------------------------------------

def serializeBillingCheck(row: Dict[str, Any], includeRaw: bool = False) -> Dict[str, Any]:
    try:
        reasons = json.loads(row.get("reason_codes_json") or "[]")
    except ValueError:
        reasons = []
    payload: Dict[str, Any] = {key: row.get(key) for key in (
        "id", "admin_site_id", "channel_id", "channel_name", "upstream_site_id",
        "upstream_site_name", "main_log_id", "request_at", "model_name",
        "prompt_tokens", "completion_tokens", "cache_read_tokens",
        "cache_creation_tokens", "main_quota", "main_usd", "main_model_ratio",
        "main_group_ratio", "main_completion_ratio", "official_usd",
        "official_input_usd_per_m", "official_cached_input_usd_per_m",
        "official_cache_write_usd_per_m", "official_output_usd_per_m",
        "upstream_expected_usd", "upstream_actual_usd", "upstream_log_id",
        "upstream_model_ratio", "upstream_group_ratio", "upstream_completion_ratio",
        "published_model_ratio", "published_group_ratio", "published_completion_ratio",
        "match_status", "status", "checked_at",
    )}
    payload["reason_codes"] = reasons if isinstance(reasons, list) else []
    if includeRaw:
        for key in ("main_other_json", "upstream_other_json"):
            raw = row.get(key)
            try:
                payload[key] = json.loads(raw) if raw else None
            except ValueError:
                payload[key] = None
    return payload


def _normalizeTimeBound(value: Optional[str]) -> Optional[str]:
    """把前端传来的时间过滤边界归一成与 request_at 一致的 app 时区 ISO 串。"""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = parse_iso_dt(text)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TIMEZONE)
    return dt.astimezone(APP_TIMEZONE).isoformat(timespec="seconds")


def billingAuditOverviewPayload(
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
) -> Dict[str, Any]:
    return billingRepo.overviewBillingChecks(
        adminSiteId, upstreamSiteId, channelId, model,
        _normalizeTimeBound(startAt), _normalizeTimeBound(endAt),
    )


def billingChecksListPayload(
    page: int,
    pageSize: int,
    status: Optional[str],
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
) -> Dict[str, Any]:
    safePage = max(1, int(page or 1))
    safeSize = min(MAX_LIST_PAGE_SIZE, max(1, int(pageSize or 20)))
    if status and status not in {"ok", "mismatch", "unknown"}:
        raise ValueError("status 只支持 ok / mismatch / unknown")
    rows, total = billingRepo.listBillingChecksPayload(
        safePage, safeSize, status, adminSiteId, upstreamSiteId, channelId, model,
        _normalizeTimeBound(startAt), _normalizeTimeBound(endAt),
    )
    return {
        "total": total,
        "page": safePage,
        "page_size": safeSize,
        "items": [serializeBillingCheck(row) for row in rows],
    }


def billingCheckDetailPayload(checkId: int) -> Optional[Dict[str, Any]]:
    row = billingRepo.getBillingCheckById(int(checkId))
    if not row:
        return None
    return serializeBillingCheck(row, includeRaw=True)


def officialPricesPayload() -> Dict[str, Any]:
    return {"items": billingRepo.listOfficialModelPrices()}


def billingAuditSettingsPayload() -> Dict[str, Any]:
    return billingRepo.getBillingAuditSettings()


def updateBillingAuditSettingsPayload(body: Dict[str, Any]) -> Dict[str, Any]:
    return billingRepo.updateBillingAuditSettings(body)


def upsertOfficialPricePayload(body: Dict[str, Any]) -> Dict[str, Any]:
    return billingRepo.upsertOfficialModelPrice(body)


def deleteOfficialPrice(modelName: str) -> bool:
    return billingRepo.deleteOfficialModelPrice(modelName)


def runBillingAuditTick() -> None:
    """调度器每分钟调用的入口：读设置判断是否到期，到期才真正跑一轮。"""
    if STOP_EVENT.is_set():
        return
    settings = billingRepo.getBillingAuditSettings()
    if not settings.get("enabled"):
        return
    now = app_now()
    nextRunAt = parse_iso_dt(
        billingRepo.getBillingMetaString("meta_next_run_at") or ""
    )
    if nextRunAt is not None and now < nextRunAt:
        return
    intervalMinutes = int(settings.get("interval_minutes") or 5)
    billingRepo.setBillingMeta(
        "meta_next_run_at",
        (now + timedelta(minutes=intervalMinutes)).isoformat(timespec="seconds"),
    )
    runBillingAuditOnce()
    lastPruneRaw = billingRepo.getBillingMetaString("meta_last_prune_at")
    lastPrune = parse_iso_dt(lastPruneRaw or "")
    if lastPrune is not None and (now - lastPrune) < timedelta(hours=1):
        return
    billingRepo.setBillingMeta("meta_last_prune_at", now.isoformat(timespec="seconds"))
    pruned = billingRepo.pruneBillingChecks(int(settings.get("retention_days") or 0))
    if pruned:
        print(f"[计费核对] 已清理 {pruned} 条过期核对记录", flush=True)
