import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import litellm
import logging
import redis.asyncio as redis
from src.models.batch import (
    init_db_pool, get_batch, create_batch, cancel_batch as db_cancel_batch,
    delete_batch as db_delete_batch, get_batches_paginated, get_batches_in_timerange,
    get_completed_batches_in_timerange, count_batches, count_batches_by_status,
    get_batch_status_counts, get_request_counts_sum, get_request_stats_by_status
)
from src.auth import verify_api_key_flexible, verify_basic_auth
from configs import get_config

# Optional fast JSON parser
try:  # pragma: no cover - performance optimization
    import orjson as _fastjson  # type: ignore
except Exception:  # pragma: no cover
    _fastjson = None

if _fastjson is not None:
    def _fast_loads(s: str):  # type: ignore
        return _fastjson.loads(s)
else:
    def _fast_loads(s: str):  # type: ignore
        return json.loads(s)

# Pydantic models for request validation
class CreateBatchRequest(BaseModel):
    input_file_id: str
    endpoint: str
    completion_window: Optional[str] = "24h"

class BatchResponse(BaseModel):
    id: str
    object: str = "batch"
    endpoint: str
    input_file_id: str
    completion_window: str
    status: str
    created_at: Optional[int] = None

router = APIRouter()

# Load configuration
config = get_config()

# Configure litellm
litellm.set_verbose = False

# Use configuration values
UPLOAD_FOLDER = config.UPLOAD_FOLDER

# Configure litellm to use vLLM endpoint if specified
VLLM_BASE_URL = config.OPENAI_API_BASE
VLLM_API_KEY = config.OPENAI_API_KEY

# Configure ThreadPoolExecutor
MAX_WORKERS = config.MAX_WORKERS
MAX_CONCURRENT_BATCHES = config.MAX_CONCURRENT_BATCHES

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configure litellm for vLLM
os.environ['OPENAI_API_BASE'] = VLLM_BASE_URL
os.environ['OPENAI_API_KEY'] = VLLM_API_KEY
logging.info(f"Configured litellm to use vLLM endpoint: {VLLM_BASE_URL}")

# Configure Redis client for worker communication
REDIS_URL = config.REDIS_URL if hasattr(config, 'REDIS_URL') else "redis://redis:6379"
redis_client = redis.from_url(REDIS_URL)


async def length_of_redis_queue():
    return await redis_client.llen("batch_queue")

async def add_to_redis_queue(batch_id: str):
    # Submit batch to Redis queue for worker processing
    try:
        # Check current queue length before pushing
        result = await redis_client.rpush("batch_queue", batch_id)
        logging.info(f"Successfully submitted batch '{batch_id}' to Redis queue for processing")
    except Exception as redis_error:
        logging.error(f"Failed to submit batch '{batch_id}' to Redis queue: {redis_error}")
        # Update batch status to failed
        await db_cancel_batch(batch_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue batch for processing: {redis_error}"
        )

