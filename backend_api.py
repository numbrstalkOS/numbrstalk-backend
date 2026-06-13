"""
Numbrstalk.com - Commerce Diagnosis Engine v14.0
Data Trust & Demo Polish: Clean brands, actionable alerts, business language
+ Confidence Upgrade, Data Readiness, Context-Aware Wording
"""
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv, io, json, os, re
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from collections import defaultdict

DATA_DIR = Path("data")

app = FastAPI(title="Numbrstalk Diagnosis Engine", version="14.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

main_data, change_data, insight_data = [], [], []
last_refresh = None

class ChatRequest(BaseModel):
    question: str = ""
    message: str = ""
    category: Optional[str] = None
    history: Optional[list] = None

def load_json(fp):
    if not fp.exists():
        return []
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []

def refresh():
    global main_data, change_data, insight_data, last_refresh
    main_data = load_json(DATA_DIR / "main.json")
    change_data = load_json(DATA_DIR / "change_detection.json")
    insight_data = load_json(DATA_DIR / "ai_insight.json")
    last_refresh = datetime.now()

refresh()

# ============================================================================
# BRAND NORMALIZATION LAYER (Improvement 1)
# ============================================================================

BRAND_CORRECTIONS = {
    "the": "The Baker's Dozen",
    "hide": "Hide & Seek",
    "bisk": "Bisk Farm",
    "open": "Open Secret",
    "karachi bakery": "Karachi Bakery",
    "britannia": "Britannia",
    "sunfeast": "Sunfeast",
    "parle": "Parle",
    "malkist": "Malkist",
    "oreo": "Oreo",
    "cadbury": "Cadbury",
    "nestle": "Nestlé",
    "tata": "Tata",
    "amul": "Amul",
    "mother dairy": "Mother Dairy",
    "dabur": "Dabur",
    "himalaya": "Himalaya",
    "lakme": "Lakmé",
    "maybelline": "Maybelline",
    "loreal": "L'Oréal",
    "nykaa": "Nykaa",
    "ponds": "Pond's",
    "nivea": "Nivea",
    "dove": "Dove",
    "my": "",
    "out": "",
    "add": "",
    "view": "",
    "login": "",
    "search": "",
}

FAKE_PRODUCTS = [
    "my cart", "out of stock", "add to cart", "view cart",
    "login", "search", "checkout", "buy now", "wishlist",
    "account", "sign in", "register", "forgot password",
    "delivery", "payment",
]

def normalize_brand(brand_name: str) -> str:
    """Clean and normalize brand names."""
    if not brand_name:
        return "Unknown"

    brand_lower = brand_name.strip().lower()

    # Exact match first
    if brand_lower in BRAND_CORRECTIONS:
        corrected = BRAND_CORRECTIONS[brand_lower]
        return corrected if corrected else "Unknown"

    # Partial matches
    for key, value in BRAND_CORRECTIONS.items():
        if key in brand_lower and key != brand_lower and len(brand_name) < 20:
            return value if value else "Unknown"

    return brand_name.strip()


def is_fake_product(product_name: str) -> bool:
    """Filter out UI text scraped as products."""
    if not product_name:
        return True

    name_lower = product_name.strip().lower()

    if any(fake in name_lower for fake in FAKE_PRODUCTS):
        return True

    if len(name_lower) < 3 or name_lower.isdigit():
        return True

    return False


def parse_discount(v):
    try:
        return float(
            str(v)
            .replace("% OFF", "")
            .replace("%", "")
            .replace("OFF", "")
            .strip()
        )
    except:
        return 0


def safe_float(v, default=0):
    try:
        return float(
            str(v).replace("%", "").replace("₹", "").replace(",", "").strip()
        )
    except:
        return default


# ============================================================================
# BUSINESS LANGUAGE MAP (Improvement 4)
# ============================================================================

BUSINESS_LANGUAGE = {
    "Promo Over-intensity": "Heavy competitor discounting",
    "Visibility Suppression": "Search visibility drop",
    "Algorithm discount audit penalty": "Possible listing/discount eligibility issue",
    "Margin Leak": "Profit margin pressure",
    "Inventory Leak": "Stock availability gap",
    "Search Rank Suppression": "Search ranking decline",
    "Critical Rank Drop": "Significant rank decline",
    "Rank Slipping": "Gradual rank decline",
    "Product Disappeared": "Product removed from rankings",
    "New Competitor Entry": "New competitor detected",
    "Deep Discount Alert": "Heavy discounting detected",
    "Stock Out": "Out of stock",
    "Ranking Dropped": "Rank decline detected",
    "Visibility Lost": "Lost search visibility",
}


def simplify_language(text: str) -> str:
    """Replace technical terms with business-friendly language."""
    for old, new in BUSINESS_LANGUAGE.items():
        text = text.replace(old, new)
    return text


# ============================================================================
# CATEGORY MAPPING (Improvement 6)
# ============================================================================

CATEGORY_MAPPING = {
    "biscuit": "Biscuits",
    "cookie": "Cookies",
    "rusk": "Rusk",
    "khari": "Khari",
    "cracker": "Crackers",
    "cream biscuit": "Cream Biscuits",
    "healthy": "Healthy Snacks",
    "bakery": "Bakery Snacks",
    "bread": "Bread",
    "cake": "Cake",
    "toast": "Toast",
    "snack": "Snacks",
    "chip": "Chips & Namkeen",
    "namkeen": "Chips & Namkeen",
    "drink": "Drinks & Juices",
    "juice": "Drinks & Juices",
    "water": "Drinks & Juices",
    "beauty": "Beauty & Cosmetics",
    "cosmetic": "Beauty & Cosmetics",
    "lipstick": "Beauty & Cosmetics",
    "cream": "Beauty & Cosmetics",
    "lotion": "Beauty & Cosmetics",
    "makeup": "Beauty & Cosmetics",
}


def map_category(keyword: str) -> str:
    """Map keywords to standard categories."""
    kw = keyword.lower().strip()
    for key, cat in CATEGORY_MAPPING.items():
        if key in kw:
            return cat
    return "General"


# ============================================================================
# DATA READINESS & CONFIDENCE (Improvement 8)
# ============================================================================

READINESS_WEIGHTS = {
    "price_sheet": 20,
    "index_scrape": 15,
    "ads_report": 20,
    "cost_matrix": 10,
    "inventory_feed": 15,
    "competitor_pricing": 10,
    "margin_sheet": 10,
}


def calculate_readiness(available_reports: list) -> dict:
    """Calculate confidence score based on uploaded reports."""
    score = 0
    available = set(available_reports)
    report_status = {}

    for report, weight in READINESS_WEIGHTS.items():
        if report in available:
            score += weight
            report_status[report] = {
                "status": "uploaded",
                "contribution": f"+{weight}%",
            }
        else:
            report_status[report] = {
                "status": "missing",
                "contribution": f"+{weight}% potential",
            }

    level = "High" if score >= 80 else "Medium" if score >= 50 else "Low"

    missing = [r for r in READINESS_WEIGHTS if r not in available]
    next_best = (
        max(missing, key=lambda r: READINESS_WEIGHTS[r]) if missing else None
    )

    return {
        "confidence_score": score,
        "confidence_level": level,
        "report_status": report_status,
        "missing_reports": missing,
        "next_best_upload": next_best,
    }


def get_context_pronoun(has_uploaded: bool = False) -> dict:
    """Return context-appropriate wording (Improvement: Your/Our fix)."""
    if has_uploaded:
        return {
            "possessive": "Your",
            "brand_label": "Your Brand",
            "action_verb": "Your",
        }
    return {
        "possessive": "Selected",
        "brand_label": "Tracked Brand",
        "action_verb": "The",
    }


# ============================================================================
# RANK MOVEMENT FORMATTER (Improvement 2)
# ============================================================================

def format_rank_movement(old_rank, new_rank) -> str:
    """Format rank change in human-readable form."""
    try:
        old = int(old_rank) if str(old_rank).replace("-", "").isdigit() else 0
        new = int(new_rank) if str(new_rank).replace("-", "").isdigit() else 0

        if old == 0 and new > 0:
            return f"Entered at #{new}"
        if old > 0 and new == 0:
            return f"Dropped out from #{old}"
        if old > 0 and new > 0:
            diff = new - old
            if diff > 0:
                return f"#{old} → #{new}, dropped {diff} positions"
            elif diff < 0:
                return f"#{old} → #{new}, improved {abs(diff)} positions"
            else:
                return f"#{old} → #{new}, no change"
        return f"{old_rank} → {new_rank}"
    except:
        return f"{old_rank} → {new_rank}"


# ============================================================================
# DATA HELPERS
# ============================================================================

def get_products(category=None):
    data = main_data
    if category:
        data = [
            r
            for r in main_data
            if str(r.get("category", "")).strip().lower() == str(category).lower()
        ]

    prods = {}
    for r in data:
        n = str(r.get("product_name", "")).strip()
        if is_fake_product(n):
            continue
        if n and n not in prods:
            prods[n] = {
                "id": str(abs(hash(n)) % 100000),
                "name": n,
                "brand": normalize_brand(str(r.get("brand", ""))),
                "price": safe_float(r.get("price")),
                "discount": parse_discount(r.get("discount", "")),
                "platform": str(r.get("platform", "")),
                "city": str(r.get("city", "")),
                "category": map_category(str(r.get("keyword", ""))),
                "stock_status": str(r.get("stock_status", "")),
            }
    return list(prods.values())


def get_top_brands(limit=5, category=None):
    bd = defaultdict(list)
    for r in main_data:
        if category and str(r.get("category", "")).strip().lower() != category.lower():
            continue
        b = normalize_brand(str(r.get("brand", "")))
        d = parse_discount(r.get("discount", ""))
        if b and b != "Unknown" and d > 0 and len(b) > 2:
            bd[b].append(d)

    res = [
        {
            "brand": b,
            "avg_discount": round(sum(ds) / len(ds), 1),
            "product_count": len(ds),
        }
        for b, ds in bd.items()
        if len(ds) >= 3
    ]
    res.sort(key=lambda x: (x["product_count"], x["avg_discount"]), reverse=True)
    return (
        res[:limit]
        if res
        else [{"brand": "No data", "avg_discount": 0, "product_count": 0}]
    )


def parse_changes(category=None):
    data = change_data
    if category:
        data = [
            r
            for r in data
            if str(r.get("keyword", "")).strip().lower() == category.lower()
        ]

    result = {
        "total_changes": len(data),
        "change_types": [],
        "keywords": [],
        "locations": [],
        "severity": {"Critical": 0, "High": 0, "Medium": 0, "Low": 0},
        "rank_drops": [],
        "rank_improvements": [],
        "new_entries": [],
        "disappeared": [],
        "platforms": [],
    }

    tc, kc, lc = defaultdict(int), defaultdict(int), defaultdict(int)

    for row in data:
        issue = str(row.get("issue_type", "")).strip()
        sev = str(row.get("severity", "")).strip()
        kw = str(row.get("keyword", "")).strip()
        loc = str(row.get("location", "")).strip()
        plat = str(row.get("platform", "")).strip()
        prod = str(row.get("product_name", "")).strip()
        old_r = str(row.get("old_rank", ""))
        new_r = str(row.get("new_rank", ""))

        if issue:
            tc[issue] += 1
        if kw:
            kc[kw] += 1
        if loc:
            lc[loc] += 1
        if sev in result["severity"]:
            result["severity"][sev] += 1

        try:
            old = int(old_r) if old_r.replace("-", "").isdigit() else 0
            new = int(new_r) if new_r.replace("-", "").isdigit() else 0
            movement = format_rank_movement(old_r, new_r)
            if old > 0 and new > 0:
                if new > old:
                    result["rank_drops"].append(
                        {
                            "product": prod,
                            "old_rank": old,
                            "new_rank": new,
                            "movement": movement,
                            "keyword": kw,
                            "location": loc,
                            "platform": plat,
                        }
                    )
                elif new < old:
                    result["rank_improvements"].append(
                        {
                            "product": prod,
                            "old_rank": old,
                            "new_rank": new,
                            "movement": movement,
                            "keyword": kw,
                            "location": loc,
                            "platform": plat,
                        }
                    )
        except:
            pass

        if old_r in ["0", "", "-"] and new_r not in ["0", "", "-", "?"]:
            result["new_entries"].append(
                {
                    "product": prod,
                    "new_rank": new_r,
                    "keyword": kw,
                    "location": loc,
                    "platform": plat,
                }
            )
        if new_r in ["0", "", "-"] and old_r not in ["0", "", "-", "?"]:
            result["disappeared"].append(
                {
                    "product": prod,
                    "old_rank": old_r,
                    "keyword": kw,
                    "location": loc,
                    "platform": plat,
                }
            )

    # Top 10 change types
    for t, c in sorted(tc.items(), key=lambda x: x[1], reverse=True)[:10]:
        result["change_types"].append({"type": simplify_language(t), "count": c})

    # Top 10 keywords
    for k, c in sorted(kc.items(), key=lambda x: x[1], reverse=True)[:10]:
        result["keywords"].append({"keyword": k, "changes": c})

    # Top 10 locations
    for l, c in sorted(lc.items(), key=lambda x: x[1], reverse=True)[:10]:
        result["locations"].append({"location": l, "changes": c})

    return result


# ============================================================================
# ALERT GENERATOR WITH DEDUPLICATION (Improvement 3)
# ============================================================================

def _generate_raw_alerts(category=None, uploaded_reports=None):
    if uploaded_reports is None:
        uploaded_reports = []

    readiness = calculate_readiness(uploaded_reports)
    context = get_context_pronoun(len(uploaded_reports) > 0)

    alerts = []
    changes = parse_changes(category)

    for drop in changes.get("rank_drops", []):
        try:
            old_r = int(drop.get("old_rank", 0))
            new_r = int(drop.get("new_rank", 0))
            diff = new_r - old_r

            if diff >= 5:
                keyword = drop.get("keyword", "")
                location = drop.get("location", "Bangalore")
                product = drop.get("product", "")
                review_date = (datetime.now() + timedelta(days=3)).strftime(
                    "%B %d, %Y"
                )

                alerts.append(
                    {
                        "id": f"alt_{abs(hash(product)) % 100000}",
                        "platform": drop.get("platform", "Blinkit"),
                        "city": location,
                        "sku": product,
                        "category": keyword,
                        "issue": simplify_language(
                            "Critical Rank Drop" if diff >= 10 else "Rank Slipping"
                        ),
                        "leak_stage": "Visibility",
                        "reason_bucket": simplify_language("Competitor Issue"),
                        "priority": "High" if diff >= 10 else "Medium",
                        "confidence": readiness["confidence_level"],
                        "confidence_score": readiness["confidence_score"],
                        "confirmed_evidence": f"{format_rank_movement(old_r, new_r)} in {location} for '{keyword}'.",
                        "likely_cause": f"{context['possessive']} competitor may have increased visibility or discounting.",
                        "recommended_action": f"Compare {context['possessive'].lower()} pricing for '{keyword}' in {location}.",
                        "review_date": review_date,
                        "success_signal": f"{context['possessive']} rank returns to top 5",
                        "impact": "High" if diff >= 10 else "Medium",
                        "detected_at": datetime.now().isoformat(),
                        "status": "Pending",
                        "readiness_score": readiness["confidence_score"],
                        "readiness_level": readiness["confidence_level"],
                        "missing_data": readiness["missing_reports"][:3],
                    }
                )
        except:
            pass

    return alerts


def generate_alerts(category=None, uploaded_reports=None):
    raw = _generate_raw_alerts(category, uploaded_reports)

    # Deduplicate by product + city + category + issue
    grouped = {}
    for alert in raw:
        key = f"{alert.get('sku','')}|{alert.get('city','')}|{alert.get('category','')}|{alert.get('issue','')}"
        if key not in grouped:
            grouped[key] = alert
            grouped[key]["occurrences"] = 1
        else:
            grouped[key]["occurrences"] += 1

    alerts = list(grouped.values())

    # Sort by priority then occurrence count
    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    alerts.sort(
        key=lambda x: (
            priority_order.get(x.get("priority", "Low"), 2),
            -x.get("occurrences", 1),
        )
    )

    return alerts[:25]


def get_alert_summary():
    raw = _generate_raw_alerts()
    actionable = generate_alerts()

    return {
        "raw_signals": len(raw),
        "actionable_alerts": len(actionable),
        "critical_active": len(
            [a for a in actionable if a.get("priority") == "High"]
        ),
        "high_priority": len(
            [a for a in actionable if a.get("priority") == "Medium"]
        ),
        "watchlist": len([a for a in actionable if a.get("priority") == "Low"]),
    }


# ============================================================================
# DEMO REPORT (Improvement 10)
# ============================================================================

@app.get("/api/demo/report")
async def demo_report():
    return {
        "category": "Bakery & Biscuits",
        "location": "Bangalore",
        "platform": "Blinkit",
        "issue": "Significant rank decline detected",
        "leak_stage": "Search visibility drop",
        "reason_bucket": "Heavy competitor discounting",
        "confidence": "High (85%)",
        "confirmed_evidence": [
            "Britannia Marie Gold dropped from #3 to #9 in HSR Layout — lost 6 positions",
            "Competitor Sunfeast increased discount from 12% to 28% on same keyword",
            "Search visibility declined 15% week-over-week",
        ],
        "likely_cause": "Sunfeast launched aggressive discounting on Blinkit Bangalore, capturing organic search visibility from Britannia.",
        "what_to_check_first": [
            "Compare your discount depth vs Sunfeast on 'biscuits' keyword",
            "Check stock availability in HSR Layout darkstores",
            "Review sponsored ad visibility for top 5 keywords",
        ],
        "recommended_action": "Do not match the discount. Launch a value combo (Marie Gold + Tea) at same price point to increase perceived value.",
        "expected_impact": "If combo launched, can recover 40% of lost visibility within 7 days without eroding margins.",
        "review_date": (datetime.now() + timedelta(days=3)).strftime("%B %d, %Y"),
        "success_signal": "Rank returns to top 5 for 'biscuits' in HSR Layout",
        "readiness_score": 85,
        "missing_data": ["Ad spend report", "Margin sheet for Marie Gold"],
        "auto_tasks": [
            "Check Sunfeast current pricing on Blinkit",
            "Verify Marie Gold stock in HSR Layout",
            "Draft combo offer proposal",
            "Review ad bids for 'biscuits' keyword",
        ],
    }


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "14.0",
        "data_rows": len(main_data),
        "change_rows": len(change_data),
    }


