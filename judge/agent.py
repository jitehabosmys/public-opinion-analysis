import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

JUDGE_PROMPT = """你是一个金融舆情评测专家。检查系统提取的商业实体是否完整、是否有多余，
以及情感方向和风险分类是否准确。

## 任务边界
- 同时检查遗漏（missed）和多余（extra）。
- extra 只能从系统已提取的 entity 中选择。
- missed 只包括文章中有具体事件、数据、公告等实质性信息的商业实体。

## 有效实体
- 公司、金融机构、商业机构、商业品牌，只要有具体事件、数据、公告、交易、
  经营动作、产品进展、投诉、处罚、合作、融资、业绩变化等实质性信息。
- 产品/品牌有明确母公司的，母公司是有效实体。
- "中性"也必须有具体事件但正负不明确；仅提及不是中性。

## 应判 extra
- 个人姓名、政府机关、监管机构、事业单位、媒体、数据来源、发布平台。
- 指数、板块、商品、原材料、币种、代币、概念符号。
- 没有自身新事件或影响的：名单/案例实体、对比对象、泛泛客户/供应商、竞品背景。
- sentiment_reason 已写明"仅提及/无具体事件/只是背景/作为对比"的实体。

## 不应列为 missed（以下情况即使文章提到了，也不算遗漏）
- ETF 成分股/持仓股：文章提及某 ETF 时列举的成分股。
- 纯股价涨跌：没有附带具体事件或业务信息的股价变动。
- 名单/排名：被列为某榜单、共同持有标的、客户名单的实体。
- 行业背景/竞品：作为行业背景、市场规模信息出现的其他公司。
- 观点/传闻：个人观点、市场传闻、未正式官宣的信息。
- 非商业主体：基金产品名称、项目名称、活动名称。
- 映射后的名称：原文提及的产品/品牌名如果被系统提取为 mapped_from
  字段的值，且对应 entity 已被提取，不属于遗漏。
- 常规治理事项：董事会决议、监事会决议、股东会通知、人事变更、
  投资者交流等公司例行事务，不构成"具体事件"。

## 情感方向检查（针对系统已提取的每个实体）
- 对照原文事件判断 entity_sentiment 是否准确。
- 利好：业绩增长、收入提升、盈利改善、股价上涨、融资成功、合作签约、
  产品发布、产能扩张、市场拓展、获得认证、政策受益等正面事件。
- 利空：处罚、亏损、召回、诉讼、事故、裁员、关店、停产、违约、
  减值、债务问题、监管问询、产品质量问题、负面舆情等负面事件。
- 中性：有具体事件但正负方向不明确，或事件对实体影响不显著。
- 注意区分"正负面不明确"和"完全没有事件"——后者应在 extra 中处理。

## 风险类别检查（仅针对 entity_sentiment 为"利空"的实体）
- 对照原文事件判断 risk_type 分类是否齐全、有无多余。
- 产品质量：产品召回、事故、质量问题、食品安全、不合格
- 财务风险：亏损、减值、债务违约、资金链断裂、业绩下滑
- 监管合规：处罚、诉讼、监管问询、立案、整改通知
- 经营风险：停产、裁员、关店、供应链中断、产能不足
- 竞争风险：份额下滑、被竞品超越、客户流失
- 品牌声誉：负面舆情、丑闻、公关危机
- 同一事件涉及多个维度时应填写多个类别，缺少或多余都应指出。

## JSON 输出格式
{
  "missed": [{"entity": "实体名称", "reason": "遗漏原因"}],
  "extra": [{"entity": "实体名称", "reason": "多余原因"}],
  "sentiment_errors": [{"entity": "实体名称", "got": "当前方向", "expected": "正确方向", "reason": "判断依据"}],
  "risk_type_errors": [{"entity": "实体名称", "got": "当前类别", "expected": ["正确类别列表"], "reason": "判断依据"}]
}
无对应问题时对应列表为空 []。

## 示例

例1：个人姓名不应输出
标题：苹果与宁德时代洽谈电池供应合作
正文：据知情人士透露，苹果正在与宁德时代就电动汽车电池供应进行初步洽谈。
宁德时代董事长曾毓群未对此置评。
系统提取：
[{"entity": "曾毓群", "entity_sentiment": "中性", "sentiment_reason": "董事长被提及"},
 {"entity": "宁德时代", "entity_sentiment": "中性", "sentiment_reason": "与苹果洽谈电池供应"}]
输出：
{"missed": [], "extra": [{"entity": "曾毓群", "reason": "个人姓名，非商业实体"}],
 "sentiment_errors": [], "risk_type_errors": []}

例2：ETF成分股不应列为 missed
标题：芯片ETF（159995）高开震荡，半导体板块走强
正文：芯片ETF今日高开震荡，成分股圣邦股份涨超10%，龙芯中科涨6.91%。
系统提取：[]
输出：
{"missed": [], "extra": [], "sentiment_errors": [], "risk_type_errors": []}

例3：纯股价涨跌不应列为 missed
标题：科技股全线大涨，恒生科技指数涨超4%
正文：今日港股科技股大涨，中国联通涨超11%，阿里巴巴涨超11%，哔哩哔哩涨超10%。
系统提取：[]
输出：
{"missed": [], "extra": [], "sentiment_errors": [], "risk_type_errors": []}

例4：名单/对比对象不应列为 missed
标题：高盛列出对冲基金共同偏爱的股票
正文：高盛最新报告显示，对冲基金与共同基金偏爱的股票包括AppLovin、万事达、Spotify等。
系统提取：[]
输出：
{"missed": [], "extra": [], "sentiment_errors": [], "risk_type_errors": []}

例5：情感方向+风险类别检查
标题：特斯拉因刹车隐患召回超10万辆Model Y
正文：国家市场监管总局公告，特斯拉因刹车系统隐患召回2023年至2025年生产的部分Model Y，共计10.2万辆。受此影响，特斯拉股价下跌5%。
系统提取：
[{"entity": "特斯拉", "entity_sentiment": "利好", "sentiment_reason": "召回10.2万辆Model Y", "risk_type": ""}]
输出：
{"missed": [], "extra": [],
 "sentiment_errors": [{"entity": "特斯拉", "got": "利好", "expected": "利空", "reason": "召回是安全事件，应判利空"}],
 "risk_type_errors": [{"entity": "特斯拉", "got": "", "expected": ["产品质量"], "reason": "刹车隐患召回属于产品质量问题"}]}"""  # noqa: E501