@router.post('/batches')
async def create_batch_route(
    data: CreateBatchRequest,
    api_key: str = Depends(verify_api_key_flexible)
):
    """Create a new batch job"""
    try:
        # Create new batch
        batch_id = f"batch_{uuid.uuid4().hex}"
        
        # Calculate expires_at (24 hours from now)
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(hours=24)
        
        # Create batch using raw SQL
        await create_batch(
            batch_id=batch_id,
            object_type='batch',
            endpoint=data.endpoint,
            input_file_id=data.input_file_id,
            completion_window=data.completion_window,
            status='validating',
            created_at=created_at,
            expires_at=expires_at
        )
        
        await add_to_redis_queue(batch_id)
        
        batch = {
            'id': batch_id,
            'object': 'batch',
            'endpoint': data.endpoint,
            'input_file_id': data.input_file_id,
            'completion_window': data.completion_window,
            'status': 'validating',
            'created_at': int(created_at.timestamp()) if created_at else None
        }
        return batch
        
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.get('/batches/{batch_id}')
async def get_batch_route(
    batch_id: str,
    api_key: str = Depends(verify_api_key_flexible)
):
    """Retrieve a specific batch"""
    print(f"Getting batch {batch_id}")
    try:
        batch = await get_batch(batch_id)
        
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={"message": f'Batch {batch_id} not found', "type": "not_found_error"}
            )
        
        return batch
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.post('/batches/{batch_id}/cancel')
async def cancel_batch_route(
    batch_id: str,
    api_key: str = Depends(verify_api_key_flexible)
):
    """Cancel a batch job"""
    try:
        batch = await get_batch(batch_id)
        
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={"message": f'Batch {batch_id} not found', "type": "not_found_error"}
            )
        
        if batch['status'] in ['completed', 'failed', 'cancelled', 'expired']:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f'Cannot cancel batch with status: {batch["status"]}',
                    "type": "invalid_request_error"
                }
            )
        
        # Update to cancelled status using raw SQL
        await db_cancel_batch(batch_id)
        
        # Return updated batch data
        batch = await get_batch(batch_id)
        return batch
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.get('/batches/status')
async def get_batch_system_status(
    api_key: str = Depends(verify_api_key_flexible)
):
    """Get the current status of the batch processing system"""
    try:
        # Get Redis queue status
        try:
            queue_length = await redis_client.llen("batch_queue")
            redis_status = "connected"
        except Exception as redis_error:
            queue_length = None
            redis_status = f"error: {redis_error}"
        
        # Get database statistics
        total_batches = await count_batches()
        active_db_batches = await count_batches_by_status(['in_progress', 'queued'])
        
        status = {
            'redis_status': redis_status,
            'queue_length': queue_length,
            'total_batches_in_db': total_batches,
            'active_batches_in_db': active_db_batches
        }
        
        return status
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.delete('/batches/{batch_id}')
async def delete_batch_route(
    batch_id: str,
    api_key: str = Depends(verify_api_key_flexible)
):
    """Delete a batch job and its associated data"""
    try:
        batch = await get_batch(batch_id)
        
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={"message": f'Batch {batch_id} not found', "type": "not_found_error"}
            )
        
        # Delete the batch files if they exist
        try:
            input_file_path = f"{UPLOAD_FOLDER}/{batch['input_file_id']}"
            if not input_file_path.endswith('.jsonl'):
                input_file_path += '.jsonl'
            if os.path.exists(input_file_path):
                os.remove(input_file_path)
        except Exception as file_error:
            logging.warning(f"Failed to delete input file for batch {batch_id}: {file_error}")
        
        try:
            if batch.get('output_file_id'):
                output_file_path = f"{UPLOAD_FOLDER}/{batch['output_file_id']}.jsonl"
                if os.path.exists(output_file_path):
                    os.remove(output_file_path)
        except Exception as file_error:
            logging.warning(f"Failed to delete output file for batch {batch_id}: {file_error}")
        
        # Delete the batch record using raw SQL
        await db_delete_batch(batch_id)
        
        return {"message": f'Batch {batch_id} deleted successfully'}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.get('/batches')
