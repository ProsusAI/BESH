import asyncpg
import os
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

# Database connection pool
_db_pool: Optional[asyncpg.Pool] = None

async def get_db_pool() -> asyncpg.Pool:
    """Get or create database connection pool"""
    global _db_pool
    if _db_pool is None:
        await init_db_pool()
    return _db_pool

async def init_db_pool():
    """Initialize database connection pool"""
    global _db_pool
    
    # Parse database connection from SQLALCHEMY_DATABASE_URI
    sqlalchemy_uri = os.getenv("SQLALCHEMY_DATABASE_URI")
    if not sqlalchemy_uri:
        raise ValueError("SQLALCHEMY_DATABASE_URI environment variable not set")
    
    parsed = urllib.parse.urlparse(sqlalchemy_uri)
    db_host = parsed.hostname
    db_port = parsed.port or 5432
    db_user = parsed.username
    db_password = parsed.password
    db_name = parsed.path.lstrip('/')
    
    _db_pool = await asyncpg.create_pool(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name,
        min_size=5,
        max_size=20
    )
    
    # Test connection
    async with _db_pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    
    logger.info("Database connection pool initialized")

    # Create tables if they don't exist
    await create_table_if_not_exists()

async def create_table_if_not_exists():
    """Create tables if they don't exist"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS batches (
                id VARCHAR(50) PRIMARY KEY,
                object VARCHAR(20) DEFAULT 'batch',
                endpoint VARCHAR(100) NOT NULL,
                input_file_id VARCHAR(50) NOT NULL,
                completion_window VARCHAR(10) DEFAULT '24h',
                status VARCHAR(20) DEFAULT 'validating',
                output_file_id VARCHAR(50),
                
                -- Timestamps
                created_at TIMESTAMP DEFAULT NOW(),
                in_progress_at TIMESTAMP,
                completed_at TIMESTAMP,
                failed_at TIMESTAMP,
                expired_at TIMESTAMP,
                cancelled_at TIMESTAMP,
                expires_at TIMESTAMP,
                finalizing_at TIMESTAMP,
                
                -- Token usage and request count fields
                total_tokens INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                request_failed INTEGER DEFAULT 0,
                request_completed INTEGER DEFAULT 0,
                request_total INTEGER DEFAULT 0
            )
        ''')
        
        # Create indexes for performance (same as original SQLAlchemy model)
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS ix_batches_status ON batches(status)
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS ix_batches_created_at ON batches(created_at)
        ''')
        
    logger.info("Tables and indexes created if they didn't exist")

def batch_row_to_dict(row) -> Dict[str, Any]:
    """Convert database row to dictionary for JSON response"""
    if not row:
        return None
    
    result = {
        'id': row['id'],
        'object': row['object'],
        'endpoint': row['endpoint'],
        'input_file_id': row['input_file_id'],
        'completion_window': row['completion_window'],
        'status': row['status'],
        'created_at': int(row['created_at'].timestamp()) if row.get('created_at') else None
    }
    
    # Add optional fields if they exist
    if row.get('output_file_id'):
        result['output_file_id'] = row['output_file_id']
    
    if row.get('in_progress_at'):
        result['in_progress_at'] = int(row['in_progress_at'].timestamp())
    
    if row.get('completed_at'):
        result['completed_at'] = int(row['completed_at'].timestamp())
    
    if row.get('failed_at'):
        result['failed_at'] = int(row['failed_at'].timestamp())
    
    if row.get('expired_at'):
        result['expired_at'] = int(row['expired_at'].timestamp())
    
    if row.get('cancelled_at'):
        result['cancelled_at'] = int(row['cancelled_at'].timestamp())
    
    if row.get('expires_at'):
        result['expires_at'] = int(row['expires_at'].timestamp())
    
    if row.get('finalizing_at'):
        result['finalizing_at'] = int(row['finalizing_at'].timestamp())
    
    # Add token usage fields if they exist
    if row.get('total_tokens') is not None:
        result['total_tokens'] = row['total_tokens']
    
    if row.get('prompt_tokens') is not None:
        result['prompt_tokens'] = row['prompt_tokens']
    
    if row.get('completion_tokens') is not None:
        result['completion_tokens'] = row['completion_tokens']
    
    if row.get('request_failed') is not None:
        result['request_failed'] = row['request_failed']
    
    if row.get('request_completed') is not None:
        result['request_completed'] = row['request_completed']
    
    if row.get('request_total') is not None:
        result['request_total'] = row['request_total']
    
    return result

async def create_batch(batch_id: str, object_type: str, endpoint: str, input_file_id: str,
                      completion_window: str, status: str, created_at: datetime, expires_at: datetime):
    """Create a new batch record"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO batches (
                id, object, endpoint, input_file_id, completion_window, 
                status, created_at, expires_at, total_tokens, prompt_tokens, 
                completion_tokens, request_failed, request_completed, request_total
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, 0, 0, 0, 0, 0)
        ''', batch_id, object_type, endpoint, input_file_id, completion_window,
             status, created_at, expires_at)

async def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """Get batch by ID"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM batches WHERE id = $1", batch_id)
        return batch_row_to_dict(row) if row else None

async def get_batches_paginated(limit: int = 20, after_created_at: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Get batches with pagination"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if after_created_at:
            rows = await conn.fetch('''
                SELECT * FROM batches 
                WHERE created_at < $1 
                ORDER BY created_at DESC 
                LIMIT $2
            ''', after_created_at, limit)
        else:
            rows = await conn.fetch('''
                SELECT * FROM batches 
                ORDER BY created_at DESC 
                LIMIT $1
            ''', limit)
        
        return [batch_row_to_dict(row) for row in rows]

async def get_batches_in_timerange(start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
    """Get batches within a time range"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT * FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
            ORDER BY created_at DESC
        ''', start_time, end_time)
        
        return [batch_row_to_dict(row) for row in rows]

