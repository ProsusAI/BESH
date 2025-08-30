#!/usr/bin/env python3
"""
Demo script testing compressed file uploads for batch processing

This script demonstrates how the batch endpoint now supports compressed files
(gzip format) to speed up uploads and reduce bandwidth usage.
"""

import requests
import json
import time
import os
import gzip

import os

user_prompt = "Hello"
system_prompt = "You are a helpful assistant."

BASE_URL = os.getenv("BASE_URL", "http://localhost:5000/v1")
MODEL = os.getenv("MODEL_NAME", 'openai/gpt-4.1-nano')
BATCH_COUNT = 10
REQUESTS_PER_BATCH = 10


def create_sample_batch_file(filename, num_requests=10, compression='gzip'):
    """Create a sample batch file for testing with gzip compression by default"""
    os.makedirs("/tmp/batch_files", exist_ok=True)
    
    requests_data = []
    for i in range(num_requests):
        request_data = {
            "custom_id": f"request_{i}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 2000
            }
        }
        requests_data.append(request_data)
    
    # Create the JSON content
    content = ""
    for req in requests_data:
        content += json.dumps(req) + '\n'
    
    # Use gzip compression for best results
    if compression == 'gzip':
        filepath = f"/tmp/batch_files/{filename}.gz"
        with gzip.open(filepath, 'wt', encoding='utf-8') as f:
            f.write(content)
        return f"{filename}.gz"
    else:
        # No compression fallback
        filepath = f"/tmp/batch_files/{filename}"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filename

def upload_file(filename):
    """Upload a file using the files API"""
    filepath = f"/tmp/batch_files/{filename}"
    
    try:
        file_size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            files = {'file': (filename, f)}
            data = {'purpose': 'batch'}
            response = requests.post(f"{BASE_URL}/files", files=files, data=data)
        
        if response.status_code == 200:
            file_info = response.json()
            print(f"✅ File uploaded: {filename} (size: {file_size:,} bytes) -> {file_info['id']}")
            
            # Show compression info if available
            if 'compression' in file_info:
                comp_info = file_info['compression']
                print(f"   📦 Compression: {comp_info['format']}, ratio: {comp_info['compression_ratio']}:1")
                print(f"   📊 Original: {comp_info['original_size']:,} bytes -> Decompressed: {comp_info['decompressed_size']:,} bytes")
            
            return file_info['id']
        else:
            print(f"❌ File upload failed for {filename}: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"❌ File upload error for {filename}: {e}")
        return None

def create_batch(file_id, endpoint="/v1/chat/completions"):
    """Create a new batch job"""
    data = {
        "input_file_id": file_id,
        "endpoint": endpoint,
        "completion_window": "24h",
        "metadata": {"demo": "batch_processing"}
    }
    
    response = requests.post(f"{BASE_URL}/batches", json=data)
    return response.json()

def get_batch_status(batch_id):
    """Get status of a specific batch"""
    response = requests.get(f"{BASE_URL}/batches/{batch_id}")
    return response.json()

def get_manager_status():
    """Get the overall batch manager status"""
    response = requests.get(f"{BASE_URL}/batches/status")
    return response.json()

def main():
    print("🚀 Batch Manager Demo - Compressed File Upload Testing")
    print("=" * 60)
    
    # Create multiple batch files with gzip compression
    batch_files = []
    print(f"📁 Creating {BATCH_COUNT} compressed batch files (gzip)...")
    for i in range(BATCH_COUNT):
        filename = create_sample_batch_file(f"demo_batch_{i}.jsonl", REQUESTS_PER_BATCH)
        batch_files.append(filename)
        print(f"✅ Created compressed batch file: {filename}")
    
    # Show compression stats
    print(f"\n📊 Compression Stats:")
    total_original = 0
    total_compressed = 0
    for filename in batch_files:
        filepath = f"/tmp/batch_files/{filename}"
        compressed_size = os.path.getsize(filepath)
        
        # Estimate uncompressed size by reading back
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            content = f.read()
            uncompressed_size = len(content.encode('utf-8'))
        
        ratio = uncompressed_size / compressed_size if compressed_size > 0 else 1
        total_original += uncompressed_size
        total_compressed += compressed_size
        
        print(f"  {filename}: {uncompressed_size:,} → {compressed_size:,} bytes (ratio: {ratio:.1f}:1)")
    
    overall_ratio = total_original / total_compressed if total_compressed > 0 else 1
    print(f"  📈 Overall: {total_original:,} → {total_compressed:,} bytes (ratio: {overall_ratio:.1f}:1)")
    
    print(f"\n📊 Initial Manager Status:")
    status = get_manager_status()
    print(json.dumps(status, indent=2))
    
    # Upload files and collect file IDs
    uploaded_file_ids = []
    print(f"\n📤 Uploading {len(batch_files)} files...")
    
    for i, filename in enumerate(batch_files):
        file_id = upload_file(filename)
        if file_id:
            uploaded_file_ids.append(file_id)
        else:
            print(f"❌ Skipping batch {i+1} due to file upload failure")
    
    if not uploaded_file_ids:
        print("❌ No files uploaded successfully, stopping demo")
        return

#     # Get existing files
#     response = requests.get(f"{BASE_URL}/files")
#     uploaded_file_ids = [file['id'] for file in response.json()['data']]
#     print(f"✅ Found {len(uploaded_file_ids)} files")
    
    # Submit multiple batches quickly to test queuing
    batch_ids = []
    print(f"\n🔄 Submitting {len(uploaded_file_ids)} batches...")
    
    for i, file_id in enumerate(uploaded_file_ids):
        batch_result = create_batch(file_id)
        if 'id' in batch_result:
            batch_ids.append(batch_result['id'])
            print(f"✅ Batch {i+1} submitted: {batch_result['id']} (Status: {batch_result.get('status', 'unknown')})")
        else:
            print(f"❌ Failed to create batch {i+1}: {batch_result}")
        
        time.sleep(0.2)
    
    final_manager_status = get_manager_status()
    print(f"\n📊 Final Manager Status:")
    print(json.dumps(final_manager_status, indent=2))

    return batch_ids

def test_large():
    batch_ids = main()
    len_batch_ids = len(batch_ids)
    
    for _ in range(200):
        
        # Get manager status
        manager_status = get_manager_status()
        
        # Check individual batch statuses
        completed_count = 0
        for batch_id in batch_ids:
            batch_status = get_batch_status(batch_id)
            status_text = batch_status.get('status', 'unknown')
            
            if status_text in ['completed', 'failed', 'cancelled']:
                completed_count += 1
        
        # Stop monitoring when all batches are done
        if completed_count == len(batch_ids):
            break
        
        time.sleep(3)  # Wait 3 seconds between checks

    # Final status summary
    retrieved_batch = 0
    completed_count = 0
    errors_count = 0
    for batch_id in batch_ids:
        batch_status = get_batch_status(batch_id)
        status_text = batch_status.get('status', 'unknown')
        request_counts = batch_status.get('request_counts', {})

        if status_text == 'completed':
            retrieved_batch += 1
        
        errors_count += request_counts.get("failed", 0)
        completed_count += request_counts.get("completed", 0)
    
    assert retrieved_batch == len_batch_ids, "Not all batches retrieved"
    assert errors_count == 0, "There are errors in the batches"
    assert completed_count == len_batch_ids * REQUESTS_PER_BATCH, "Not all batches completed"

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️  Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
