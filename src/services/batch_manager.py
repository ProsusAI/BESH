"""Service layer containing background batch processing manager."""

from __future__ import annotations

import os
import queue
import threading
import time
import logging
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime
from typing import TYPE_CHECKING

from configs import get_config
from ..models.batch import Batch, db  # type: ignore
from ..models.token_usage import TokenUsage  # type: ignore

from flask import Flask

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
config = get_config()
UPLOAD_FOLDER = config.UPLOAD_FOLDER
MAX_WORKERS = config.MAX_WORKERS
MAX_CONCURRENT_BATCHES = config.MAX_CONCURRENT_BATCHES

# Configure litellm environment variables once
os.environ["OPENAI_API_BASE"] = config.OPENAI_API_BASE
os.environ["OPENAI_API_KEY"] = config.OPENAI_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ---------------------------------------------------------------------------
# Global manager instance (set via init_batch_manager)
# ---------------------------------------------------------------------------
_batch_manager: "GlobalBatchManager | None" = None


class GlobalBatchManager:  # noqa: D101
    def __init__(self, app: "Flask", max_workers: int, max_concurrent_batches: int):
        self.app = app
        self.max_workers = max_workers
        self.max_concurrent_batches = max_concurrent_batches

        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self.batch_queue: queue.Queue[str] = queue.Queue()
        self.active_batches: dict[str, float] = {}
        self.batch_futures = {}
        self.lock = threading.Lock()
        self.manager_running = True

        self.manager_thread = threading.Thread(
            target=self._manager_loop, daemon=True
        )
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self.manager_thread.start()
        self.monitor_thread.start()

    # --------------------------- Public API --------------------------------
    def submit_batch(self, batch_id: str) -> None:  # noqa: D401
        with self.lock:
            if len(self.active_batches) < self.max_concurrent_batches:
                self._start_batch_processing(batch_id)
            else:
                self.batch_queue.put(batch_id)
                self._update_batch_status(batch_id, "queued")

    def get_status(self) -> dict[str, int]:  # noqa: D401
        with self.lock:
            return {
                "active_batches": len(self.active_batches),
                "queued_batches": self.batch_queue.qsize(),
                "max_workers": self.max_workers,
                "max_concurrent_batches": self.max_concurrent_batches,
            }

    def shutdown(self):  # noqa: D401
        self.manager_running = False
        self.thread_pool.shutdown(wait=True)

    # ------------------------ Internal helpers -----------------------------
    def _start_batch_processing(self, batch_id: str) -> None:
        future = self.thread_pool.submit(self._process_batch_worker, batch_id)
        self.active_batches[batch_id] = time.time()
        self.batch_futures[batch_id] = future

    def _manager_loop(self):
        # Use concurrent.futures.wait for clearer waiting semantics and simpler loop
        POLL_TIMEOUT = 1  # seconds

        while self.manager_running:
            try:
                # Wait for at least one future to complete or timeout
                if self.batch_futures:
                    done, _ = wait(
                        list(self.batch_futures.values()),
                        timeout=POLL_TIMEOUT,
                        return_when="FIRST_COMPLETED",
                    )
                else:
                    # Nothing running – just sleep to avoid busy-looping
                    time.sleep(POLL_TIMEOUT)
                    done = set()

                # Clean up completed batch bookkeeping
                for fut in done:
                    # Find corresponding batch id(s)
                    bids = [bid for bid, f in self.batch_futures.items() if f is fut]
                    for bid in bids:
                        with self.lock:
                            self.active_batches.pop(bid, None)
                            self.batch_futures.pop(bid, None)

                # Start new batches if capacity available
                with self.lock:
                    while (
                        len(self.active_batches) < self.max_concurrent_batches
                        and not self.batch_queue.empty()
                    ):
                        try:
                            next_batch = self.batch_queue.get_nowait()
                            self._start_batch_processing(next_batch)
                        except queue.Empty:
                            break
            except Exception as exc:  # pragma: no cover
                logging.error("Error in manager loop: %s", exc)
                time.sleep(5)

    def _monitor_loop(self):
        while self.manager_running:
            try:
                with self.lock:
                    logging.info(
                        "Batch Status - Active: %s, Queued: %s",
                        len(self.active_batches),
                        self.batch_queue.qsize(),
                    )
                time.sleep(30)
            except Exception as exc:  # pragma: no cover
                logging.error("Error in monitor loop: %s", exc)
                time.sleep(60)

    # --------------------- Batch processing work ---------------------------
    def _update_batch_status(self, batch_id: str, status: str) -> None:
        with self.app.app_context():
            batch = Batch.query.filter_by(id=batch_id).first()
            if batch:
                batch.status = status
                if status == "in_progress":
                    batch.in_progress_at = datetime.utcnow()
                elif status == "failed":
                    batch.failed_at = datetime.utcnow()
                db.session.commit()

    def _process_batch_worker(self, batch_id: str):
        try:
            self._update_batch_status(batch_id, "in_progress")
            process_batch_with_pool(batch_id, self.thread_pool, self.app)
        except Exception as exc:  # pragma: no cover
            logging.error("Error processing batch %s: %s", batch_id, exc)
            self._update_batch_status(batch_id, "failed")
        finally:
            # Ensure scoped session is removed for this thread to avoid leaks
            try:
                db.session.remove()
            except Exception:
                pass


