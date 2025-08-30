#!/usr/bin/env python3
"""
Test script for OpenAI Batch API
"""
import requests
import json
import time
import sys
import os
import pytest

user_prompt = "Hello"
system_prompt = "You are a helpful assistant."


BASE_URL = os.getenv("BASE_URL", "http://localhost:5000/v1")
MODEL = os.getenv("MODEL_NAME", 'openai/gpt-4.1-nano')

def test_file_upload():
    """Test file upload functionality"""
    print("Testing file upload...")
    
    # Create a test JSONL file
    test_data = [
        {
            "custom_id": "request-1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                "max_tokens": 1000,
                "temperature": 0.0
            }
        },
        {
            "custom_id": "request-2",
            "method": "POST", 
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                "max_tokens": 1000,
                "temperature": 0.0
            }
        }
    ]
    
    # Write test file
    with open('/tmp/test_batch.jsonl', 'w') as f:
        for item in test_data:
            f.write(json.dumps(item) + '\n')
    
    # Upload file
    try:
        with open('/tmp/test_batch.jsonl', 'rb') as f:
            files = {'file': f}
            data = {'purpose': 'batch'}
            response = requests.post(f"{BASE_URL}/files", files=files, data=data)
        
        if response.status_code == 200:
            file_info = response.json()
            print(f"✓ File uploaded successfully: {file_info['id']}")
            return file_info['id']
        else:
            print(f"✗ File upload failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"✗ File upload error: {e}")
        return None

def test_create_batch(file_id):
    """Test batch creation"""
    print("Testing batch creation...")
    
    batch_data = {
        "input_file_id": file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
        "metadata": {"test": "true"}
    }
    
    try:
        response = requests.post(f"{BASE_URL}/batches", json=batch_data)
        
        if response.status_code == 200:
            batch_info = response.json()
            print(f"✓ Batch created successfully: {batch_info['id']}")
            return batch_info['id']
        else:
            print(f"✗ Batch creation failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"✗ Batch creation error: {e}")
        return None

def test_get_batch(batch_id):
    """Test batch retrieval"""
    print("Testing batch retrieval...")
    
    try:
        response = requests.get(f"{BASE_URL}/batches/{batch_id}")
        
        if response.status_code == 200:
            batch_info = response.json()
            print(f"✓ Batch retrieved successfully: {batch_info['status']}")
            return batch_info
        else:
            print(f"✗ Batch retrieval failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"✗ Batch retrieval error: {e}")
        return None

def test_list_batches():
    """Test batch listing"""
    print("Testing batch listing...")
    
    try:
        response = requests.get(f"{BASE_URL}/batches")
        
        if response.status_code == 200:
            batches = response.json()
            print(f"✓ Batches listed successfully: {len(batches.get('data', []))} batches")
            return True
        else:
            print(f"✗ Batch listing failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Batch listing error: {e}")
        return False

def test_dashboard():
    """Test dashboard endpoint"""
    print("Testing dashboard endpoint...")
    
    try:
        # Test basic dashboard
        response = requests.get(f"{BASE_URL}/batches/dashboard")
        
        if response.status_code == 200:
            dashboard = response.json()
            print(f"✓ Dashboard retrieved successfully")
            
            # Verify structure
            if 'batches' in dashboard and 'summary' in dashboard and 'pagination' in dashboard:
                batches = dashboard['batches']
                summary = dashboard['summary']
                pagination = dashboard['pagination']
                
                print(f"  - Total batches in summary: {summary.get('total_batches', 0)}")
                print(f"  - Batches in current page: {len(batches)}")
                print(f"  - Overall error rate: {summary.get('overall_error_rate_percentage', 0)}%")
                print(f"  - Overall tokens: {summary.get('overall_token_usage', {}).get('total_tokens', 0)}")
                
                # Test first batch data structure if available
                if batches:
                    first_batch = batches[0]
                    required_fields = ['id', 'status', 'error_rate_percentage', 'token_usage']
                    missing_fields = [field for field in required_fields if field not in first_batch]
                    
                    if not missing_fields:
                        print(f"  - First batch example: {first_batch['id']} ({first_batch['status']}) - {first_batch['error_rate_percentage']}% error rate")
                        print(f"  - Token usage: {first_batch['token_usage'].get('total_tokens', 0)} tokens")
                    else:
                        print(f"  ⚠ Missing fields in batch data: {missing_fields}")
                
                return True
            else:
                print(f"✗ Dashboard structure invalid - missing required sections")
                return False
        else:
            print(f"✗ Dashboard failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Dashboard error: {e}")
        return False

def test_dashboard_pagination():
    """Test dashboard pagination"""
    print("Testing dashboard pagination...")
    
    try:
        # Test with pagination parameters
        response = requests.get(f"{BASE_URL}/batches/dashboard?page=1&limit=5")
        
        if response.status_code == 200:
            dashboard = response.json()
            pagination = dashboard.get('pagination', {})
            
            print(f"✓ Dashboard pagination works")
            print(f"  - Page: {pagination.get('page', 'N/A')}")
            print(f"  - Limit: {pagination.get('limit', 'N/A')}")
            print(f"  - Has more: {pagination.get('has_more', 'N/A')}")
            
            # Verify we don't get more than requested limit
            batches = dashboard.get('batches', [])
            if len(batches) <= 5:
                print(f"  - Correct batch count: {len(batches)} (≤ 5)")
                return True
            else:
                print(f"  ⚠ Too many batches returned: {len(batches)} (should be ≤ 5)")
                return False
        else:
            print(f"✗ Dashboard pagination failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Dashboard pagination error: {e}")
        return False

@pytest.fixture(scope="module")
def file_id():
    """Fixture that uploads the test file and returns its id"""
    fid = test_file_upload()
    assert fid is not None, "File upload failed in fixture"
    return fid


@pytest.fixture(scope="module")
def batch_id(file_id):
    """Fixture that creates a batch using the uploaded file and returns its id"""
    bid = test_create_batch(file_id)
    assert bid is not None, "Batch creation failed in fixture"
    return bid

def main():
    """Run all tests"""
    print("Starting OpenAI Batch API Tests...")
    print("=" * 50)
    
    # Test file upload
    file_id = test_file_upload()
    if not file_id:
        print("File upload failed, stopping tests")
        sys.exit(1)
    
    # Test batch creation
    batch_id = test_create_batch(file_id)
    if not batch_id:
        print("Batch creation failed, stopping tests")
        sys.exit(1)
    
    # Wait a moment for processing
    print("Waiting for batch processing...")
    time.sleep(5)
    
    # Test batch retrieval
    batch_info = test_get_batch(batch_id)
    print(batch_info)
    if not batch_info:
        print("Batch retrieval failed")
        sys.exit(1)

    # Fetch output file
    output_file_id = batch_info['output_file_id']
    print(f"Output file ID: {output_file_id}")
    response = requests.get(f"{BASE_URL}/files/{output_file_id}/content")
    
    [print(json.loads(line)) for line in response.text.splitlines()]
    
    # Test batch listing
    if not test_list_batches():
        print("Batch listing failed")
        sys.exit(1)
    
    print("=" * 50)
    print("All tests completed successfully!")

if __name__ == "__main__":
    main()

