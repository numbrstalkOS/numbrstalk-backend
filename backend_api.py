"""
Numbrstalk.com - Commerce Diagnosis Engine v11.0
Leak Stage → Reason Bucket → Evidence → Comparison → Likely Cause → Missing Data → Impact → Follow-up
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

app = FastAPI(title="Numbrstalk Diagnosis Engine", version="11.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

main_data, change_data, insight_data = [], [], []
last_refresh = None

class ChatRequest(BaseModel):
    question: str

def load_json(fp):
    if not fp.exists(): return []
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

def parse_discount(v):
    try: return float(str(v).replace('% OFF','').replace('%','').replace('OFF','').strip())
    except: return 0

def safe_float(v, default=0):
    try: return float(str(v).replace('%','').replace('₹','').replace(',','').strip())
    except: return default

def get_products(category=None):
    data = [r for r in main_data if str(r.get('category','')).strip().lower() == str(category).lower()] if category else main_data
    prods = {}
    for r in data:
        n = str(r.get('product_name','')).strip()
        if n and n not in prods:
            prods[n] = {"id":str(abs(hash(n))%100000),"name":n,"brand":str(r.get('brand','')),"price":safe_float(r.get('price')),"discount":parse_discount(r.get('discount','')),"platform":str(r.get('platform','')),"city":str(r.get('city','')),"stock_status":str(r.get('stock_status',''))}
    return list(prods.values())

def get_top_brands(limit=5, category=None):
    bd = defaultdict(list)
    for r in main_data:
        if category and str(r.get('category','')).strip().lower() != category.lower(): continue
        b = str(r.get('brand','')).strip()
        d = parse_discount(r.get('discount',''))
        if b and d > 0 and len(b) > 2: bd[b].append(d)
    res = [{"brand":b,"avg_discount":round(sum(ds)/len(ds),1),"product_count":len(ds)} for b,ds in bd.items() if len(ds)>=3]
    res.sort(key=lambda x:(x['product_count'],x['avg_discount']), reverse=True)
    return res[:limit] if res else [{"brand":"No data","avg_discount":0,"product_count":0}]

def parse_changes(category=None):
    data = change_data
    if category: data = [r for r in data if str(r.get('keyword','')).strip().lower() == category.lower()]
    result = {"total_changes":len(data),"change_types":[],"keywords":[],"locations":[],"severity":{"Critical":0,"High":0,"Medium":0,"Low":0},"rank_drops":[],"rank_improvements":[],"new_entries":[],"disappeared":[],"platforms":[]}
    tc,kc,lc = defaultdict(int),defaultdict(int),defaultdict(int)
    for row in data:
        issue=str(row.get('issue_type','')).strip(); sev=str(row.get('severity','')).strip()
        kw=str(row.get('keyword','')).strip(); loc=str(row.get('location','')).strip()
        plat=str(row.get('platform','')).strip(); prod=str(row.get('product_name','')).strip()
        old_r=str(row.get('old_rank','')); new_r=str(row.get('new_rank',''))
        if issue: tc[issue]+=1
        if kw: kc[kw]+=1
        if loc: lc[loc]+=1
        if sev in result['severity']: result['severity'][sev]+=1
        try:
            old=int(old_r) if old_r.replace('-','').isdigit() else 0
            new=int(new_r) if new_r.replace('-','').isdigit() else 0
            if old>0 and new>0:
                if new>old: result['rank_drops'].append({"product":prod,"old_rank":old,"new_rank":new,"keyword":kw,"location":loc,"platform":plat})
                elif new<old: result['rank_improvements'].append({"product":prod,"old_rank":old,"new_rank":new,"keyword":kw,"location":loc,"platform":plat})
        except: pass
        if old_r in ['0','','-'] and new_r not in ['0','','-','?']: result['new_entries'].append({"product":prod,"new_rank":new_r,"keyword":kw,"location":loc,"platform":plat})
        if new_r in ['0','','-'] and old_r not in ['0','','-','?']: result['disappeared'].append({"product":prod,"old_rank":old_r,"keyword":kw,"location":loc,"platform":plat})
    for t,c in sorted(tc.items(),key=lambda x:x[1],reverse=True)[:10]: result['change_types'].append({"type":t,"count":c})
    for k,c in sorted(kc.items(),key=lambda x:x[1],reverse=True)[:10]: result['keywords'].append({"keyword":k,"changes":c})
    for l,c in sorted(lc.items(),key=lambda x:x[1],reverse=True)[:10]: result['locations'].append({"location":l,"changes":c})
    return result

# ============================================================================
# DIAGNOSIS ENGINE v11.0 — Full comparison + likely vs confirmed + missing data
# ============================================================================

def get_category_avg_rank(keyword):
    """Get average rank for a keyword category."""
    ranks = []
    for r in change_data:
        if str(r.get('keyword','')).strip().lower() == keyword.lower():
            try: ranks.append(int(r.get('new_rank',0)))
            except: pass
    return round(sum(ranks)/len(ranks),1) if ranks else 0

def generate_alerts(category=None):
    """Generate alerts with v11.0 diagnosis: Comparison, Confirmed/Likely, Missing Data, Impact, Follow-up."""
    alerts = []
    changes = parse_changes(category)
    products = get_products(category)
    
    for drop in changes.get('rank_drops', []):
        try:
            old_r = int(drop.get('old_rank', 0))
            new_r = int(drop.get('new_rank', 0))
            diff = new_r - old_r
            if diff >= 5:
                keyword = drop.get('keyword','')
                location = drop.get('location','Bangalore')
                product = drop.get('product','')
                platform = drop.get('platform','Blinkit')
                cat_avg = get_category_avg_rank(keyword)
                review_date = (datetime.now() + timedelta(days=3)).strftime('%B %d, %Y')
                
                alerts.append({
                    "id": f"alt_{abs(hash(product))%100000}",
                    "platform": platform,
                    "city": location,
                    "sku": product,
                    "category": keyword,
                    "issue": "Critical Rank Drop" if diff >= 10 else "Rank Slipping",
                    
                    # Core diagnosis
                    "leak_stage": "Visibility",
                    "reason_bucket": "Competitor Issue" if diff >= 8 else "Visibility Issue",
                    "priority": "High" if diff >= 10 else "Medium",
                    "confidence": "High" if cat_avg > 0 else "Medium",
                    
                    # Confirmed evidence vs likely cause
                    "confirmed_evidence": f"Rank dropped from #{old_r} to #{new_r} ({diff} positions lost) in {location} for '{keyword}'.",
                    "likely_cause": "Competitor visibility or offer pressure. A competing product may have gained rank, pushing this SKU down.",
                    "missing_data_needed": [
                        "Competitor pricing and discount data for this keyword",
                        "Sales/orders report to confirm revenue impact",
                        "Ad spend and visibility data to check if paid visibility changed"
                    ],
                    
                    # Comparison
                    "compared_against": f"Category average rank for '{keyword}' is #{cat_avg}. Your product was at #{old_r}, now at #{new_r}.",
                    
                    # Action plan
                    "what_to_check_first": [
                        f"Compare your price and discount vs top 5 competitors in '{keyword}'",
                        f"Check stock availability in {location}",
                        "Review sponsored ad visibility and keyword bids"
                    ],
                    "recommended_action": f"Compare pricing for '{keyword}' in {location}. If your discount is below category average, consider a tactical offer.",
                    "expected_impact": "If discount gap is confirmed, adjusting offer depth may recover rank within 3-5 days.",
                    
                    # Follow-up
                    "review_date": review_date,
                    "success_signal": f"Rank returns to top 5 for '{keyword}'",
                    "if_not_improved": "Increase ad visibility or launch a limited-time bundle offer",
                    
                    # Metadata
                    "impact": "High" if diff >= 10 else "Medium",
                    "detected_at": datetime.now().isoformat(),
                    "status": "Pending"
                })
        except: pass
    
    for d in changes.get('disappeared', [])[:3]:
        product = d.get('product','')
        keyword = d.get('keyword','')
        location = d.get('location','Bangalore')
        review_date = (datetime.now() + timedelta(days=2)).strftime('%B %d, %Y')
        
        alerts.append({
            "id": f"alt_{abs(hash(product))%100000}",
            "platform": d.get('platform','Blinkit'),
            "city": location,
            "sku": product,
            "category": keyword,
            "issue": "Product Disappeared",
            "leak_stage": "Visibility",
            "reason_bucket": "Competitor Issue",
            "priority": "High",
            "confidence": "High",
            "confirmed_evidence": f"'{product}' vanished from top 30 rankings in {location} for '{keyword}'.",
            "likely_cause": "Product may be out of stock, delisted, or pushed out by competitor entries.",
            "missing_data_needed": ["Stock status confirmation", "Listing status on platform", "Competitor new entry data"],
            "compared_against": f"Previous ranking position in top 30 for '{keyword}' category.",
            "what_to_check_first": ["Verify stock availability immediately", "Check if listing is active on the platform", "Look for new competitor products in this category"],
            "recommended_action": "Check stock status and listing visibility urgently. If delisted, contact platform support.",
            "expected_impact": "Restoring visibility can recover ranking within 24-48 hours if stock is available.",
            "review_date": review_date,
            "success_signal": "Product reappears in top 30 rankings",
            "if_not_improved": "Investigate competitor entries and consider ad visibility boost",
            "impact": "High",
            "detected_at": datetime.now().isoformat(),
            "status": "Pending"
        })
    
    for entry in changes.get('new_entries', [])[:2]:
        product = entry.get('product','')
        keyword = entry.get('keyword','')
        review_date = (datetime.now() + timedelta(days=3)).strftime('%B %d, %Y')
        
        alerts.append({
            "id": f"alt_{abs(hash(product))%100000}",
            "platform": entry.get('platform','Blinkit'),
            "city": entry.get('location','Bangalore'),
            "sku": product,
            "category": keyword,
            "issue": "New Competitor Entry",
            "leak_stage": "Competitor Pressure",
            "reason_bucket": "Competitor Issue",
            "priority": "Medium",
            "confidence": "Medium",
            "confirmed_evidence": f"'{product}' entered top 30 at rank #{entry.get('new_rank','?')} in '{keyword}'.",
            "likely_cause": "New competitor launching in this category. May affect existing product visibility and pricing.",
            "missing_data_needed": ["New entrant's pricing strategy", "Their discount and offer structure", "Their stock availability"],
            "compared_against": "Existing top 30 competitors in this category.",
            "what_to_check_first": ["Track new entrant's price and discount for 48 hours", "Monitor if they gain further rank", "Check if they're running promotional offers"],
            "recommended_action": "Monitor this competitor for 48 hours. Prepare a response if they gain significant traction.",
            "expected_impact": "Early detection allows proactive response before market share is affected.",
            "review_date": review_date,
            "success_signal": "New entrant's rank stabilizes or declines",
            "if_not_improved": "Consider a defensive offer or ad visibility increase",
            "impact": "Medium",
            "detected_at": datetime.now().isoformat(),
            "status": "Pending"
        })
    
    return alerts[:25]

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health():
    return {"status":"ok","version":"11.0","data_rows":len(main_data),"change_rows":len(change_data)}

@app.get("/api/categories")
async def categories():
    return {"categories":sorted(set(str(r.get('category','')).strip() for r in main_data if str(r.get('category','')).strip()))}

@app.get("/api/dashboard")
async def dashboard(category: Optional[str] = None):
    return {"totalProducts":len(get_products(category)),"lastUpdated":str(last_refresh)}

@app.get("/api/products")
async def products(category: Optional[str] = None):
    prods = get_products(category)
    return {"products":prods[:100],"count":len(prods)}

@app.get("/api/changes")
async def changes(category: Optional[str] = None):
    return parse_changes(category)

@app.get("/api/insights/ai")
async def insights_ai(category: Optional[str] = None):
    c = parse_changes(category)
    if c['total_changes']==0: return {"headline":"📭 No changes","summary":"No data.","key_metrics":{"total_changes":0,"critical_alerts":0,"top_location":"N/A","main_risk":"N/A"},"critical_issues":[],"actions":[]}
    tl = c['locations'][0] if c['locations'] else {'location':'N/A','changes':0}
    tk = c['keywords'][0] if c['keywords'] else {'keyword':'N/A','changes':0}
    mc = c['change_types'][0] if c['change_types'] else {'type':'N/A','count':0}
    return {"headline":f"📊 {c['total_changes']:,} changes","summary":f"{c['total_changes']:,} changes. {tl['location']} leads.","key_metrics":{"total_changes":c['total_changes'],"critical_alerts":c['severity']['Critical'],"top_location":tl['location'],"main_risk":mc['type']},"critical_issues":[f"{mc['type']}: {mc['count']}"],"actions":[{"priority":"High","action":f"Audit {tl['location']}"}]}

@app.get("/api/top-brands")
async def top_brands(limit:int=5, category: Optional[str] = None):
    return {"brands":get_top_brands(limit, category)}

@app.get("/api/alerts")
async def alerts(category: Optional[str] = None):
    a = generate_alerts(category)
    return {"alerts":a,"count":len(a)}

@app.post("/api/alerts/{alert_id}/status")
async def update_alert_status(alert_id: str, status: str = Query(..., pattern="^(Pending|In Progress|Done|Ignored)$")):
    return {"alert_id":alert_id,"status":status,"updated":True}

@app.get("/api/actions/summary")
async def action_summary(category: Optional[str] = None):
    a = generate_alerts(category)
    high = [x for x in a if x['priority']=='High']
    return {"total_alerts":len(a),"high_priority":len(high),"alerts":a[:15]}

@app.get("/api/reports/generate")
async def report():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["Product","Brand","Price","Discount","Platform","City"])
    for p in get_products()[:500]: w.writerow([p['name'],p['brand'],p['price'],p['discount'],p['platform'],p['city']])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=report.csv"})

@app.get("/api/template/download")
async def template():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["impressions","clicks","product_views","add_to_cart","checkout_initiated","payment_success_rate","in_stock_pct","selling_price","discount","rating","cod_available","roas"])
    w.writerow(["50000","5000","4500","200","80","65","85","500","10","4.2","yes","2.5"])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=template.csv"})

@app.post("/api/diagnose/upload")
async def diagnose_upload(file:UploadFile=File(...)):
    content=await file.read()
    df=pd.read_csv(io.BytesIO(content)) if file.filename.endswith('.csv') else pd.read_excel(io.BytesIO(content))
    return {"filename":file.filename,"rows":len(df),"leak_stage":"Analysis pending"}

@app.post("/api/chat")
async def chat(request:ChatRequest):
    a = generate_alerts()
    high = [x for x in a if x['priority']=='High']
    if high:
        h = high[0]
        return {"answer":f"🚨 {len(high)} high-priority issues. Top: {h['issue']} — {h['confirmed_evidence']} Likely cause: {h['likely_cause']} Review by: {h['review_date']}."}
    return {"answer":"I'm Lilly! No critical leaks detected. Check the Alerts tab for details."}
@app.post("/api/chat")
async def chat(request:ChatRequest):
    a = generate_alerts()
    high = [x for x in a if x['priority']=='High']
    if high:
        h = high[0]
        return {"answer":f"🚨 {len(high)} high-priority issues. Top: {h['issue']} — {h['confirmed_evidence']} Likely cause: {h['likely_cause']} Review by: {h['review_date']}."}
    return {"answer":"I'm Lilly! No critical leaks detected. Check the Alerts tab for details."}

# ============================================================================
# v12.0 — DATA UPLOAD MAPPING LAYER
# ============================================================================

REPORT_TEMPLATES = {
    "sales": {
        "required": ["orders", "revenue", "date"],
        "optional": ["product_name", "brand", "category", "platform", "city", "quantity", "avg_order_value", "returns", "cancellations"],
        "label": "Sales Report"
    },
    "ads": {
        "required": ["spend", "impressions", "clicks"],
        "optional": ["ctr", "cpc", "cpa", "roas", "conversion_rate", "campaign_name", "keyword", "audience", "platform"],
        "label": "Ads / Marketing Report"
    },
    "stock": {
        "required": ["product_name", "stock_quantity", "date"],
        "optional": ["warehouse", "city", "pincode", "in_stock_pct", "reorder_level", "days_of_cover", "listing_status"],
        "label": "Inventory / Stock Report"
    },
    "funnel": {
        "required": ["sessions", "product_views", "add_to_cart", "checkout_initiated", "orders"],
        "optional": ["payment_attempted", "payment_successful", "bounce_rate", "device", "traffic_source", "landing_page"],
        "label": "Funnel / Conversion Report"
    },
    "payment": {
        "required": ["payment_attempted", "payment_successful", "date"],
        "optional": ["payment_failure_rate", "upi_success", "card_success", "cod_usage", "razorpay_drop", "payment_method", "gateway"],
        "label": "Payment / Gateway Report"
    },
    "competitor_pricing": {
        "required": ["competitor_name", "product_name", "competitor_price", "date"],
        "optional": ["our_price", "competitor_discount", "competitor_rank", "platform", "city"],
        "label": "Competitor Pricing Report"
    }
}

def detect_report_type(columns):
    columns_lower = [c.lower().strip() for c in columns]
    scores = {}
    for report_type, template in REPORT_TEMPLATES.items():
        required = template["required"]
        optional = template["optional"]
        matched_required = [c for c in required if c in columns_lower]
        matched_optional = [c for c in optional if c in columns_lower]
        total_matched = len(matched_required) + len(matched_optional)
        score = (len(matched_required) / len(required) * 60) + (len(matched_optional) / len(optional) * 40) if required else 0
        scores[report_type] = {
            "label": template["label"],
            "score": round(score, 1),
            "matched_required": matched_required,
            "matched_optional": matched_optional,
            "missing_required": [c for c in required if c not in columns_lower],
            "missing_optional": [c for c in optional if c not in columns_lower],
            "total_columns_expected": len(required) + len(optional),
            "total_columns_matched": total_matched
        }
    best = max(scores.items(), key=lambda x: x[1]["score"]) if scores else (None, {"score": 0})
    return {"detected_type": best[0], "details": best[1], "all_scores": {k: v["score"] for k, v in scores.items()}}

@app.post("/api/upload/map")
async def upload_and_map(file: UploadFile = File(...)):
    content = await file.read()
    if file.filename.endswith('.csv'):
        df = pd.read_csv(io.BytesIO(content))
    elif file.filename.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(io.BytesIO(content))
    else:
        raise HTTPException(400, "Unsupported format. Upload CSV or Excel.")
    if df.empty:
        raise HTTPException(400, "File is empty.")
    columns = list(df.columns)
    detection = detect_report_type(columns)
    confidence_boost = 0
    if detection["detected_type"]:
        score = detection["details"]["score"]
        if score >= 80: confidence_boost = 0.3
        elif score >= 50: confidence_boost = 0.15
        else: confidence_boost = 0.05
    return {
        "filename": file.filename,
        "rows": len(df),
        "columns_found": columns,
        "report_detection": {
            "detected_type": detection["detected_type"],
            "label": detection["details"].get("label", "Unknown"),
            "match_score": detection["details"].get("score", 0),
            "matched_columns": detection["details"].get("total_columns_matched", 0),
            "expected_columns": detection["details"].get("total_columns_expected", 0)
        },
        "mapping": {
            "matched_required": detection["details"].get("matched_required", []),
            "matched_optional": detection["details"].get("matched_optional", []),
            "missing_required": detection["details"].get("missing_required", []),
            "missing_optional": detection["details"].get("missing_optional", []),
        },
        "confidence_upgrade": f"+{int(confidence_boost * 100)}% — Diagnosis confidence improved",
        "next_steps": "Upload missing columns for a more precise diagnosis." if detection["details"].get("missing_required") else "All required columns present. Full diagnosis available.",
        "all_type_scores": detection.get("all_scores", {})
    }

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)
