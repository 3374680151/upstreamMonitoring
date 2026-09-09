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
from backend.integrations.sub2api_logs import (
    USAGE_PAGE_SIZE as SUB2API_USAGE_PAGE_SIZE,
    fetchSub2apiUsageLogsWithAuth,
)
from backend.repositories import billing_audit as billingRepo
from backend.repositories.admin_sites import (
    admin_site_quota_per_unit,
    list_admin_site_rows,
)
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    get_site_or_404,
    list_channel_discovery_links,
    site_auth_ready,
    site_groups_from_row,
    site_quota_per_unit,
)
from backend.services.billing_audit_rules import (
    EPS,
    officialCost,
    relDiff,
    safeNumber,
)
from backend.services.billing_audit_sub2api import (
    judgeSub2apiUpstream,
    sub2apiPublishedGroupRatios,
)
from backend.services.billing_audit_notify import maybePushRedAlert
from backend.services.channel_match_service import list_channel_upstream_bindings
from backend.services.monitoring_service import (
    cache_newapi_pricing_payload,
    get_newapi_pricing_cache,
)

MAIN_LOG_PAGE_SIZE = 100
MAIN_LOG_MAX_PAGES = 10
UPSTREAM_LOG_PAGE_SIZE = 100
UPSTREAM_LOG_MAX_PAGES = 10
SUB2API_USAGE_MAX_PAGES = 6
PENDING_LIMIT_PER_RUN = 300
MAX_LIST_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# 判定纯函数（共享部分在 billing_audit_rules，NewAPI 倍率口径在下方）
# ---------------------------------------------------------------------------

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
        # 上一页首条日志的锚（id+created_at）：部分 NewAPI 版本把 p=0/1 都钳到
        # 第 1 页，连续两页内容相同就跳过这次重复，不污染已拉窗口。
        self.lastPageFirstKey: Optional[Tuple[Any, Any]] = None


def _upstreamPlatform(ctx: _UpstreamContext) -> str:
    return str((ctx.site or {}).get("platform") or "newapi").strip().lower()


def _buildUsedUpstreamLogMap(
    entries: List[Dict[str, Any]],
) -> Dict[int, Dict[int, List[str]]]:
    """历史占用条目 → ``{upstream_site_id: {upstream_log_id: [参考时间 ISO]}}``。

    时间参考优先取日志自身的 upstream_log_created_at；历史行没有该列时退回
    主站 request_at（匹配窗内两者相差秒级，足够区分「同一条」与「同 id 新日志」）。
    """
    usedBySiteLog: Dict[int, Dict[int, List[str]]] = {}
    for entry in entries:
        siteId = int(entry.get("upstream_site_id") or 0)
        logId = int(entry.get("upstream_log_id") or 0)
        if siteId <= 0 or logId <= 0:
            continue
        reference = str(
            entry.get("upstream_log_created_at") or entry.get("request_at") or ""
        )
        usedBySiteLog.setdefault(siteId, {}).setdefault(logId, []).append(reference)
    return usedBySiteLog


def _upstreamLogAlreadyUsed(
    usedBySiteLog: Dict[int, Dict[int, List[str]]],
    upstreamSiteId: Optional[int],
    logId: Any,
    logCreatedIso: Any,
    windowSeconds: int,
) -> bool:
    """该上游日志是否已被历史行消费：id 命中且参考时间落在日志时间 ±窗口内。

    只比 id 会误杀重建库后重新计数的新日志（时间差以天计）；不比时间则同
    id 新日志无法翻案。时间解析失败的参考条目按命中处理（保守，宁可漏配
    不重复配）。
    """
    siteId = int(upstreamSiteId or 0)
    try:
        numericId = int(logId or 0)
    except (TypeError, ValueError):
        return False
    references = usedBySiteLog.get(siteId, {}).get(numericId)
    if not references:
        return False
    if logCreatedIso is None:
        return True
    for reference in references:
        referenceAt = parse_iso_dt(reference) if reference else None
        if referenceAt is None:
            return True
        delta = abs((logCreatedIso - referenceAt).total_seconds())
        if delta <= windowSeconds:
            return True
    return False


