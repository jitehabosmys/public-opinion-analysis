"""Financial risk control public opinion dataset EDA"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os

DATA_PATH = "data/test_news_0603.csv"
OUTPUT_DIR = "eda_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

df = pd.read_csv(DATA_PATH)
print(f"Shape: {df.shape}")
print(f"\n=== Columns & dtypes ===")
print(df.dtypes.to_string())

# ── Null overview ──
print(f"\n=== Null values ===")
null_stats = pd.DataFrame({
    "null_count": df.isnull().sum(),
    "null_rate": (df.isnull().sum() / len(df)).round(4)
})
print(null_stats[null_stats["null_count"] > 0].to_string())

# ── English mappings for plots ──
SENT_MAP = {"正面": "positive", "中性": "neutral", "负面": "negative"}
REGION_MAP = {"中央": "central", "华东": "east_china", "华南": "south_china"}
GROUP_MAP = {"APP": "APP", "网页": "web", "网页(U)": "web(U)", "报纸": "newspaper", "杂志": "magazine"}

df["sentiment_en"] = df["doc_sentiment"].map(SENT_MAP)
df["region_en"] = df["pub_region"].map(REGION_MAP)

# ── Sentiment ──
print(f"\n=== Sentiment distribution ===")
sent_dist = df["doc_sentiment"].value_counts()
print(sent_dist.to_string())
print(f"\n=== Sentiment score stats ===")
print(df["sentiment_score"].describe())

# ── Source ──
print(f"\n=== Site name top 20 ===")
print(df["site_name"].value_counts().head(20).to_string())
print(f"\n=== Pub region ===")
print(df["pub_region"].value_counts().to_string())
print(f"\n=== Group name ===")
print(df["group_name"].value_counts().to_string())

# ── Date range ──
df["pub_date_parsed"] = pd.to_datetime(df["pub_date"], errors="coerce")
print(f"\n=== Date range ===")
print(f"  earliest: {df['pub_date_parsed'].min()}")
print(f"  latest  : {df['pub_date_parsed'].max()}")
print(f"  span    : {(df['pub_date_parsed'].max() - df['pub_date_parsed'].min()).days} days")

# ── rel_id ──
print(f"\n=== rel_id type breakdown ===")
is_date_rel = df["rel_id"].astype(str).str.match(r"^\d+$") & (df["rel_id"].astype(str).str.len() < 25)
is_hash_rel = df["rel_id"].astype(str).str.len() == 32
print(f"  date-prefix (numeric <25): {is_date_rel.sum()} ({is_date_rel.mean():.1%})")
print(f"  32-char hash             : {is_hash_rel.sum()} ({is_hash_rel.mean():.1%})")
print(f"  other                    : {(~(is_date_rel | is_hash_rel)).sum()}")

hash_rel_ids = df.loc[is_hash_rel, "rel_id"]
group_sizes = hash_rel_ids.value_counts()
print(f"\n  hash unique rel_ids: {group_sizes.nunique()}")
print(f"  size=1 (singleton) : {(group_sizes == 1).sum()} groups")
print(f"  size>=2 (clustered): {(group_sizes >= 2).sum()} groups")
print(f"  max cluster size   : {group_sizes.max()}")

date_rel_rows = df[is_date_rel].copy()
date_rel_rows["rel_date_prefix"] = date_rel_rows["rel_id"].astype(str).str[:8]
date_rel_rows["pub_date_clean"] = date_rel_rows["pub_date"].str.replace("-", "")
mismatch = (date_rel_rows["rel_date_prefix"] != date_rel_rows["pub_date_clean"]).sum()
print(f"\n  date-prefix != pub_date: {mismatch} / {len(date_rel_rows)}")

# ── Charts (all English labels) ──
plt.rcParams["font.family"] = "DejaVu Sans"

daily_sent = df.groupby(["pub_date_parsed", "sentiment_en"]).size().unstack(fill_value=0)
top_30_dates = daily_sent.sort_index().tail(30)

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

sent_en = df["sentiment_en"].value_counts()
sent_en.plot.pie(
    ax=axes[0, 0], autopct="%.1f%%", startangle=90,
    colors=["#4CAF50", "#FFC107", "#f44336"]
)
axes[0, 0].set_title("Sentiment Distribution")
axes[0, 0].set_ylabel("")

sns.histplot(df["sentiment_score"], bins=50, ax=axes[0, 1])
axes[0, 1].set_title("Sentiment Score Distribution")
axes[0, 1].set_xlabel("score")

top_30_dates.plot(
    ax=axes[1, 0], kind="line", marker="o", markersize=3,
    color=["#4CAF50", "#FFC107", "#f44336"]
)
axes[1, 0].set_title("Daily Sentiment Volume (last 30 days)")
axes[1, 0].set_xlabel("date")
axes[1, 0].set_ylabel("count")

top10 = df["pub_code"].value_counts().head(10)
top10.plot.bar(ax=axes[1, 1])
axes[1, 1].set_title("Top 10 Sources (pub_code)")
axes[1, 1].set_xlabel("")
axes[1, 1].tick_params(axis="x", rotation=45)

plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/overview.png", dpi=150)
print(f"  saved: {OUTPUT_DIR}/overview.png")

# ── Content length ──
df["content_len"] = df["content"].astype(str).str.len()
print(f"\n=== Content length (chars) ===")
print(df["content_len"].describe().to_string())

fig, ax = plt.subplots(figsize=(10, 5))
order = ["positive", "neutral", "negative"]
sns.boxplot(data=df, x="sentiment_en", y="content_len",
            hue="sentiment_en", palette=["#4CAF50", "#FFC107", "#f44336"],
            order=order, ax=ax, legend=False)
ax.set_title("Content Length by Sentiment")
ax.set_xlabel("sentiment")
ax.set_ylabel("content length (chars)")
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/content_length_by_sentiment.png", dpi=150)
print(f"  saved: {OUTPUT_DIR}/content_length_by_sentiment.png")

print("\n=== EDA done ===")