async def list_batches(
    after: Optional[str] = None,
    limit: int = 20,
    user: dict = Depends(verify_basic_auth)
):
    """List all batches"""
    try:
        limit = min(limit, 100)  # Max 100
        
        after_created_at = None
        if after:
            # Simple pagination using created_at timestamp
            try:
                after_batch = await get_batch(after)
                if after_batch and after_batch.get('created_at'):
                    after_created_at = datetime.fromtimestamp(after_batch['created_at'])
            except:
                pass
        
        batches = await get_batches_paginated(limit, after_created_at)
        
        return {
            'object': 'list',
            'data': batches,
            'has_more': len(batches) == limit
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.get('/analytics/timeline')
async def get_batch_timeline(
    days: int = 7,
    user: dict = Depends(verify_basic_auth)
):
    """Get batch creation analytics for the specified time range (default 7 days)"""
    try:
        from datetime import datetime, timedelta
        
        days = min(max(days, 1), 30)  # Limit to 1-30 days
        
        # Calculate time range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        # Get batches created in the time range
        batches = await get_batches_in_timerange(start_time, end_time)
        
        # Determine interval size based on days
        # For 1-2 days: 15-minute intervals
        # For 3-7 days: 1-hour intervals
        # For 8+ days: 4-hour intervals
        if days <= 2:
            interval_minutes = 15
        elif days <= 7:
            interval_minutes = 60
        else:
            interval_minutes = 240
        
        # Create intervals
        intervals = []
        current_time = start_time
        
        while current_time < end_time:
            interval_end = current_time + timedelta(minutes=interval_minutes)
            
            # Count batches in this interval
            count = sum(1 for batch in batches 
                       if current_time <= datetime.fromtimestamp(batch['created_at']) < interval_end)
            
            # Format label based on interval size
            if interval_minutes <= 60:
                label = current_time.strftime('%m/%d %H:%M')
            else:
                label = current_time.strftime('%m/%d %H:00')
            
            intervals.append({
                'timestamp': current_time.isoformat(),
                'count': count,
                'label': label
            })
            
            current_time = interval_end
        
        # Calculate summary statistics
        total_batches = len(batches)
        avg_per_interval = total_batches / len(intervals) if intervals else 0
        max_in_interval = max(interval['count'] for interval in intervals) if intervals else 0
        
        return {
            'object': 'batch_timeline',
            'intervals': intervals,
            'summary': {
                'total_batches': total_batches,
                'avg_per_interval': round(avg_per_interval, 2),
                'max_in_interval': max_in_interval,
                'interval_minutes': interval_minutes,
                'days': days,
                'time_range': {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

@router.get('/analytics/tokens')
async def get_token_analytics(
    days: int = 7,
    user: dict = Depends(verify_basic_auth)
):
    """Get token usage analytics for the specified time range (default 7 days)"""
    try:
        from datetime import datetime, timedelta
        
        days = min(max(days, 1), 30)  # Limit to 1-30 days
        
        # Calculate time range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        # Get completed batches in the time range
        completed_batches = await get_completed_batches_in_timerange(start_time, end_time)
        
        # Determine interval size based on days
        # For 1-2 days: 15-minute intervals
        # For 3-7 days: 1-hour intervals
        # For 8+ days: 4-hour intervals
        if days <= 2:
            interval_minutes = 15
        elif days <= 7:
            interval_minutes = 60
        else:
            interval_minutes = 240
        
        # Create intervals
        intervals = []
        current_time = start_time
        
        while current_time < end_time:
            interval_end = current_time + timedelta(minutes=interval_minutes)
            
            # Sum tokens and duration for batches completed in this interval
            input_tokens = 0
            output_tokens = 0
            total_duration = 0
            batch_count = 0
            request_completed = 0
            
            for b in completed_batches:
                if b.get('completed_at') and current_time <= datetime.fromtimestamp(b['completed_at']) < interval_end:
                    input_tokens += b.get('prompt_tokens') or 0
                    output_tokens += b.get('completion_tokens') or 0
                    request_completed += b.get('request_completed') or 0
                    
                    # Calculate duration for this batch
                    if b.get('in_progress_at') and b.get('completed_at'):
                        in_progress_time = datetime.fromtimestamp(b['in_progress_at'])
                        completed_time = datetime.fromtimestamp(b['completed_at'])
                        duration = completed_time - in_progress_time
                        total_duration += duration.total_seconds()
                        batch_count += 1
            
            # Format label based on interval size
            if interval_minutes <= 60:
                label = current_time.strftime('%m/%d %H:%M')
            else:
                label = current_time.strftime('%m/%d %H:00')
            
            intervals.append({
                'timestamp': current_time.isoformat(),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': input_tokens + output_tokens,
                'request_completed': request_completed,
                'duration_seconds': total_duration,
                'avg_duration_seconds': total_duration / batch_count if batch_count > 0 else 0,
                'batch_count': batch_count,
                'label': label
            })
            
            current_time = interval_end
        
        # Calculate summary statistics
        total_input_tokens = sum(interval['input_tokens'] for interval in intervals)
        total_output_tokens = sum(interval['output_tokens'] for interval in intervals)
        total_tokens = total_input_tokens + total_output_tokens
        total_duration = sum(interval['duration_seconds'] for interval in intervals)
        total_batches = sum(interval['batch_count'] for interval in intervals)
        total_request_completed = sum(interval['request_completed'] for interval in intervals)
        avg_per_interval = total_tokens / len(intervals) if intervals else 0
        peak_interval = max(interval['total_tokens'] for interval in intervals) if intervals else 0
        avg_duration = total_duration / total_batches if total_batches > 0 else 0
        
        return {
            'object': 'token_timeline',
            'intervals': intervals,
            'summary': {
                'total_input_tokens': total_input_tokens,
                'total_output_tokens': total_output_tokens,
                'total_tokens': total_tokens,
                'total_duration_seconds': total_duration,
                'avg_duration_seconds': round(avg_duration, 2),
                'total_batches': total_batches,
                'total_request_completed': total_request_completed,
                'avg_per_interval': round(avg_per_interval, 2),
                'peak_interval': peak_interval,
                'interval_minutes': interval_minutes,
                'days': days,
                'time_range': {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )


@router.get('/dashboard')
async def get_batches_dashboard(
    page: int = 1,
    limit: int = 10,
    days: int = 7,
    user: dict = Depends(verify_basic_auth)
):
    """Get dashboard view of batches with pagination, token usage, and error rates"""
    try:
        # Get pagination parameters
        page = max(page, 1)
        limit = min(limit, 50)  # Max 50 batches per page
        offset = (page - 1) * limit
        days = min(max(days, 1), 30)  # Limit to 1-30 days
        
        # Filter batches to the specified time window (default 7 days)
        from datetime import datetime, timedelta
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        
        # Get batches from the time range
        all_batches_in_window = await get_batches_in_timerange(start_time, end_time)
        
        # Calculate pagination
        total_batches = len(all_batches_in_window)
        batches = all_batches_in_window[offset:offset + limit]
        
        # Prepare dashboard data
        dashboard_batches = []
        for batch in batches:
            # Calculate error rate
            total_requests = batch.get('request_total') or 0
            failed_requests = batch.get('request_failed') or 0

            error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0
            
            # Build batch dashboard entry
            batch_data = {
                'id': batch['id'],
                'status': batch['status'],
                'endpoint': batch['endpoint'],
                'created_at': datetime.fromtimestamp(batch['created_at']).isoformat() if batch.get('created_at') else None,
                'completed_at': datetime.fromtimestamp(batch['completed_at']).isoformat() if batch.get('completed_at') else None,
                'duration_seconds': None,
                'request_counts': {
                    'total': batch.get('request_total') or 0,
                    'completed': batch.get('request_completed') or 0,
                    'failed': batch.get('request_failed') or 0
                },
                'error_rate_percentage': round(error_rate, 2),
                'token_usage': {
                    'total_tokens': batch.get('total_tokens') or 0,
                    'prompt_tokens': batch.get('prompt_tokens') or 0,
                    'completion_tokens': batch.get('completion_tokens') or 0,
                    'total_cost': 0.0,  # Cost calculation removed
                    'request_count': batch.get('request_total') or 0
                }
            }
            
            # Calculate duration if both timestamps are available
            if batch.get('in_progress_at') and batch.get('completed_at'):
                in_progress_time = datetime.fromtimestamp(batch['in_progress_at'])
                completed_time = datetime.fromtimestamp(batch['completed_at'])
                duration = completed_time - in_progress_time
                batch_data['duration_seconds'] = duration.total_seconds()
            
            dashboard_batches.append(batch_data)
        
        # Calculate overall statistics
        overall_total_tokens = 0
        overall_prompt_tokens = 0
        overall_completion_tokens = 0
        overall_total_cost = 0.0
        overall_total_requests = 0
        overall_total_request_completed = 0
        for b in all_batches_in_window:
            overall_total_tokens += b.get('total_tokens') or 0
            overall_prompt_tokens += b.get('prompt_tokens') or 0
            overall_completion_tokens += b.get('completion_tokens') or 0
            overall_total_cost += 0.0  # Cost calculation removed
            overall_total_requests += b.get('request_total') or 0
            overall_total_request_completed += b.get('request_completed') or 0
        
        # Get batch status counts
        status_counts = {}
        for batch in all_batches_in_window:
            status = batch['status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get detailed request statistics by status
        request_stats = await get_request_stats_by_status(start_time, end_time)
        
        # Calculate overall fail rate
        total_requests_sum, failed_requests_sum = await get_request_counts_sum(start_time, end_time)
        overall_error_rate = 0
        if total_requests_sum > 0:
            overall_error_rate = (failed_requests_sum / total_requests_sum) * 100
        
        # Build summary statistics
        summary = {
            'total_batches': total_batches,
            'batches_by_status': status_counts,
            'batches_completed': request_stats['batches_completed'],
            'batches_in_progress': request_stats['batches_in_progress'],
            'batches_pending': request_stats['batches_pending'],
            'batches_failed': request_stats['batches_failed'],
            'batches_cancelled': request_stats['batches_cancelled'],
            'overall_error_rate_percentage': round(overall_error_rate, 2),
            'total_tokens': overall_total_tokens,
            'prompt_tokens': overall_prompt_tokens,
            'completion_tokens': overall_completion_tokens,
            'total_cost': float(overall_total_cost),
            'total_requests': overall_total_requests,
            'total_request_completed': overall_total_request_completed,
            'requests_completed': request_stats['requests_completed'],
            'requests_in_progress': request_stats['requests_in_progress'],
            'requests_pending': request_stats['requests_pending'],
            'requests_failed': request_stats['requests_failed'],
            'time_range_days': days
        }
        
        # Pagination info
        has_more = (offset + limit) < total_batches
        pagination = {
            'page': page,
            'limit': limit,
            'total_batches': total_batches,
            'has_more': has_more,
            'next_page': page + 1 if has_more else None,
            'prev_page': page - 1 if page > 1 else None
        }
        
        return {
            'object': 'dashboard',
            'batches': dashboard_batches,
            'summary': summary,
            'pagination': pagination
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "server_error"}
        )

def process_single_request(request_line, batch_id=None):
    """Process a single request line and return the result"""
    try:
        # Use fastest available JSON loader
        request_data = _fast_loads(request_line) if request_line and request_line.strip() else {}
        
        try:
            # Use litellm for completion (synchronous version) - unpack body directly to avoid intermediate dicts
            body = request_data['body']
            model = body['model']
            messages = body['messages']
            ll_completion = litellm.completion
            if len(body) == 2:
                response = ll_completion(model=model, messages=messages)
            else:
                # Avoid building a new dict; pass known keys directly and the rest via generator-comprehension
                # Build kwargs minimally
                extra_kwargs = {k: v for k, v in body.items() if k not in ('model', 'messages')}
                response = ll_completion(model=model, messages=messages, **extra_kwargs)
            
            result = {
                "id": f"batch_req_{uuid.uuid4().hex}",
                "custom_id": request_data['custom_id'],
                "response": {
                    "status_code": 200,
                    "request_id": f"req_{uuid.uuid4().hex}",
                    "body": response.model_dump()
                },
                "error": None
            }
            
            # Track token usage as an aggregate dict if batch_id is provided
            if batch_id:
                try:
                    usage = getattr(response, 'usage', None)
                    result['_token_usage'] = {
                        'batch_id': batch_id,
                        'request_id': result['id'],
                        'custom_id': request_data.get('custom_id'),
                        'model': request_data['body'].get('model'),
                        'total_tokens': getattr(usage, 'total_tokens', 0) or 0,
                        'prompt_tokens': getattr(usage, 'prompt_tokens', 0) or 0,
                        'completion_tokens': getattr(usage, 'completion_tokens', 0) or 0,
                        'cost': 0.0
                    }
                except Exception as token_error:
                    logging.warning(f"Failed to track token usage for request {result['id']}: {token_error}")
            
        except Exception as e:
            result = {
                "id": f"batch_req_{uuid.uuid4().hex}",
                "custom_id": request_data['custom_id'],
                "response": None,
                "error": {
                    "code": "processing_error",
                    "message": str(e)
                }
            }
        
        return result
        
    except Exception as e:
        # Handle JSON parsing errors
        return {
            "id": f"batch_req_{uuid.uuid4().hex}",
            "custom_id": "unknown",
            "response": None,
            "error": {
                "code": "parsing_error",
                "message": str(e)
            }
        }

