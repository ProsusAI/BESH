import asyncio
import aiofiles
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
import sys

import redis.asyncio as redis
import os
import litellm
try:
    import uvloop  # type: ignore
    uvloop.install()
except ImportError:
    pass

# Import batch operations from the main app
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
from src.models.batch import (
    init_db_pool, get_batch, update_batch_status, 
    update_batch_completion
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure litellm
litellm.set_verbose = False
os.environ['OPENAI_API_BASE'] = os.getenv('OPENAI_API_BASE', 'http://localhost:8000/v1')
os.environ['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY', 'dummy-key')

import httpx

# Calculate optimal connection pool size
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '128'))
# Connection pool should be ~1.5x MAX_WORKERS to handle request overlap
max_connections = int(os.getenv('LITELLM_MAX_CONNECTIONS', str(MAX_WORKERS * 2)))
max_keepalive = int(os.getenv('LITELLM_MAX_KEEPALIVE_CONNECTIONS', str(MAX_WORKERS)))

litellm.client_session = httpx.AsyncClient(
    timeout=httpx.Timeout(300.0, connect=60.0),
    limits=httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive
    )
)
logger.info(f"Configured LiteLLM with max_connections={max_connections}, max_keepalive={max_keepalive}")

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379')
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '128'))
MODEL_NAME = os.getenv('MODEL_NAME', '')

# Override OPENAI_API_KEY with TEST_API_KEY if set
if os.getenv('TESTING') == 'active':
    print(f"\n\n\nOverriding OPENAI_API_KEY with TEST_API_KEY\n\n\n")
    os.environ['OPENAI_API_KEY'] = os.getenv('TEST_API_KEY')