def _ensureUpstreamLogs(ctx: _UpstreamContext, neededFrom: datetime, windowSeconds: int) -> None:
    """保证已拉日志窗口覆盖 neededFrom - window（覆盖不到就尽力拉到页数上限）。

    NewAPI 上游走 /api/log/self、sub2api 上游走 /api/v1/usage，两者都按时间
    倒序返回，翻页直到覆盖目标时间或到页数上限；同一轮内多个请求共享已拉的
    日志，避免逐行重复请求上游。
    """
    isSub2api = _upstreamPlatform(ctx) == "sub2api"
    maxPages = SUB2API_USAGE_MAX_PAGES if isSub2api else UPSTREAM_LOG_MAX_PAGES
    pageSize = SUB2API_USAGE_PAGE_SIZE if isSub2api else UPSTREAM_LOG_PAGE_SIZE
    target = neededFrom - timedelta(seconds=windowSeconds)
    while True:
        if ctx.logs is None:
            ctx.logs = []
        covered = ctx.oldest is not None and ctx.oldest <= target
        if covered or ctx.exhausted or ctx.pagesFetched >= maxPages:
            return
        if STOP_EVENT.is_set():
            return
        page = ctx.pagesFetched
        ctx.pagesFetched += 1
        if isSub2api:
            ok, logs, _meta, error = fetchSub2apiUsageLogsWithAuth(
                ctx.site, page, pageSize
            )
        else:
            ok, logs, _meta, error = fetchNewapiSelfConsumeLogs(
                ctx.site, page, pageSize
            )
        if not ok:
            print(f"[计费核对] 上游日志拉取失败 base={ctx.base} page={page} err={error}", flush=True)
            ctx.exhausted = True
            return
        if not logs:
            ctx.exhausted = True
            return
        # 钳位去重：p=0/1 同页（新版 NewAPI 把 p<1 钳到第 1 页，实测主站与
        # zc-api rc.25 上游皆如此）。重复页跳过入窗，不重复占翻页预算外的覆盖。
        firstKey = (logs[0].get("id"), logs[0].get("created_at"))
        if firstKey == ctx.lastPageFirstKey:
            continue
        ctx.lastPageFirstKey = firstKey
        for log in logs:
            ctx.logs.append(log)
            created = log.get("created_iso")
            if created and (ctx.oldest is None or created < ctx.oldest):
                ctx.oldest = created


