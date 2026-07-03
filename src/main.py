# Copyright © 2025 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from configs import get_config
from src.routes.batch import router as batch_router
from src.routes.files import router as files_router
from src.routes.batch import add_to_redis_queue, length_of_redis_queue
from src.models.batch import get_lost_batches, get_lost_batches_on_start_up
from src.auth import verify_basic_auth

# Global task reference to keep cron job running
cron_task = None

async def cron_job():
    """Periodic task to check for lost batches every 15 minutes"""
    while True:
        try:
            if await length_of_redis_queue() == 0:
                lost_batches = await get_lost_batches()
                print(f"Found {len(lost_batches)} lost batches")
                for batch_id in lost_batches:
                    await add_to_redis_queue(batch_id)
        except Exception as e:
            print(f"Error in cron job: {e}")
        
        # Wait 5 minutes (300 seconds) before next run
        await asyncio.sleep(300)

async def startup_recovery():
    """Recovery task to handle lost batches on startup"""
    try:
        lost_batches = await get_lost_batches_on_start_up()
        print(f"Found {len(lost_batches)} lost batches on start up")
        for batch_id in lost_batches:
            await add_to_redis_queue(batch_id)
    except Exception as e:
        print(f"Error in startup recovery: {e}")

# Resolve configuration
config_obj = get_config()

# Create FastAPI app
app = FastAPI(
    title="Batch Endpoint API",
    description="FastAPI application for batch processing",
    version="1.0.0"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan event handler"""
    global cron_task
    
    # Run startup recovery
    await startup_recovery()
    
    # Start the background cron job
    cron_task = asyncio.create_task(cron_job())
    print("Background cron job started")

    # get yielded when lifespan is done
    yield
    
    # cancel the cron job
    if cron_task:
        cron_task.cancel()
        try:
            await cron_task
        except asyncio.CancelledError:
            print("Background cron job cancelled")
        except Exception as e:
            print(f"Error cancelling cron job: {e}")

app = FastAPI(lifespan=lifespan)

# Enable CORS for all domains – tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(batch_router, prefix="/v1")
app.include_router(files_router, prefix="/v1")

# Ensure upload directory exists
os.makedirs(config_obj.UPLOAD_FOLDER, exist_ok=True)

# Static files
static_folder = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_folder):
    app.mount("/static", StaticFiles(directory=static_folder), name="static")

# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "OK"}

# Serve static files with auth
@app.get("/", include_in_schema=False)
@app.get("/{path:path}", include_in_schema=False)
async def serve_static(request: Request, path: str = "", user: dict = Depends(verify_basic_auth)):
    """Serve files from static folder or fall back to index.html"""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    
    if path and os.path.exists(os.path.join(static_dir, path)):
        return FileResponse(os.path.join(static_dir, path))
    
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    raise HTTPException(status_code=404, detail="index.html not found")

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info"
    )
