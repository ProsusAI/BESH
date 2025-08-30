import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

class Config:
    """Base configuration class"""
    
    # Flask Configuration
    SECRET_KEY = os.getenv('SECRET_KEY', 'you_should_replace_this')
    FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.getenv('FLASK_PORT', 5000))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    
    # Database Configuration
    BASE_DIR = Path(__file__).parent.parent
    db_path = BASE_DIR / 'src' / 'database' / 'app.db'
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'SQLALCHEMY_DATABASE_URI', 
        f"sqlite:///{db_path.as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = os.getenv('SQLALCHEMY_TRACK_MODIFICATIONS', 'False').lower() == 'true'
    
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
    API_PORT = int(os.getenv('API_PORT', 5000))
    
    @classmethod
    def init_app(cls, app):
        """Initialize Flask app with configuration"""
        app.config.from_object(cls)
        
        # Ensure upload folder exists
        os.makedirs(cls.UPLOAD_FOLDER, exist_ok=True)
        
        # Set environment variables for libraries that need them
        os.environ['OPENAI_API_BASE'] = cls.OPENAI_API_BASE
        os.environ['OPENAI_API_KEY'] = cls.OPENAI_API_KEY

class DevelopmentConfig(Config):
    """Development configuration"""
    FLASK_DEBUG = True

class ProductionConfig(Config):
    """Production configuration"""
    FLASK_DEBUG = False
    
    # Override with more secure production settings
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY must be set in production environment")

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

def get_config(config_name=None):
    """Get configuration object by name"""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')
    return config.get(config_name, DevelopmentConfig)