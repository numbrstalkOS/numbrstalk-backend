"""
Numbrstalk.com - Commerce Diagnosis Engine v10.0
Leak Stage → Reason Bucket → Evidence → Priority → Confidence
"""
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv, io, json, os, re
import pandas as pd
from datetime import datetime
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from collections import defaultdict

DATA_DIR = Path("data")

app = FastAPI(title="Numbrstalk Diagnosis Engine", version="10.0.0")
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
    tc,kc,lc,pc = defaultdict(int),defaultdict(int),defaultdict(int),defaultdict(int)
    for row in data:
        issue=str(row.get('issue_type','')).strip(); sev=str(row.get('severity','')).strip()
        kw=str(row.get('keyword','')).strip(); loc=str(row.get('location','')).strip()
        plat=str(row.get('platform','')).strip(); prod=str(row.get('product_name','')).strip()
        old_r=str(row.get('old_rank','')); new_r=str(row.get('new_rank',''))
        if issue: tc[issue]+=1
        if kw: kc[kw]+=1
        if loc: lc[loc]+=1
        if plat: pc[plat]+=1
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
# DIAGNOSIS ENGINE — Leak Stage + Reason Bucket + Priority + Confidence
# ============================================================================

def determine_leak_stage(change_type, rank_drop, discount, stock_status, is_disappeared, is_new_entry):
    """Classify where the business is leaking."""
    if stock_status and 'out' in str(stock_status).lower():
        return "Stock / Availability"
    if is_disappeared:
        return "Visibility"
    if rank_drop and rank_drop >= 10:
        return "Visibility"
    if rank_drop and 5 <= rank_drop < 10:
        return "Visibility"
    if discount and discount > 40:
        return "Competitor Pressure"
    if is_new_entry:
        return "Competitor Pressure"
    return "Visibility"

def determine_reason_bucket(leak_stage, discount, rank_drop, is_disappeared):
    """Classify the likely reason for the leak."""
    if leak_stage == "Visibility":
        if is_disappeared: return "Competitor Issue"
        if rank_drop and rank_drop >= 10: return "Competitor Issue"
        return "Visibility Issue"
    if leak_stage == "Competitor Pressure":
        if discount and discount > 40: return "Pricing Issue"
        return "Competitor Issue"
    if leak_stage == "Stock / Availability":
        return "Stock Issue"
    return "Visibility Issue"

def calculate_priority(leak_stage, rank_drop, discount, is_disappeared):
    """Calculate priority score."""
    if leak_stage == "Stock / Availability": return "High"
    if is_disappeared: return "High"
    if rank_drop and rank_drop >= 10: return "High"
    if discount and discount > 50: return "High"
    if rank_drop and rank_drop >= 5: return "Medium"
    return "Low"

def calculate_confidence(has_rank_data, has_discount_data, has_stock_data, has_competitor_data):
    """Calculate confidence based on available evidence."""
    score = 0
    if has_rank_data: score += 30
    if has_discount_data: score += 25
    if has_stock_data: score += 25
    if has_competitor_data: score += 20
    if score >= 80: return "High"
    if score >= 50: return "Medium"
    return "Low"

