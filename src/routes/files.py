import os
import uuid
import json
import gzip
import zipfile
import bz2
import io
from typing import Iterator
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from datetime import datetime
from configs import get_config

files_bp = Blueprint('files', __name__)

# Load configuration
config = get_config()

# Use configuration values
UPLOAD_FOLDER = config.UPLOAD_FOLDER


def stream_jsonl_lines(file_storage, compression_format: str | None) -> Iterator[str]:
    """Yield decoded JSONL lines from an uploaded FileStorage object.

    This function streams the uploaded payload chunk-by-chunk to avoid loading
    large files into memory. It supports uncompressed and gzip-compressed
    uploads. Other formats fall back to a simple read which may still require
    additional memory.
    """

    if compression_format == 'gzip':
        # Wrap the binary stream directly with GzipFile and TextIOWrapper for
        # transparent decompression + decoding.
        with gzip.GzipFile(fileobj=file_storage.stream) as gz:
            for raw in gz:
                yield raw.decode('utf-8')

    elif compression_format in (None, ''):
        # Plain text – iterate over raw binary lines and decode.
        for raw in file_storage.stream:
            yield raw.decode('utf-8')

    else:
        # Fallback: unsupported streaming compression (zip/bz2). We read the
        # entire content (could be large) and decompress using existing util.
        # This maintains compatibility but not memory efficiency.
        content = file_storage.read()
        if compression_format:
            content = decompress_file(content, compression_format)
        for line in content.decode('utf-8').splitlines(True):
            yield line

def detect_compression_format(filename: str):
    """Detect the compression format of a file based on its extension."""
    filename_lower = filename.lower()
    if filename_lower.endswith(('.gz', '.gzip')):
        return 'gzip'
    if filename_lower.endswith('.zip'):
        return 'zip'
    if filename_lower.endswith('.bz2'):
        return 'bz2'
    return None

def decompress_file(file_content, compression_format):
    """Decompress file content based on the compression format"""
    try:
        if compression_format == 'gzip':
            return gzip.decompress(file_content)
        elif compression_format == 'zip':
            with zipfile.ZipFile(io.BytesIO(file_content)) as zip_file:
                # Get the first file in the zip
                file_list = zip_file.namelist()
                if not file_list:
                    raise ValueError("Empty zip file")
                # Use the first file, preferably a .jsonl file
                target_file = None
                for file in file_list:
                    if file.endswith('.jsonl'):
                        target_file = file
                        break
                if not target_file:
                    target_file = file_list[0]
                return zip_file.read(target_file)
        elif compression_format == 'bz2':
            return bz2.decompress(file_content)
        else:
            raise ValueError(f"Unsupported compression format: {compression_format}")
    except Exception as e:
        raise ValueError(f"Failed to decompress file: {str(e)}")

@files_bp.route('/files', methods=['POST'])
def upload_file():
    """Upload a file for batch processing"""
    try:
        if 'file' not in request.files:
            return jsonify({
                'error': {
                    'message': 'No file provided',
                    'type': 'invalid_request_error'
                }
            }), 400
        
        file = request.files['file']
        purpose = request.form.get('purpose', 'batch')
        
        if file.filename == '':
            return jsonify({
                'error': {
                    'message': 'No file selected',
                    'type': 'invalid_request_error'
                }
            }), 400
        
        # Generate unique file ID
        file_id = f"file_{uuid.uuid4().hex[:8]}"
        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.jsonl")
        
        # Determine compression format (by extension only to avoid reading into memory)
        compression_format = detect_compression_format(filename)

        # For compression ratio stats we rely on the raw request payload size if available
        original_size = request.content_length or 0

        # Stream-decompress / copy while validating each JSONL line
        try:
            with open(file_path, 'w', encoding='utf-8') as dest:
                for raw_line in stream_jsonl_lines(file, compression_format):
                    line = raw_line.rstrip('\n')
                    if line.strip():
                        json.loads(line)  # validate JSON per line
                    dest.write(line + '\n')
        except json.JSONDecodeError:
            # Remove partially written file
            if os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({
                'error': {
                    'message': 'Invalid JSONL format',
                    'type': 'invalid_request_error'
                }
            }), 400
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({
                'error': {
                    'message': f'Failed to process file: {str(e)}',
                    'type': 'invalid_request_error'
                }
            }), 400

        # File successfully written, gather stats
        file_size = os.path.getsize(file_path)
        
        response_data = {
            'id': file_id,
            'object': 'file',
            'bytes': file_size,
            'created_at': int(datetime.utcnow().timestamp()),
            'filename': filename,
            'purpose': purpose
        }
        
        # Add compression information if file was compressed and original size known
        if compression_format and original_size:
            response_data['compression'] = {
                'format': compression_format,
                'original_size': original_size,
                'decompressed_size': file_size,
                'compression_ratio': round(original_size / file_size, 2) if file_size > 0 else 1
            }
        
        return jsonify(response_data), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@files_bp.route('/files/<file_id>', methods=['GET'])
def get_file_info(file_id):
    """Get file information"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.jsonl")
        
        if not os.path.exists(file_path):
            return jsonify({
                'error': {
                    'message': f'File {file_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        file_size = os.path.getsize(file_path)
        created_at = os.path.getctime(file_path)
        
        return jsonify({
            'id': file_id,
            'object': 'file',
            'bytes': file_size,
            'created_at': int(created_at),
            'filename': f"{file_id}.jsonl",
            'purpose': 'batch'
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500
    
@files_bp.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    """Delete a file"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.jsonl")
        os.remove(file_path)
        return jsonify({
            'id': file_id,
            'object': 'file',
            'deleted': True
        }), 200
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@files_bp.route('/files/<file_id>/content', methods=['GET'])
def download_file(file_id):
    """Download file content"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.jsonl")
        
        if not os.path.exists(file_path):
            return jsonify({
                'error': {
                    'message': f'File {file_id} not found',
                    'type': 'not_found_error'
                }
            }), 404
        
        return send_file(file_path, as_attachment=True, download_name=f"{file_id}.jsonl")
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@files_bp.route('/files', methods=['GET'])
def list_files():
    """List all uploaded files"""
    try:
        files = []
        
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.endswith('.jsonl'):
                file_id = filename.replace('.jsonl', '')
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file_size = os.path.getsize(file_path)
                created_at = os.path.getctime(file_path)
                
                files.append({
                    'id': file_id,
                    'object': 'file',
                    'bytes': file_size,
                    'created_at': int(created_at),
                    'filename': filename,
                    'purpose': 'batch'
                })
        
        # Sort by created_at descending
        files.sort(key=lambda x: x['created_at'], reverse=True)
        
        return jsonify({
            'object': 'list',
            'data': files
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