@app.get("/api/categories")
async def categories():
    return {
        "categories": sorted(
            set(
                str(r.get("category", "")).strip()
                for r in main_data
                if str(r.get("category", "")).strip()
            )
        )
    }


@app.get("/api/dashboard")
async def dashboard(category: Optional[str] = None):
    return {
        "totalProducts": len(get_products(category)),
        "lastUpdated": str(last_refresh),
        "alertSummary": get_alert_summary(),
    }


@app.get("/api/products")
async def products(category: Optional[str] = None):
    prods = get_products(category)
    return {"products": prods[:100], "count": len(prods)}


@app.get("/api/changes")
async def changes(category: Optional[str] = None):
    return parse_changes(category)


@app.get("/api/insights/ai")
async def insights_ai(category: Optional[str] = None):
    c = parse_changes(category)

    if c["total_changes"] == 0:
        return {
            "headline": "📭 No changes",
            "summary": "No data.",
            "key_metrics": {
                "total_changes": 0,
                "critical_alerts": 0,
                "top_location": "N/A",
                "main_risk": "N/A",
            },
            "critical_issues": [],
            "actions": [],
        }

    tl = c["locations"][0] if c["locations"] else {"location": "N/A", "changes": 0}

    return {
        "headline": f"📊 {c['total_changes']:,} marketplace changes",
        "summary": f"{c['total_changes']:,} changes. {tl['location']} leads.",
        "key_metrics": {
            "total_changes": c["total_changes"],
            "critical_alerts": c["severity"]["Critical"],
            "top_location": tl["location"],
            "main_risk": c["change_types"][0]["type"] if c["change_types"] else "N/A",
        },
        "critical_issues": [
            f"{c['change_types'][0]['type'] if c['change_types'] else 'N/A'}: {c['change_types'][0]['count'] if c['change_types'] else 0}"
        ],
        "actions": [{"priority": "High", "action": f"Audit {tl['location']}"}],
    }