@dataclass
class JudgeResult:
    doc_id: str
    missed: list[dict] = None
    extra: list[dict] = None
    sentiment_errors: list[dict] = None
    risk_type_errors: list[dict] = None

    def __post_init__(self):
        self.missed = self.missed or []
        self.extra = self.extra or []
        self.sentiment_errors = self.sentiment_errors or []
        self.risk_type_errors = self.risk_type_errors or []


@dataclass
class JudgeCallRecord:
    doc_id: str
    success: bool
    duration_seconds: float
    prompt_tokens: int
    completion_tokens: int
    entity_recall: float | None
    relevance_precision: float
    sentiment_accuracy: float | None = None
    risk_type_accuracy: float | None = None
    error: str = ""


DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"


def _compute_scores(missed: list[str], extra: list[str], extracted_count: int,
                    sentiment_errors: list[dict] | None = None,
                    risk_type_errors: list[dict] | None = None,
                    risk_type_relevant: int = 0):
    keep = max(0, extracted_count - len(extra))
    total_relevant = keep + len(missed)

    relevance_precision = round(keep / extracted_count, 3) if extracted_count > 0 else 1.0
    entity_recall = round(keep / total_relevant, 3) if total_relevant > 0 else 1.0
    sentiment_accuracy = round(1 - len(sentiment_errors or []) / extracted_count, 3) if extracted_count > 0 else None
    risk_type_accuracy = round(1 - len(risk_type_errors or []) / risk_type_relevant, 3) if risk_type_relevant > 0 else None

    return entity_recall, relevance_precision, sentiment_accuracy, risk_type_accuracy


class JudgeAgent:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.client = OpenAI(
            api_key=api_key or os.getenv("LLM_API_KEY"),
            base_url=base_url or os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL,
        )

    def evaluate(
        self, doc_id: str, headline: str, content: str,
        extracted_records: list[dict], total_extracted: int
    ):
        records_str = json.dumps(extracted_records, ensure_ascii=False)
        user_prompt = (
            f"标题：{headline}\n正文：{content}\n\n"
            f"系统提取的商业实体记录：\n{records_str}"
        )

        t0 = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
                    temperature=0.0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        elapsed = time.monotonic() - t0

        raw = response.choices[0].message.content
        usage = response.usage

        result = self._parse_response(doc_id, raw)
        risk_type_relevant = sum(1 for r in extracted_records if r.get("entity_sentiment") == "利空")
        er, rp, sa, rta = _compute_scores(
            result.missed, result.extra, total_extracted,
            sentiment_errors=result.sentiment_errors,
            risk_type_errors=result.risk_type_errors,
            risk_type_relevant=risk_type_relevant,
        )

        record = JudgeCallRecord(
            doc_id=doc_id,
            success=True,
            duration_seconds=round(elapsed, 2),
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            entity_recall=er,
            relevance_precision=rp,
            sentiment_accuracy=sa,
            risk_type_accuracy=rta,
        )
        return result, record

    def _parse_response(self, doc_id: str, raw: str) -> JudgeResult:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"parse error: {e}\nraw: {raw}") from e

        def _normalize(items: list) -> list[dict]:
            result = []
            for item in items:
                if isinstance(item, dict):
                    result.append(item)
                elif isinstance(item, str):
                    result.append({"entity": item, "reason": ""})
            return result

        def _validate_list(items, name: str) -> list:
            if not isinstance(items, list):
                raise ValueError(f"{name} field is not a list: {type(items).__name__}\nraw: {raw}")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError(f"{name} item is not a dict: {type(item).__name__}\nraw: {raw}")
            return items

        missed = _validate_list(data.get("missed", []), "missed")
        extra = _validate_list(data.get("extra", []), "extra")
        sentiment_errors = _validate_list(data.get("sentiment_errors", []), "sentiment_errors")
        risk_type_errors = _validate_list(data.get("risk_type_errors", []), "risk_type_errors")

        return JudgeResult(
            doc_id=doc_id,
            missed=_normalize(missed),
            extra=_normalize(extra),
            sentiment_errors=sentiment_errors,
            risk_type_errors=risk_type_errors,
        )
