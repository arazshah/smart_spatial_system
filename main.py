# main.py
import os
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

# ایمپورت مستقیم کلاس‌های موجود در پکیج رسمی نصب شده شما
from geochat_kernel.runtime.app_container import KernelAppContainer
from geochat_kernel.bootstrap.plugin_loader import PluginLoader
from geochat_kernel.runtime.query_pipeline import QueryPipeline

app = FastAPI(title="GeoChat Spatial AI Engine", version="1.0.0")

# ساخت کانتینر برای مدیریت وضعیت و تزریق وابستگی‌ها
container = KernelAppContainer()

class QueryRequest(BaseModel):
    query: str
    metadata: Optional[Dict[str, Any]] = None

@app.on_event("startup")
async def startup_event():
    # لود خودکار پلاگین‌ها از پوشه plugins در زمان استارت سرور
    plugins_folder = "plugins"
    os.makedirs(plugins_folder, exist_ok=True)
    
    loader = PluginLoader(container, plugins_folder=plugins_folder)
    await loader.discover_register_initialize()
    
    # ذخیره پایپ‌لاین در وضعیت اپلیکیشن برای دسترسی در زمان درخواست‌ها
    app.state.pipeline = QueryPipeline(container)
    print(f"🚀 Plugins loaded successfully from '{plugins_folder}'!")

@app.post("/query")
async def run_query(request: QueryRequest):
    pipeline: QueryPipeline = app.state.pipeline
    try:
        response = await pipeline.run(request.query, metadata=request.metadata or {})
        
        # سریالایز کردن پاسخ برای خروجی API
        return {
            "is_success": response.is_success,
            "user_message": {
                "summary": response.user_message.summary if response.user_message else "",
                "detail": response.user_message.detail if response.user_message else ""
            } if response.user_message else None,
            "artifacts": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "path": getattr(a, 'path', None),
                    "metadata": getattr(a, 'metadata', {})
                } for a in response.artifacts
            ] if response.artifacts else [],
            "errors": [str(e) for e in response.errors] if response.errors else []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("🚀 در حال راه‌اندازی موتور هوشمند مکانی GeoChat...")
    uvicorn.run(app, host="0.0.0.0", port=8080)