@app.get("/api/top-brands")
async def top_brands(limit: int = 5, category: Optional[str] = None):
    return {"brands": get_top_brands(limit, category)}


@app.get("/api/alerts")
async def alerts(
    category: Optional[str] = None,
    uploaded: Optional[str] = Query(None),
):
    available = [r.strip() for r in uploaded.split(",")] if uploaded else []
    a = generate_alerts(category, available)
    readiness = calculate_readiness(available)
    context = get_context_pronoun(len(available) > 0)

    return {
        "alerts": a,
        "count": len(a),
        "summary": get_alert_summary(),
        "readiness": readiness,
        "context": context,
    }


@app.get("/api/actions/summary")
async def action_summary(
    category: Optional[str] = None,
    uploaded: Optional[str] = Query(None),
):
    available = [r.strip() for r in uploaded.split(",")] if uploaded else []
    a = generate_alerts(category, available)
    high = [x for x in a if x["priority"] == "High"]
    return {"total_alerts": len(a), "high_priority": len(high), "alerts": a[:15]}


@app.get("/api/reports/generate")
async def report():
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Product", "Brand", "Price", "Discount", "Platform", "City"])
    for p in get_products()[:500]:
        w.writerow(
            [p["name"], p["brand"], p["price"], p["discount"], p["platform"], p["city"]]
        )
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=numbrstalk_report.csv"},
    )