def recover_incomplete_batches(app):
    """Recover batches that were in progress before restart/redeployment"""
    try:
        with app.app_context():
            # Find batches in intermediate states that should be restarted
            incomplete_batches = Batch.query.filter(
                Batch.status.in_(['queued', 'in_progress', 'validating'])
            ).all()
            
            if incomplete_batches:
                logging.info(f"Found {len(incomplete_batches)} incomplete batches to recover")
                
                for batch in incomplete_batches:
                    try:
                        # Reset status based on current state
                        if batch.status == 'in_progress':
                            # If it was in progress, reset to validating to restart processing
                            batch.status = 'validating'
                            batch.in_progress_at = None  # Clear the in_progress timestamp
                        elif batch.status == 'queued':
                            # Keep as queued, will be picked up by the manager
                            pass
                        elif batch.status == 'validating':
                            # Keep as validating, will be processed
                            pass
                        
                        logging.info(f"Recovering batch {batch.id} from status '{batch.status}'")
                        
                    except Exception as e:
                        logging.error(f"Error recovering batch {batch.id}: {e}")
                        continue
                
                # Commit the status updates
                db.session.commit()
                
                # Re-submit all recovered batches to the manager
                for batch in incomplete_batches:
                    try:
                        if _batch_manager is not None:
                            _batch_manager.submit_batch(batch.id)
                            logging.info(f"Re-submitted batch {batch.id} to batch manager")
                    except Exception as e:
                        logging.error(f"Error re-submitting batch {batch.id}: {e}")
                
                logging.info(f"Successfully recovered {len(incomplete_batches)} batches")
            else:
                logging.info("No incomplete batches found to recover")
                
    except Exception as e:
        logging.error(f"Error during batch recovery: {e}")


# ---------------------------------------------------------------------------
# Public initializer / accessor
# ---------------------------------------------------------------------------


def init_batch_manager(app: "Flask") -> "GlobalBatchManager":  # noqa: D401
    """Create the singleton GlobalBatchManager and return it.

    This mirrors the API expected by route modules so that they can simply do
    ``from src.services.batch_manager import init_batch_manager`` and call it.
    """

    global _batch_manager

    if _batch_manager is None:
        _batch_manager = GlobalBatchManager(
            app=app,
            max_workers=MAX_WORKERS,
            max_concurrent_batches=MAX_CONCURRENT_BATCHES,
        )

        # Recover any unfinished batches from the database so they resume work
        recover_incomplete_batches(app)

    return _batch_manager


def get_batch_manager() -> "GlobalBatchManager | None":  # noqa: D401
    """Return the singleton instance if it has been initialised."""

    return _batch_manager


