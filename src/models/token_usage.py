from src.models.user import db
from datetime import datetime

class TokenUsage(db.Model):
    __tablename__ = 'token_usage'
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(50), nullable=False, index=True)
    request_id = db.Column(db.String(50), nullable=False)
    custom_id = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(50), nullable=True)
    total_tokens = db.Column(db.Integer, default=0)
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    cost = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<TokenUsage batch_id={self.batch_id} tokens={self.total_tokens} cost={self.cost}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'batch_id': self.batch_id,
            'request_id': self.request_id,
            'custom_id': self.custom_id,
            'model': self.model,
            'total_tokens': self.total_tokens,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'cost': self.cost,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @staticmethod
    def get_batch_summary(batch_id):
        """Get token usage summary for a specific batch"""
        from sqlalchemy import func
        result = db.session.query(
            func.sum(TokenUsage.total_tokens).label('total_tokens'),
            func.sum(TokenUsage.prompt_tokens).label('prompt_tokens'),
            func.sum(TokenUsage.completion_tokens).label('completion_tokens'),
            func.sum(TokenUsage.cost).label('total_cost'),
            func.count(TokenUsage.id).label('request_count')
        ).filter(TokenUsage.batch_id == batch_id).first()
        
        return {
            'batch_id': batch_id,
            'total_tokens': result.total_tokens or 0,
            'prompt_tokens': result.prompt_tokens or 0,
            'completion_tokens': result.completion_tokens or 0,
            'total_cost': float(result.total_cost or 0.0),
            'request_count': result.request_count or 0
        }