def generate_alerts(category=None):
    """Generate alerts with full diagnosis: Leak Stage + Reason + Priority + Confidence."""
    alerts = []
    changes = parse_changes(category)
    products = get_products(category)
    
    for drop in changes.get('rank_drops', []):
        try:
            old_r = int(drop.get('old_rank', 0))
            new_r = int(drop.get('new_rank', 0))
            diff = new_r - old_r
            if diff >= 5:
                leak_stage = determine_leak_stage("rank_drop", diff, None, None, False, False)
                reason_bucket = determine_reason_bucket(leak_stage, None, diff, False)
                priority = calculate_priority(leak_stage, diff, None, False)
                confidence = calculate_confidence(True, False, False, True)
                
                alerts.append({
                    "id": f"alt_{abs(hash(drop.get('product','')))%100000}",
                    "platform": drop.get('platform', 'Blinkit'),
                    "city": drop.get('location', 'Bangalore'),
                    "sku": drop.get('product', ''),
                    "category": drop.get('keyword', ''),
                    "issue": "Critical Rank Drop" if diff >= 10 else "Rank Slipping",
                    "reason": f"Rank dropped from #{old_r} to #{new_r} ({diff} positions). Competitor may have gained visibility.",
                    "leak_stage": leak_stage,
                    "reason_bucket": reason_bucket,
                    "priority": priority,
                    "confidence": confidence,
                    "evidence": f"Rank movement: #{old_r} → #{new_r}. Location: {drop.get('location','Bangalore')}. Keyword: {drop.get('keyword','')}.",
                    "recommended_action": f"Check competitor pricing and stock for '{drop.get('keyword','')}' in {drop.get('location','Bangalore')}. Review your discount vs category average.",
                    "impact": "High" if diff >= 10 else "Medium",
                    "detected_at": datetime.now().isoformat(),
                    "status": "Pending"
                })
        except: pass
    
    for d in changes.get('disappeared', [])[:3]:
        alerts.append({
            "id": f"alt_{abs(hash(d.get('product','')))%100000}",
            "platform": d.get('platform', 'Blinkit'),
            "city": d.get('location', 'Bangalore'),
            "sku": d.get('product', ''),
            "category": d.get('keyword', ''),
            "issue": "Product Disappeared",
            "reason": f"'{d.get('product','')}' vanished from top 30. Likely delisted, out of stock, or pushed out by competitors.",
            "leak_stage": "Visibility",
            "reason_bucket": "Competitor Issue",
            "priority": "High",
            "confidence": "High",
            "evidence": f"Product was in top 30 and disappeared. Location: {d.get('location','Bangalore')}. Keyword: {d.get('keyword','')}.",
            "recommended_action": "Check stock status, listing status, and competitor entries in this category immediately.",
            "impact": "High",
            "detected_at": datetime.now().isoformat(),
            "status": "Pending"
        })
    
    for entry in changes.get('new_entries', [])[:2]:
        alerts.append({
            "id": f"alt_{abs(hash(entry.get('product','')))%100000}",
            "platform": entry.get('platform', 'Blinkit'),
            "city": entry.get('location', 'Bangalore'),
            "sku": entry.get('product', ''),
            "category": entry.get('keyword', ''),
            "issue": "New Competitor Entry",
            "reason": f"'{entry.get('product','')}' entered top 30. New competition in '{entry.get('keyword','')}'.",
            "leak_stage": "Competitor Pressure",
            "reason_bucket": "Competitor Issue",
            "priority": "Medium",
            "confidence": "Medium",
            "evidence": f"New entry at rank #{entry.get('new_rank','?')}. Keyword: {entry.get('keyword','')}. Location: {entry.get('location','Bangalore')}.",
            "recommended_action": "Track this competitor's price, discount, and rank for 48 hours.",
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
    return {"status":"ok","version":"10.0","data_rows":len(main_data),"change_rows":len(change_data)}

@app.get("/api/categories")
async def categories():
    return {"categories":sorted(set(str(r.get('category','')).strip() for r in main_data if str(r.get('category','')).strip()))}

@app.get("/api/dashboard")
async def dashboard(category: Optional[str] = None):
    prods = get_products(category)
    return {"totalProducts":len(prods),"lastUpdated":str(last_refresh)}

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
    return {"headline":f"📊 {c['total_changes']:,} changes — {mc['type']} dominant","summary":f"{c['total_changes']:,} changes. {tl['location']} leads.","key_metrics":{"total_changes":c['total_changes'],"critical_alerts":c['severity']['Critical'],"top_location":tl['location'],"main_risk":mc['type']},"critical_issues":[f"{mc['type']}: {mc['count']}",f"{tl['location']}: {tl['changes']}",f"'{tk['keyword']}': {tk['changes']}"],"actions":[{"priority":"High","action":f"Audit {tl['location']}"},{"priority":"Medium","action":f"Review '{tk['keyword']}'"}]}

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
        return {"answer":f"🚨 {len(high)} high-priority issues. Top leak: {high[0]['leak_stage']} — {high[0]['reason_bucket']}. {high[0]['recommended_action']}"}
    return {"answer":"I'm Lilly! No critical leaks detected. Check the Alerts tab for details."}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)
