"""
Hermes · 商业数据仪表盘整合 —— 参考实现（对应 qianjin-commerce-toolkit SKILL.md 8.3）

把原始销售指标（收入 / 配额 / 管线 / 线索）按区域、代表、时间段汇总整合，
输出结构化仪表盘数据与数据质量评估。强调：达成率安全计算、数据新鲜度标注、
异常值标记、口径一致、幂等。

仅作参考实现；生产环境应接入真实数据源并补齐分页/增量/告警。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


@dataclass
class MetricPoint:
    rep_id: str
    region: str
    metric_type: str  # revenue, quota, pipeline, leads
    value: Decimal
    metric_date: datetime
    source: str  # crm, manual, import


@dataclass
class RegionSummary:
    region: str
    total_revenue: Decimal = Decimal("0")
    total_quota: Decimal = Decimal("0")
    attainment_pct: Optional[Decimal] = None
    rep_count: int = 0
    pipeline_value: Decimal = Decimal("0")
    pipeline_count: int = 0
    data_freshness: str = "current"  # current | delayed | stale


class SalesDataConsolidator:
    """销售数据整合引擎"""

    FRESHNESS_THRESHOLDS = {
        "current": timedelta(hours=2),
        "delayed": timedelta(hours=8),
        # 超过 8 小时标记为 stale
    }

    ANOMALY_THRESHOLDS = {
        "attainment_high": Decimal("200"),  # >200% 可能是数据错误
        "attainment_low": Decimal("20"),     # <20% 需要关注
    }

    def __init__(self, metrics: list[MetricPoint]):
        self.metrics = metrics
        self.now = datetime.utcnow()

    def build_dashboard(self) -> dict:
        """构建完整的仪表盘数据"""
        return {
            "generated_at": self.now.isoformat(),
            "region_summary": self._build_region_summaries(),
            "top_performers": self._get_top_performers(n=5),
            "pipeline_snapshot": self._build_pipeline_snapshot(),
            "trend_data": self._build_trend_data(months=6),
            "anomalies": self._detect_anomalies(),
            "data_quality": self._assess_data_quality(),
        }

    def _build_region_summaries(self) -> list[dict]:
        regions: dict[str, RegionSummary] = {}

        for m in self.metrics:
            if m.region not in regions:
                regions[m.region] = RegionSummary(region=m.region)
            summary = regions[m.region]

            if m.metric_type == "revenue":
                summary.total_revenue += m.value
            elif m.metric_type == "quota":
                summary.total_quota += m.value
            elif m.metric_type == "pipeline":
                summary.pipeline_value += m.value
                summary.pipeline_count += 1

        for summary in regions.values():
            summary.attainment_pct = self._safe_attainment(
                summary.total_revenue, summary.total_quota
            )
            summary.rep_count = len(set(
                m.rep_id for m in self.metrics
                if m.region == summary.region
            ))
            summary.data_freshness = self._check_freshness(summary.region)

        return [self._serialize_region(s) for s in regions.values()]

    def _safe_attainment(self, revenue: Decimal,
                         quota: Decimal) -> Optional[Decimal]:
        """安全计算达成率，处理除零"""
        if not quota or quota == 0:
            return None  # 前端显示为"待设定"
        return (revenue / quota * 100).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )

    def _check_freshness(self, region: str) -> str:
        region_metrics = [m for m in self.metrics if m.region == region]
        if not region_metrics:
            return "stale"
        latest = max(m.metric_date for m in region_metrics)
        age = self.now - latest
        if age <= self.FRESHNESS_THRESHOLDS["current"]:
            return "current"
        elif age <= self.FRESHNESS_THRESHOLDS["delayed"]:
            return "delayed"
        return "stale"

    def _detect_anomalies(self) -> list[dict]:
        """检测数据异常"""
        anomalies = []
        rep_data = self._aggregate_by_rep()
        for rep_id, data in rep_data.items():
            att = self._safe_attainment(data["revenue"], data["quota"])
            if att is None:
                anomalies.append({
                    "rep_id": rep_id,
                    "type": "missing_quota",
                    "message": f"代表 {rep_id} 配额未设定",
                })
            elif att > self.ANOMALY_THRESHOLDS["attainment_high"]:
                anomalies.append({
                    "rep_id": rep_id,
                    "type": "high_attainment",
                    "value": float(att),
                    "message": f"代表 {rep_id} 达成率 {att}% 异常偏高，请核实",
                })
        return anomalies

    def _assess_data_quality(self) -> dict:
        """数据质量评估"""
        total = len(self.metrics)
        if total == 0:
            return {"score": 0, "issues": ["无数据"]}

        issues = []
        null_values = sum(1 for m in self.metrics if m.value is None)
        if null_values > 0:
            issues.append(f"{null_values} 条记录值为空")

        seen = set()
        duplicates = 0
        for m in self.metrics:
            key = (m.rep_id, m.metric_type, m.metric_date)
            if key in seen:
                duplicates += 1
            seen.add(key)
        if duplicates > 0:
            issues.append(f"{duplicates} 条疑似重复记录")

        score = max(0, 100 - null_values * 5 - duplicates * 10)
        return {"score": score, "issues": issues}

    def _get_top_performers(self, n: int = 5) -> list[dict]:
        rep_data = self._aggregate_by_rep()
        sorted_reps = sorted(
            rep_data.items(),
            key=lambda x: x[1]["revenue"],
            reverse=True
        )
        return [
            {"rep_id": rep_id, **data}
            for rep_id, data in sorted_reps[:n]
        ]

    def _aggregate_by_rep(self) -> dict:
        result = {}
        for m in self.metrics:
            if m.rep_id not in result:
                result[m.rep_id] = {
                    "region": m.region,
                    "revenue": Decimal("0"),
                    "quota": Decimal("0"),
                }
            if m.metric_type == "revenue":
                result[m.rep_id]["revenue"] += m.value
            elif m.metric_type == "quota":
                result[m.rep_id]["quota"] += m.value
        return result

    def _build_pipeline_snapshot(self) -> list[dict]:
        pipeline_metrics = [m for m in self.metrics if m.metric_type == "pipeline"]
        return [{
            "total_value": float(sum(m.value for m in pipeline_metrics)),
            "count": len(pipeline_metrics),
        }]

    def _build_trend_data(self, months: int) -> list[dict]:
        cutoff = self.now - timedelta(days=months * 30)
        recent = [m for m in self.metrics
                  if m.metric_date >= cutoff and m.metric_type == "revenue"]
        monthly = {}
        for m in recent:
            key = m.metric_date.strftime("%Y-%m")
            monthly[key] = monthly.get(key, Decimal("0")) + m.value
        return [{"month": k, "revenue": float(v)}
                for k, v in sorted(monthly.items())]

    def _serialize_region(self, s: RegionSummary) -> dict:
        return {
            "region": s.region,
            "total_revenue": float(s.total_revenue),
            "total_quota": float(s.total_quota),
            "attainment_pct": float(s.attainment_pct) if s.attainment_pct else None,
            "rep_count": s.rep_count,
            "pipeline_value": float(s.pipeline_value),
            "data_freshness": s.data_freshness,
        }


if __name__ == "__main__":
    demo = [
        MetricPoint("REP-042", "华东", "revenue", Decimal("820000"),
                    datetime.utcnow(), "crm"),
        MetricPoint("REP-042", "华东", "quota", Decimal("600000"),
                    datetime.utcnow(), "crm"),
    ]
    engine = SalesDataConsolidator(demo)
    import json
    print(json.dumps(engine.build_dashboard(), ensure_ascii=False, indent=2))