async def get_completed_batches_in_timerange(start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
    """Get completed batches within a time range"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT * FROM batches 
            WHERE completed_at >= $1 AND completed_at <= $2
            AND completed_at IS NOT NULL
            AND in_progress_at IS NOT NULL
            AND created_at IS NOT NULL
            ORDER BY completed_at DESC
        ''', start_time, end_time)
        
        return [batch_row_to_dict(row) for row in rows]

async def update_batch_status(batch_id: str, status: str):
    """Update batch status with appropriate timestamp"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if status == "in_progress":
            await conn.execute(
                "UPDATE batches SET status = $1, in_progress_at = NOW() WHERE id = $2",
                status, batch_id
            )
        elif status == "completed":
            await conn.execute(
                "UPDATE batches SET status = $1, completed_at = NOW() WHERE id = $2", 
                status, batch_id
            )
        elif status == "failed":
            await conn.execute(
                "UPDATE batches SET status = $1, failed_at = NOW() WHERE id = $2",
                status, batch_id
            )
        elif status == "cancelled":
            await conn.execute(
                "UPDATE batches SET status = $1, cancelled_at = NOW() WHERE id = $2",
                status, batch_id
            )
        else:
            await conn.execute(
                "UPDATE batches SET status = $1 WHERE id = $2",
                status, batch_id
            )

async def update_batch_completion(batch_id: str, output_file_path: str, request_total: int, request_completed: int, 
                                request_failed: int, prompt_tokens: int, completion_tokens: int, 
                                total_tokens: int):
    """Update batch with completion data"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE batches SET 
                status = 'completed',
                completed_at = NOW(),
                output_file_id = $1,
                request_total = $2,
                request_completed = $3,
                request_failed = $4,
                prompt_tokens = $5,
                completion_tokens = $6,
                total_tokens = $7
            WHERE id = $8
        ''', output_file_path, int(request_total), int(request_completed), int(request_failed),
             int(prompt_tokens), int(completion_tokens), int(total_tokens), batch_id)

async def cancel_batch(batch_id: str):
    """Cancel a batch"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE batches SET status = 'cancelled', cancelled_at = NOW() WHERE id = $1",
            batch_id
        )

