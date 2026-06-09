import json
import os
import time
import random
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SYSTEM_PROMPT = """你是一个金融舆情分析师。分析新闻文章，提取存在具体舆情事件或业务影响的商业实体，
并评估事件对它们的影响。

核心原则：行业分析/行业综述/市场报告中，具体公司的经营数据、财务目标、
产能目标、市场份额变化等实质性信息属于有效事件，不应视为
"仅被提及"或"行业背景"。

## 必须输出
- 公司、金融机构、商业机构、商业品牌，只要有具体事件、数据、公告、交易、
  经营动作、产品进展、投诉、处罚、合作、融资、业绩变化等实质性信息。
- 产品/品牌有明确母公司的，输出母公司，用 mapped_from 记录产品名。
- 同一文档同一公司同一影响方向合并为一行；方向相反的可以拆分。
- 同一事件对不同公司影响相反时分别输出。

## 明确不输出
- 个人姓名、政府机关、监管机构、事业单位、媒体、数据来源、发布平台。
- 指数、板块、商品、原材料、币种、代币、概念符号。
- 研报发布方、提问平台。

## JSON 输出格式
[
  {
    "entity": "商业实体名称",
    "mapped_from": "产品/品牌名（多个用逗号分隔，无则留空）",
    "entity_sentiment": "利好 / 中性 / 利空",
    "impact_level": "大 / 中 / 小（仅在利空时填写，中性或利好时留空）",
    "sentiment_reason": "判断理由（简要说明，不要使用双引号）",
    "risk_type": "利空时填写风险类别，多个用逗号分隔，可选值：产品质量、财务风险、监管合规、经营风险、竞争风险、品牌声誉；中性/利好时留空"
  }
]
无有效实体时返回空列表 []。

## 影响程度分级
- 大：事件对公司整体产生重大冲击，如破产、并购、重大处罚、业绩暴雷、大规模停产、工厂事故
- 中：事件有可量化影响但不致命，如新产品发布、中标项目、常规诉讼、产线投产
- 小：事件对公司整体影响有限，如边缘业务调整、小额投资、局部产品问题、轻微舆情

## 风险类别说明（仅 entity_sentiment 为"利空"时填写）
- 产品质量：产品召回、事故、质量问题、食品安全、不合格
- 财务风险：亏损、减值、债务违约、资金链断裂、业绩下滑
- 监管合规：处罚、诉讼、监管问询、立案、整改通知
- 经营风险：停产、裁员、关店、供应链中断、产能不足
- 竞争风险：份额下滑、被竞品超越、客户流失
- 品牌声誉：负面舆情、丑闻、公关危机
同一事件涉及多个维度时填写多个。

## 示例
标题：2026年中国人形机器人产业报告发布，头部厂商加速量产
正文：IDC报告预测2026年中国人形机器人市场产量同比增长94%。特斯拉Optimus、
小米铁大、智元等头部厂商均加快了研发和量产进程，美的集团也在加速布局。
输出：
[
  {"entity": "特斯拉", "mapped_from": "Optimus", "entity_sentiment": "利好",
   "impact_level": "中",
   "sentiment_reason": "IDC报告预测行业增长94%，特斯拉Optimus量产进程加快"},
  {"entity": "小米集团", "mapped_from": "铁大", "entity_sentiment": "利好",
   "impact_level": "中",
   "sentiment_reason": "小米铁大研发和量产进程加快，受益于行业增长"},
  {"entity": "美的集团", "mapped_from": "", "entity_sentiment": "利好",
   "impact_level": "小",
   "sentiment_reason": "公司加速布局人形机器人领域，但无具体产品或量产数据"}
]

## 映射示例
原文提及"抖音"，输出：
  {"entity": "字节跳动", "mapped_from": "抖音"}
原文提及"万网"，事件指向阿里云服务，输出：
  {"entity": "阿里巴巴（注意：这是阿里巴巴集团，不是阿里云）", "mapped_from": "万网"}
原文提及"OPPO Pad 5 Pro"，输出：
  {"entity": "广东欧珀移动通信有限公司", "mapped_from": "OPPO Pad 5 Pro"}"""