def _ensurePricing(ctx: _UpstreamContext) -> Optional[Dict[str, Any]]:
    """上游公示价格：NewAPI 优先复用监控模块的 pricing 缓存（未命中直拉一次）；
    sub2api 无 /api/pricing，公示倍率直接取监控快照（groups/rates 口径）。"""
    if ctx.pricingLoaded:
        return ctx.pricing
    ctx.pricingLoaded = True
    payload: Any = None
    site = ctx.site or {}
    siteId = int(site.get("id") or 0)
    if _upstreamPlatform(ctx) == "sub2api":
        ctx.pricing = {
            "models": {},
            "group_ratios": sub2apiPublishedGroupRatios(site),
        }
        return ctx.pricing
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
    usedBySiteLog: Optional[Dict[int, Dict[int, List[str]]]] = None,
) -> Dict[str, Any]:
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
        return {"id": int(row["id"]), "status": "unknown", "reason_codes": ["no_binding"]}
    base = str(link["base"])
    ctx = ctxByBase.get(base)
    if ctx is None:
        ctx = _UpstreamContext(base, link.get("site_row"))
        ctxByBase[base] = ctx
    if ctx.site is None or not site_auth_ready(ctx.site):
        _finalize(row, patch, "no_token", "unknown", ["no_token"])
        return {"id": int(row["id"]), "status": "unknown", "reason_codes": ["no_token"]}
    upstreamPlatform = str(ctx.site.get("platform") or "newapi").strip().lower()
    if upstreamPlatform not in {"newapi", "sub2api"}:
        # 上游平台没有可核对的日志接口（设计稿 4.2：no_log_api）。
        _finalize(row, patch, "no_log_api", "unknown", ["no_log_api"])
        return {"id": int(row["id"]), "status": "unknown", "reason_codes": ["no_log_api"]}
    # 审计期回填站点归属：老行插入时可能还没有发现来源映射。
    patch["upstream_site_id"] = int(ctx.site.get("id") or 0) or None
    patch["upstream_site_name"] = str(ctx.site.get("name") or "") or None

    requestAt = parse_iso_dt(str(row.get("request_at") or ""))
    if requestAt is None:
        _finalize(row, patch, "unmatched", "unknown", ["no_upstream_log"])
        return {"id": int(row["id"]), "status": "unknown", "reason_codes": ["no_upstream_log"]}
    _ensureUpstreamLogs(ctx, requestAt, windowSeconds)
    if STOP_EVENT.is_set():
        # 停机时不把「没拉完日志」定格成 unmatched，留待下一轮重判。
        return {"id": int(row["id"]), "status": "pending", "reason_codes": []}
    logs = ctx.logs or []
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    upstreamSiteId = int(ctx.site.get("id") or 0) or None
    for log in logs:
        logId = log.get("id")
        if logId is None or logId in ctx.usedLogIds:
            continue
        # 本轮内同一条上游日志只配一条主站记录；跨轮判重带站点+时间
        # （上游重建库后 id 重新计数，裸 id 会误杀新日志）。
        if _upstreamLogAlreadyUsed(
            usedBySiteLog or {}, upstreamSiteId, logId,
            log.get("created_iso"), windowSeconds,
        ):
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
        return {"id": int(row["id"]), "status": "unknown", "reason_codes": ["no_upstream_log"]}

    upLog = best[1]
    ctx.usedLogIds.add(upLog.get("id"))
    patch["upstream_log_id"] = upLog.get("id")
    # 记下上游日志自身时间，供后续轮次跨轮判重（见 BILLING_CHECK_COLUMN_ADDITIONS）。
    if upLog.get("created_iso") is not None:
        patch["upstream_log_created_at"] = (
            upLog["created_iso"].astimezone(APP_TIMEZONE).isoformat(timespec="seconds")
        )
    patch["upstream_other_json"] = _compactJson(upLog.get("other"))

    if upstreamPlatform == "sub2api":
        # sub2api 上游（P2）：实扣即美元（actual_cost），无 quota 换算；
        # 暗改判定 = 日志自记 rate_multiplier vs 监控快照公示倍率
        # （金额兜底），详见 billing_audit_sub2api。
        upstreamUsd = float(upLog.get("usd") or 0.0)
        row["_cache_ratio_hint"] = None
        upPatch, upRed, upGray = judgeSub2apiUpstream(
            row, upLog, link.get("matched_groups") or [],
            (_ensurePricing(ctx) or {}).get("group_ratios") or {}, tol,
        )
        patch.update(upPatch)
        gray = upGray
        red.extend(upRed)
    else:
        upQuota = float(upLog.get("quota") or 0)
        # quota→美元基准按上游站点覆盖（NULL 回退全局/主站有效值）。
        upstreamQuotaPerUnit = site_quota_per_unit(ctx.site) or quotaPerUnit
        upstreamUsd = upQuota / upstreamQuotaPerUnit
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

        # 判定 1：上游暗改倍率。上游日志自记倍率与公示快照逐项对比（优先证据），
        # 自记倍率缺失时退回金额对比（期望成本按公示倍率反推）。
        expectedUsd = _expectedUpstreamUsd(row, modelPub, groupRatio, upstreamQuotaPerUnit)
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
                if relDiff(float(actual), float(expected)) > tol:
                    red.append("upstream_ratio_drift")
                    break
        elif expectedUsd is not None:
            driftRunnable = True
            if relDiff(upstreamUsd, expectedUsd) > tol:
                red.append("upstream_ratio_drift")
        if not driftRunnable and gray is None:
            gray = "ratio_field_missing"

    # 官方价格 → 官方成本；价格缺失或单价不可用（如 per_call 缺按次价）按
    # 设计稿整行归灰，绝不拿残缺单价硬算。
    modelName = str(row.get("model_name") or "")
    if modelName not in priceCache:
        priceCache[modelName] = billingRepo.getOfficialModelPrice(modelName)
    priceRow = priceCache[modelName]
    if priceRow:
        patch["official_input_usd_per_m"] = priceRow.get("input_usd_per_m")
        patch["official_cached_input_usd_per_m"] = priceRow.get("cached_input_usd_per_m")
        patch["official_cache_write_usd_per_m"] = priceRow.get("cache_write_usd_per_m")
        patch["official_output_usd_per_m"] = priceRow.get("output_usd_per_m")
        officialUsd = officialCost(priceRow, row)
        patch["official_usd"] = officialUsd
        if officialUsd is None:
            gray = gray or "no_official_price"
    else:
        gray = gray or "no_official_price"

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
    mPrice = safeNumber(mainOther.get("model_price"))
    mainCacheRatio = safeNumber(mainOther.get("cache_ratio"))
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
        if recalc is not None and relDiff(mainQuota, recalc) > tol:
            red.append("main_billing_inconsistent")

    # 判定 3：主站/上游实际费用比 vs 分组倍率之比（上游侧取日志自记倍率，
    # NewAPI 为 other.group_ratio，sub2api 为 rate_multiplier）。
    gUp = upLog.get("group_ratio")
    if gMain is not None and gUp is not None and mainUsd > 0 and upstreamUsd > 0:
        expectedMarkup = float(gMain) / float(gUp)
        actualMarkup = mainUsd / upstreamUsd
        if relDiff(actualMarkup, expectedMarkup) > tol:
            red.append("group_ratio_gap")

    # 判定 4：亏本请求。
    if math.isfinite(upstreamUsd) and mainUsd < upstreamUsd - EPS:
        red.append("negative_margin")

    # 灰优先于红（设计稿 3.3 规则 0）：核不了的行绝不标红，红因仍留在
    # reason_codes 里供详情查看。
    status = "unknown" if gray else ("mismatch" if red else "ok")
    reasons = red + ([gray] if gray else [])
    _finalize(row, patch, "matched", status, reasons)
    return {
        "id": int(row["id"]),
        "status": status,
        "reason_codes": reasons,
        "model_name": str(row.get("model_name") or ""),
        "main_usd": row.get("main_usd"),
        "upstream_actual_usd": patch.get("upstream_actual_usd"),
    }


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
            # 首轮只收第 0 页（最新一页），做有界回填；翻页自 p=0 起，
            # 这里的「第一页」判断必须与起始页基准一致。
            if logId > cursor and (not firstRun or page == 0):
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
        "mismatched": 0,
    }
    # quota→美元基准按主站覆盖（NULL 回退全局设置）；行级 main_usd 用它换算。
    effectiveSettings = dict(settings)
    mainOverride = admin_site_quota_per_unit(adminSite)
    if mainOverride:
        effectiveSettings["quota_per_unit"] = mainOverride
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
    # 灰行重判（P2）：no_binding / no_token / no_log_api 的成因被修复后
    # （建绑定、补登录态、sub2api 适配上线），按 checked_at 限速翻面重审；
    # unmatched（no_upstream_log）同样 1h 冷却——上游日志拉取瞬时失败、
    # 或上游重建库/行号漂移的历史误判都靠重判翻案，只审近 7 天的行。
    rejudgeCutoff = (app_now() - timedelta(hours=1)).isoformat(timespec="seconds")
    unmatchedFloor = (app_now() - timedelta(days=7)).isoformat(timespec="seconds")
    rejudge = billingRepo.listRejudgeBillingChecks(
        adminSiteId, rejudgeCutoff, PENDING_LIMIT_PER_RUN,
        unmatchedRequestAtFloor=unmatchedFloor,
    )
    seenIds = {int(row["id"]) for row in pending}
    pending = pending + [row for row in rejudge if int(row["id"]) not in seenIds]
    ctxByBase: Dict[str, _UpstreamContext] = {}
    priceCache: Dict[str, Optional[Dict[str, Any]]] = {}
    # 跨轮占用池（按上游站点分组的 {log_id: [参考时间]}）每主站加载一次：
    # 上游重建库后日志 id 会重新计数（gopay 2026-09 实测），判重必须带时间。
    usedBySiteLog = _buildUsedUpstreamLogMap(
        billingRepo.listUsedUpstreamLogEntries(adminSiteId)
    )
    for row in pending:
        if STOP_EVENT.is_set():
            break
        result = _auditRow(
            row, channelLinks, ctxByBase, effectiveSettings, priceCache, usedBySiteLog
        )
        summary["checked"] += 1
        if result.get("status") == "mismatch":
            summary["mismatched"] += 1
            summary.setdefault("mismatch_rows", []).append(result)
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
                summary = _runForAdminSite(adminSite, settings)
                # push_on_red（P2 生效）：本轮新判红行走邮件/企微摘要推送；
                # mismatch_rows 只在进程内用，不进 API 契约。
                redRows = summary.pop("mismatch_rows", [])
                try:
                    maybePushRedAlert(adminSite, settings, redRows)
                except Exception as push_exc:  # noqa: BLE001 - 推送失败不影响核对结果
                    print(
                        f"[计费核对] 红色告警推送失败 id={adminSite.get('id')} err={push_exc}",
                        flush=True,
                    )
                summaries.append(summary)
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


def normalizeTimeBound(value: Optional[str]) -> Optional[str]:
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
    reasonCode: Optional[str] = None,
) -> Dict[str, Any]:
    safePage = max(1, int(page or 1))
    safeSize = min(MAX_LIST_PAGE_SIZE, max(1, int(pageSize or 20)))
    if status and status not in {"ok", "mismatch", "unknown"}:
        raise ValueError("status 只支持 ok / mismatch / unknown")
    rows, total = billingRepo.listBillingChecksPayload(
        safePage, safeSize, status, adminSiteId, upstreamSiteId, channelId, model,
        normalizeTimeBound(startAt), normalizeTimeBound(endAt),
        reasonCode=reasonCode,
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
