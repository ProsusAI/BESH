import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(
        description="Delete all files and/or batches from the API."
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:5000/v1",
        help="Base URL for the API (default: %(default)s)",
    )
    parser.add_argument(
        "--files",
        action="store_true",
        help="Delete all files",
    )
    parser.add_argument(
        "--batches",
        action="store_true",
        help="Delete all batches",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=5,
        help="Number of threads for file deletion (default: %(default)s)",
    )
    return parser.parse_args()

def delete_file(file_id, base_url):
    """Delete a single file by ID"""
    try:
        response = requests.delete(f"{base_url}/files/{file_id}")
        response.raise_for_status()  # Raise an exception for bad status codes
        print(f"✅ Deleted file {file_id}")
        return True, file_id
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to delete file {file_id}: {e}")
        return False, file_id

def delete_all_files(base_url, threads):
    # Get all files
    response = requests.get(f"{base_url}/files")
    uploaded_file_ids = [file['id'] for file in response.json()['data']]
    print(f"✅ Found {len(uploaded_file_ids)} files")

    if uploaded_file_ids:
        successful_deletions = 0
        failed_deletions = 0

        with ThreadPoolExecutor(max_workers=threads) as executor:
            # Submit all deletion tasks
            future_to_file_id = {
                executor.submit(delete_file, file_id, base_url): file_id
                for file_id in uploaded_file_ids
            }

            # Process completed tasks
            for future in as_completed(future_to_file_id):
                success, file_id = future.result()
                if success:
                    successful_deletions += 1
                else:
                    failed_deletions += 1

        print(f"\n📊 Deletion Summary:")
        print(f"   ✅ Successfully deleted: {successful_deletions} files")
        print(f"   ❌ Failed to delete: {failed_deletions} files")
    else:
        print("No files to delete")

def delete_all_batches(base_url):
    while True:
        response = requests.get(f"{base_url}/batches")
        batches = response.json()['data']
        print(f"✅ Found {len(batches)} batches")

        if len(batches) == 0:
            break

        for batch in batches:
            response = requests.delete(f"{base_url}/batches/{batch['id']}")
            print(f"✅ Deleted batch {batch['id']}")

if __name__ == "__main__":
    args = parse_args()
    if not args.files and not args.batches:
        print("Nothing to do. Use --files and/or --batches to specify what to delete.")
        sys.exit(0)
    if args.files:
        delete_all_files(args.base_url, args.threads)
    if args.batches:
        delete_all_batches(args.base_url)