class FileProcessor:
    def __init__(self, max_concurrency: int = 128, redis_url: str = "redis://redis:6379"):
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None

        # Use same upload folder as main app
        self.upload_folder = os.getenv("UPLOAD_FOLDER", "/tmp/batch_files")
        
    async def initialize(self):
        """Initialize database and Redis connections"""
        try:
            # Initialize database connection pool using batch.py function
            await init_db_pool()
            logger.info("Database connection successful")
            
            # Initialize Redis connection
            logger.info(f"Connecting to Redis: {self.redis_url}")
            self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            logger.info("Redis connection successful")
            
        except Exception as e:
            logger.error(f"Failed to initialize connections: {e}")
            raise

    async def update_job_status(self, batch_id: str, status: str):
        """Update job status in database using batch.py function"""
        # Map processing status to in_progress for consistency with batch.py
        status_mapping = {"processing": "in_progress"}
        actual_status = status_mapping.get(status, status)
        await update_batch_status(batch_id, actual_status)

    async def get_batch(self, batch_id: str):
        """Get batch from database using batch.py function"""
        return await get_batch(batch_id)

    async def save_batch_results(self, file_path: str, results: list):
        """Save batch results to file"""
        
        async with aiofiles.open(file_path, "w") as file:
            for result in results:
                line = json.dumps(result, default=lambda o: o.__dict__) + "\n"
                await file.write(line)

    async def update_batch_completion(self, batch_id: str, output_file_path: str, successful_count: int, error_count: int, 
                                    prompt_tokens: int, completion_tokens: int, total_tokens: int):
        """Update batch with completion data using batch.py function"""
        await update_batch_completion(
            batch_id,
            output_file_path,
            successful_count + error_count,  # request_total
            successful_count,                # request_completed
            error_count,                     # request_failed
            prompt_tokens, 
            completion_tokens, 
            total_tokens
        )


    async def process_file_stream(self, batch_id: str):
        """Process file stream with high concurrency and streaming writes"""
        start_time = datetime.now()
        logger.info(f"Starting to process batch: '{batch_id}'")

        # Get batch data using simplified method
        try:
            batch = await self.get_batch(batch_id)
            if not batch:
                logger.error(f"Batch '{batch_id}' not found in database")
                return
            
            logger.info(f"Found batch: ID={batch['id']}, Status={batch['status']}, File={batch['input_file_id']}")
        except Exception as db_error:
            import traceback
            traceback.print_exc()
            logger.error(f"Database error while querying batch '{batch_id}': {db_error}")
            return
        
        try:
            # Update job status to processing
            await self.update_job_status(batch_id, "processing")
            
            # Get input file path
            input_file_path = f"{self.upload_folder}/{batch['input_file_id']}"
            if not input_file_path.endswith('.jsonl'):
                input_file_path += '.jsonl'
            
            # Check if file exists
            if not os.path.exists(input_file_path):
                raise Exception(f"Input file not found: {input_file_path}")
            
            # Bounded task pool and streaming writes
            tasks = set()
            line_number = 0
            max_in_flight = self.max_concurrency * 2

            successful_count = 0
            error_count = 0
            total_prompt_tokens = 0
            total_completion_tokens = 0
            total_total_tokens = 0

            output_file_id = f"r_{batch_id}"
            output_file_path = f"{self.upload_folder}/{output_file_id}.jsonl"

            def _accumulate_usage(usage_obj: Any):
                nonlocal total_prompt_tokens, total_completion_tokens, total_total_tokens
                if not usage_obj:
                    return
                if isinstance(usage_obj, dict):
                    total_prompt_tokens += usage_obj.get('prompt_tokens', 0) or 0
                    total_completion_tokens += usage_obj.get('completion_tokens', 0) or 0
                    total_total_tokens += usage_obj.get('total_tokens', 0) or 0
                else:
                    total_prompt_tokens += getattr(usage_obj, 'prompt_tokens', 0) or 0
                    total_completion_tokens += getattr(usage_obj, 'completion_tokens', 0) or 0
                    total_total_tokens += getattr(usage_obj, 'total_tokens', 0) or 0

            async with aiofiles.open(input_file_path, 'r') as infile, aiofiles.open(output_file_path, 'w') as outfile:
                async for raw_line in infile:
                    line = raw_line.strip()
                    if not line:
                        continue

                    task = asyncio.create_task(
                        self.process_line_with_semaphore(line, line_number, batch_id)
                    )
                    tasks.add(task)
                    line_number += 1

                    # Keep the number of in-flight tasks bounded
                    if len(tasks) >= max_in_flight:
                        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for completed in done:
                            try:
                                result = completed.result()
                                _accumulate_usage(result.get('usage'))
                                successful_count += 1
                            except Exception as e:
                                result = {
                                    "id": f"batch_req_{uuid.uuid4().hex}",
                                    "custom_id": "unknown",
                                    "response": None,
                                    "error": {
                                        "code": "executor_error",
                                        "message": str(e)
                                    }
                                }
                                error_count += 1

                            await outfile.write(json.dumps(result, default=lambda o: o.__dict__) + "\n")
                        tasks = pending

                # Drain remaining tasks
                while tasks:
                    done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for completed in done:
                        try:
                            result = completed.result()
                            _accumulate_usage(result.get('usage'))
                            successful_count += 1
                        except Exception as e:
                            result = {
                                "id": f"batch_req_{uuid.uuid4().hex}",
                                "custom_id": "unknown",
                                "response": None,
                                "error": {
                                    "code": "executor_error",
                                    "message": str(e)
                                }
                            }
                            error_count += 1

                        await outfile.write(json.dumps(result, default=lambda o: o.__dict__) + "\n")

            if (successful_count + error_count) == 0:
                raise Exception("No valid request lines found in input file")

            # Update batch completion status after streaming writes
            await self.update_batch_completion(
                batch_id,
                output_file_id,
                successful_count,
                error_count,
                total_prompt_tokens,
                total_completion_tokens,
                total_total_tokens
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Job {batch_id} completed: {successful_count} successful, {error_count} errors in {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"Error processing file {batch_id}: {e}")
            await self.update_job_status(batch_id, "failed")

    async def process_line_with_semaphore(self, line: str, line_number: int, job_id: str):
        """Process single line with concurrency control"""
        async with self.semaphore:
            return await self.process_line(line, line_number, job_id)

    async def process_line(self, line: str, line_number: int, job_id: str):
        """Process individual line"""
        custom_id = f"req_{line_number}"  # Default custom_id
        try:
            # Parse the request line (follows OpenAI batch format)
            request_data = json.loads(line)
            custom_id = request_data.get('custom_id', custom_id)

            # Make the litellm call
            response = await self.make_http_call(request_data)
            
            # Format result to match OpenAI batch format
            result = {
                "id": f"batch_req_{uuid.uuid4().hex}",
                "custom_id": custom_id,
                "response": response,
                "error": None,
                "usage": response.get('usage')  # Extract usage for aggregation
            }
            
            return result
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON at line {line_number}: {e}"
            logger.error(error_msg)
            # Return error result instead of raising
            return {
                "id": f"batch_req_{uuid.uuid4().hex}",
                "custom_id": custom_id,
                "response": None,
                "error": {
                    "code": "invalid_json",
                    "message": error_msg
                }
            }
            
        except Exception as e:
            error_msg = f"Error processing line {line_number}: {e}"
            logger.error(error_msg)
            # Return error result instead of raising
            return {
                "id": f"batch_req_{uuid.uuid4().hex}",
                "custom_id": custom_id,
                "response": None,
                "error": {
                    "code": "processing_error",
                    "message": error_msg
                }
            }

    async def make_http_call(self, request_data: dict, retries: int = 3) -> Dict[str, Any]:
        """Make async litellm completion call with retry logic"""
        for attempt in range(retries):
            try:
                # Extract data from request_data.body (matches OpenAI batch format)
                body = request_data['body'].copy()  # Copy to avoid modifying original
                model = body.get('model')
                messages = body.get('messages')

                # Override model name if set
                if MODEL_NAME:
                    model = MODEL_NAME
                
                # Smart max_tokens handling: Remove it to let the model decide
                # This prevents "max_tokens too large" errors
                # The model will generate up to its context window limit
                if 'max_tokens' in body:
                    #del body['max_tokens']
                    body['max_tokens'] = 5000
                if 'max_completion_tokens' in body:
                    del body['max_completion_tokens']
                
                # Use async litellm for completion
                if len(body) == 2:
                    response = await litellm.acompletion(
                        model=model, 
                        messages=messages
                    )
                else:
                    # Pass extra kwargs
                    extra_kwargs = {k: v for k, v in body.items() if k not in ('model', 'messages')}
                    response = await litellm.acompletion(
                        model=model, 
                        messages=messages, 
                        **extra_kwargs
                    )
                
                # Convert response to dict format
                result = {
                    "status_code": 200,
                    "request_id": f"req_{uuid.uuid4().hex}",
                    "body": response.model_dump() if hasattr(response, 'model_dump') else dict(response),
                    "usage": getattr(response, 'usage', None)
                }
            
                return result

            except Exception as e:
                if attempt == retries - 1:
                    raise
                
                # Exponential backoff
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
        
        raise Exception("Max retries exceeded")


# Global processor instance
processor = FileProcessor(max_concurrency=MAX_WORKERS, redis_url=REDIS_URL)

async def worker_node(worker_id: int):
    """Simple worker node that processes jobs from Redis queue"""
    logger.info(f"Worker {worker_id} started and waiting for jobs...")
    await asyncio.sleep(worker_id * 1.0) # Make them start at different times
    
    while True:
        try:
            # Atomically move from queue to processing list to avoid job loss
            job_id = await processor.redis_client.brpoplpush("batch_queue", "batch_processing", timeout=1)
            if job_id:
                logger.info(f"Worker {worker_id} received job: {job_id}")
                try:
                    await processor.process_file_stream(job_id)
                    # Ack on success: remove one occurrence from processing list
                    await processor.redis_client.lrem("batch_processing", 1, job_id)
                except Exception as e:
                    # Likely transient (e.g., API overload). Sleep briefly and requeue
                    logger.error(f"Worker {worker_id} failed processing job {job_id}: {e}")
                    await asyncio.sleep(1.0)
                    try:
                        # Remove from processing and push back to main queue
                        await processor.redis_client.lrem("batch_processing", 1, job_id)
                        await processor.redis_client.rpush("batch_queue", job_id)
                        logger.info(f"Worker {worker_id} requeued job: {job_id}")
                    except Exception as requeue_error:
                        logger.error(f"Worker {worker_id} failed to requeue job {job_id}: {requeue_error}")
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.1)

async def main():
    """Initialize processor and start workers concurrently"""
    await processor.initialize()
    worker_count = int(os.getenv("WORKER_COUNT", "2"))
    await asyncio.gather(*[worker_node(i) for i in range(worker_count)])

if __name__ == "__main__":
    asyncio.run(main())