def process_batch_with_pool(batch_id, thread_pool, app=None):
    """Process batch requests using the shared global thread pool"""
    try:
        # Create a new application context for the thread
        if app is None:
            # Fallback to current_app if no app instance provided (for backward compatibility)
            from flask import current_app
            app = current_app._get_current_object()
        # Import here to avoid circular imports with routes
        from src.routes.batch import process_single_request  # type: ignore
        
        with app.app_context():
            batch = Batch.query.filter_by(id=batch_id).first()
            if not batch:
                return
            
            # Check if batch was cancelled
            if batch.status == 'cancelled':
                return

            # Read input file
            input_file_path = f"{UPLOAD_FOLDER}/{batch.input_file_id}"

            if ".jsonl" not in input_file_path:
                input_file_path = input_file_path + ".jsonl"
            
            # Read all lines first
            request_lines = []
            try:
                with open(input_file_path, 'r') as f:
                    for line in f:
                        if line.strip():
                            request_lines.append(line)
            except FileNotFoundError:
                raise Exception(f"Input file not found: {input_file_path}")
            
            if not request_lines:
                raise Exception("No valid request lines found in input file")
            
            # Process requests using the shared thread pool
            # We'll submit individual requests to the pool instead of creating a separate pool
            results = []
            token_usages = []
            
            # Submit all tasks to the global thread pool
            future_to_line = {}
            for line in request_lines:
                # Check for cancellation before submitting each request
                batch.query.session.refresh(batch)
                if batch.status == 'cancelled':
                    return
                
                future = thread_pool.submit(process_single_request, line, batch_id)
                future_to_line[future] = line
            
            # Collect results as they complete
            for future in as_completed(future_to_line):
                try:
                    # Check for cancellation periodically
                    batch.query.session.refresh(batch)
                    if batch.status == 'cancelled':
                        # Cancel remaining futures
                        for f in future_to_line:
                            if not f.done():
                                f.cancel()
                        return
                    
                    result = future.result()
                    
                    # Extract and save token usage data
                    if '_token_usage' in result:
                        token_usages.append(result['_token_usage'])
                        del result['_token_usage']  # Remove from result before saving
                    
                    results.append(result)
                except Exception as e:
                    # Handle any unexpected errors from the worker function
                    results.append({
                        "id": f"batch_req_{uuid.uuid4().hex}",
                        "custom_id": "unknown",
                        "response": None,
                        "error": {
                            "code": "executor_error",
                            "message": str(e)
                        }
                    })
            
            # Final cancellation check
            batch.query.session.refresh(batch)
            if batch.status == 'cancelled':
                return
            
            # Save output file
            output_file_id = f"file_{uuid.uuid4().hex}"
            output_file_path = f"{UPLOAD_FOLDER}/{output_file_id}.jsonl"
            
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
            
            with open(output_file_path, 'w') as f:
                for result in results:
                    f.write(json.dumps(result) + '\n')
            
            # Count errors for logging
            error_count = len([r for r in results if r['error'] is not None])
            completed_count = len([r for r in results if r['error'] is None])
            
            # Save token usage data in bulk
            if token_usages:
                try:
                    db.session.add_all(token_usages)
                    db.session.commit()
                    logging.info(f"Batch {batch_id}: Saved {len(token_usages)} token usage records")
                except Exception as token_error:
                    logging.error(f"Batch {batch_id}: Failed to save token usage data: {token_error}")
                    db.session.rollback()
            
            # Update batch status
            batch.status = 'completed'
            batch.completed_at = datetime.utcnow()
            batch.output_file_id = output_file_id
            batch.request_counts = {
                'total': len(results),
                'completed': completed_count,
                'failed': error_count
            }
            
            # Log batch completion with error count
            logging.info(f"Batch {batch_id} completed: {completed_count} successful, {error_count} errors out of {len(results)} total requests")
            
            db.session.commit()
            
            # Explicitly remove the session tied to this worker thread
            db.session.remove()
            
    except Exception as e:
        # Update batch status to failed
        print(f"Error processing batch {batch_id}: {e}")
        try:
            if app is None:
                print(f"Error updating batch status: No Flask app instance available")
                return
                
            with app.app_context():
                batch = Batch.query.filter_by(id=batch_id).first()
                if batch:
                    batch.status = 'failed'
                    batch.failed_at = datetime.utcnow()
                    batch.errors = [{'message': str(e)}]
                    db.session.commit()
                    # Clean up session after failure handling
                    db.session.remove()
        except Exception as commit_error:
            print(f"Error updating batch status: {commit_error}")