@app.get("/api/confidence/upgrade")
async def confidence_upgrade(uploaded: Optional[str] = Query(None)):
    """
    Returns confidence upgrade path for the Confidence Upgrade tab.
    Pass comma-separated report types: ?uploaded=price_sheet,index_scrape
    """
    available = [r.strip() for r in uploaded.split(",")] if uploaded else []
    readiness = calculate_readiness(available)
    context = get_context_pronoun(len(available) > 0)

    checklist = []
    for report, weight in READINESS_WEIGHTS.items():
        is_done = report in available
        checklist.append(
            {
                "report": report.replace("_", " ").title(),
                "weight": weight,
                "done": is_done,
                "label": f"{'✅' if is_done else '❌'} {report.replace('_', ' ').title()} ({'+' + str(weight) + '%' if is_done else '+' + str(weight) + '% potential'})",
            }
        )

    return {
        "current_confidence": readiness["confidence_score"],
        "confidence_level": readiness["confidence_level"],
        "possessive": context["possessive"],
        "brand_label": context["brand_label"],
        "checklist": checklist,
        "next_best_upload": readiness["next_best_upload"],
        "next_best_label": readiness["next_best_upload"].replace("_", " ").title()
        if readiness["next_best_upload"]
        else "All reports uploaded",
        "upload_remaining": len(readiness["missing_reports"]),
    }


