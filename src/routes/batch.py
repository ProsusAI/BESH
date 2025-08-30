import os
import json
import uuid
import threading
import queue
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
import litellm
import logging
from src.models.batch import Batch, db
from src.models.token_usage import TokenUsage
from configs import get_config

# After existing imports add service mapping
from src.services.batch_manager import init_batch_manager as service_init_batch_manager, \
    GlobalBatchManager as ServiceGlobalBatchManager, recover_incomplete_batches  # noqa: E501

# Re-export service functions/classes so existing code continues to work
init_batch_manager = service_init_batch_manager  # type: ignore
GlobalBatchManager = ServiceGlobalBatchManager  # type: ignore

batch_bp = Blueprint('batch', __name__)

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

# Global instance of batch manager - will be initialized with app in main.py
batch_manager = None


def init_batch_manager(app):
    """Initialize the global batch manager with the Flask app instance"""
    global batch_manager
    if batch_manager is None:
        batch_manager = GlobalBatchManager(app=app, max_workers=MAX_WORKERS, max_concurrent_batches=MAX_CONCURRENT_BATCHES)
        
        # Recover any incomplete batches after manager initialization
        recover_incomplete_batches(app)
        
    return batch_manager

@batch_bp.route('/batches', methods=['POST'])
def create_batch():
    """Create a new batch job"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'input_file_id' not in data or 'endpoint' not in data:
            return jsonify({
                'error': {
                    'message': 'Missing required fields: input_file_id and endpoint',
                    'type': 'invalid_request_error'
                }
            }), 400
        
        # Create new batch
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        completion_window = data.get('completion_window', '24h')
        
        # Calculate expires_at (24 hours from now)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        
        batch = Batch(
            id=batch_id,
            object='batch',
            endpoint=data['endpoint'],
            input_file_id=data['input_file_id'],
            completion_window=completion_window,
            status='validating',
            created_at=datetime.utcnow(),
            expires_at=expires_at
        )
        
        # Set metadata using the property
        batch.batch_metadata = data.get('metadata', {})
        
        db.session.add(batch)
        db.session.commit()
        
        # Submit batch to global batch manager for processing
        if batch_manager is None:
            # Auto-initialize if not done yet (fallback safety)
            init_batch_manager(current_app._get_current_object())
        batch_manager.submit_batch(batch_id)
        
        return jsonify(batch.to_dict()), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/<batch_id>', methods=['GET'])
def get_batch(batch_id):
    """Retrieve a specific batch"""
    try:
        batch = Batch.query.filter_by(id=batch_id).first()
        
        if not batch:
            return jsonify({
                'error': {
                    'message': f'Batch {batch_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        return jsonify(batch.to_dict()), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/<batch_id>/cancel', methods=['POST'])
def cancel_batch(batch_id):
    """Cancel a batch job"""
    try:
        batch = Batch.query.filter_by(id=batch_id).first()
        
        if not batch:
            return jsonify({
                'error': {
                    'message': f'Batch {batch_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        if batch.status in ['completed', 'failed', 'cancelled', 'expired']:
            return jsonify({
                'error': {
                    'message': f'Cannot cancel batch with status: {batch.status}',
                    'type': 'invalid_request_error'
                }
            }), 400
        
        batch.status = 'cancelling'
        batch.cancelled_at = datetime.utcnow()
        db.session.commit()
        
        # Update to cancelled status - the background processing will check this
        batch.status = 'cancelled'
        db.session.commit()
        
        return jsonify(batch.to_dict()), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/<batch_id>', methods=['DELETE'])
def delete_batch(batch_id):
    """Delete a batch job and its associated data"""
    try:
        batch = Batch.query.filter_by(id=batch_id).first()
        
        if not batch:
            return jsonify({
                'error': {
                    'message': f'Batch {batch_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        # Delete associated token usage records
        TokenUsage.query.filter_by(batch_id=batch_id).delete()
        
        # Delete the batch files if they exist
        try:
            input_file_path = f"{UPLOAD_FOLDER}/{batch.input_file_id}"
            if not input_file_path.endswith('.jsonl'):
                input_file_path += '.jsonl'
            if os.path.exists(input_file_path):
                os.remove(input_file_path)
        except Exception as file_error:
            logging.warning(f"Failed to delete input file for batch {batch_id}: {file_error}")
        
        try:
            if batch.output_file_id:
                output_file_path = f"{UPLOAD_FOLDER}/{batch.output_file_id}.jsonl"
                if os.path.exists(output_file_path):
                    os.remove(output_file_path)
        except Exception as file_error:
            logging.warning(f"Failed to delete output file for batch {batch_id}: {file_error}")
        
        # Delete the batch record
        db.session.delete(batch)
        db.session.commit()
        
        return jsonify({
            'message': f'Batch {batch_id} deleted successfully'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches', methods=['GET'])
def list_batches():
    """List all batches"""
    try:
        after = request.args.get('after')
        limit = min(int(request.args.get('limit', 20)), 100)  # Max 100
        
        query = Batch.query.order_by(Batch.created_at.desc())
        
        if after:
            # Simple pagination using created_at timestamp
            try:
                after_batch = Batch.query.filter_by(id=after).first()
                if after_batch:
                    query = query.filter(Batch.created_at < after_batch.created_at)
            except:
                pass
        
        batches = query.limit(limit).all()
        
        return jsonify({
            'object': 'list',
            'data': [batch.to_dict() for batch in batches],
            'has_more': len(batches) == limit
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/status', methods=['GET'])
def get_batch_manager_status():
    """Get the current status of the batch manager"""
    try:
        if batch_manager is None:
            # Auto-initialize if not done yet (fallback safety)
            init_batch_manager(current_app._get_current_object())
        status = batch_manager.get_status()
        
        # Also get database statistics
        total_batches = Batch.query.count()
        active_db_batches = Batch.query.filter(Batch.status.in_(['in_progress', 'queued'])).count()
        
        status.update({
            'total_batches_in_db': total_batches,
            'active_batches_in_db': active_db_batches
        })
        
        return jsonify(status), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/analytics/timeline', methods=['GET'])
def get_batch_timeline():
    """Get batch creation analytics for the last 24 hours in 15-minute intervals"""
    try:
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Calculate 24 hours ago
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        # Get batches created in the last 24 hours
        batches = Batch.query.filter(
            Batch.created_at >= start_time,
            Batch.created_at <= end_time
        ).all()
        
        # Create 15-minute intervals
        intervals = []
        current_time = start_time
        
        while current_time < end_time:
            interval_end = current_time + timedelta(minutes=15)
            
            # Count batches in this interval
            count = sum(1 for batch in batches 
                       if current_time <= batch.created_at < interval_end)
            
            intervals.append({
                'timestamp': current_time.isoformat(),
                'count': count,
                'label': current_time.strftime('%H:%M')
            })
            
            current_time = interval_end
        
        # Calculate summary statistics
        total_batches = len(batches)
        avg_per_interval = total_batches / len(intervals) if intervals else 0
        max_in_interval = max(interval['count'] for interval in intervals) if intervals else 0
        
        return jsonify({
            'object': 'batch_timeline',
            'intervals': intervals,
            'summary': {
                'total_batches': total_batches,
                'avg_per_interval': round(avg_per_interval, 2),
                'max_in_interval': max_in_interval,
                'time_range': {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/analytics/tokens', methods=['GET'])
def get_token_analytics():
    """Get token usage analytics for the last 24 hours in 15-minute intervals"""
    try:
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Calculate 24 hours ago
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        # Get completed batches in the last 24 hours with their token usage and duration
        batches_with_tokens = db.session.query(
            Batch.completed_at,
            Batch.created_at,
            Batch.in_progress_at,
            func.sum(TokenUsage.prompt_tokens).label('input_tokens'),
            func.sum(TokenUsage.completion_tokens).label('output_tokens'),
            func.sum(TokenUsage.total_tokens).label('total_tokens')
        ).join(
            TokenUsage, Batch.id == TokenUsage.batch_id
        ).filter(
            Batch.completed_at >= start_time,
            Batch.completed_at <= end_time,
            Batch.completed_at.isnot(None),
            Batch.in_progress_at.isnot(None),
            Batch.created_at.isnot(None)
        ).group_by(Batch.id, Batch.completed_at, Batch.in_progress_at, Batch.created_at).all()
        
        # Create 15-minute intervals
        intervals = []
        current_time = start_time
        
        while current_time < end_time:
            interval_end = current_time + timedelta(minutes=15)
            
            # Sum tokens and duration for batches completed in this interval
            input_tokens = 0
            output_tokens = 0
            total_duration = 0
            batch_count = 0
            
            for batch_data in batches_with_tokens:
                if batch_data.completed_at and current_time <= batch_data.completed_at < interval_end:
                    input_tokens += batch_data.input_tokens or 0
                    output_tokens += batch_data.output_tokens or 0
                    
                    # Calculate duration for this batch
                    if batch_data.in_progress_at and batch_data.completed_at:
                        duration = batch_data.completed_at - batch_data.in_progress_at
                        total_duration += duration.total_seconds()
                        batch_count += 1
            
            intervals.append({
                'timestamp': current_time.isoformat(),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': input_tokens + output_tokens,
                'duration_seconds': total_duration,
                'avg_duration_seconds': total_duration / batch_count if batch_count > 0 else 0,
                'batch_count': batch_count,
                'label': current_time.strftime('%H:%M')
            })
            
            current_time = interval_end
        
        # Calculate summary statistics
        total_input_tokens = sum(interval['input_tokens'] for interval in intervals)
        total_output_tokens = sum(interval['output_tokens'] for interval in intervals)
        total_tokens = total_input_tokens + total_output_tokens
        total_duration = sum(interval['duration_seconds'] for interval in intervals)
        total_batches = sum(interval['batch_count'] for interval in intervals)
        avg_per_interval = total_tokens / len(intervals) if intervals else 0
        peak_interval = max(interval['total_tokens'] for interval in intervals) if intervals else 0
        avg_duration = total_duration / total_batches if total_batches > 0 else 0
        
        return jsonify({
            'object': 'token_timeline',
            'intervals': intervals,
            'summary': {
                'total_input_tokens': total_input_tokens,
                'total_output_tokens': total_output_tokens,
                'total_tokens': total_tokens,
                'total_duration_seconds': total_duration,
                'avg_duration_seconds': round(avg_duration, 2),
                'total_batches': total_batches,
                'avg_per_interval': round(avg_per_interval, 2),
                'peak_interval': peak_interval,
                'time_range': {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/<batch_id>/token_usage', methods=['GET'])
def get_batch_token_usage(batch_id):
    """Get token usage statistics for a specific batch"""
    try:
        batch = Batch.query.filter_by(id=batch_id).first()
        
        if not batch:
            return jsonify({
                'error': {
                    'message': f'Batch {batch_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        # Get token usage summary
        token_summary = TokenUsage.get_batch_summary(batch_id)
        
        return jsonify(token_summary), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@batch_bp.route('/batches/dashboard', methods=['GET'])
def get_batches_dashboard():
    """Get dashboard view of batches with pagination, token usage, and error rates"""
    try:
        # Get pagination parameters
        page = max(int(request.args.get('page', 1)), 1)
        limit = min(int(request.args.get('limit', 10)), 50)  # Max 50 batches per page
        offset = (page - 1) * limit
        
        # Filter batches to the same 24-hour window as analytics graphs for consistency
        from datetime import datetime, timedelta
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        # Get batches from the last 24 hours with pagination
        base_query = Batch.query.filter(
            Batch.created_at >= start_time,
            Batch.created_at <= end_time
        ).order_by(Batch.created_at.desc())
        
        total_batches = base_query.count()
        batches = base_query.offset(offset).limit(limit).all()
        
        # Prepare dashboard data
        dashboard_batches = []
        for batch in batches:
            # Get token usage summary for this batch
            token_summary = TokenUsage.get_batch_summary(batch.id)
            
            # Calculate error rate
            request_counts = batch.request_counts
            total_requests = request_counts.get('total', 0)
            failed_requests = request_counts.get('failed', 0)
            error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0
            
            # Build batch dashboard entry
            batch_data = {
                'id': batch.id,
                'status': batch.status,
                'endpoint': batch.endpoint,
                'created_at': batch.created_at.isoformat() if batch.created_at else None,
                'completed_at': batch.completed_at.isoformat() if batch.completed_at else None,
                'duration_seconds': None,
                'request_counts': request_counts,
                'error_rate_percentage': round(error_rate, 2),
                'token_usage': {
                    'total_tokens': token_summary.get('total_tokens', 0),
                    'prompt_tokens': token_summary.get('prompt_tokens', 0),
                    'completion_tokens': token_summary.get('completion_tokens', 0),
                    'total_cost': token_summary.get('total_cost', 0.0),
                    'request_count': token_summary.get('request_count', 0)
                }
            }
            
            # Calculate duration if both timestamps are available
            if batch.in_progress_at and batch.completed_at:
                duration = batch.completed_at - batch.in_progress_at
                batch_data['duration_seconds'] = duration.total_seconds()
            
            dashboard_batches.append(batch_data)
        
        # Calculate overall statistics (limited to same 24-hour window for consistency)
        from sqlalchemy import func
        
        # Overall token usage across batches in the 24-hour window
        overall_tokens = db.session.query(
            func.sum(TokenUsage.total_tokens).label('total_tokens'),
            func.sum(TokenUsage.prompt_tokens).label('prompt_tokens'),
            func.sum(TokenUsage.completion_tokens).label('completion_tokens'),
            func.sum(TokenUsage.cost).label('total_cost'),
            func.count(TokenUsage.id).label('total_requests')
        ).join(Batch, TokenUsage.batch_id == Batch.id).filter(
            Batch.created_at >= start_time,
            Batch.created_at <= end_time
        ).first()
        
        # Overall batch statistics (within 24-hour window)
        status_stats = db.session.query(
            Batch.status,
            func.count(Batch.id).label('count')
        ).filter(
            Batch.created_at >= start_time,
            Batch.created_at <= end_time
        ).group_by(Batch.status).all()
        
        # Calculate overall error rate (within 24-hour window)
        overall_request_counts = db.session.query(
            func.sum(func.json_extract(Batch.request_counts_json, '$.total')).label('total_requests'),
            func.sum(func.json_extract(Batch.request_counts_json, '$.failed')).label('failed_requests')
        ).filter(
            Batch.created_at >= start_time,
            Batch.created_at <= end_time
        ).first()
        
        overall_error_rate = 0
        if overall_request_counts.total_requests and overall_request_counts.total_requests > 0:
            overall_error_rate = (overall_request_counts.failed_requests or 0) / overall_request_counts.total_requests * 100
        
        # Build summary statistics
        summary = {
            'total_batches': total_batches,
            'batches_by_status': {status: count for status, count in status_stats},
            'overall_error_rate_percentage': round(overall_error_rate, 2),
            'overall_token_usage': {
                'total_tokens': overall_tokens.total_tokens or 0,
                'prompt_tokens': overall_tokens.prompt_tokens or 0,
                'completion_tokens': overall_tokens.completion_tokens or 0,
                'total_cost': float(overall_tokens.total_cost or 0.0),
                'total_requests': overall_tokens.total_requests or 0
            }
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
        
        return jsonify({
            'object': 'dashboard',
            'batches': dashboard_batches,
            'summary': summary,
            'pagination': pagination
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

def process_single_request(request_line, batch_id=None):
    """Process a single request line and return the result"""
    try:
        request_data = json.loads(request_line.strip())
        
        try:
            # Use litellm for completion (synchronous version)
            extra_kwargs = {i:j for i,j in request_data['body'].items() if i not in ['model', 'messages']}
            response = litellm.completion(
                model=request_data['body']['model'],
                messages=request_data['body']['messages'],
                **extra_kwargs
            )
            
            result = {
                "id": f"batch_req_{uuid.uuid4().hex[:8]}",
                "custom_id": request_data['custom_id'],
                "response": {
                    "status_code": 200,
                    "request_id": f"req_{uuid.uuid4().hex[:8]}",
                    "body": response.model_dump()
                },
                "error": None
            }
            
            # Track token usage if batch_id is provided
            if batch_id:
                try:
                    # Extract token costs from response
                    usage = response.usage if hasattr(response, 'usage') else None
                    # Usage(completion_tokens=79, prompt_tokens=12, total_tokens=91
                    
                    token_usage = TokenUsage(
                        batch_id=batch_id,
                        request_id=result['id'],
                        custom_id=request_data['custom_id'],
                        model=request_data['body']['model'],
                        total_tokens=usage.total_tokens if usage else 0,
                        prompt_tokens=usage.prompt_tokens if usage else 0,
                        completion_tokens=usage.completion_tokens if usage else 0,
                    )
                    result['_token_usage'] = token_usage
                except Exception as token_error:
                    logging.warning(f"Failed to track token usage for request {result['id']}: {token_error}")
            
        except Exception as e:
            result = {
                "id": f"batch_req_{uuid.uuid4().hex[:8]}",
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
            "id": f"batch_req_{uuid.uuid4().hex[:8]}",
            "custom_id": "unknown",
            "response": None,
            "error": {
                "code": "parsing_error",
                "message": str(e)
            }
        }

