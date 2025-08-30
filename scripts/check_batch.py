import requests
import sys
import json
import argparse

def test_get_batch(batch_id, base_url):
    """Test batch retrieval"""
    print("Testing batch retrieval...")

    try:
        response = requests.get(f"{base_url}/batches/{batch_id}")

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

def print_batch_preview(batch_id, base_url, max_lines=10):
    # Test batch retrieval
    batch_info = test_get_batch(batch_id, base_url)
    print("Batch info for batch_id: ", batch_id)
    print(json.dumps(batch_info, indent=2))
    print("-" * 100)

    if not batch_info:
        print("Batch retrieval failed")
        sys.exit(1)

    # Fetch output file
    output_file_id = batch_info.get('output_file_id')
    if not output_file_id:
        print("No output_file_id found in batch info.")
        sys.exit(1)
    print(f"Output file ID: {output_file_id}")
    response = requests.get(f"{base_url}/files/{output_file_id}/content")

    for nr, line in enumerate(response.text.splitlines()):
        try:
            print(json.dumps(json.loads(line), indent=2))
        except Exception as e:
            print(f"Error parsing line {nr}: {e}")
            print(line)
        print("-" * 100)

        if nr >= max_lines:
            break

def main():
    parser = argparse.ArgumentParser(description="Check and preview a batch by batch_id.")
    parser.add_argument("batch_id", type=str, help="Batch ID to check")
    parser.add_argument("--base-url", type=str, default="http://localhost:5000/v1", help="Base URL for the API")
    parser.add_argument("--max-lines", type=int, default=10, help="Maximum number of lines to preview from output file")
    args = parser.parse_args()

    print_batch_preview(args.batch_id, args.base_url, args.max_lines)

if __name__ == "__main__":
    main()