INTENT_KEYWORDS = {
    "biscuit": ["biscuit", "biscuits", "bakery", "cookie", "cookies", "rusk", "khari", "cream biscuits", "cracker", "bread", "cake", "toast"],
    "biscuits": ["biscuit", "biscuits", "bakery", "cookie", "cookies", "rusk", "khari", "cream biscuits", "cracker", "bread", "cake", "toast"],
    "bakery": ["biscuit", "biscuits", "bakery", "cookie", "cookies", "rusk", "khari", "cream biscuits", "cracker", "bread", "cake", "toast"],
    "cookie": ["cookie", "cookies", "biscuit", "biscuits", "bakery", "cream biscuits"],
    "cookies": ["cookie", "cookies", "biscuit", "biscuits", "bakery", "cream biscuits"],
    "rusk": ["rusk", "bakery", "biscuit", "biscuits"],
    "khari": ["khari", "bakery", "biscuit", "biscuits"],

    "shampoo": ["shampoo", "anti dandruff shampoo", "hair care", "head & shoulders", "loreal"],
    "agarbatti": ["agarbatti", "incense", "incense sticks", "spiritual needs"],
    "cotton": ["cotton wicks", "diya batti", "spiritual needs"],
    "wick": ["cotton wicks", "diya batti", "spiritual needs"],
    "wicks": ["cotton wicks", "diya batti", "spiritual needs"],
}


