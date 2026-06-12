"""
Numbrstalk.com - Diagnostic Intelligence Backend API v9.0
Action Engine: Alerts + Action Desk + Rule Engine
Live data from local JSON files (updated hourly by GitHub Actions)
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

OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

app = FastAPI(title="Numbrstalk API", version="9.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

try:
    ai_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    ai_available = True
except:
    ai_client = None
    ai_available = False

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
            prods[n] = {
                "id": str(abs(hash(n))%100000),
                "name": n,
                "brand": str(r.get('brand','')),
                "price": safe_float(r.get('price')),
                "discount": parse_discount(r.get('discount','')),
                "platform": str(r.get('platform','')),
                "city": str(r.get('city','')),
                "stock_status": str(r.get('stock_status',''))
            }
    return list(prods.values())

def get_top_brands(limit=5, category=None):
    bd = defaultdict(list)
    for r in main_data:
        if category and str(r.get('category','')).strip().lower() != category.lower(): continue
        b = str(r.get('brand','')).strip()
        d = parse_discount(r.get('discount',''))
        if b and d > 0 and len(b) > 3:  # Skip short/noise brand names
            bd[b].append(d)
    res = []
    for b, ds in bd.items():
        if len(ds) >= 5:  # Only brands with 5+ products
            res.append({"brand":b,"avg_discount":round(sum(ds)/len(ds),1),"product_count":len(ds)})
    res.sort(key=lambda x:x['avg_discount'], reverse=True)
    return res[:limit] if res else [{"brand":"No data","avg_discount":0,"product_count":0}]

def parse_changes(category=None):
    data = change_data
    if category: data = [r for r in data if str(r.get('keyword','')).strip().lower() == category.lower() or str(r.get('category','')).strip().lower() == category.lower()]
    result = {"total_changes":len(data),"change_types":[],"keywords":[],"locations":[],"severity":{"Critical":0,"High":0,"Medium":0,"Low":0},"rank_drops":[],"rank_improvements":[],"new_entries":[],"disappeared":[],"platforms":[]}
    tc, kc, lc, pc = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    for row in data:
        issue = str(row.get('issue_type','')).strip()
        sev = str(row.get('severity','')).strip()
        kw = str(row.get('keyword','')).strip()
        loc = str(row.get('location','')).strip()
        plat = str(row.get('platform','')).strip()
        prod = str(row.get('product_name','')).strip()
        old_r = str(row.get('old_rank',''))
        new_r = str(row.get('new_rank',''))
        if issue: tc[issue] += 1
        if kw: kc[kw] += 1
        if loc: lc[loc] += 1
        if plat: pc[plat] += 1
        if sev in result['severity']: result['severity'][sev] += 1
        try:
            old = int(old_r) if old_r.replace('-','').isdigit() else 0
            new = int(new_r) if new_r.replace('-','').isdigit() else 0
            if old > 0 and new > 0:
                if new > old: result['rank_drops'].append({"product":prod,"old_rank":old,"new_rank":new,"keyword":kw,"location":loc,"platform":plat})
                elif new < old: result['rank_improvements'].append({"product":prod,"old_rank":old,"new_rank":new,"keyword":kw,"location":loc,"platform":plat})
        except: pass
        if old_r in ['0','','-'] and new_r not in ['0','','-','?']: result['new_entries'].append({"product":prod,"new_rank":new_r,"keyword":kw,"location":loc,"platform":plat})
        if new_r in ['0','','-'] and old_r not in ['0','','-','?']: result['disappeared'].append({"product":prod,"old_rank":old_r,"keyword":kw,"location":loc,"platform":plat})
    for t,c in sorted(tc.items(),key=lambda x:x[1],reverse=True)[:10]: result['change_types'].append({"type":t,"count":c})
    for k,c in sorted(kc.items(),key=lambda x:x[1],reverse=True)[:10]: result['keywords'].append({"keyword":k,"changes":c})
    for l,c in sorted(lc.items(),key=lambda x:x[1],reverse=True)[:10]: result['locations'].append({"location":l,"changes":c})
    for p,c in sorted(pc.items(),key=lambda x:x[1],reverse=True): result['platforms'].append({"platform":p,"changes":c})
    return result

def ai_insights(category=None):
    c = parse_changes(category)
    if c['total_changes']==0: return {"headline":"📭 No changes detected","summary":"No data for this category.","key_metrics":{"total_changes":0,"critical_alerts":0,"top_location":"N/A","main_risk":"N/A"},"critical_issues":[],"actions":[]}
    tl = c['locations'][0] if c['locations'] else {'location':'N/A','changes':0}
    tk = c['keywords'][0] if c['keywords'] else {'keyword':'N/A','changes':0}
    mc = c['change_types'][0] if c['change_types'] else {'type':'N/A','count':0}
    return {"headline":f"📊 {c['total_changes']:,} changes — {mc['type']} dominant","summary":f"{c['total_changes']:,} changes. {tl['location']} leads. '{tk['keyword']}' most volatile.","key_metrics":{"total_changes":c['total_changes'],"critical_alerts":c['severity']['Critical'],"top_location":tl['location'],"main_risk":mc['type']},"critical_issues":[f"{mc['type']}: {mc['count']}",f"{tl['location']}: {tl['changes']}",f"'{tk['keyword']}': {tk['changes']}"],"actions":[{"priority":"High","action":f"Audit {tl['location']}"},{"priority":"Medium","action":f"Review '{tk['keyword']}'"}]}

# ============================================================================
# ACTION ENGINE — Alerts + Rules + Action Desk
# ============================================================================

def generate_alerts(category=None):
    """Rule engine: automatically generate alerts from data patterns."""
    alerts = []
    changes = parse_changes(category)
    products = get_products(category)
    
    # Rule 1: Rank drops ≥5 positions
    for drop in changes.get('rank_drops', []):
        try:
            old_r = int(drop.get('old_rank', 0))
            new_r = int(drop.get('new_rank', 0))
            if new_r - old_r >= 5:
                alerts.append({
                    "id": f"alt_{abs(hash(drop.get('product','')))%100000}",
                    "platform": drop.get('platform', 'Blinkit'),
                    "city": drop.get('location', 'Bangalore'),
                    "sku": drop.get('product', ''),
                    "category": drop.get('keyword', ''),
                    "issue": "Ranking Dropped",
                    "reason": f"Rank dropped from #{old_r} to #{new_r} ({new_r - old_r} positions lost)",
                    "impact": "High" if new_r - old_r >= 10 else "Medium",
                    "detected_at": datetime.now().isoformat(),
                    "recommended_action": "Check sales velocity, stock levels, competitor pricing, and ad visibility on this SKU",
                    "status": "Pending"
                })
        except: pass
    
    # Rule 2: Discount > 35% = competitor pressure
    for p in products:
        if p.get('discount', 0) > 35:
            alerts.append({
                "id": f"alt_{abs(hash(p.get('name','')))%100000}",
                "platform": p.get('platform', 'Blinkit'),
                "city": p.get('city', 'Bangalore'),
                "sku": p.get('name', ''),
                "category": category or 'All',
                "issue": "Deep Discount Alert",
                "reason": f"Product at {p.get('discount', 0)}% discount — possible price war or margin risk",
                "impact": "High" if p.get('discount', 0) > 50 else "Medium",
                "detected_at": datetime.now().isoformat(),
                "recommended_action": "Run tactical discount, create bundle offer, avoid permanent MRP reduction",
                "status": "Pending"
            })
            break
    
    # Rule 3: Stock out
    for p in products:
        stock = str(p.get('stock_status', '')).lower()
        if 'out' in stock:
            alerts.append({
                "id": f"alt_{abs(hash(p.get('name','')))%100000}",
                "platform": p.get('platform', 'Blinkit'),
                "city": p.get('city', 'Bangalore'),
                "sku": p.get('name', ''),
                "category": category or 'All',
                "issue": "Stock Out",
                "reason": "Product showing out of stock — lost sales and visibility",
                "impact": "High",
                "detected_at": datetime.now().isoformat(),
                "recommended_action": "Restock SKU immediately, pause ads on low-stock items, notify platform manager",
                "status": "Pending"
            })
            break
    
    # Rule 4: Product disappeared from top 30
    for d in changes.get('disappeared', [])[:3]:
        alerts.append({
            "id": f"alt_{abs(hash(d.get('product','')))%100000}",
            "platform": d.get('platform', 'Blinkit'),
            "city": d.get('location', 'Bangalore'),
            "sku": d.get('product', ''),
            "category": d.get('keyword', ''),
            "issue": "Visibility Lost",
            "reason": "Product disappeared from top 30 rankings — check stock, pricing, and competitor entries",
            "impact": "High",
            "detected_at": datetime.now().isoformat(),
            "recommended_action": "Check stock availability, listing quality, pricing vs competitors, and increase ad visibility",
            "status": "Pending"
        })
    
    # Rule 5: New competitor entry
    for entry in changes.get('new_entries', [])[:2]:
        alerts.append({
            "id": f"alt_{abs(hash(entry.get('product','')))%100000}",
            "platform": entry.get('platform', 'Blinkit'),
            "city": entry.get('location', 'Bangalore'),
            "sku": entry.get('product', ''),
            "category": entry.get('keyword', ''),
            "issue": "New Competitor Entry",
            "reason": "A new product entered the top 30 in your category — monitor for competitive pressure",
            "impact": "Medium",
            "detected_at": datetime.now().isoformat(),
            "recommended_action": "Track the new entrant's pricing, discounts, and ranking over next 48 hours",
            "status": "Pending"
        })
    
    return alerts[:30]

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health():
    return {"status":"ok","version":"9.0","data_rows":len(main_data),"change_rows":len(change_data)}

@app.get("/api/categories")
async def categories():
    cats = sorted(set(str(r.get('category','')).strip() for r in main_data if str(r.get('category','')).strip()))
    return {"categories":cats}

@app.get("/api/dashboard")
async def dashboard(category: Optional[str] = None):
    prods = get_products(category)
    return {"totalProducts":len(prods),"platforms":list(set(p['platform'] for p in prods)),"lastUpdated":str(last_refresh)}

@app.get("/api/products")
async def products(category: Optional[str] = None):
    prods = get_products(category)
    return {"products":prods[:100],"count":len(prods)}

@app.get("/api/changes")
async def changes(category: Optional[str] = None):
    return parse_changes(category)

@app.get("/api/insights/ai")
async def insights_ai(category: Optional[str] = None):
    return ai_insights(category)

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
    high = [x for x in a if x['impact']=='High']
    return {"total_alerts":len(a),"high_priority":len(high),"top_3_actions":[x['recommended_action'] for x in high[:3]],"summary":f"{len(high)} high-priority actions need attention. Top issue: {high[0]['issue'] if high else 'None'}","alerts":a[:15]}

@app.get("/api/reports/generate")
async def report(category: Optional[str] = None):
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["Product","Brand","Price","Discount","Platform","City"])
    for p in get_products(category)[:500]: w.writerow([p['name'],p['brand'],p['price'],p['discount'],p['platform'],p['city']])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=report.csv"})

@app.get("/api/report/category")
async def category_report(category: str):
    prods = [r for r in main_data if category.lower() in str(r.get('keyword','')).lower() or category.lower() in str(r.get('category','')).lower()]
    brands = defaultdict(lambda: {"count":0,"prices":[],"discounts":[]})
    prices, discounts, platforms = [], [], set()
    for p in prods:
        b = str(p.get('brand',''))
        brands[b]["count"] += 1
        pr = safe_float(p.get('price'))
        d = parse_discount(p.get('discount',''))
        if pr>0: brands[b]["prices"].append(pr); prices.append(pr)
        if d>0: brands[b]["discounts"].append(d); discounts.append(d)
        if p.get('platform'): platforms.add(str(p['platform']))
    top = sorted(brands.items(),key=lambda x:x[1]["count"],reverse=True)[:10]
    tb = [{"brand":n,"products":d["count"],"avg_price":round(sum(d["prices"])/len(d["prices"]),1) if d["prices"] else 0,"avg_discount":round(sum(d["discounts"])/len(d["discounts"]),1) if d["discounts"] else 0} for n,d in top]
    pranges = {"Under ₹50":0,"₹50-100":0,"₹100-200":0,"₹200+":0}
    for p in prices:
        if p<50: pranges["Under ₹50"]+=1
        elif p<100: pranges["₹50-100"]+=1
        elif p<200: pranges["₹100-200"]+=1
        else: pranges["₹200+"]+=1
    hd = [{"name":str(p.get('product_name',''))[:50],"brand":str(p.get('brand','')),"discount":parse_discount(p.get('discount',''))} for p in prods if parse_discount(p.get('discount',''))>30][:5]
    return {"category":category,"total_products":len(prods),"total_brands":len(brands),"avg_price":round(sum(prices)/len(prices),1) if prices else 0,"min_price":min(prices) if prices else 0,"max_price":max(prices) if prices else 0,"avg_discount":round(sum(discounts)/len(discounts),1) if discounts else 0,"platforms":list(platforms),"top_brands":tb,"price_ranges":pranges,"high_discount_products":hd}

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
    return {"filename":file.filename,"rows":len(df),"leak_stage":"Analysis pending","signal_groups_found":list(df.columns)}

@app.post("/api/chat")
async def chat(request:ChatRequest):
    c = parse_changes()
    plats = list(set(str(r.get('platform','')).strip() for r in change_data if r.get('platform')))
    a = generate_alerts()
    high = [x for x in a if x['impact']=='High']
    if high:
        return {"answer":f"🚨 {len(high)} high-priority alerts need attention. Top issue: {high[0]['issue']} — {high[0]['reason']}. Check the Alerts tab for details."}
    return {"answer":f"I'm Lilly! Latest scan: {c['total_changes']:,} changes across {len(plats)} platforms. No critical alerts right now."}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)
