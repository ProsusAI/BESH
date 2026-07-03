# Copyright © 2025 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Override OPENAI_API_KEY with TEST_API_KEY if set
if os.getenv('TESTING') == 'active':
    print(f"\n\n\nOverriding OPENAI_API_KEY with TEST_API_KEY\n\n\n")
    os.environ['OPENAI_API_KEY'] = os.getenv('TEST_API_KEY')

class Config:
    """Base configuration class"""
    
    # Database Configuration
    BASE_DIR = Path(__file__).parent.parent
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:')
    
    # File Upload Configuration
    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/tmp/batch_files').rstrip('/')
    
    # vLLM/OpenAI Configuration
    OPENAI_API_BASE = os.getenv('OPENAI_API_BASE', 'http://localhost:8000/v1')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'dummy-key')
    
    # Batch Processing Configuration
    MAX_WORKERS = int(os.getenv('MAX_WORKERS', 64))
    MAX_CONCURRENT_BATCHES = int(os.getenv('MAX_CONCURRENT_BATCHES', 1))
    
    # HuggingFace Configuration
    HF_TOKEN = os.getenv('HF_TOKEN', os.getenv('HUGGING_FACE_HUB_TOKEN', 'get_your_own'))
    HUGGING_FACE_HUB_TOKEN = os.getenv('HUGGING_FACE_HUB_TOKEN', os.getenv('HF_TOKEN', 'get_your_own'))
    
    # Model Configuration
    MODEL_NAME = os.getenv('MODEL_NAME', 'openai/gpt-oss-20b')
    
    # vLLM Server Configuration
    VLLM_HOST = os.getenv('VLLM_HOST', '0.0.0.0')
    VLLM_PORT = int(os.getenv('VLLM_PORT', 8000))
    TENSOR_PARALLEL_SIZE = int(os.getenv('TENSOR_PARALLEL_SIZE', 1))
    
    # API Configuration
    API_PORT = int(os.getenv('API_PORT', 8080))

def get_config():
    """Get configuration object by name"""
    return Config()