def find_relevant_alerts(user_text: str, alerts: list) -> list:
    text = user_text.lower()

    search_terms = []

    for trigger, terms in INTENT_KEYWORDS.items():
        if trigger in text:
            search_terms.extend(terms)

    if not search_terms:
        search_terms = [w for w in re.findall(r"[a-zA-Z0-9&]+", text) if len(w) >= 4]

    matched = []

    for alert in alerts:
        searchable = " ".join([
            str(alert.get("category", "")),
            str(alert.get("sku", "")),
            str(alert.get("confirmed_evidence", "")),
            str(alert.get("issue", "")),
            str(alert.get("likely_cause", "")),
            str(alert.get("city", "")),
            str(alert.get("platform", "")),
        ]).lower()

        if any(term.lower() in searchable for term in search_terms):
            matched.append(alert)

    return matched


@app.post("/api/chat")
async def chat(request: ChatRequest):
    user_text = (request.message or request.question or "").strip().lower()

    if not user_text:
        return {
            "answer": "I'm Lilly Commerce Assistant. Ask me about marketplace performance, competitor activity, rankings, pricing, stock, or upload data for a full diagnosis."
        }

    alerts = generate_alerts()
    high = [x for x in alerts if x.get("priority") == "High"]

    relevant_alerts = find_relevant_alerts(user_text, alerts)
    relevant_high = [x for x in relevant_alerts if x.get("priority") == "High"]

    selected_alerts = relevant_high or relevant_alerts or high or alerts

    if selected_alerts:
        top_alert = selected_alerts[0]

        return {
            "answer": (
                f"{top_alert.get('issue', 'Marketplace issue detected')}: "
                f"{top_alert.get('confirmed_evidence', 'A ranking or visibility change was detected')} "
                f"Likely cause: {top_alert.get('likely_cause', 'Competitor movement or marketplace visibility change')}. "
                f"Recommended action: {top_alert.get('recommended_action', 'Review pricing, visibility, and stock for this location')}. "
                f"Confidence: {top_alert.get('confidence_score', 0)}%. "
                f"Location: {top_alert.get('city', 'Not specified')}."
            )
        }

    return {
        "answer": "I checked the current marketplace signals, but I could not find a matching alert for this question. Try asking by category, product, city, or keyword."
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
