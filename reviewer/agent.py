import json
import os
import time
import random
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

REVIEWER_PROMPT = """你是一个金融舆情评测专家。检查系统提取的商业实体是否完整、是否有多余，
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


## JSON 输出格式
{
  "missed": [{"entity": "实体名称", "reason": "遗漏原因"}],
  "extra": [{"entity": "实体名称", "reason": "多余原因"}],
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
{"missed": [], "extra": [{"entity": "曾毓群", "reason": "个人姓名，非商业实体"}]}

例2：ETF成分股不应列为 missed
标题：芯片ETF（159995）高开震荡，半导体板块走强
正文：芯片ETF今日高开震荡，成分股圣邦股份涨超10%，龙芯中科涨6.91%。
系统提取：[]
输出：
{"missed": [], "extra": []}

例3：纯股价涨跌不应列为 missed
标题：科技股全线大涨，恒生科技指数涨超4%
正文：今日港股科技股大涨，中国联通涨超11%，阿里巴巴涨超11%，哔哩哔哩涨超10%。
系统提取：[]
输出：
{"missed": [], "extra": []}

例4：名单/对比对象不应列为 missed
标题：高盛列出对冲基金共同偏爱的股票
正文：高盛最新报告显示，对冲基金与共同基金偏爱的股票包括AppLovin、万事达、Spotify等。
系统提取：[]
输出：
{"missed": [], "extra": []}
"""  # noqa: E501


@dataclass
class CallRecord:
    doc_id: str
    success: bool
    duration_seconds: float
    prompt_tokens: int
    completion_tokens: int
    entities_count: int
    error: str = ""


DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"


class ReviewerAgent:
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

    def review(self, doc_id: str, headline: str, content: str,
               existing_entities: list[dict], max_retries: int = 5):
        existing_str = json.dumps(existing_entities, ensure_ascii=False)
        user_prompt = (
            f"标题：{headline}\n正文：{content}\n\n"
            f"系统已抽取的商业实体：\n{existing_str}"
        )

        last_err = None
        raw = ""
        for attempt in range(max_retries):
            try:
                t0 = time.monotonic()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": REVIEWER_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                elapsed = time.monotonic() - t0

                raw = response.choices[0].message.content
                usage = response.usage

                missed = self._parse_response(raw)
                record = CallRecord(
                    doc_id=doc_id,
                    success=True,
                    duration_seconds=round(elapsed, 2),
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                    entities_count=len(missed),
                )
                return missed, record

            except ValueError as e:
                detail = f"\nraw: {raw}" if raw else ""
                raise ValueError(f"{e}{detail}") from e
            except Exception as e:
                last_err = e
                status = getattr(e, "status_code", 0)
                if status == 429:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [reviewer 429] retry {attempt+1}/{max_retries}, wait {wait:.1f}s")
                    time.sleep(wait)
                else:
                    raise

        raise last_err or RuntimeError("max retries exceeded")

    def _parse_response(self, raw: str) -> list[dict]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Reviewer returned invalid JSON: {e}") from e

        missed = data.get("missed", [])
        if not isinstance(missed, list):
            raise ValueError(f"missed field is not a list: {type(missed).__name__}\nraw: {raw}")

        results = []
        for i, item in enumerate(missed):
            if not isinstance(item, dict):
                raise ValueError(f"missed item {i} is not a dict: {type(item).__name__}")
            results.append({
                "entity": item.get("entity", ""),
                "reason": item.get("reason", ""),
            })
        return results