async def delete_batch(batch_id: str):
    """Delete a batch"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM batches WHERE id = $1", batch_id)

async def count_batches() -> int:
    """Count total batches"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM batches")

async def count_batches_by_status(statuses: List[str]) -> int:
    """Count batches by status"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM batches WHERE status = ANY($1)", statuses
        )

async def get_batch_status_counts() -> List[tuple]:
    """Get batch counts grouped by status"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT status, COUNT(*) as count FROM batches GROUP BY status")

async def get_request_counts_sum(start_time: datetime, end_time: datetime) -> tuple:
    """Get sum of request counts within time range"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchrow('''
            SELECT 
                COALESCE(SUM(request_total), 0) as total_batches,
                COALESCE(SUM(request_failed), 0) as failed_batches
            FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
        ''', start_time, end_time)
        return result['total_batches'], result['failed_batches']

async def get_lost_batches() -> List[str]:
    """Get lost batches - only batches that were never completed"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id FROM batches 
            WHERE status in ('validating', 'queued')
            AND completed_at IS NULL
        ''')
        returning_ids = [row['id'] for row in rows]
    
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE batches SET status = 'queued' WHERE id = ANY($1)
        ''', returning_ids)
    
    return returning_ids

async def get_lost_batches_on_start_up() -> List[str]:
    """Get lost batches on start up - only batches that were never completed"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id FROM batches 
            WHERE status in ('validating', 'in_progress', 'queued')
            AND completed_at IS NULL
        ''')
        returning_ids = [row['id'] for row in rows]
    
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE batches SET status = 'queued' WHERE id = ANY($1)
        ''', returning_ids)
    
    return returning_ids


async def get_request_stats_by_status(start_time: datetime, end_time: datetime) -> Dict[str, Any]:
    """Get request statistics grouped by batch status within a time range"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get total requests for completed batches
        completed_result = await conn.fetchrow('''
            SELECT 
                COALESCE(SUM(request_completed), 0) as requests_completed,
                COALESCE(SUM(request_failed), 0) as requests_failed,
                COALESCE(SUM(request_total), 0) as requests_total
            FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
            AND status = 'completed'
        ''', start_time, end_time)
        
        # Get requests for in-progress batches
        in_progress_result = await conn.fetchrow('''
            SELECT 
                COALESCE(SUM(request_total), 0) as requests_in_progress
            FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
            AND status = 'in_progress'
        ''', start_time, end_time)
        
        # Get requests for pending/queued/validating batches
        pending_result = await conn.fetchrow('''
            SELECT 
                COALESCE(SUM(request_total), 0) as requests_pending
            FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
            AND status IN ('validating', 'queued')
        ''', start_time, end_time)
        
        # Get batch counts by status
        batch_status_counts = await conn.fetch('''
            SELECT status, COUNT(*) as count
            FROM batches 
            WHERE created_at >= $1 AND created_at <= $2
            GROUP BY status
        ''', start_time, end_time)
        
        status_counts = {row['status']: row['count'] for row in batch_status_counts}
        
        return {
            'requests_completed': completed_result['requests_completed'],
            'requests_failed': completed_result['requests_failed'],
            'requests_total': completed_result['requests_total'],
            'requests_in_progress': in_progress_result['requests_in_progress'],
            'requests_pending': pending_result['requests_pending'],
            'batches_by_status': status_counts,
            'batches_completed': status_counts.get('completed', 0),
            'batches_in_progress': status_counts.get('in_progress', 0),
            'batches_pending': status_counts.get('validating', 0) + status_counts.get('queued', 0),
            'batches_failed': status_counts.get('failed', 0),
            'batches_cancelled': status_counts.get('cancelled', 0)
        }