@dataclass
class EntityEvalResult:
    entity: str
    mapped_from: str = ""
    entity_sentiment: str = "中性"
    impact_level: str = ""
    sentiment_reason: str = ""
    risk_type: list[str] = field(default_factory=list)


@dataclass
class CallRecord:
    doc_id: str
    success: bool
    duration_seconds: float
    prompt_tokens: int
    completion_tokens: int
    entities_count: int
    error: str = ""


SYSTEM_PROMPT_RELAXED = """你是一个金融舆情分析师。分析新闻文章，提取文章中明确提及的商业实体，并评估事件影响。

## 必须输出
- 新闻中明确提及的公司、金融机构、商业机构、商业品牌，只要文章对其有描述性信息
  （包括业务介绍、行业地位、市场表现、被提及参与事件等），都应当输出。
- 产品/品牌有明确母公司的，输出母公司，用 mapped_from 记录。

## 仅排除以下类别
- 个人姓名、政府机关、监管机构、事业单位、媒体、数据来源、发布平台。
- 指数、板块、商品、原材料、币种、代币、概念符号。

## JSON 输出格式
[
  {
    "entity": "商业实体名称",
    "mapped_from": "产品/品牌名（多个用逗号分隔，无则留空）",
    "entity_sentiment": "利好 / 中性 / 利空",
    "impact_level": "大 / 中 / 小（仅在利空时填写）",
    "sentiment_reason": "判断理由",
    "risk_type": "利空时填写风险类别，多个用逗号分隔"
  }
]
无有效实体时返回空列表 []。"""

DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"


class EntityEvalAgent:
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

    def evaluate(self, doc_id: str, headline: str, content: str, max_retries: int = 5,
                  system_prompt: str | None = None):
        user_prompt = f"标题：{headline}\n正文：{content}"
        prompt = system_prompt or SYSTEM_PROMPT

        last_err = None
        raw = ""
        for attempt in range(max_retries):
            try:
                t0 = time.monotonic()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                elapsed = time.monotonic() - t0

                raw = response.choices[0].message.content
                usage = response.usage

                entities = self._parse_response(raw)
                record = CallRecord(
                    doc_id=doc_id,
                    success=True,
                    duration_seconds=round(elapsed, 2),
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                    entities_count=len(entities),
                )
                return entities, record

            except ValueError as e:
                detail = f"\nraw: {raw}" if raw else ""
                raise ValueError(f"{e}{detail}") from e
            except Exception as e:
                last_err = e
                status = getattr(e, "status_code", 0)
                if status == 429:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [429] retry {attempt+1}/{max_retries}, wait {wait:.1f}s")
                    time.sleep(wait)
                else:
                    raise

        raise last_err or RuntimeError("max retries exceeded")

    def _parse_response(self, raw: str) -> list[EntityEvalResult]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM returned invalid JSON: {e}") from e

        if isinstance(data, list):
            raw_entities = data
        elif isinstance(data, dict):
            raw_entities = data.get("entities", data.get("results", []))
        else:
            raise ValueError(f"unexpected JSON structure: {type(data).__name__}")

        if not isinstance(raw_entities, list):
            raise ValueError(f"entities field is not a list: {type(raw_entities).__name__}")

        results = []
        for i, item in enumerate(raw_entities):
            if not isinstance(item, dict):
                raise ValueError(f"entity {i} is not a dict: {type(item).__name__}")
            raw_risk = item.get("risk_type", [])
            if isinstance(raw_risk, str):
                raw_risk = [raw_risk] if raw_risk else []
            results.append(EntityEvalResult(
                entity=item.get("entity", ""),
                mapped_from=item.get("mapped_from", ""),
                entity_sentiment=item.get("entity_sentiment", "中性"),
                impact_level=item.get("impact_level", ""),
                sentiment_reason=item.get("sentiment_reason", ""),
                risk_type=raw_risk,
            ))
        return results
