from src.models.user import db
from datetime import datetime
from sqlalchemy import Index

# Use SQLAlchemy's native JSON type instead of Text columns + manual (de)serialization.
try:
    from sqlalchemy.dialects.postgresql import JSON  # Prefer Postgres implementation when available
except ImportError:  # Fallback – SQLAlchemy exposes a generic JSON type that works for SQLite/MySQL
    from sqlalchemy.types import JSON  # type: ignore

class Batch(db.Model):
    __tablename__ = 'batches'
    
    id = db.Column(db.String(50), primary_key=True)
    object = db.Column(db.String(20), default='batch')
    endpoint = db.Column(db.String(100), nullable=False)
    input_file_id = db.Column(db.String(50), nullable=False)
    completion_window = db.Column(db.String(10), default='24h')
    status = db.Column(db.String(20), default='validating')
    output_file_id = db.Column(db.String(50), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    in_progress_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    failed_at = db.Column(db.DateTime, nullable=True)
    expired_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    finalizing_at = db.Column(db.DateTime, nullable=True)
    
    # JSON fields – leverage native JSON where supported (falls back to Text for SQLite)
    metadata_json = db.Column(JSON, default=dict)
    request_counts_json = db.Column(JSON, default=dict)
    errors_json = db.Column(JSON, default=list)

    # Explicit indexes for common query predicates
    __table_args__ = (
        Index("ix_batches_status", "status"),
        Index("ix_batches_created_at", "created_at"),
    )
    
    @property
    def batch_metadata(self):
        # JSON column already returns Python dict – ensure empty dict fallback
        return self.metadata_json or {}
    
    @batch_metadata.setter
    def batch_metadata(self, value):
        self.metadata_json = value or {}
    
    @property
    def request_counts(self):
        return self.request_counts_json or {}
    
    @request_counts.setter
    def request_counts(self, value):
        self.request_counts_json = value or {}
    
    @property
    def errors(self):
        return self.errors_json or []
    
    @errors.setter
    def errors(self, value):
        self.errors_json = value or []
    
    def to_dict(self):
        """Convert batch to dictionary for JSON response"""
        result = {
            'id': self.id,
            'object': self.object,
            'endpoint': self.endpoint,
            'input_file_id': self.input_file_id,
            'completion_window': self.completion_window,
            'status': self.status,
            'created_at': int(self.created_at.timestamp()) if self.created_at else None,
            'metadata': self.batch_metadata
        }
        
        # Add optional fields if they exist
        if self.output_file_id:
            result['output_file_id'] = self.output_file_id
        
        if self.in_progress_at:
            result['in_progress_at'] = int(self.in_progress_at.timestamp())
        
        if self.completed_at:
            result['completed_at'] = int(self.completed_at.timestamp())
        
        if self.failed_at:
            result['failed_at'] = int(self.failed_at.timestamp())
        
        if self.expired_at:
            result['expired_at'] = int(self.expired_at.timestamp())
        
        if self.cancelled_at:
            result['cancelled_at'] = int(self.cancelled_at.timestamp())
        
        if self.expires_at:
            result['expires_at'] = int(self.expires_at.timestamp())
        
        if self.finalizing_at:
            result['finalizing_at'] = int(self.finalizing_at.timestamp())
        
        if self.request_counts:
            result['request_counts'] = self.request_counts
        
        if self.errors:
            result['errors'] = self.errors
        
        return result

