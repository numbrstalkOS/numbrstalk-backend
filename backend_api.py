"""
Numbrstalk.com - Commerce Diagnosis Engine v13.0
Complete Diagnosis Result + Missing Data + Confidence Upgrade + Auto-Tasks + Demo
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

app = FastAPI(title="Numbrstalk Diagnosis Engine", version="13.0.0")
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

def get_category_avg_rank(keyword):
    ranks = []
    for r in change_data:
        if str(r.get('keyword','')).strip().lower() == keyword.lower():
            try: ranks.append(int(r.get('new_rank',0)))
            except: pass
    return round(sum(ranks)/len(ranks),1) if ranks else 0

def generate_alerts(category=None):
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
                cat_avg = get_category_avg_rank(keyword)
                review_date = (datetime.now() + timedelta(days=3)).strftime('%B %d, %Y')
                alerts.append({
                    "id": f"alt_{abs(hash(product))%100000}",
                    "platform": drop.get('platform','Blinkit'),
                    "city": location,
                    "sku": product,
                    "category": keyword,
                    "issue": "Critical Rank Drop" if diff >= 10 else "Rank Slipping",
                    "leak_stage": "Visibility",
                    "reason_bucket": "Competitor Issue" if diff >= 8 else "Visibility Issue",
                    "priority": "High" if diff >= 10 else "Medium",
                    "confidence": "High" if cat_avg > 0 else "Medium",
                    "confirmed_evidence": f"Rank dropped from #{old_r} to #{new_r} ({diff} positions lost) in {location} for '{keyword}'.",
                    "likely_cause": "Competitor visibility or offer pressure.",
                    "missing_data_needed": ["Competitor pricing data", "Sales report to confirm revenue impact"],
                    "compared_against": f"Category average rank is #{cat_avg}.",
                    "what_to_check_first": [f"Compare your discount vs top 5 in '{keyword}'", f"Check stock in {location}", "Review ad visibility"],
                    "recommended_action": f"Compare pricing for '{keyword}' in {location}. If discount is below average, consider a tactical offer.",
                    "expected_impact": "Adjusting offer depth may recover rank in 3-5 days.",
                    "review_date": review_date,
                    "success_signal": f"Rank returns to top 5",
                    "if_not_improved": "Increase ad visibility or launch bundle offer",
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
            "confirmed_evidence": f"'{product}' vanished from top 30 in {location}.",
            "likely_cause": "Product may be out of stock, delisted, or pushed out by competitors.",
            "missing_data_needed": ["Stock status", "Listing status on platform"],
            "compared_against": f"Previous top 30 ranking for '{keyword}'.",
            "what_to_check_first": ["Verify stock availability", "Check listing status", "Look for new competitor entries"],
            "recommended_action": "Check stock and listing status urgently.",
            "expected_impact": "Restoring visibility can recover ranking in 24-48 hours.",
            "review_date": review_date,
            "success_signal": "Product reappears in top 30",
            "if_not_improved": "Investigate competitor entries and consider ad boost",
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
            "confirmed_evidence": f"'{product}' entered top 30 at #{entry.get('new_rank','?')} in '{keyword}'.",
            "likely_cause": "New competitor launching in this category.",
            "missing_data_needed": ["New entrant's pricing", "Their discount structure"],
            "compared_against": "Existing top 30 competitors.",
            "what_to_check_first": ["Track new entrant's price for 48 hours", "Monitor rank movement"],
            "recommended_action": "Monitor for 48 hours. Prepare response if they gain traction.",
            "expected_impact": "Early detection allows proactive response.",
            "review_date": review_date,
            "success_signal": "New entrant's rank stabilizes or declines",
            "if_not_improved": "Consider defensive offer or ad increase",
            "impact": "Medium",
            "detected_at": datetime.now().isoformat(),
            "status": "Pending"
        })
    return alerts[:25]

# ============================================================================
# REPORT TEMPLATES (v12.0)
# ============================================================================

REPORT_TEMPLATES = {
    "sales": {"required":["orders","revenue","date"],"optional":["product_name","brand","category","platform","city","quantity","avg_order_value","returns","cancellations"],"label":"Sales Report"},
    "ads": {"required":["spend","impressions","clicks"],"optional":["ctr","cpc","cpa","roas","conversion_rate","campaign_name","keyword","audience","platform"],"label":"Ads / Marketing Report"},
    "stock": {"required":["product_name","stock_quantity","date"],"optional":["warehouse","city","pincode","in_stock_pct","reorder_level","days_of_cover","listing_status"],"label":"Inventory / Stock Report"},
    "funnel": {"required":["sessions","product_views","add_to_cart","checkout_initiated","orders"],"optional":["payment_attempted","payment_successful","bounce_rate","device","traffic_source","landing_page"],"label":"Funnel / Conversion Report"},
    "payment": {"required":["payment_attempted","payment_successful","date"],"optional":["payment_failure_rate","upi_success","card_success","cod_usage","razorpay_drop","payment_method","gateway"],"label":"Payment / Gateway Report"},
    "competitor_pricing": {"required":["competitor_name","product_name","competitor_price","date"],"optional":["our_price","competitor_discount","competitor_rank","platform","city"],"label":"Competitor Pricing Report"}
}

def detect_report_type(columns):
    columns_lower = [c.lower().strip() for c in columns]
    scores = {}
    for report_type, template in REPORT_TEMPLATES.items():
        required = template["required"]
        optional = template["optional"]
        matched_required = [c for c in required if c in columns_lower]
        matched_optional = [c for c in optional if c in columns_lower]
        score = (len(matched_required)/len(required)*60) + (len(matched_optional)/len(optional)*40) if required else 0
        scores[report_type] = {"label":template["label"],"score":round(score,1),"matched_required":matched_required,"matched_optional":matched_optional,"missing_required":[c for c in required if c not in columns_lower],"missing_optional":[c for c in optional if c not in columns_lower],"total_columns_expected":len(required)+len(optional),"total_columns_matched":len(matched_required)+len(matched_optional)}
    best = max(scores.items(),key=lambda x:x[1]["score"]) if scores else (None,{"score":0})
    return {"detected_type":best[0],"details":best[1],"all_scores":{k:v["score"] for k,v in scores.items()}}

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health():
    return {"status":"ok","version":"13.0","data_rows":len(main_data),"change_rows":len(change_data)}

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

@app.post("/api/upload/map")
async def upload_and_map(file: UploadFile = File(...)):
    content = await file.read()
    if file.filename.endswith('.csv'): df = pd.read_csv(io.BytesIO(content))
    elif file.filename.endswith(('.xlsx','.xls')): df = pd.read_excel(io.BytesIO(content))
    else: raise HTTPException(400, "Unsupported format.")
    if df.empty: raise HTTPException(400, "File is empty.")
    detection = detect_report_type(list(df.columns))
    confidence_boost = 0
    if detection["detected_type"]:
        score = detection["details"]["score"]
        if score >= 80: confidence_boost = 0.3
        elif score >= 50: confidence_boost = 0.15
        else: confidence_boost = 0.05
    return {
        "filename":file.filename,"rows":len(df),"columns_found":list(df.columns),
        "report_detection":{"detected_type":detection["detected_type"],"label":detection["details"].get("label","Unknown"),"match_score":detection["details"].get("score",0),"matched_columns":detection["details"].get("total_columns_matched",0),"expected_columns":detection["details"].get("total_columns_expected",0)},
        "mapping":{"matched_required":detection["details"].get("matched_required",[]),"matched_optional":detection["details"].get("matched_optional",[]),"missing_required":detection["details"].get("missing_required",[]),"missing_optional":detection["details"].get("missing_optional",[])},
        "confidence_upgrade":f"+{int(confidence_boost*100)}%",
        "next_steps":"Upload missing columns for more precise diagnosis." if detection["details"].get("missing_required") else "All required columns present."
    }

# ============================================================================
# v13.0 — FULL DIAGNOSIS + MISSING DATA + CONFIDENCE UPGRADE + AUTO-TASKS + DEMO
# ============================================================================

@app.post("/api/diagnose/full")
async def full_diagnosis(file: UploadFile = File(...)):
    content = await file.read()
    if file.filename.endswith('.csv'): df = pd.read_csv(io.BytesIO(content))
    elif file.filename.endswith(('.xlsx','.xls')): df = pd.read_excel(io.BytesIO(content))
    else: raise HTTPException(400, "Unsupported format.")
    
    columns = list(df.columns)
    columns_lower = [c.lower().strip() for c in columns]
    detection = detect_report_type(columns)
    
    has_sales = any(c in columns_lower for c in ["orders","revenue"])
    has_funnel = any(c in columns_lower for c in ["add_to_cart","checkout_initiated"])
    has_payment = any(c in columns_lower for c in ["payment_successful","payment_success_rate"])
    has_ads = any(c in columns_lower for c in ["spend","roas","cpc"])
    has_stock = any(c in columns_lower for c in ["stock_quantity","in_stock_pct"])
    has_competitor = any(c in columns_lower for c in ["competitor_price","competitor_name"])
    
    diagnosis = {
        "issue":"Performance Analysis","leak_stage":"Insufficient Data","reason_bucket":"Multiple Factors Possible",
        "confidence":"Low","confirmed_evidence":[],"likely_cause":"","what_to_check_first":[],
        "recommended_action":"","review_date":(datetime.now()+timedelta(days=3)).strftime("%B %d, %Y"),
        "success_signal":"","missing_data_prompt":[],"auto_tasks":[],"confidence_upgrade_path":[]
    }
    
    evidence = []
    
    if "payment_success_rate" in columns_lower:
        ps = pd.to_numeric(df["payment_success_rate"], errors='coerce').mean()
        evidence.append(f"Payment success rate: {ps:.0f}%")
        if ps < 70:
            diagnosis["leak_stage"] = "Payment"
            diagnosis["reason_bucket"] = "Payment Gateway Issue"
            diagnosis["issue"] = "Payment Leak Detected"
            diagnosis["confirmed_evidence"].append(f"Payment success rate is {ps:.0f}% — below 70% threshold")
            diagnosis["likely_cause"] = "UPI failure, Razorpay timeout, or COD unavailability"
            diagnosis["what_to_check_first"] = ["Razorpay/UPI failure logs","COD availability by pincode","Payment gateway response time"]
            diagnosis["recommended_action"] = "Fix payment gateway issues before increasing ad spend. Check UPI success rates and consider backup provider."
            diagnosis["success_signal"] = "Payment success rate returns above 75%"
            diagnosis["auto_tasks"] = ["Check Razorpay UPI failure logs","Verify COD availability for top 10 pincodes","Test payment gateway with test transaction","Review payment success rate after 48 hours"]
    
    if "add_to_cart" in columns_lower and "checkout_initiated" in columns_lower:
        atc = pd.to_numeric(df["add_to_cart"], errors='coerce').sum()
        ci = pd.to_numeric(df["checkout_initiated"], errors='coerce').sum()
        if atc > 0:
            rate = ci/atc
            evidence.append(f"Cart-to-Checkout rate: {rate:.1%}")
            if rate < 0.4:
                diagnosis["leak_stage"] = "Cart-to-Checkout"
                diagnosis["reason_bucket"] = "Cart Friction"
                diagnosis["issue"] = "Checkout Drop-off"
                diagnosis["confirmed_evidence"].append(f"Only {rate:.0%} of add-to-carts reach checkout")
                diagnosis["likely_cause"] = "Delivery charge shock, forced login, or missing COD"
                diagnosis["auto_tasks"].extend(["Check delivery charge impact on cart abandonment","Verify if COD is available for affected pincodes"])
    
    if "in_stock_pct" in columns_lower:
        stock = pd.to_numeric(df["in_stock_pct"], errors='coerce').mean()
        evidence.append(f"In-stock: {stock:.0f}%")
        if stock < 70:
            diagnosis["leak_stage"] = "Stock / Availability"
            diagnosis["reason_bucket"] = "Stock Issue"
            diagnosis["issue"] = "Stock Availability Risk"
            diagnosis["auto_tasks"].append(f"Restock inventory — current level at {stock:.0f}%")
    
    if "roas" in columns_lower:
        roas = pd.to_numeric(df["roas"], errors='coerce').mean()
        evidence.append(f"ROAS: {roas:.1f}x")
        if roas < 2: diagnosis["auto_tasks"].append("Pause low-ROAS keywords and review ad targeting")
    
    diagnosis["confirmed_evidence"] = evidence if evidence else ["Data uploaded but no critical signals detected yet."]
    
    missing = []
    if not has_sales: missing.append("Sales Report (orders, revenue) — to confirm revenue impact")
    if not has_funnel: missing.append("Funnel Report (add_to_cart, checkout_initiated) — to find conversion leaks")
    if not has_payment: missing.append("Payment Report (payment_success_rate) — to check gateway health")
    if not has_ads: missing.append("Ads Report (spend, roas) — to check marketing efficiency")
    if not has_stock: missing.append("Stock Report (in_stock_pct) — to check availability")
    if not has_competitor: missing.append("Competitor Pricing Report — to detect competitive pressure")
    
    if missing:
        diagnosis["missing_data_prompt"] = missing
        diagnosis["confidence"] = "Low" if len(missing) > 3 else "Medium"
    else:
        diagnosis["confidence"] = "High"
        diagnosis["missing_data_prompt"] = ["All key data sources uploaded. Full diagnosis available."]
    
    diagnosis["confidence_upgrade_path"] = [
        {"current":"Low","if_you_upload":"Sales Report","upgrades_to":"Medium","reason":"Revenue impact can be confirmed"},
        {"current":"Medium","if_you_upload":"Funnel + Payment Report","upgrades_to":"High","reason":"Leak stage and reason can be precisely identified"}
    ]
    
    if not diagnosis["auto_tasks"]:
        diagnosis["auto_tasks"] = ["Upload sales data to confirm revenue impact","Upload funnel data to identify conversion leaks","Check competitor pricing in your category"]
    
    return {"filename":file.filename,"rows":len(df),"report_type":detection["detected_type"],"diagnosis":diagnosis}

@app.get("/api/demo/diagnosis")
async def demo_diagnosis():
    return {
        "issue":"Sales Drop","leak_stage":"Payment","reason_bucket":"Payment Gateway Issue","confidence":"High",
        "confirmed_evidence":["Payment success rate dropped from 82% to 51% in last 7 days","Rank dropped from #3 to #9 in HSR Layout for 'biscuits'","Competitor discount increased from 5% to 18%"],
        "likely_cause":"UPI/Razorpay failure spike combined with competitor offer pressure",
        "what_to_check_first":["Razorpay UPI failure logs for last 48 hours","COD availability for top 5 pincodes","Competitor pricing gap vs your discount"],
        "recommended_action":"Fix gateway issue before increasing ad spend. Check UPI success rates and consider backup provider.",
        "review_date":(datetime.now()+timedelta(days=3)).strftime("%B %d, %Y"),
        "success_signal":"Payment success rate returns above 75%",
        "missing_data_prompt":["Sales Report — to confirm revenue impact","Ads Report — to check if paid visibility changed"],
        "confidence_upgrade_path":[{"current":"Medium","if_you_upload":"Sales Report","upgrades_to":"High","reason":"Revenue impact can be precisely calculated"}],
        "auto_tasks":["Check Razorpay UPI failure logs","Compare competitor discount gap","Verify stock in HSR Layout","Check delivery charge impact on cart abandonment","Review rank after 3 days"]
    }

@app.post("/api/diagnose/upload")
async def diagnose_upload(file:UploadFile=File(...)):
    content=await file.read()
    df=pd.read_csv(io.BytesIO(content)) if file.filename.endswith('.csv') else pd.read_excel(io.BytesIO(content))
    return {"filename":file.filename,"rows":len(df),"leak_stage":"Analysis pending"}

@app.post("/api/chat")
async def chat(request: ChatRequest):
    # Handle both question and message formats
    user_text = request.question or getattr(request, 'message', None) or ""
    if not user_text.strip():
        return {"answer": "I'm Lilly! Ask me about your marketplace performance, competitor activity, or upload data for a full diagnosis."}
    
    a = generate_alerts()
    high = [x for x in a if x['priority']=='High']
    if high:
        h = high[0]
        return {"answer": f"🚨 {len(high)} high-priority issues. Top: {h['issue']} — {h['confirmed_evidence']} Likely cause: {h['likely_cause']} Review by: {h['review_date']}."}
    return {"answer": f"I'm Lilly! Latest scan shows {len(a)} alerts. No critical issues detected. Check the Alerts tab for details."}
if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)
